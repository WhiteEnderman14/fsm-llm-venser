"""The compiled (flat) machine that the engine runs and the tools print or draw."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fsm_llm_venser.machine.schema import EventDef, Say


@dataclass(frozen=True)
class Call:
    """A call to a plugin function (action or guard): its name and its arguments."""

    name: str
    args: tuple = ()

    def __str__(self) -> str:
        """Text of the call: name(arguments)."""
        return f"{self.name}({', '.join(map(str, self.args))})" if self.args else self.name


@dataclass(frozen=True)
class Alt:
    """One alternative of a transition, with everything needed to run it."""

    to: str  # flat target: a leaf state or a final state of the root
    guard: Call | None = None
    do: tuple[Call, ...] = ()  # actions of the transition itself
    after: tuple[Call, ...] = ()  # on_any_transition actions, innermost level first
    exits: tuple[str, ...] = ()  # states and composites left, innermost first
    enters: tuple[str, ...] = ()  # states and composites entered, outermost first
    origin: str = "own"  # "own", or "from_any_state" if inherited


@dataclass
class FlatState:
    """A leaf state with its transitions already resolved to flat names."""

    name: str  # qualified, e.g. EMOTIONS.INVITE
    goal: str = ""
    say: Say | None = None
    fallback: dict[str, str] = field(default_factory=dict)
    on_invalid: str | None = None
    transitions: dict[str, list[Alt]] = field(default_factory=dict)

    @property
    def enabled(self) -> list[str]:
        """The events that this state accepts."""
        return list(self.transitions)


@dataclass
class FlatFinal:
    """A final state of the whole machine."""

    name: str
    say: Say | None = None
    entry: tuple[Call, ...] = ()


@dataclass
class Rule:
    """A compiled rule: an event and the patterns that force it."""

    event: str
    patterns: tuple[re.Pattern, ...]


@dataclass
class FlatMachine:
    """A machine where nesting has been removed: only leaf states with qualified names."""

    name: str
    initial: str
    max_retries: int
    events: dict[str, EventDef]
    rules: list[Rule]
    prompts: dict[str, str]
    states: dict[str, FlatState]
    finals: dict[str, FlatFinal]
    parent: dict[str, str | None]  # every state and composite -> the composite that holds it
    entry: dict[str, tuple[Call, ...]]  # entry actions by state or composite name
    exit: dict[str, tuple[Call, ...]]
    start_enters: tuple[str, ...]  # composites and initial state entered when the run starts
    initials: dict[str, str]  # composite -> the leaf state it starts in
    guards: set[str] = field(default_factory=set)  # plugin names used by the file
    actions: set[str] = field(default_factory=set)
