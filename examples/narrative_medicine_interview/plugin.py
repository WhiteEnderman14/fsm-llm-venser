"""Plugin of the narrative-medicine interview: notes, emotions and the narrative card."""

from __future__ import annotations

import textwrap

from pydantic import BaseModel, Field

from fsm_llm_venser.engine.context import Context
from fsm_llm_venser.llm.classifier import Analysis
from fsm_llm_venser.plugins.base import Plugin

DISCLAIMER = (
    "Patient-authored narrative summary produced by an automated tool. It is not a diagnosis "
    "or clinical assessment and should be reviewed with the person it describes."
)
SCRIBE_PROMPT = "You are a careful scribe. Never add interpretation, diagnosis or advice."


class NarrativeAnalysis(Analysis):
    """What the classifier returns for this interview, besides the event."""

    note: str = Field(
        default="",
        description="One sentence summarising what the person shared, in their own words. "
        "Empty if they shared nothing.",
    )
    emotions: list[str] = Field(
        default_factory=list,
        description="Emotions the person explicitly named in this reply. Never infer any.",
    )


class Vars(BaseModel):
    """Variables kept between turns. The prompts can use them as placeholders ({heard})."""

    notes: dict[str, list[str]] = Field(default_factory=dict)  # qualified state -> notes
    emotions: list[str] = Field(default_factory=list)
    heard: str = "(nothing yet)"  # the notes as text, for the prompts
    card: dict = Field(default_factory=dict)  # the narrative card, built at the end


plugin = Plugin("narrative", vars_model=Vars, analysis_model=NarrativeAnalysis)


def format_notes(notes: dict[str, list[str]]) -> str:
    """Write the notes as a bullet list, one line per stage.

    Args:
        notes (dict[str, list[str]]): The notes collected so far, by state.

    Returns:
        str: The notes as text.
    """
    return "\n".join(f"- {stage}: {' / '.join(items)}" for stage, items in notes.items())


# --- actions (called from on_any_transition in the machine file) -----------------------------


@plugin.action
def record_note(ctx: Context, *events: str) -> None:
    """Keep the note of the reply when the event is one of `events`.

    The analysis is None when the machine runs on events only, so nothing is recorded then.

    Args:
        ctx (Context): The context of the turn.
        *events (str): Events of the machine.
    """
    if ctx.analysis and ctx.event in events and ctx.analysis.note:
        ctx.vars.notes.setdefault(ctx.state, []).append(ctx.analysis.note)
        ctx.vars.heard = format_notes(ctx.vars.notes)


@plugin.action
def collect_emotions(ctx: Context) -> None:
    """Add the emotions named in the reply to the list, without duplicates.

    Args:
        ctx (Context): The context of the turn.
    """
    if ctx.analysis:
        ctx.vars.emotions = list(dict.fromkeys([*ctx.vars.emotions, *ctx.analysis.emotions]))


# --- narrative card ---------------------------------------------------------------------------


class NarrativeCard(BaseModel):
    """The final artefact of the notebook: a short card in parallel-chart style."""

    story_in_brief: str = Field(
        description="3-4 neutral sentences summarising the person's story, only from what was said."
    )
    key_events: list[str] = Field(
        default_factory=list, description="Key events, in the order the person described them."
    )
    emotions: list[str] = Field(
        default_factory=list, description="Emotions the person explicitly named."
    )
    meaning: str = Field(
        default="", description="What the experience means to the person, in their terms."
    )
    supports: list[str] = Field(
        default_factory=list, description="People, places or routines that support the person."
    )
    in_their_words: list[str] = Field(
        default_factory=list,
        description="Up to 3 SHORT verbatim quotes copied exactly from the person's replies.",
    )
    to_discuss_with_care_team: list[str] = Field(
        default_factory=list,
        description="Only things the person themselves said they want the care team to know or do.",
    )


@plugin.action
def build_card(ctx: Context) -> None:
    """Build the narrative card with the LLM and keep it in ctx.vars.card.

    Quotes that do not appear in the person's replies are dropped (grounding check). If there
    is no LLM, or it fails, the card is made from the raw notes.

    Args:
        ctx (Context): The context of the turn.
    """
    replies = [reply for _, reply in ctx.transcript]
    material = (
        "Structure the following interview material into a narrative card. "
        "Use ONLY what is written here.\n\n"
        f"Notes by stage:\n{format_notes(ctx.vars.notes)}\n\n"
        f"Emotions named: {', '.join(ctx.vars.emotions) or '(none)'}\n\n"
        "Person's replies (for verbatim quotes):\n" + "\n".join(f"- {r}" for r in replies)
    )
    try:
        card = ctx.llm.classify(SCRIBE_PROMPT, material, NarrativeCard)
    except Exception:  # no LLM (run-word) or it failed
        notes = [note for items in ctx.vars.notes.values() for note in items]
        story = " ".join(notes) or "(no content shared)"
        ctx.vars.card = {
            **NarrativeCard(story_in_brief=story, emotions=ctx.vars.emotions).model_dump(),
            "dropped_quotes": 0,
            "generated_by": "fallback (no LLM)",
        }
        return
    quotes = [
        q for q in card.in_their_words if any(q.strip().lower() in r.lower() for r in replies)
    ]
    dropped = len(card.in_their_words) - len(quotes)
    card.in_their_words = quotes
    ctx.vars.card = {
        **card.model_dump(),
        "dropped_quotes": dropped,
        "generated_by": "llm + grounding check",
    }


# --- listener ---------------------------------------------------------------------------------


@plugin.listener("final", state="DONE")
def print_card(ctx: Context, hook: str) -> None:
    """Print the narrative card when the interview ends in DONE.

    Args:
        ctx (Context): The context of the turn.
        hook (str): The hook point.
    """
    card = ctx.vars.card
    print("\n== NARRATIVE CARD ==")
    print(textwrap.fill(card["story_in_brief"], 100))
    sections = [
        ("Key events", "key_events"),
        ("Emotions named", "emotions"),
        ("Supports", "supports"),
        ("In their words", "in_their_words"),
        ("To discuss with the care team", "to_discuss_with_care_team"),
    ]
    for title, key in sections:
        if card.get(key):
            print(f"{title}: " + "; ".join(card[key]))
    if card.get("meaning"):
        print("Meaning: " + card["meaning"])
    print(f"[{card['generated_by']}; invented quotes dropped: {card['dropped_quotes']}]")
    print(DISCLAIMER)
