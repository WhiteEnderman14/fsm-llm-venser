"""Sources of replies for a run: a scenario file, or a person at the keyboard."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from fsm_llm_venser.machine import loader


class Replier(Protocol):
    """Anything that can answer a question."""

    def reply(self, question: str) -> str:
        """Answer the question of the machine.

        Args:
            question (str): The question of the machine.

        Returns:
            str: The reply.
        """
        ...


class Scenario(BaseModel):
    """A scripted conversation: the replies of the person, in order."""

    model_config = ConfigDict(extra="forbid")

    name: str
    replies: list[str] = Field(min_length=1)
    when_exhausted: str = "I would rather stop here."  # reply once the list is used up


def load_scenario(path: str | Path) -> Scenario:
    """Read a scenario file.

    Args:
        path (str | Path): Path of the YAML file.

    Returns:
        Scenario: The scenario.
    """
    return Scenario.model_validate(loader.load_file(path))


class ScriptedReplier:
    """Gives the replies of a scenario in order, then `when_exhausted`."""

    def __init__(self, scenario: Scenario) -> None:
        """Create the object."""
        self._replies = iter(scenario.replies)
        self._last = scenario.when_exhausted

    def reply(self, question: str) -> str:
        """Return the next scripted reply (the question is ignored).

        Args:
            question (str): The question of the machine.

        Returns:
            str: The reply.
        """
        return next(self._replies, self._last)


class InteractiveReplier:
    """Reads the replies from the terminal."""

    def reply(self, question: str) -> str:
        """Show the question and read the answer.

        Args:
            question (str): The question of the machine.

        Returns:
            str: The reply.
        """
        print(f"\n{question}")
        return input("> ").strip()
