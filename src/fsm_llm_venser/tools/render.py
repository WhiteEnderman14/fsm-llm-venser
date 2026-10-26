"""Text table and Graphviz drawing of a compiled machine."""

from __future__ import annotations

from pathlib import Path

from graphviz import Digraph, ExecutableNotFound

from fsm_llm_venser.engine.trace import TurnRecord
from fsm_llm_venser.machine.model import Alt, FlatMachine


def edge_label(event: str, alt: Alt) -> str:
    """Label of an arrow, in the notation of the slides: EVENT [guard] / actions.

    Args:
        event (str): The event.
        alt (Alt): The alternative that the arrow represents.

    Returns:
        str: The label text.
    """
    label = event
    if alt.guard:
        label += f" [{alt.guard}]"
    if alt.do:
        label += " / " + ", ".join(map(str, alt.do))
    return label


def _short(name: str) -> str:
    """The last part of a qualified name: EMOTIONS.INVITE -> INVITE.

    Args:
        name (str): A qualified state name.

    Returns:
        str: The last part of the name.
    """
    return name.rsplit(".", 1)[-1]


def transition_table(machine: FlatMachine) -> str:
    """Print the compiled transition table: for every state its arrows, fallbacks and on_invalid.

    Args:
        machine (FlatMachine): The compiled machine.

    Returns:
        str: The table as text.
    """
    lines = [
        f"machine {machine.name}   initial: {machine.initial}   max_retries: {machine.max_retries}"
    ]
    for state in machine.states.values():
        lines.append(f"\n{state.name}")
        for event, alts in state.transitions.items():
            for alt in alts:
                note = "   (from_any_state)" if alt.origin != "own" else ""
                lines.append(f"  {edge_label(event, alt)} -> {alt.to}{note}")
        if state.fallback:
            rules = ", ".join(f"{e} -> {r}" for e, r in state.fallback.items())
            lines.append(f"  fallback after {machine.max_retries} retries: {rules}")
        if state.on_invalid:
            lines.append(f"  on_invalid: {state.on_invalid}")
    lines.append("\nfinals: " + ", ".join(machine.finals))
    return "\n".join(lines)


def path_of(trace: list[TurnRecord]) -> set[tuple[str, str]]:
    """The arrows a run went through, to draw them in red.

    Args:
        trace (list[TurnRecord]): The list of TurnRecord of a run.

    Returns:
        set[tuple[str, str]]: A set of (state, next state) pairs.
    """
    return {(turn.state, turn.next_state) for turn in trace}


def to_graphviz(machine: FlatMachine, highlight: set[tuple[str, str]] | None = None) -> Digraph:
    """Build the diagram, with one box (cluster) for each nested machine.

    Arrows from from_any_state are not drawn one by one: a note lists them.

    Args:
        machine (FlatMachine): The compiled machine.
        highlight (set[tuple[str, str]] | None): Arrows (state, next state) to draw in red.

    Returns:
        Digraph: The diagram.
    """
    highlight = highlight or set()
    dot = Digraph(
        name=machine.name,
        graph_attr={"rankdir": "LR", "fontname": "Helvetica"},
        node_attr={"fontname": "Helvetica"},
        edge_attr={"fontname": "Helvetica", "fontsize": "10"},
    )

    # states and composites grouped by the composite that holds them
    children: dict[str | None, list[str]] = {}
    for name, parent in machine.parent.items():
        children.setdefault(parent, []).append(name)

    def add_members(graph: Digraph, parent: str | None) -> None:
        """Add the nodes of a level; a composite becomes a cluster with its own nodes.

        Args:
            graph (Digraph): The graph (or cluster) to add nodes to.
            parent (str | None): Qualified name of the composite, or None for the root level.
        """
        for name in children.get(parent, []):
            if name in machine.initials:
                with graph.subgraph(name=f"cluster_{name.replace('.', '_')}") as sub:
                    sub.attr(label=_short(name), style="rounded", fontname="Helvetica")
                    add_members(sub, name)
            else:
                graph.node(name, label=_short(name), shape="ellipse")

    add_members(dot, None)
    for final in machine.finals:
        dot.node(final, shape="doublecircle")
    dot.node("__start__", shape="point")
    dot.edge("__start__", machine.initial)

    def edge(src: str, dst: str, label: str, **style: str) -> None:
        """Draw one arrow, in red if it is on the path."""
        if (src, dst) in highlight:
            style.update(color="red", fontcolor="red", penwidth="2")
        dot.edge(src, dst, label=label, **style)

    inherited = set()
    for state in machine.states.values():
        for event, alts in state.transitions.items():
            for alt in alts:
                if alt.origin == "own":
                    edge(state.name, alt.to, edge_label(event, alt))
                else:
                    inherited.add(f"{event} -> {alt.to}")
        for event, replacement in state.fallback.items():  # dashed: taken after the retries
            target = state.transitions[replacement][0].to
            label = f"after {machine.max_retries} retries: {event} -> {replacement}"
            edge(state.name, target, label, style="dashed")
    if inherited:
        dot.node(
            "__any__", label="from any state:\\n" + "\\n".join(sorted(inherited)), shape="note"
        )
    return dot


def save_svg(dot: Digraph, path: str | Path) -> Path:
    """Write the diagram as an SVG file with the system `dot` program.

    Args:
        dot (Digraph): The diagram.
        path (str | Path): Output file.

    Returns:
        Path: The path written.

    Raises:
        RuntimeError: If Graphviz is not installed.
    """
    try:
        svg = dot.pipe(format="svg")
    except ExecutableNotFound:
        raise RuntimeError(
            "Graphviz is not installed: install the `graphviz` package of your system"
        ) from None
    path = Path(path)
    path.write_bytes(svg)
    return path
