"""The `ctx` object that plugin functions receive."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass
class Context:
    """What a guard, action or listener can see about the run."""

    state: str  # qualified name of the current state (or of the composite, in enter/exit hooks)
    event: str | None = None  # the event of this turn
    analysis: Any = None  # output of the classifier; None in event mode
    question: str = ""
    last_reply: str = ""  # the latest reply of the person
    counts: dict[str, int] = field(
        default_factory=dict
    )  # times each event happened, this turn excluded
    retries: int = 0  # retries in the current state
    turns: int = 0
    vars: Any = None  # the variables of the plugin; only actions get the real object
    transcript: list = field(default_factory=list)  # (question, reply) pairs
    llm: Any = None

    def readonly(self) -> Context:
        """Copy the context so that guards and listeners cannot change the run.

        Returns:
            Context: A context with copies of the variables and of the lists.
        """
        return replace(
            self,
            vars=copy.deepcopy(self.vars),
            counts=dict(self.counts),
            transcript=list(self.transcript),
        )
