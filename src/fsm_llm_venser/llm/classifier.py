"""Turn classifier: rules first, then the LLM, then a guard on what the LLM answered."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fsm_llm_venser.engine.context import Context
from fsm_llm_venser.llm.providers import LLMClient
from fsm_llm_venser.llm.utterance import fill
from fsm_llm_venser.machine.model import FlatMachine, FlatState

Source = Literal["rule", "llm", "guard", "error"]


def _hide_source(schema: dict) -> None:
    """Remove `source` from the schema sent to the LLM: the engine sets it, not the model.

    Args:
        schema (dict): The JSON schema built by Pydantic (changed in place).
    """
    schema.get("properties", {}).pop("source", None)


class Analysis(BaseModel):
    """What the classifier returns. A plugin can subclass it to ask for more fields."""

    model_config = ConfigDict(json_schema_extra=_hide_source)

    event: str = Field(description="Exactly one event from the allowed list.")
    rationale: str = Field(default="", description="One short sentence explaining the choice.")
    source: Source = "llm"  # who decided: a rule, the LLM, the guard or an error


def match_event(answer: str, enabled: list[str]) -> str:
    """Map an answer that is only written differently to the enabled event it means.

    Small models often answer `answer`, `"ANSWER"` or `ANSWER: the person agrees`. The first word
    of the answer that names an enabled event (ignoring case) is used; if there is none the answer
    is returned as it is, and the guard below replaces it.

    Args:
        answer (str): The event as written by the model.
        enabled (list[str]): The events the current state accepts.

    Returns:
        str: The enabled event, or the answer unchanged.
    """
    if answer in enabled:
        return answer
    wanted = {name.upper(): name for name in enabled}
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", answer):
        if word.upper() in wanted:
            return wanted[word.upper()]
    return answer


def format_events(machine: FlatMachine, enabled: list[str]) -> str:
    """Describe the enabled events for the prompt.

    Args:
        machine (FlatMachine): The compiled machine (it holds the descriptions).
        enabled (list[str]): The events the current state accepts.

    Returns:
        str: One line per event with its description, followed by its examples.
    """
    lines = []
    for name, event in machine.events.items():
        if name in enabled:
            lines.append(f"- {name}: {event.description}")
            lines += [f"    e.g. {example!r}" for example in event.examples]
    return "\n".join(lines)


def classify_turn(
    client: LLMClient,
    machine: FlatMachine,
    state: FlatState,
    question: str,
    reply: str,
    ctx: Context,
    model: type[Analysis] = Analysis,
) -> Analysis:
    """Turn the reply of the person into one event that the state accepts.

    Three layers, like in the course notebook: regular expression rules (no LLM), the LLM with
    structured output, and a guard that replaces an event the state does not accept with the
    state's `on_invalid` event.

    Args:
        client (LLMClient): The LLM client.
        machine (FlatMachine): The compiled machine.
        state (FlatState): The state the machine is in.
        question (str): What the machine asked.
        reply (str): What the person answered.
        ctx (Context): The context of the turn (read-only).
        model (type[Analysis]): The class of the answer; a plugin may pass a subclass of Analysis.

    Returns:
        Analysis: The analysis. If the LLM fails the event is `on_invalid` and the source is "error".
    """
    enabled = state.enabled

    # 1. rules: a match decides without asking the LLM
    for rule in machine.rules:
        if rule.event in enabled:
            for pattern in rule.patterns:
                if pattern.search(reply):
                    return model(
                        event=rule.event, rationale=f"rule: {pattern.pattern}", source="rule"
                    )

    # 2. the LLM
    try:
        values = {
            "state": state.name,
            "goal": state.goal,
            "last_reply": ctx.last_reply or "(none yet)",
        }
        system = fill(machine.prompts["classifier_system"], ctx, **values)
        user = fill(
            machine.prompts["classifier_user"],
            ctx,
            **values,
            events=format_events(machine, enabled),
            question=question,
            reply=reply,
        )
        analysis = client.classify(system, user, model)
    except Exception as exc:  # a failing model must not stop the run
        note = f"{type(exc).__name__}: {exc}"[:200]
        return model(event=state.on_invalid, rationale=note, source="error")
    analysis.source = "llm"
    analysis.event = match_event(analysis.event, enabled)

    # 3. guard: an event that this state does not accept becomes the fail-safe event
    if analysis.event not in enabled:
        analysis.rationale = (
            f"{analysis.event!r} is not enabled in {state.name}. {analysis.rationale}"
        )
        analysis.event, analysis.source = state.on_invalid, "guard"
    return analysis
