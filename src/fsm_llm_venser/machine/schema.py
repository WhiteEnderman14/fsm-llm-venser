"""Pydantic models of the machine file.

They only check the structure of the file. Checks that need the whole machine (does a target
exist, is an event declared, ...) are done when the machine is compiled (machine/compiler.py). Unknown
keys are rejected, so typos are reported instead of being ignored.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from fsm_llm_venser.machine import loader
from fsm_llm_venser.machine.templates import placeholders

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
SEPARATOR = "."  # qualified state names look like EMOTIONS.INVITE


class _Strict(BaseModel):
    """Base class: unknown keys are errors."""

    model_config = ConfigDict(extra="forbid")


def _check_call(value: Any) -> Any:
    """Accept a call written as `name` or as a one-key mapping `name: argument(s)`.

    Args:
        value (Any): The value read from the file.

    Returns:
        Any: The value, if valid.
    """
    if isinstance(value, str) or (isinstance(value, dict) and len(value) == 1):
        return value
    raise ValueError("a call is `name` or a one-key mapping `name: argument(s)`")


Call = Annotated[str | dict[str, Any], AfterValidator(_check_call)]


class EventDef(_Strict):
    """An event of the alphabet: a description for the LLM and optional examples."""

    description: str = Field(min_length=1)
    examples: list[str] = Field(default_factory=list)


def _event_shorthand(value: Any) -> Any:
    """Allow `EVENT: "description"` instead of `EVENT: {description: ...}`.

    Args:
        value (Any): The value read from the file.

    Returns:
        Any: The event as a mapping.
    """
    return {"description": value} if isinstance(value, str) else value


class Rule(_Strict):
    """A deterministic rule applied before the LLM: a matching reply forces the event."""

    event: Name
    match: list[str] = Field(min_length=1)
    ignore_case: bool = True

    @field_validator("match")
    @classmethod
    def _valid_regex(cls, patterns: list[str]) -> list[str]:
        """Check that every pattern is a valid regular expression.

        Args:
            patterns (list[str]): Regular expressions to check.

        Returns:
            list[str]: The same patterns.
        """
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        return patterns


class Say(_Strict):
    """What the machine says in a state (or in a final state)."""

    mode: Literal["llm", "static"] = "llm"
    text: str | None = None  # the text, in static mode
    clarify: str | None = None  # static text used instead of `text` when retries > 0
    fallback: str | None = None  # text used if the LLM cannot write the question
    task: str | None = None  # name of a prompt that replaces the default task

    @model_validator(mode="after")
    def _check(self) -> Say:
        """Static mode needs a text; every text must be a valid template."""
        if self.mode == "static" and not self.text:
            raise ValueError("`say.mode: static` requires `text`")
        for text in (self.text, self.clarify, self.fallback):
            if text is not None:
                placeholders(text)
        return self


class Alternative(_Strict):
    """One way to follow an event: a target, an optional guard and optional actions."""

    to: Name
    guard: Call | None = None
    do: list[Call] = Field(default_factory=list)


Target = Name | Alternative | Annotated[list[Alternative], Field(min_length=1)]


class State(_Strict):
    """A state. With `fsm` it is a composite state that holds a whole nested machine."""

    goal: str | None = None
    say: Say | None = None
    transitions: dict[Name, Target] = Field(default_factory=dict)
    fallback: dict[Name, Name] = Field(default_factory=dict)  # event -> event after max_retries
    on_invalid: Name | None = None  # event used when the LLM answer is unusable
    entry: list[Call] = Field(default_factory=list)
    exit: list[Call] = Field(default_factory=list)
    fsm: Nested | None = None

    @model_validator(mode="after")
    def _check(self) -> State:
        """A composite state cannot have transitions, fallback, say or on_invalid of its own."""
        if self.fsm is not None:
            own = {"transitions": self.transitions, "fallback": self.fallback, "say": self.say}
            used = [key for key, value in own.items() if value]
            if used or self.on_invalid:
                raise ValueError(
                    "a composite state (with `fsm`) cannot have transitions, fallback, say or "
                    "on_invalid: it is left through the `finals` of its nested machine"
                )
        return self


class Nested(_Strict):
    """A machine inside a state. `finals` maps each final state to the outer state to jump to."""

    initial: Name
    states: dict[Name, State] = Field(min_length=1)
    finals: dict[Name, Name] = Field(min_length=1)
    from_any_state: dict[Name, Name] = Field(default_factory=dict)
    on_any_transition: list[Call] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Nested:
        """The initial state must exist, and a name cannot be both state and final."""
        _check_names(self.initial, self.states, self.finals)
        return self


def _none_to_empty(value: Any) -> Any:
    """Allow a final state written without a body (`DONE:`).

    Args:
        value (Any): The value read from the file.

    Returns:
        Any: The value, or an empty mapping.
    """
    return {} if value is None else value


class RootFinal(_Strict):
    """A final state of the whole machine: its text and the actions run when it is entered."""

    say: Say | None = None
    entry: list[Call] = Field(default_factory=list)


class Machine(_Strict):
    """The whole machine file."""

    name: str
    initial: Name
    max_retries: int = Field(default=2, ge=0)
    events: dict[Name, Annotated[EventDef, BeforeValidator(_event_shorthand)]]
    rules: list[Rule] = Field(default_factory=list)
    from_any_state: dict[Name, Name] = Field(default_factory=dict)
    on_any_transition: list[Call] = Field(default_factory=list)
    states: dict[Name, State] = Field(min_length=1)
    finals: dict[Name, Annotated[RootFinal, BeforeValidator(_none_to_empty)]] = Field(min_length=1)
    prompts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Machine:
        """Check the initial state, the names and the braces of the prompts."""
        _check_names(self.initial, self.states, self.finals)
        for name, text in self.prompts.items():
            try:
                placeholders(text)
            except ValueError as exc:
                raise ValueError(f"prompts.{name}: {exc}") from exc
        return self


def _check_names(initial: str, states: dict, finals: dict) -> None:
    """Shared check of a machine level (the root or a nested one).

    Args:
        initial (str): Name of the initial state.
        states (dict): The leaf states of the machine.
        finals (dict): The final states.
    """
    if initial not in states:
        raise ValueError(f"initial state {initial!r} is not in `states`")
    both = set(states) & set(finals)
    if both:
        raise ValueError(f"names used both as state and as final: {sorted(both)}")


State.model_rebuild()
Nested.model_rebuild()


def load_machine(path: str | Path) -> Machine:
    """Read a machine file and check its structure.

    Args:
        path (str | Path): Path of the YAML file.

    Returns:
        Machine: The validated machine.

    Raises:
        pydantic.ValidationError: If the structure is wrong.
    """
    return Machine.model_validate(loader.load_file(path))
