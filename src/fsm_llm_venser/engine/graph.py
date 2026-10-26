"""The LangGraph graph of a machine: one node per state, as in the course notebook.

Every state node plays one turn and then a router picks the node of the new state. A final
state is a node that closes the run, and one extra node stops the run on an error or when the
turn cap is reached.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from fsm_llm_venser.engine.runner import EngineError, Runner
from fsm_llm_venser.engine.trace import Result, RunState
from fsm_llm_venser.tools.replies import Replier

STOP = "__stop__"


class GraphState(TypedDict, total=False):
    """What the graph passes from node to node."""

    rs: RunState
    replier: Any
    max_turns: int
    error: str
    result: Result


def build_graph(runner: Runner):
    """Build and compile the graph of a machine.

    Args:
        runner (Runner): The runner whose machine becomes the graph.

    Returns:
        A compiled LangGraph app.
    """
    machine = runner.machine
    graph = StateGraph(GraphState)

    def turn(data: GraphState) -> GraphState:
        """Node of a state: play one turn.

        Args:
            data (GraphState): What the graph passes from node to node.

        Returns:
            GraphState: What the node changes in the state.
        """
        try:
            runner.step(data["rs"], data["replier"])
        except EngineError as exc:
            return {"error": str(exc)}
        return {}

    def close(data: GraphState) -> GraphState:
        """Node of a final state: close the run.

        Args:
            data (GraphState): What the graph passes from node to node.

        Returns:
            GraphState: What the node changes in the state.
        """
        return {"result": runner.finish(data["rs"])}

    def stop(data: GraphState) -> GraphState:
        """Node that ends a run that did not reach a final state.

        Args:
            data (GraphState): What the graph passes from node to node.

        Returns:
            GraphState: What the node changes in the state.
        """
        if data.get("error"):
            return {"result": runner.end(data["rs"], "error", message=data["error"])}
        return {"result": runner.end(data["rs"], "max_turns", message="turn cap reached")}

    def route(data: GraphState) -> str:
        """Choose the next node: the one of the current state, or stop.

        Args:
            data (GraphState): What the graph passes from node to node.

        Returns:
            str: The name of the next node.
        """
        rs = data["rs"]
        if data.get("error"):
            return STOP
        if rs.state in machine.finals:
            return rs.state
        return STOP if rs.turns >= data["max_turns"] else rs.state

    for name in machine.states:
        graph.add_node(name, turn)
    for name in machine.finals:
        graph.add_node(name, close)
    graph.add_node(STOP, stop)

    nodes = [*machine.states, *machine.finals, STOP]
    graph.add_conditional_edges(START, route, {name: name for name in nodes})
    for name in machine.states:
        graph.add_conditional_edges(name, route, {n: n for n in nodes})
    for name in [*machine.finals, STOP]:
        graph.add_edge(name, END)
    return graph.compile()


def run_graph(runner: Runner, replier: Replier, rs: RunState, max_turns: int) -> Result:
    """Run the graph from the state `rs` until a final state, an error or the turn cap.

    Args:
        runner (Runner): The runner.
        replier (Replier): Gives the replies.
        rs (RunState): The state of the run (already started).
        max_turns (int): Turn cap.

    Returns:
        Result: The result of the run.
    """
    app = build_graph(runner)
    # one node run per turn, plus the closing node; leave some room
    out = app.invoke(
        {"rs": rs, "replier": replier, "max_turns": max_turns},
        config={"recursion_limit": max_turns + 10},
    )
    return out["result"]
