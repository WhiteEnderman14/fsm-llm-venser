# Writing plugins

A machine file only names guards and actions. The Python code behind those names lives in a
plugin, a normal `.py` file that you pass with `--plugin`. The YAML never loads or runs code, so
you can read a machine file without trusting it; a plugin is different, it runs with your
privileges, so read it before you use someone else's.

You do not need a plugin to start. The two guards listed [below](#guards-that-come-with-the-project)
work in every machine. Write a plugin when you want to keep data between turns, ask the model for
extra information about each reply, build something at the end of the run, or log what happens.

Contents: [the three kinds](#the-three-kinds) | [a first plugin](#a-first-plugin) |
[guards](#guards) | [actions](#actions) | [variables](#variables) | [listeners](#listeners-and-hooks) |
[ctx](#ctx) | [extra fields from the classifier](#extra-fields-from-the-classifier) |
[guards that come with the project](#guards-that-come-with-the-project) |
[adding an LLM provider](#adding-an-llm-provider) | [running a plugin](#running-a-plugin) | [errors](#errors) | [checklist](#checklist)

## The three kinds

| Kind | Called from | Signature | May |
|---|---|---|---|
| guard | `guard:` in a transition | `(ctx, *args) -> bool` | only read |
| action | `do`, `entry`, `exit`, `on_any_transition` in the machine file | `(ctx, *args) -> None` | change `ctx.vars` |
| listener | not in the file; subscribes to a hook in Python | `(ctx, hook) -> None` | only observe |

The split is on purpose. Anything that changes what happens next belongs in the machine file, as
a guard or an action, where you can see it. A listener cannot change the run, so it is the right
place for logging, a progress bar or a user interface.

## A first plugin

A file that counts answers and prints when a stage is entered:

```python
"""Plugin of a small interview."""

from pydantic import BaseModel

from fsm_llm_venser.engine.context import Context
from fsm_llm_venser.plugins.base import Plugin


class Vars(BaseModel):
    """Variables kept between turns."""

    answers: int = 0


plugin = Plugin("demo", vars_model=Vars)


@plugin.action
def count_answer(ctx: Context) -> None:
    """Count one answer.

    Args:
        ctx (Context): The context of the turn.
    """
    ctx.vars.answers += 1


@plugin.guard
def talked_enough(ctx: Context, limit: int) -> bool:
    """True once the person has answered `limit` times.

    Args:
        ctx (Context): The context of the turn.
        limit (int): Number of answers.

    Returns:
        bool: True if the count reached the limit.
    """
    return ctx.vars.answers >= limit


@plugin.listener("enter")
def show_state(ctx: Context, hook: str) -> None:
    """Print each state when it is entered.

    Args:
        ctx (Context): The context of the turn.
        hook (str): The hook point.
    """
    print("entered", ctx.state)
```

And the machine file that uses it:

```yaml
on_any_transition:
  - count_answer                          # action, no argument

states:
  ASK:
    transitions:
      ANSWER:
        - to: DONE
          guard: {talked_enough: 3}       # guard with one argument
        - to: ASK
```

Run it with `fsm-llm-venser run machine.yaml --plugin plugin.py ...`. The file must define a
module-level `plugin = Plugin(...)`, otherwise loading fails with a clear message.
`examples/narrative_medicine_interview/plugin.py` is a complete example that also builds a final
result with the model. `examples/support_ticket/plugin.py` shows every kind of plugin code (a
guard, actions with and without arguments, five kinds of listener, `vars_model`, `analysis_model`)
and is the one to copy from for a feature.

## Guards

```python
@plugin.guard
def long_interview(ctx):
    return ctx.turns > 20
```

- The first argument is always `ctx`. The arguments after it are the ones written in the YAML:
  `{talked_enough: 3}` calls `talked_enough(ctx, 3)`, `{between: [2, 5]}` calls `between(ctx, 2, 5)`.
- Return a real `bool`. A guard that raises stops the run (see [errors](#errors)).
- A guard gets a copy of the context. Changing `ctx.vars` inside it has no effect on the run.
- The name in the YAML is the function name. Use `@plugin.guard(name="other")` to choose another.
- Two guards with the same name in one plugin raise an error when the file is loaded.

Guards are checked in the order of the alternatives and checked only for the event that
happened, so keep them cheap and free of side effects.

## Actions

```python
@plugin.action
def inc(ctx, name):
    setattr(ctx.vars, name, getattr(ctx.vars, name) + 1)
```

An action is the only place that may change `ctx.vars`. It can be used in four places:

| Where in the file | Runs |
|---|---|
| `do` of a transition | when that alternative is taken |
| `entry` / `exit` of a state or composite | when it is entered / left |
| `entry` of a final state | when the run ends there |
| `on_any_transition` | after every transition at that level and below |

The order inside one move is: `exit` actions (innermost first), `do`, `on_any_transition`,
`entry` actions (outermost first). When the machine runs on events only (`run-word`),
`ctx.analysis` is `None` and `ctx.llm` is `None`, so an action that depends on them should check
first, as `record_note` does in the example.

## Variables

`vars_model` is a Pydantic model with a default for every field. The engine creates one instance
at the start of the run and gives it to actions as `ctx.vars`.

```python
class Vars(BaseModel):
    notes: dict[str, list[str]] = Field(default_factory=dict)
    heard: str = "(nothing yet)"

plugin = Plugin("narrative", vars_model=Vars)
```

- Without a `vars_model`, `ctx.vars` is a plain dict.
- Every field can be used as a `{placeholder}` in the prompts and in `say` texts: a field called
  `heard` is `{heard}`. A name that is neither a built-in placeholder nor a field stops the run
  with a message.
- Guards and listeners get a deep copy, so they cannot change the real object by accident.
- The variables are in the result of the run (`result.vars`).

## Listeners and hooks

A listener watches a point of the run:

```python
@plugin.listener("enter", state="EMOTIONS.*")
def log_enter(ctx, hook):
    print("entered", ctx.state)

@plugin.listener("transition", event="DISTRESS")
def alert(ctx, hook):
    ...
```

`state` accepts a qualified name or a glob (`EMOTIONS.*`), `event` an event name. Both are optional;
without them the listener is called every time.

| Hook | Fires |
|---|---|
| `start` | once, before the first question |
| `enter`, `exit` | for every state and every composite entered or left, in the order of the move |
| `utterance` | after the machine has said something (a question or a final text) |
| `classified` | after the reply became an event, before the move |
| `transition` | after a move. `ctx.state` is the state where the question was asked |
| `final` | when the run reaches a final state |
| `end` | at the very end, whatever the status |

An unknown hook name raises an error when the plugin is loaded, so a typo shows up at once.

## ctx

| Field | Meaning |
|---|---|
| `state` | Qualified current state. In `enter` / `exit` hooks it is the state or composite concerned. |
| `event` | The event of this turn. |
| `analysis` | What the classifier returned (`event`, `rationale`, `source`, plus your extra fields). `None` in event mode. |
| `question` | The question just asked. |
| `last_reply` | The latest reply of the person. |
| `counts` | How many times each event has happened, this turn not counted. |
| `retries` | Retries in the current state (what `max_retries` is compared with). |
| `turns` | Turns completed so far. |
| `vars` | The variables of the plugin. |
| `transcript` | List of `(question, reply)` pairs so far. |
| `llm` | The LLM client, or `None` in event mode. Its `classify(system, user, Model)` returns a Pydantic object; `write(system, user)` returns text. Use it to build something at the end, like the card in the example. |

## Extra fields from the classifier

By default the model returns an event and a short rationale. A subclass of `Analysis` makes it
fill more fields in the same call, for free:

```python
from pydantic import Field
from fsm_llm_venser.llm.classifier import Analysis

class MyAnalysis(Analysis):
    note: str = Field(default="", description="One sentence summarising the reply.")
    emotions: list[str] = Field(default_factory=list, description="Emotions the person named.")

plugin = Plugin("demo", vars_model=Vars, analysis_model=MyAnalysis)
```

Every new field needs a default and a good `description`, because the description is what tells
the model what to write. Actions read them from `ctx.analysis`.

## Guards that come with the project

Always available, no plugin needed. They live in `src/fsm_llm_venser/plugins/builtin.py`, which is
also a short example of how to write one.

| Guard | True when |
|---|---|
| `when: "EXPR"` | The counter expression holds. Comparisons, `and`, `or`, `not`, whole numbers and these names: an event name (times it already happened), `retries`, `turns`. |
| `reply_matches: "REGEX"` | The latest reply matches the regular expression, ignoring case. |

```yaml
transitions:
  BRIEF:
    - to: OPENING
      guard: {when: "BRIEF < 2 and turns < 10"}
    - to: SKIPPED
  ANSWER:
    - to: URGENT
      guard: {reply_matches: '\bpain\b'}
    - to: NEXT
```

`when` is parsed, not evaluated: only the listed syntax is accepted, so a typo in the file cannot
run code. A guard with the same name in your plugin replaces the built-in one. See
[file-format.md](file-format.md#guards-and-actions) for how guards are written in the machine file.

## Adding an LLM provider

A provider is a function that builds a LangChain chat model. Register it with `register_provider`,
at the top of a plugin file, and `--provider NAME` accepts it from then on. The engine is not
touched.

```python
from fsm_llm_venser import Plugin, register_provider


def make_openai_compatible(name, temperature, options):
    """Any server that speaks the OpenAI API (LM Studio, vLLM, llama.cpp, a gateway).

    Args:
        name (str): Model name.
        temperature (float): Sampling temperature.
        options (dict): Extra options passed through.

    Returns:
        Any: The chat model.
    """
    from langchain_openai import ChatOpenAI  # pip install langchain-openai

    return ChatOpenAI(
        model=name,
        temperature=temperature,
        base_url="http://localhost:1234/v1",
        api_key="not-needed",
        **options,
    )


register_provider("local-openai", make_openai_compatible, {"qwen2.5-7b-instruct": "untested"})
plugin = Plugin("my-plugin")
```

```bash
uv run fsm-llm-venser check-llm --provider local-openai --plugin plugin.py
uv run fsm-llm-venser run machine.yaml --plugin plugin.py --interactive --provider local-openai
```

`register_provider(name, make, models=None, default=None, extra=None)`: `make` takes
`(model name, temperature, options)`; `models` maps known names to `"default"` or `"untested"` and
only feeds the `models` command; `default` is the model used when `--model` is missing; `extra` is
the pip extra shown in the error when the package is missing. Any LangChain chat model that
supports `with_structured_output` or can follow a JSON instruction works. Without the command
line, `get_llm(provider, model)` returns a client you can give to `Runner`, or you can pass your
own object with `classify(system, user, output_model)` and `write(system, user)` methods.

## Running a plugin

```bash
uv run fsm-llm-venser validate machine.yaml --plugin plugin.py
uv run fsm-llm-venser run-word machine.yaml --plugin plugin.py CONFIRM ANSWER
uv run fsm-llm-venser run machine.yaml --plugin plugin.py --interactive
```

In Python: `Runner(flat_machine, plugin, client, on_turn).run(replier)`, with `load_plugin` and
`compile_machine` from `fsm_llm_venser`. In a container, put the plugin next to the machine under
`examples/` (it is mounted) and refer to it by its path inside the container.

## Errors

An exception raised by plugin code is not caught: the run stops with the traceback, so the line
that failed is easy to find. A name used in the YAML that the plugin does not define is reported
when that part of the machine runs, not by `validate`. Try the paths with `run-word` first.

## Checklist

- the file defines `plugin = Plugin(...)` at module level;
- every guard and action named in the YAML exists, with the right number of arguments;
- guards only read; only actions touch `ctx.vars`;
- fields of `vars_model` have defaults; extra analysis fields have defaults and descriptions;
- an action that needs the model or the analysis copes with `None`.
