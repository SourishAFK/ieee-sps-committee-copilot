"""Outreach pipeline: turn a flagged conference into a conversation.

Spotting a conference is the easy half. This module tracks each one through a
pipeline and drafts the actual letter, because "we should email them" is where
most chapter initiatives quietly die.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import knowledge as kb
from . import llm, store
from .config import settings

# Ordered pipeline. `identified` and `declined` are the two resting states.
STAGES: list[tuple[str, str]] = [
    ("identified", "Spotted and scored, nobody assigned yet"),
    ("researching", "Finding the general chair and the right contact address"),
    ("contacted", "Introduction sent, awaiting reply"),
    ("in_conversation", "They replied; discussing what hosting would involve"),
    ("proposal_sent", "Formal hosting proposal or bid submitted"),
    ("committed", "Agreed in principle to hold it with us"),
    ("declined", "Not proceeding - keep the note for next year"),
]
STAGE_IDS = [s for s, _ in STAGES]

SYSTEM = """You draft outreach emails for an IEEE student branch chapter that \
wants to host or co-host technical events on its campus.

Write like a capable student volunteer, not a marketing department. Short \
paragraphs, concrete offers, no adjectives that cannot be verified. Never \
invent facilities, past events, numbers or endorsements that were not supplied. \
The goal of the first email is a reply, not a signed agreement."""


def stage_index(stage: str) -> int:
    return STAGE_IDS.index(stage) if stage in STAGE_IDS else 0


def advance(conference_id: int, to_stage: str, note: str | None = None) -> dict[str, Any]:
    """Move a conference along the pipeline, keeping a dated note trail."""
    if to_stage not in STAGE_IDS:
        raise ValueError(f"unknown stage: {to_stage}")

    current = store.get_outreach(conference_id) or {}
    notes = current.get("notes") or ""
    if note:
        notes = f"{notes}\n[{date.today().isoformat()}] {note}".strip()

    fields: dict[str, Any] = {"stage": to_stage, "notes": notes}

    # Give every live stage a default follow-up date so nothing stalls silently.
    if to_stage in ("contacted", "in_conversation", "proposal_sent"):
        gap = {"contacted": 10, "in_conversation": 7, "proposal_sent": 14}[to_stage]
        fields["next_action_date"] = (date.today() + timedelta(days=gap)).isoformat()
        fields["next_action"] = {
            "contacted": "Send a polite follow-up if there is still no reply",
            "in_conversation": "Reply with the venue details and dates we can offer",
            "proposal_sent": "Check whether the committee has reviewed our proposal",
        }[to_stage]
    elif to_stage in ("committed", "declined"):
        fields["next_action"] = None
        fields["next_action_date"] = None

    store.upsert_outreach(conference_id, **fields)
    return store.get_outreach(conference_id) or {}


def _venue_pitch() -> str:
    c = settings.chapter
    return (
        f"{c.institution} in {c.city}, {c.country}, with a main auditorium seating "
        f"about {c.venue_capacity}, supported by the {c.name} and {c.section}."
    )


def prose_title(conf: dict[str, Any]) -> str:
    """Title as it should read mid-sentence.

    Listing titles are prefixed with their feed's label ("Call for Organizers:
    IEEE Conference on..."), which reads as a stutter inside a sentence that
    already says "the call for organizers for...".
    """
    title = str(conf.get("title") or "your conference")
    for prefix in ("Call for Organizers:", "Call for Proposals:", "Call for Papers:"):
        if title.lower().startswith(prefix.lower()):
            return title[len(prefix):].strip()
    return title


def _template_draft(conf: dict[str, Any], contact_name: str | None) -> str:
    c = settings.chapter
    greeting = f"Dear {contact_name}," if contact_name else "Dear Organising Committee,"
    title = prose_title(conf)
    when = conf.get("start_date")
    when_line = f" scheduled for {when}" if when else ""
    is_call = conf.get("kind") == "call_for_organizers"

    if is_call:
        body = (
            f"I am writing in response to the open call for organizers for {title}. "
            f"Our chapter would like to be considered as a host institution.\n\n"
            f"We are {_venue_pitch()} Our committee has organised technical events on "
            f"campus and we have faculty willing to serve on the organising committee.\n\n"
            f"Could you tell me what the proposal requires and whether a student branch "
            f"chapter may submit jointly with its Section? I am happy to send a short "
            f"venue and capability note if that would help."
        )
    else:
        body = (
            f"I am the Chair of the {c.name} at {c.institution}. I came across "
            f"{title}{when_line} and wanted to ask whether the organising committee has "
            f"considered {c.city} as a venue, either for the main event or for a "
            f"satellite workshop or local edition.\n\n"
            f"We can offer {_venue_pitch()} Our chapter can provide student volunteers, "
            f"local logistics support and publicity across nearby institutions.\n\n"
            f"If there is interest, I would be glad to send a short venue note or arrange "
            f"a call at your convenience."
        )

    return (
        f"{greeting}\n\n{body}\n\nThank you for your time.\n\n"
        f"Best regards,\n{c.signature}"
    )


def draft_email(
    conference_id: int, contact_name: str | None = None, use_llm: bool = True
) -> dict[str, Any]:
    """Draft an outreach email for a conference. Always returns something."""
    conf = store.get_conference(conference_id)
    if not conf:
        raise ValueError(f"no conference with id {conference_id}")

    c = settings.chapter
    is_call = conf.get("kind") == "call_for_organizers"
    subject = (
        f"Hosting proposal enquiry - {conf.get('acronym') or conf.get('title', '')[:60]}"
        if is_call
        else f"{c.institution} as a venue for {conf.get('acronym') or conf.get('title', '')[:50]}"
    )

    body = None
    if use_llm and llm.available():
        precedent = kb.find_similar(conf.get("title", ""), k=3)
        prompt = f"""Draft an outreach email.

