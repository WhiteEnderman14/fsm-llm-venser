"""What the machine says: a fixed text, or a question written by the LLM."""

from __future__ import annotations

from fsm_llm_venser.engine.context import Context
from fsm_llm_venser.llm.providers import LLMClient
from fsm_llm_venser.machine.model import FlatFinal, FlatMachine, FlatState
from fsm_llm_venser.machine.schema import Say
from fsm_llm_venser.machine.templates import TemplateError, placeholders, render


def fill(template: str, ctx: Context, **values: str) -> str:
    """Fill a template. Names that are not given are looked up in the plugin variables.

    Args:
        template (str): Text with {name} placeholders.
        ctx (Context): The context of the turn; its `vars` can provide extra placeholders.
        **values (str): The placeholders that the engine fills itself.

    Returns:
        str: The text with the values filled in.

    Raises:
        TemplateError: If a placeholder has no value anywhere.
    """
    values = dict(values)
    for name in placeholders(template) - values.keys():
        if isinstance(ctx.vars, dict):
            value = ctx.vars.get(name)
        else:
            value = getattr(ctx.vars, name, None)
        if value is None:
            raise TemplateError(
                f"no value for placeholder {{{name}}} (not a variable of the plugin)"
            )
        values[name] = value
    return render(template, values)


def _base(name: str, goal: str, ctx) -> dict[str, str]:
    """The placeholders that every prompt can use.

    Args:
        name (str): Qualified name of the state.
        goal (str): The goal of the state.
        ctx (Context): The context of the turn.

    Returns:
        dict[str, str]: The values every prompt can use.
    """
    return {"state": name, "goal": goal, "last_reply": ctx.last_reply or "(none yet)"}


def write_question(client: LLMClient, machine: FlatMachine, state: FlatState, ctx: Context) -> str:
    """Produce what the machine says in a state.

    Args:
        client (LLMClient): The LLM client.
        machine (FlatMachine): The compiled machine (its prompts are used).
        state (FlatState): The state that is asking.
        ctx (Context): The context of the turn.

    Returns:
        str: The text: the static text of the state, or a question written by the LLM.

    Raises:
        RuntimeError: If the LLM returns an empty question. Other errors come from the model.
    """
    say = state.say or Say()
    base = _base(state.name, state.goal, ctx)
    if say.mode == "static":
        text = say.clarify if ctx.retries > 0 and say.clarify else say.text
        return fill(text, ctx, **base)

    prompts = machine.prompts
    hint = ""
    if ctx.retries > 0 and "clarify_hint" in prompts:  # the previous reply was not usable
        hint = fill(prompts["clarify_hint"], ctx, **base, clarify_hint="")
    task = fill(prompts[say.task or "question_task"], ctx, **base, clarify_hint=hint)
    system = fill(prompts["question_system"], ctx, **base)
    user = fill(prompts["question_user"], ctx, **base, task=task)
    text = client.write(system, user).strip()
    if not text:
        raise RuntimeError("the model returned an empty question")
    return text


def fallback_question(state: FlatState, ctx: Context) -> str | None:
    """The static text to use when the LLM fails.

    Args:
        state (FlatState): The state that is asking.
        ctx (Context): The context of the turn.

    Returns:
        str | None: The `say.fallback` text of the state, or None if it has none.
    """
    if state.say is None or not state.say.fallback:
        return None
    return fill(state.say.fallback, ctx, **_base(state.name, state.goal, ctx))


def final_text(final: FlatFinal, ctx: Context) -> str:
    """The text of a final state (always static).

    Args:
        final (FlatFinal): The final state.
        ctx (Context): The context of the turn.

    Returns:
        str: The text, or an empty string if the final state says nothing.
    """
    if final.say is None or not final.say.text:
        return ""
    return fill(final.say.text, ctx, **_base(final.name, "", ctx))
