"""The turn logic. Both ways of running (events, or text with an LLM) use the same _advance."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from fsm_llm_venser.engine.context import Context
from fsm_llm_venser.engine.trace import Result, RunState, TurnRecord
from fsm_llm_venser.llm.classifier import Analysis, classify_turn
from fsm_llm_venser.llm.utterance import fallback_question, final_text, write_question
from fsm_llm_venser.machine.model import Alt, Call, FlatMachine, FlatState
from fsm_llm_venser.plugins import builtin
from fsm_llm_venser.plugins.base import Plugin
from fsm_llm_venser.tools.replies import Replier

log = logging.getLogger("fsm_llm_venser")


class EngineError(RuntimeError):
    """A run cannot continue (for example the LLM failed and the state has no fallback text)."""


class Runner:
    """Runs a compiled machine.

    Args:
        machine: The compiled machine.
        plugin: The plugin with the guards, actions and listeners the machine names.
        llm: The LLM client (not needed to run a sequence of events).
        on_turn: Called with the record of every turn, e.g. to print it.

    Raises:
        ValueError: If the machine names a guard or action that nobody provides.
    """

    def __init__(
        self,
        machine: FlatMachine,
        plugin: Plugin | None = None,
        llm: Any = None,
        on_turn: Callable[[TurnRecord], None] | None = None,
    ) -> None:
        """Create the object."""
        self.machine = machine
        self.plugin = plugin or Plugin("none")
        self.llm = llm
        self.on_turn = on_turn
        # the guards of the builtin plugin are always there; the user's own ones win
        self.guards = {**builtin.plugin.guards, **self.plugin.guards}
        self.actions = self.plugin.actions
        missing = (machine.guards - set(self.guards)) | (machine.actions - set(self.actions))
        if missing:
            raise ValueError(f"the plugin does not provide: {', '.join(sorted(missing))}")

    # --- run state and context --------------------------------------------------------------

    def new_state(self) -> RunState:
        """Create the state of a new run: initial state, empty variables, counters at zero."""
        vars_ = self.plugin.vars_model() if self.plugin.vars_model else {}
        counts = {event: 0 for event in self.machine.events}
        return RunState(state=self.machine.initial, vars=vars_, counts=counts)

    def start(self, rs: RunState) -> None:
        """Begin a run: call the `start` hook, then enter the initial state (and its composites).

        Args:
            rs (RunState): The state of the run.
        """
        self._fire("start", rs)
        for name in self.machine.start_enters:
            self._run(self.machine.entry.get(name, ()), rs, [], state=name)
            self._fire("enter", rs, state=name)

    def _ctx(self, rs: RunState, **extra: Any) -> Context:
        """Build the context for plugin code. `extra` overrides fields (event, analysis, ...).

        Args:
            rs (RunState): The state of the run.
            **extra (Any): Context fields to override (event, analysis, question, ...).

        Returns:
            Context: The context.
        """
        fields = dict(
            state=rs.state,
            counts=rs.counts,
            retries=rs.retries,
            turns=rs.turns,
            vars=rs.vars,
            last_reply=rs.last_reply,
            transcript=rs.transcript,
            llm=self.llm,
        )
        return Context(**{**fields, **extra})

    # --- calling plugin code ----------------------------------------------------------------

    def _fire(
        self,
        hook: str,
        rs: RunState,
        state: str | None = None,
        event: str | None = None,
        **extra: Any,
    ) -> None:
        """Call the listeners of a hook, with a read-only context.

        Args:
            hook (str): The hook point.
            rs (RunState): The state of the run.
            state (str | None): State (or composite) the hook is about; default: the current state.
            event (str | None): The event of the hook, if any.
            **extra (Any): Context fields to override (event, analysis, question, ...).
        """
        name = state or rs.state
        for listener in self.plugin.listeners:
            if listener.matches(hook, name, event):
                ctx = self._ctx(rs, state=name, event=event, **extra).readonly()
                listener.fn(ctx, hook)

    def _run(self, calls: Iterable[Call], rs: RunState, done: list[str], **extra: Any) -> None:
        """Run actions with the live variables and write their names in `done`.

        Args:
            calls (Iterable[Call]): The calls to run.
            rs (RunState): The state of the run.
            done (list[str]): List where the names of the executed calls are added.
            **extra (Any): Context fields to override (event, analysis, question, ...).
        """
        for call in calls:
            self.actions[call.name](self._ctx(rs, **extra), *call.args)
            done.append(str(call))

    def _guard_holds(self, call: Call, rs: RunState, **extra: Any) -> bool:
        """Evaluate a guard with a read-only context.

        Args:
            call (Call): The guard to evaluate.
            rs (RunState): The state of the run.
            **extra (Any): Context fields to override (event, analysis, question, ...).

        Returns:
            bool: True if the guard holds.
        """
        ctx = self._ctx(rs, **extra).readonly()
        return bool(self.guards[call.name](ctx, *call.args))

    # --- one turn ---------------------------------------------------------------------------

    def _select(self, rs: RunState, state: FlatState, event: str, **extra: Any) -> Alt:
        """Pick the first alternative of `event` whose guard holds (no guard means it holds).

        Args:
            rs (RunState): The state of the run.
            state (FlatState): The current state.
            event (str): The event to follow.
            **extra (Any): Context fields to override (event, analysis, question, ...).

        Returns:
            Alt: The alternative to follow.
        """
        if event not in state.transitions:
            raise EngineError(f"event {event} is not enabled in {state.name}")
        for alt in state.transitions[event]:
            if alt.guard is None or self._guard_holds(alt.guard, rs, event=event, **extra):
                return alt
        raise EngineError(f"no alternative applies in {state.name} for {event}")

    def _advance(
        self, rs: RunState, event: str, analysis: Analysis | None, question: str, reply: str
    ) -> TurnRecord:
        """Take one transition: fallback, guards, exit/entry actions, counters, listeners.

        Args:
            rs (RunState): The state of the run.
            event (str): The event.
            analysis (Analysis | None): What the classifier returned (None in event mode).
            question (str): What the machine asked.
            reply (str): What the person answered.

        Returns:
            TurnRecord: The record of the turn.
        """
        machine, state = self.machine, self.machine.states[rs.state]
        rs.last_reply = reply
        extra = dict(analysis=analysis, question=question)

        alt = self._select(rs, state, event, **extra)
        replaced = None
        # a self-loop that has used up its retries is replaced by the fallback event
        if alt.to == state.name and event in state.fallback and rs.retries >= machine.max_retries:
            replaced, event = event, state.fallback[event]
            alt = self._select(rs, state, event, **extra)

        done: list[str] = []
        extra["event"] = event
        for name in alt.exits:  # innermost state first
            self._run(machine.exit.get(name, ()), rs, done, state=name, **extra)
            self._fire("exit", rs, state=name, event=event)
        self._run(alt.do, rs, done, **extra)
        self._run(alt.after, rs, done, **extra)  # on_any_transition
        for name in alt.enters:  # outermost state first
            self._run(machine.entry.get(name, ()), rs, done, state=name, **extra)
            self._fire("enter", rs, state=name, event=event)

        rs.counts[event] = rs.counts.get(event, 0) + 1
        rs.retries = rs.retries + 1 if alt.to == state.name else 0
        rs.turns += 1
        rs.transcript.append((question, reply))
        rs.state = alt.to
        record = TurnRecord(
            turn=rs.turns,
            state=state.name,
            question=question,
            reply=reply,
            analysis=analysis.model_dump() if analysis else None,
            event=event,
            replaced=replaced,
            next_state=alt.to,
            actions=done,
        )
        rs.trace.append(record)
        self._fire("transition", rs, state=state.name, event=event, analysis=analysis)
        if self.on_turn:
            self.on_turn(record)
        return record

    def step(self, rs: RunState, replier: Replier) -> TurnRecord:
        """Play one turn with the LLM: ask, listen, classify, move.

        Args:
            rs (RunState): The state of the run.
            replier (Replier): Gives the replies.

        Returns:
            TurnRecord: The record of the turn.
        """
        state = self.machine.states[rs.state]
        question = self._ask(rs, state)
        reply = replier.reply(question)
        ctx = self._ctx(rs, question=question, last_reply=reply).readonly()
        analysis = classify_turn(
            self.llm,
            self.machine,
            state,
            question,
            reply,
            ctx,
            self.plugin.analysis_model or Analysis,
        )
        self._fire("classified", rs, event=analysis.event, analysis=analysis, question=question)
        return self._advance(rs, analysis.event, analysis, question, reply)

    def _ask(self, rs: RunState, state: FlatState) -> str:
        """Get what the state says; if the LLM fails use the fallback text of the state.

        Args:
            rs (RunState): The state of the run.
            state (FlatState): The state that speaks.

        Returns:
            str: The text of the question.
        """
        ctx = self._ctx(rs).readonly()
        try:
            question = write_question(self.llm, self.machine, state, ctx)
        except Exception as exc:
            question = fallback_question(state, ctx)
            if question is None:
                raise EngineError(f"cannot write the question for {state.name}: {exc}") from exc
            log.warning("question for %s: using the fallback text (%s)", state.name, exc)
        self._fire("utterance", rs, question=question)
        return question

    # --- end of a run -----------------------------------------------------------------------

    def finish(self, rs: RunState) -> Result:
        """Close a run that reached a final state: its entry actions, its text, the hooks.

        Args:
            rs (RunState): The state of the run.

        Returns:
            Result: The result of the run.
        """
        final = self.machine.finals[rs.state]
        self._run(final.entry, rs, [])
        text = final_text(final, self._ctx(rs).readonly())
        self._fire("utterance", rs, question=text)
        self._fire("final", rs)
        return self.end(rs, "final", text)

    def end(self, rs: RunState, status: str, text: str | None = None, message: str = "") -> Result:
        """Call the `end` hook and build the result of the run.

        Args:
            rs (RunState): The state of the run.
            status (str): "final", "max_turns" or "error".
            text (str | None): Text said by the final state, if any.
            message (str): What is wrong.

        Returns:
            Result: The result of the run.
        """
        self._fire("end", rs)
        return Result(
            status=status,
            final_state=rs.state if rs.state in self.machine.finals else None,
            final_text=text,
            vars=rs.vars,
            turns=rs.turns,
            transcript=list(rs.transcript),
            trace=list(rs.trace),
            message=message,
        )

    # --- the two ways of running ------------------------------------------------------------

    def run(self, replier: Replier, max_turns: int = 100) -> Result:
        """Run the machine with the LLM. The turns are played by a LangGraph graph.

        Args:
            replier (Replier): Gives the replies (a ScriptedReplier or an InteractiveReplier).
            max_turns (int): Stop after this many turns.

        Returns:
            Result: The result of the run.
        """
        from fsm_llm_venser.engine.graph import run_graph

        rs = self.new_state()
        self.start(rs)
        return run_graph(self, replier, rs, max_turns)

    def run_events(self, events: Iterable[str], max_turns: int = 100) -> Result:
        """Run a sequence of events directly, without LLM and without questions.

        Args:
            events (Iterable[str]): The events to feed, in order.
            max_turns (int): Stop after this many turns.

        Returns:
            Result: The result. The status is "error" if an event is not accepted by the current state,
            or if the sequence does not end in a final state.
        """
        rs = self.new_state()
        self.start(rs)
        for position, event in enumerate(events, start=1):
            if rs.state in self.machine.finals:
                return self.end(
                    rs,
                    "error",
                    message=f"event {position} ({event}) comes after the final state {rs.state}",
                )
            if rs.turns >= max_turns:
                return self.end(rs, "max_turns", message="turn cap reached")
            state = self.machine.states[rs.state]
            if event not in state.transitions:
                return self.end(
                    rs, "error", message=f"event {position}: {event} is not enabled in {state.name}"
                )
            self._advance(rs, event, None, "", "")
        if rs.state in self.machine.finals:
            return self.finish(rs)
        return self.end(
            rs, "error", message=f"the sequence ended in {rs.state}, which is not final"
        )