CONFERENCE
Title: {conf.get('title')}
Type: {conf.get('kind')}
Dates: {conf.get('start_date') or 'not announced'}
Location so far: {conf.get('location') or 'not announced'}
Proposal deadline: {conf.get('proposal_deadline') or 'none stated'}
Link: {conf.get('url') or 'n/a'}
Why we flagged it: {'; '.join(conf.get('fit_reasons') or [])}

SENDER
{c.chair_name}, Chair of {c.name}
{c.institution}, {c.city}, {c.country}
IEEE Region {c.region}, {c.section}
Venue capacity: about {c.venue_capacity} seats
Contact: {c.chair_email or 'omit if blank'}

RECIPIENT
{contact_name or 'the organising committee (name unknown - use a neutral greeting)'}

RELATED SPS EVENTS WE CAN REFERENCE (only if genuinely relevant)
{chr(10).join('- ' + p['title'] for p in precedent) or '- none'}

Write the email body only - no subject line, no markdown. Under 220 words.
{"This is an open call for organizers, so ask directly what the hosting proposal requires and whether a student chapter may submit jointly with its Section."
 if is_call else
 "They have not asked for hosts, so be modest: ask whether they would consider our city for the event or a satellite workshop."}
End with a sign-off using the sender details above."""
        body = llm.complete(SYSTEM, prompt)

    source = "llm" if body else "template"
    if not body:
        body = _template_draft(conf, contact_name)

    draft = f"Subject: {subject}\n\n{body}"
    store.upsert_outreach(conference_id, draft=draft)
    return {"subject": subject, "body": body, "draft": draft, "source": source}


def pipeline() -> dict[str, list[dict[str, Any]]]:
    """Everything in outreach, bucketed by stage for a board view."""
    buckets: dict[str, list[dict[str, Any]]] = {s: [] for s in STAGE_IDS}
    for row in store.list_outreach():
        buckets.setdefault(row.get("stage") or "identified", []).append(row)
    return buckets


def funnel_stats() -> dict[str, int]:
    return {stage: len(rows) for stage, rows in pipeline().items()}
