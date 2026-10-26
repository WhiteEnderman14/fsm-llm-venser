"""Plugin of the support-ticket example: it uses every kind of plugin code.

Guards:     attempts_at_least   (custom; `when` and `reply_matches` come from the engine)
Actions:    remember, bump, set_priority, announce, log_step, create_ticket
Listeners:  start, enter (with a glob), transition (with an event), final, end
Variables:  the Vars model; any of its fields can be a {placeholder} in the prompts
Analysis:   the classifier also returns a short `detail` of each reply
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from fsm_llm_venser.engine.context import Context
from fsm_llm_venser.llm.classifier import Analysis
from fsm_llm_venser.plugins.base import Plugin


class SupportAnalysis(Analysis):
    """What the classifier returns here, besides the event."""

    detail: str = Field(
        default="",
        description="The useful fact of the reply in a few words (device, system, symptom, "
        "impact). Empty if the reply has none.",
    )


class Vars(BaseModel):
    """Variables kept between turns. The prompts can use them as placeholders."""

    facts: dict[str, list[str]] = Field(default_factory=dict)  # qualified state -> details
    heard: str = "(nothing yet)"  # the facts as text, for the prompts
    priority: str = "normal"
    ticket_id: str = ""
    visits: int = 0
    steps: int = 0
    attempts: int = 0
    checks_done: int = 0
    log: list[str] = Field(default_factory=list)


plugin = Plugin("support", vars_model=Vars, analysis_model=SupportAnalysis)


# --- guard --------------------------------------------------------------------------------------


@plugin.guard
def attempts_at_least(ctx: Context, limit: int) -> bool:
    """True once the person has tried at least `limit` fixes.

    Args:
        ctx (Context): The context of the turn (read-only for a guard).
        limit (int): Number of fix attempts.

    Returns:
        bool: True if the attempts reached the limit.
    """
    return ctx.vars.attempts >= limit


# --- actions ------------------------------------------------------------------------------------


def _format(facts: dict[str, list[str]]) -> str:
    """Write the facts as a bullet list, one line per step.

    Args:
        facts (dict[str, list[str]]): The details collected so far, by state.

    Returns:
        str: The facts as text.
    """
    return "\n".join(f"- {state}: {' / '.join(items)}" for state, items in facts.items())


@plugin.action
def remember(ctx: Context, *events: str) -> None:
    """Keep the detail of the reply when the event is one of `events`.

    The analysis is None when the machine runs on events only, so nothing is kept then.

    Args:
        ctx (Context): The context of the turn.
        *events (str): Events of the machine.
    """
    if ctx.analysis and ctx.event in events and ctx.analysis.detail:
        ctx.vars.facts.setdefault(ctx.state, []).append(ctx.analysis.detail)
        ctx.vars.heard = _format(ctx.vars.facts)


@plugin.action
def bump(ctx: Context, name: str) -> None:
    """Add one to a counter of the variables.

    Args:
        ctx (Context): The context of the turn.
        name (str): Name of an integer field of Vars.
    """
    setattr(ctx.vars, name, getattr(ctx.vars, name) + 1)


@plugin.action
def set_priority(ctx: Context, level: str) -> None:
    """Set the priority of the ticket.

    Args:
        ctx (Context): The context of the turn.
        level (str): "normal" or "high".
    """
    ctx.vars.priority = level


@plugin.action
def announce(ctx: Context, message: str) -> None:
    """Add a line to the log of the run.

    Args:
        ctx (Context): The context of the turn.
        message (str): The line.
    """
    ctx.vars.log.append(message)


@plugin.action
def log_step(ctx: Context) -> None:
    """Log the move of the turn (state, event, source of the decision).

    Args:
        ctx (Context): The context of the turn.
    """
    source = ctx.analysis.source if ctx.analysis else "event"
    ctx.vars.log.append(f"{ctx.state} --{ctx.event}--> ({source})")


@plugin.action
def create_ticket(ctx: Context) -> None:
    """Make the ticket id. A real desk would call its ticket system here.

    Args:
        ctx (Context): The context of the turn.
    """
    ctx.vars.ticket_id = f"T-{ctx.turns:04d}"


# --- listeners (they only watch) ----------------------------------------------------------------


@plugin.listener("start")
def show_start(ctx: Context, hook: str) -> None:
    """Say that the run begins.

    Args:
        ctx (Context): The context of the turn.
        hook (str): The hook point.
    """
    print("[support] new request")


@plugin.listener("enter", state="TROUBLESHOOT.*")
def show_checks(ctx: Context, hook: str) -> None:
    """Show every state entered inside the troubleshooting box (a glob on the state).

    Args:
        ctx (Context): The context of the turn.
        hook (str): The hook point.
    """
    print(f"[support] check: {ctx.state}")


@plugin.listener("transition", event="HUMAN")
def show_handoff(ctx: Context, hook: str) -> None:
    """Show when the person asks for a human (a filter on the event).

    Args:
        ctx (Context): The context of the turn.
        hook (str): The hook point.
    """
    print(f"[support] human requested in {ctx.state}")


@plugin.listener("final")
def show_final(ctx: Context, hook: str) -> None:
    """Print the ticket when the run ends.

    Args:
        ctx (Context): The context of the turn.
        hook (str): The hook point.
    """
    if ctx.vars.ticket_id:
        print(f"\n== TICKET {ctx.vars.ticket_id} | priority {ctx.vars.priority} ==")
        print(ctx.vars.heard)


@plugin.listener("end")
def show_end(ctx: Context, hook: str) -> None:
    """Print a one-line summary of the counters.

    Args:
        ctx (Context): The context of the turn.
        hook (str): The hook point.
    """
    v = ctx.vars
    print(
        f"[support] turns={ctx.turns} steps={v.steps} fix attempts={v.attempts} checks={v.checks_done}"
    )
