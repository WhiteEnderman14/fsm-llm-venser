"""Data of a run: the state it is in, one record per turn, and the final result."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnRecord:
    """What happened in one turn."""

    turn: int
    state: str
    question: str
    reply: str
    analysis: dict | None  # classifier output; None in event mode
    event: str  # the event that was used
    replaced: str | None  # the event it replaced, if the fallback took over
    next_state: str
    actions: list[str]  # actions that ran, in order


@dataclass
class RunState:
    """Everything that changes while the machine runs."""

    state: str
    vars: Any  # the variables of the plugin
    counts: dict[str, int] = field(default_factory=dict)  # times each event happened
    retries: int = 0  # consecutive turns spent in the current state
    turns: int = 0
    last_reply: str = ""
    transcript: list[tuple[str, str]] = field(default_factory=list)
    trace: list[TurnRecord] = field(default_factory=list)


@dataclass
class Result:
    """The outcome of a run."""

    status: str  # "final", "max_turns" or "error"
    final_state: str | None
    final_text: str | None
    vars: Any
    turns: int
    transcript: list[tuple[str, str]]
    trace: list[TurnRecord]
    message: str = ""
