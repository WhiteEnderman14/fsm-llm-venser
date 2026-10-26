"""General command line of the engine: `fsm-llm-venser <command>`.

It works with any machine file; nothing here is specific to one machine.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

import yaml
from pydantic import ValidationError

from fsm_llm_venser import __version__
from fsm_llm_venser.engine.runner import EngineError, Runner
from fsm_llm_venser.engine.trace import Result, TurnRecord
from fsm_llm_venser.llm.providers import format_models, get_llm
from fsm_llm_venser.machine.compiler import CompileError, compile_machine
from fsm_llm_venser.machine.model import FlatMachine
from fsm_llm_venser.machine.schema import load_machine
from fsm_llm_venser.plugins.base import Plugin, load_plugin
from fsm_llm_venser.tools.render import path_of, save_svg, to_graphviz, transition_table
from fsm_llm_venser.tools.replies import InteractiveReplier, ScriptedReplier, load_scenario

# errors caused by wrong input (file, plugin, options): one line and exit code 1
INPUT_ERRORS = (
    yaml.YAMLError,
    ValidationError,
    CompileError,
    ImportError,
    ValueError,
    OSError,
    RuntimeError,
)


def _load(args: argparse.Namespace, for_llm: bool) -> tuple[FlatMachine, Plugin | None]:
    """Read the machine file (and the plugin, if given) and compile the machine.

    Args:
        args (argparse.Namespace): The parsed command line.
        for_llm (bool): Also check what is needed to talk to an LLM.

    Returns:
        tuple[FlatMachine, Plugin | None]: The compiled machine and the plugin.
    """
    plugin = load_plugin(args.plugin) if getattr(args, "plugin", None) else None
    return compile_machine(load_machine(args.file), for_llm=for_llm), plugin


def cmd_validate(args: argparse.Namespace) -> int:
    """validate: check the file and print a summary.

    Args:
        args (argparse.Namespace): The parsed command line.

    Returns:
        int: The exit code (0 ok, 1 wrong input, 3 the run did not end in a final state).
    """
    flat, _ = _load(args, for_llm=True)
    print(
        f"OK: {flat.name}: {len(flat.states)} states ({len(flat.initials)} composite), "
        f"{len(flat.finals)} finals, {len(flat.events)} events"
    )
    return 0


def cmd_table(args: argparse.Namespace) -> int:
    """table: print the compiled transition table.

    Args:
        args (argparse.Namespace): The parsed command line.

    Returns:
        int: The exit code (0 ok, 1 wrong input, 3 the run did not end in a final state).
    """
    flat, _ = _load(args, for_llm=False)
    print(transition_table(flat))
    return 0


def cmd_draw(args: argparse.Namespace) -> int:
    """draw: write the diagram of the machine as an SVG file.

    Args:
        args (argparse.Namespace): The parsed command line.

    Returns:
        int: The exit code (0 ok, 1 wrong input, 3 the run did not end in a final state).
    """
    flat, _ = _load(args, for_llm=False)
    out = args.output or f"{flat.name}.svg"
    print(f"wrote {save_svg(to_graphviz(flat), out)}")
    return 0


def _show(result: Result) -> None:
    """Print the end of a run.

    Args:
        result (Result): The result of the run.
    """
    if result.final_text:
        print(f"\n{result.final_text}")
    note = f" ({result.message})" if result.message else ""
    print(
        f"\nstatus: {result.status}{note}; final state: {result.final_state}; turns: {result.turns}"
    )


def cmd_run_word(args: argparse.Namespace) -> int:
    """run-word: feed a sequence of events, without LLM.

    Args:
        args (argparse.Namespace): The parsed command line.

    Returns:
        int: The exit code (0 ok, 1 wrong input, 3 the run did not end in a final state).
    """
    flat, plugin = _load(args, for_llm=False)

    def on_turn(record: TurnRecord) -> None:
        """Print one turn."""
        replaced = f" (was {record.replaced})" if record.replaced else ""
        print(f"{record.state} --{record.event}--> {record.next_state}{replaced}")

    result = Runner(flat, plugin, on_turn=on_turn).run_events(args.events, args.max_turns)
    _show(result)
    return 0 if result.status == "final" else 3


def cmd_run(args: argparse.Namespace) -> int:
    """run: run the machine with an LLM.

    Args:
        args (argparse.Namespace): The parsed command line.

    Returns:
        int: The exit code (0 ok, 1 wrong input, 3 the run did not end in a final state).
    """
    flat, plugin = _load(args, for_llm=True)
    if args.scenario:
        replier = ScriptedReplier(load_scenario(args.scenario))
    elif args.interactive:
        replier = InteractiveReplier()
    else:
        raise ValueError("choose where the replies come from: --scenario FILE or --interactive")

    def on_turn(record: TurnRecord) -> None:
        """Print one turn."""
        if not args.interactive:
            print(f"\nQ: {record.question}\nA: {record.reply}")
        source = record.analysis["source"] if record.analysis else "-"
        print(f"   [{source}] {record.state} --{record.event}--> {record.next_state}")

    client = get_llm(args.provider, args.model, args.temperature)
    result = Runner(flat, plugin, client, on_turn).run(replier, args.max_turns)
    _show(result)
    if args.draw:
        print(f"wrote {save_svg(to_graphviz(flat, path_of(result.trace)), args.draw)}")
    return 0 if result.status == "final" else 3


def cmd_check_llm(args: argparse.Namespace) -> int:
    """check-llm: send one tiny request to see if the model answers.

    Args:
        args (argparse.Namespace): The parsed command line.

    Returns:
        int: The exit code (0 ok, 1 wrong input, 3 the run did not end in a final state).
    """
    if args.plugin:  # a plugin can add providers
        load_plugin(args.plugin)
    client = get_llm(args.provider, args.model)
    try:
        reply = client.write("You are a connection test.", "Reply with the single word: ok")
    except Exception as exc:  # network, missing model, wrong key: anything the provider raises
        hint = ""
        if args.provider == "ollama":
            hint = "\nIs the server running (`ollama serve`), the model pulled (`ollama pull llama3.2`)\nand the server reachable at OLLAMA_HOST (in a container: http://ollama:11434)?"
        print(f"the model did not answer: {exc}{hint}", file=sys.stderr)
        return 1
    print(reply.strip())
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """models: list the known models.

    Args:
        args (argparse.Namespace): The parsed command line.

    Returns:
        int: The exit code (0 ok, 1 wrong input, 3 the run did not end in a final state).
    """
    if args.plugin:
        load_plugin(args.plugin)
    print(format_models())
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Define the commands and their options."""
    parser = argparse.ArgumentParser(prog="fsm-llm-venser", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(
        name: str,
        func: Callable[[argparse.Namespace], int],
        help_: str,
        machine: bool = True,
        plugin: bool = True,
    ):
        """Add a command; most take a machine file and an optional plugin.

        Args:
            name (str): Name of the command.
            func (Callable): Function that runs the command.
            help_ (str): Help text of the command.
            machine (bool): Whether the command takes a machine file.
            plugin (bool): Whether it also takes a plugin file.
        """
        p = sub.add_parser(name, help=help_)
        p.set_defaults(func=func)
        if machine:
            p.add_argument("file", help="machine file (YAML)")
        if plugin:
            p.add_argument("--plugin", help="plugin file (Python)")
        return p

    command("validate", cmd_validate, "check a machine file")
    command("table", cmd_table, "print the compiled transition table", plugin=False)

    p = command("draw", cmd_draw, "draw the machine as an SVG file", plugin=False)
    p.add_argument("-o", "--output", help="output file (default: <name>.svg)")

    p = command("run-word", cmd_run_word, "run a sequence of events (no LLM)")
    p.add_argument("events", nargs="+")
    p.add_argument("--max-turns", type=int, default=100)

    p = command("run", cmd_run, "run the machine with an LLM")
    p.add_argument("--scenario", help="scenario file with scripted replies")
    p.add_argument("--interactive", action="store_true", help="type the replies yourself")
    p.add_argument(
        "--provider", default="ollama", help="ollama, gemini, claude or one added by a plugin"
    )
    p.add_argument("--model", help="model name (default: the default of the provider)")
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--max-turns", type=int, default=100)
    p.add_argument(
        "--draw", metavar="SVG", help="draw the machine with the path of this run in red"
    )

    p = command("check-llm", cmd_check_llm, "check that a model answers", machine=False)
    p.add_argument(
        "--provider", default="ollama", help="ollama, gemini, claude or one added by a plugin"
    )
    p.add_argument("--model")

    command("models", cmd_models, "list the known models", machine=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv (Sequence[str] | None): Arguments (default: sys.argv).

    Returns:
        Exit code: 0 ok, 1 wrong input, 3 the run did not end in a final state.
    """
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CompileError as exc:
        print("Invalid machine:\n  " + "\n  ".join(exc.errors), file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"Invalid machine file:\n{exc}", file=sys.stderr)
        return 1
    except EngineError as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 3
    except INPUT_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
