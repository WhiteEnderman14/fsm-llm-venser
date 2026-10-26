"""Compile a Machine (what the file says) into a FlatMachine (what the engine runs).

Nested machines are flattened at any depth:
  - state names become qualified (EMOTIONS.INVITE)
  - an arrow that enters a composite state goes to the initial state of its nested machine
  - a final state of a nested machine becomes an arrow to the outer state it points to
  - from_any_state rules are copied into every state they cover
  - for every move we work out which states are exited and entered, so that exit/entry actions
    run in the right order
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fsm_llm_venser.machine.model import Alt, Call, FlatFinal, FlatMachine, FlatState, Rule
from fsm_llm_venser.machine.schema import SEPARATOR, Alternative, Machine, State, Target

# prompts that the engine always needs to talk to an LLM
CORE_PROMPTS = (
    "classifier_system",
    "classifier_user",
    "question_system",
    "question_user",
    "question_task",
)


class CompileError(Exception):
    """The machine is not consistent. `errors` has one message for each problem found."""

    def __init__(self, errors: list[str]) -> None:
        """Create the object."""
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass
class _Level:
    """One machine level: the root machine, or the nested machine of a composite state."""

    prefix: str  # "" for the root, "EMOTIONS." for a nested machine
    initial: str
    states: dict
    finals: dict  # root: name -> RootFinal; nested: name -> outer state to jump to
    from_any: dict
    on_any: list
    parent: _Level | None
    owner: str | None  # qualified name of the composite that holds this level


def compile_machine(machine: Machine, for_llm: bool = True) -> FlatMachine:
    """Compile and check a machine.

    Args:
        machine (Machine): The machine read from the file.
        for_llm (bool): Also check what is needed to talk to an LLM (prompts, goals, on_invalid).
            Event mode (run-word, table, draw) does not need it.

    Returns:
        FlatMachine: The flat machine.

    Raises:
        CompileError: If the machine has errors (all of them are reported together).
    """
    return _Compiler(machine, for_llm).run()


class _Compiler:
    """Does the work of compile_machine and collects the errors."""

    def __init__(self, machine: Machine, for_llm: bool) -> None:
        """Create the object."""
        self.m = machine
        self.for_llm = for_llm
        self.errors: list[str] = []
        self.levels: dict[str | None, _Level] = {}  # by owner
        self.parent: dict[str, str | None] = {}  # state or composite -> enclosing composite
        self.entry: dict[str, tuple[Call, ...]] = {}
        self.exit: dict[str, tuple[Call, ...]] = {}
        self.leaves: list[tuple[_Level, str, object, str]] = []  # (level, name, state, qualified)
        self.guards: set[str] = set()
        self.actions: set[str] = set()

    def error(self, where: str, message: str) -> None:
        """Record an error (the same one is not recorded twice).

        Args:
            where (str): Where the problem is (state or section).
            message (str): What is wrong.
        """
        text = f"{where}: {message}"
        if text not in self.errors:
            self.errors.append(text)

    def run(self) -> FlatMachine:
        """Compile everything, then raise if any error was found."""
        m = self.m
        root = _Level("", m.initial, m.states, m.finals, m.from_any_state, m.on_any_transition,
                      None, None)  # fmt: skip
        self.levels[None] = root
        self._collect(root)
        for level in self.levels.values():
            if level.parent is not None:  # where do the finals of a nested machine lead?
                for final, target in level.finals.items():
                    self._resolve(level.parent, target, f"{level.prefix}{final}")

        states = {q: self._flat_state(lvl, n, s, q) for lvl, n, s, q in self.leaves}
        finals = {
            n: FlatFinal(n, f.say, self._calls(f.entry, "actions")) for n, f in m.finals.items()
        }
        rules = self._rules()
        self._check_states(states)
        if self.for_llm:
            for name in CORE_PROMPTS:
                if name not in m.prompts:
                    self.error(f"prompts.{name}", "missing (needed to run with an LLM)")
        if self.errors:
            raise CompileError(self.errors)

        initial = self._resolve(root, m.initial, "initial")
        start = (*reversed(self._ancestors(initial)), initial)  # composites, then the state
        return FlatMachine(
            name=m.name,
            initial=initial,
            max_retries=m.max_retries,
            events=m.events,
            rules=rules,
            prompts=m.prompts,
            states=states,
            finals=finals,
            parent=self.parent,
            entry=self.entry,
            exit=self.exit,
            start_enters=start,
            initials={
                name: self._resolve(lvl, lvl.initial, name)
                for name, lvl in self.levels.items()
                if name
            },
            guards=self.guards,
            actions=self.actions,
        )

    # --- structure ------------------------------------------------------------------------

    def _collect(self, level: _Level) -> None:
        """Register the states of a level; composites are visited recursively.

        Args:
            level (_Level): The machine level where the name is written.
        """
        for name, state in level.states.items():
            qualified = level.prefix + name
            self.parent[qualified] = level.owner
            if state.entry:
                self.entry[qualified] = self._calls(state.entry, "actions")
            if state.exit:
                self.exit[qualified] = self._calls(state.exit, "actions")
            if state.fsm is None:
                self.leaves.append((level, name, state, qualified))
                continue
            nested = state.fsm
            child = _Level(qualified + SEPARATOR, nested.initial, nested.states, nested.finals,
                           nested.from_any_state, nested.on_any_transition, level, qualified)  # fmt: skip
            self.levels[qualified] = child
            self._collect(child)

    def _ancestors(self, name: str) -> list[str]:
        """The composites that contain a state, innermost first.

        Args:
            name (str): A state or composite (qualified).

        Returns:
            list[str]: The composites that contain it, innermost first.
        """
        chain, parent = [], self.parent.get(name)
        while parent is not None:
            chain.append(parent)
            parent = self.parent.get(parent)
        return chain

    def _resolve(self, level: _Level, name: str, where: str) -> str | None:
        """Find the flat state (or final of the root) that `name` means when written in `level`.

        Args:
            level (_Level): The machine level where the name is written.
            name (str): The name to resolve.
            where (str): Where the problem is, for the message.

        Returns:
            str | None: The flat name, or None (the error is recorded).
        """
        if name in level.states:
            state = level.states[name]
            if state.fsm is None:
                return level.prefix + name
            child = self.levels[level.prefix + name]  # entering a composite = entering its initial
            return self._resolve(child, child.initial, where)
        if name in level.finals:
            if level.parent is None:
                return name  # a final state of the whole machine
            return self._resolve(level.parent, level.finals[name], where)  # jump outwards
        self.error(where, f"unknown state {name!r}")
        return None

    # --- states and transitions -----------------------------------------------------------

    def _flat_state(self, level: _Level, name: str, state: State, qualified: str) -> FlatState:
        """Resolve the transitions of a leaf state and add the inherited ones.

        Args:
            level (_Level): The machine level where the name is written.
            name (str): Name of the state inside its level.
            state (State): The state as written in the file.
            qualified (str): Qualified name of the state (like EMOTIONS.INVITE).

        Returns:
            FlatState: The state with its transitions.
        """
        transitions: dict[str, list[Alt]] = {}
        for event, target in state.transitions.items():
            self._declared(event, qualified)
            alts = self._alternatives(level, qualified, target)
            if alts:
                transitions[event] = alts

        # from_any_state: the state's own transitions win, then the nearest level outwards
        lvl = level
        while lvl is not None:
            for event, target in lvl.from_any.items():
                if event not in transitions:
                    self._declared(event, qualified)
                    flat = self._resolve(lvl, target, f"{lvl.prefix}from_any_state.{event}")
                    if flat is not None:
                        transitions[event] = [
                            self._alt(qualified, flat, level, origin="from_any_state")
                        ]
            lvl = lvl.parent

        for event, replacement in state.fallback.items():
            self._declared(event, qualified)
            self._declared(replacement, qualified)
        if state.on_invalid is not None:
            self._declared(state.on_invalid, qualified)
        return FlatState(
            qualified,
            state.goal or "",
            state.say,
            dict(state.fallback),
            state.on_invalid,
            transitions,
        )

    def _alternatives(self, level: _Level, source: str, target: Target) -> list[Alt]:
        """Turn the target of an event (a name, an alternative or a list) into Alt objects.

        Args:
            level (_Level): The machine level where the name is written.
            source (str): Qualified name of the state.
            target (Target): What the file says after the event: a name, an alternative or a list.

        Returns:
            list[Alt]: The alternatives, in the order of the file.
        """
        specs = target if isinstance(target, list) else [target]
        alts = []
        for spec in specs:
            if isinstance(spec, str):
                spec = Alternative(to=spec)
            flat = self._resolve(level, spec.to, source)
            guard = self._calls([spec.guard], "guards")[0] if spec.guard else None
            do = self._calls(spec.do, "actions")
            if flat is not None:
                alts.append(self._alt(source, flat, level, guard=guard, do=do))
        if specs and isinstance(specs[-1], Alternative) and specs[-1].guard is not None:
            self.error(
                source, "the last alternative of an event must have no guard (default branch)"
            )
        return alts

    def _alt(
        self,
        source: str,
        flat: str,
        level: _Level,
        *,
        guard: Call | None = None,
        do: tuple[Call, ...] = (),
        origin: str = "own",
    ) -> Alt:
        """Build an Alt with the exits, the entries and the on_any_transition actions.

        Args:
            source (str): Qualified name of the state the move starts from.
            flat (str): Flat name of the state the move ends in.
            level (_Level): The machine level where the name is written.
            guard (Call | None): The guard of the alternative, if any.
            do (tuple[Call, ...]): Actions of the transition.
            origin (str): "own", or "from_any_state" if the transition is inherited.

        Returns:
            Alt: The alternative.
        """
        exits, enters = self._moves(source, flat)
        return Alt(flat, guard, do, self._after(level), exits, enters, origin)

    def _moves(self, source: str, target: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Which states and composites a move leaves and enters. A self-loop leaves nothing.

        Args:
            source (str): Qualified name of the state the move starts from.
            target (str): Flat name of the state the move ends in.

        Returns:
            tuple[tuple[str, ...], tuple[str, ...]]: The states left and the states entered.
        """
        if source == target:
            return (), ()
        is_state = target in self.parent  # a final state of the root is not a state
        src = self._ancestors(source)
        dst = self._ancestors(target) if is_state else []
        exits = (source, *[c for c in src if c not in dst])  # innermost first
        enters = (*[c for c in reversed(dst) if c not in src], *([target] if is_state else []))
        return exits, enters

    def _after(self, level: _Level) -> tuple[Call, ...]:
        """The on_any_transition actions that apply at a level, innermost level first.

        Args:
            level (_Level): The machine level where the name is written.

        Returns:
            tuple[Call, ...]: The actions to run.
        """
        calls: tuple[Call, ...] = ()
        while level is not None:
            calls += self._calls(level.on_any, "actions")
            level = level.parent
        return calls

    def _calls(self, items: list, kind: str) -> tuple[Call, ...]:
        """Turn `name` or `{name: args}` items into Call objects and remember the names used.

        Args:
            items (list): Calls as written in the file: `name` or `{name: argument}`.
            kind (str): "guards" or "actions": which set of names to fill.

        Returns:
            tuple[Call, ...]: The calls.
        """
        calls = []
        for item in items:
            if isinstance(item, str):
                call = Call(item)
            else:
                ((name, arg),) = item.items()
                call = Call(name, tuple(arg) if isinstance(arg, list) else (arg,))
            getattr(self, kind).add(call.name)
            calls.append(call)
        return tuple(calls)

    def _declared(self, event: str, where: str) -> None:
        """Check that an event is in the alphabet.

        Args:
            event (str): The event.
            where (str): Where the problem is, for the message.
        """
        if event not in self.m.events:
            self.error(where, f"event {event!r} is not declared in `events`")

    # --- rules and checks -----------------------------------------------------------------

    def _rules(self) -> list[Rule]:
        """Compile the regular expressions of the rules."""
        rules = []
        for i, rule in enumerate(self.m.rules):
            self._declared(rule.event, f"rules[{i}]")
            flags = re.IGNORECASE if rule.ignore_case else 0
            rules.append(Rule(rule.event, tuple(re.compile(p, flags) for p in rule.match)))
        return rules

    def _check_states(self, states: dict[str, FlatState]) -> None:
        """Checks on every leaf state: it must be left somehow, and (with an LLM) it needs a goal.

        Args:
            states (dict[str, FlatState]): The flat states.
        """
        for name, state in states.items():
            if not state.transitions:
                self.error(name, "state has no transition (dead end)")
            if not self.for_llm:
                continue
            llm_says = state.say is None or state.say.mode == "llm"
            if state.on_invalid is None:
                self.error(name, "needs `on_invalid` to run with an LLM")
            if llm_says and not state.goal:
                self.error(name, "needs a `goal` (its question is written by the LLM)")
            if state.say and state.say.task and state.say.task not in self.m.prompts:
                self.error(name, f"say.task {state.say.task!r} is not in `prompts`")
