"""Event playbook generator.

Turns an approved pitch into the thing a committee can actually execute: a
budget with named line items, a working-backwards timeline, role assignments,
and the IEEE approvals that have to be started early.

Budget figures come from the curated format data, not from the model, so the
numbers stay defensible in front of a faculty advisor.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from . import knowledge as kb
from . import llm
from .config import settings

# Share of total budget by line item, per format family. Rough but honest:
# these reflect where money actually goes on an Indian campus event.
BUDGET_SHAPES: dict[str, dict[str, float]] = {
    "default": {
        "Speaker travel & honorarium": 0.30,
        "Catering (tea/lunch)": 0.25,
        "Materials, kits & printing": 0.15,
        "Venue setup, AV & recording": 0.15,
        "Certificates, prizes & mementos": 0.10,
        "Contingency": 0.05,
    },
    "webinar": {
        "Platform / streaming licence": 0.40,
        "Speaker honorarium or token": 0.35,
        "Promotion & design": 0.15,
        "Contingency": 0.10,
    },
    "hackathon": {
        "Prize pool": 0.35,
        "Food across the event": 0.30,
        "Compute / cloud credits": 0.12,
        "Venue, power & networking": 0.10,
        "Swag & printing": 0.08,
        "Contingency": 0.05,
    },
    "seasonal_school": {
        "Speaker travel & accommodation": 0.32,
        "Participant accommodation & meals": 0.30,
        "Venue & AV": 0.12,
        "Materials & kits": 0.10,
        "Local transport": 0.08,
        "Contingency": 0.08,
    },
    "conference_host": {
        "Venue & AV (multi-hall)": 0.25,
        "Catering across all days": 0.22,
        "Publication & proceedings costs": 0.15,
        "Keynote travel & accommodation": 0.15,
        "Registration platform & banking": 0.08,
        "Printing, signage & kits": 0.07,
        "Contingency (IEEE expects a real one)": 0.08,
    },
}

ROLES = [
    ("General / Event Chair", "Owns the outcome, chairs committee meetings, signs off spend"),
    ("Technical Programme Lead", "Speakers, agenda, content quality, dry runs"),
    ("Logistics Lead", "Venue, AV, catering, accommodation, transport"),
    ("Finance Lead", "Budget tracking, sponsor invoicing, reimbursements, IEEE reporting"),
    ("Publicity Lead", "Posters, social posts, mailing lists, partner chapter promotion"),
    ("Registration Lead", "Sign-up form, attendance list, certificates"),
    ("Volunteer Coordinator", "Rosters on the day, briefing, session support"),
]

# Approvals that have long, fixed lead times and are the usual cause of slippage.
IEEE_APPROVALS: dict[str, list[str]] = {
    "conference_host": [
        "Submit an IEEE conference application through MCE and secure a financially sponsoring OU",
        "Agree in writing which OU carries financial risk (your Section, not the student branch)",
        "Confirm technical co-sponsorship terms with IEEE SPS",
        "Register the conference for IEEE Xplore publication if papers will be published",
    ],
    "seasonal_school": [
        "Submit a Seasonal School proposal in the annual SPS cycle",
        "Confirm faculty leads and a named academic programme committee",
    ],
    "distinguished_lecture": [
        "Submit a Distinguished Lecturer request to SPS with proposed dates",
        "Confirm the DL's travel arrangements and who books them",
    ],
    "sp_cup": [
        "Register your campus team(s) in the current SP Cup / VIP Cup cycle",
    ],
    "chapter_initiative": [
        "Submit a Chapter Initiative proposal to SPS with a named community partner",
    ],
    "default": [
        "Inform your IEEE Section and Student Branch counsellor before announcing dates",
        "Record the event in vTools Events so it counts toward chapter reporting",
    ],
}


def _budget(fmt: dict[str, Any], target_total: int | None = None) -> dict[str, Any]:
    lo, hi = fmt.get("budget_min", 0), fmt.get("budget_max", 0)
    total = target_total if target_total else int((lo + hi) / 2)
    shape = BUDGET_SHAPES.get(fmt["id"], BUDGET_SHAPES["default"])
    lines = [
        {"item": item, "share": round(share * 100), "amount": int(round(total * share))}
        for item, share in shape.items()
    ]
    # Absorb rounding drift into the last line so the column sums exactly.
    drift = total - sum(line["amount"] for line in lines)
    if lines:
        lines[-1]["amount"] += drift
    return {
        "currency": "INR",
        "total": total,
        "range": [lo, hi],
        "lines": lines,
    }


def _milestones(fmt: dict[str, Any], event_day: date) -> list[dict[str, str]]:
    weeks = fmt.get("lead_weeks", 8)
    plan = [
        (1.00, "Committee approves the idea; owner and faculty advisor named"),
        (0.90, "Theme, target audience and success measure agreed in writing"),
        (0.80, "Funding request or sponsor approach begins; co-hosting chapter confirmed"),
        (0.65, "Speakers invited and confirmed in writing"),
        (0.50, "Venue booked; date announced publicly; registration opens"),
        (0.35, "Promotion push; partner chapters circulate to their members"),
        (0.20, "Materials finalised; volunteer roles assigned and briefed"),
        (0.08, "Full technical dry run; reminder to registrants"),
        (0.02, "Final headcount to catering; print certificates and signage"),
        (0.00, "Event day - collect attendance, photos and feedback before anyone leaves"),
        (-0.04, "Thank-you notes, reimbursements, and log the event for annual reporting"),
    ]
    out = []
    for fraction, task in plan:
        when = event_day - timedelta(weeks=weeks * fraction)
        out.append(
            {
                "date": when.isoformat(),
                "weeks_before": round(weeks * fraction, 1),
                "task": task,
            }
        )
    return out


def _parse_day(value: str | None, fmt: dict[str, Any]) -> date:
    if value:
        try:
            return datetime.fromisoformat(str(value)).date()
        except ValueError:
            pass
    return date.today() + timedelta(weeks=fmt.get("lead_weeks", 8))


def generate(
    pitch: dict[str, Any],
    event_date: str | None = None,
    budget_total: int | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Build a full execution plan for a pitch."""
    text = " ".join(str(pitch.get(k) or "") for k in ("title", "idea", "format", "audience"))
    fmt = kb.resolve_format(str(pitch.get("format") or "")) or kb.suggest_format(text)
    if not fmt:
        fmt = kb.get_format("workshop") or {"id": "workshop", "name": "Workshop", "lead_weeks": 8}

    day = _parse_day(event_date, fmt)
    societies = kb.match_societies(text, k=2)
    approvals = IEEE_APPROVALS.get(fmt["id"], []) + IEEE_APPROVALS["default"]

    volunteers = fmt.get("volunteers", 8)
    roles = ROLES[: max(3, min(len(ROLES), volunteers))]

    plan: dict[str, Any] = {
        "title": pitch.get("title") or "Untitled event",
        "format": fmt,
        "event_date": day.isoformat(),
        "budget": _budget(fmt, budget_total),
        "milestones": _milestones(fmt, day),
        "roles": [{"role": r, "responsibility": d} for r, d in roles],
        "approvals": approvals,
        "co_societies": [
            {"abbr": s["abbr"], "name": s["name"], "pitch_angle": s["pitch_angle"]}
            for s in societies
        ],
        "precedent": kb.find_similar(text, k=4),
        "risks": fmt.get("risks", []),
        "funding_programme": fmt.get("sps_program"),
        "promotion": _promotion_plan(day),
        "source": "rules",
    }

    if use_llm and llm.available():
        extra = _llm_pass(pitch, plan)
        if extra:
            plan.update(extra)
            plan["source"] = f"rules + {settings.llm.provider}"
    return plan


def _promotion_plan(event_day: date) -> list[dict[str, str]]:
    beats = [
        (21, "Announce with a save-the-date post and open registration"),
        (14, "Speaker spotlight post; email the student branch mailing list"),
        (7, "Share the agenda; ask partner chapters to repost"),
        (2, "Last-call post and WhatsApp reminder to registrants"),
        (0, "Live posts during the event; photos the same evening"),
        (-2, "Thank-you post tagging speakers and sponsors; share the recording"),
    ]
    return [
        {"date": (event_day - timedelta(days=d)).isoformat(), "action": action}
        for d, action in beats
    ]


def _llm_pass(pitch: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
    fmt = plan["format"]
    prompt = f"""An IEEE SPS student chapter is planning this event.

EVENT
Title: {plan['title']}
Idea: {pitch.get('idea')}
Format: {fmt['name']}
Date: {plan['event_date']}
Budget: INR {plan['budget']['total']:,}
Expected audience: {pitch.get('audience') or 'not specified'}
Venue capacity available: {settings.chapter.venue_capacity}
Location: {settings.chapter.institution}, {settings.chapter.city}, {settings.chapter.country}
Funding programme: {plan.get('funding_programme') or 'none'}

Return JSON with:
  "agenda": array of 4-8 objects, each {{"time": "10:00-11:00", "item": "..."}} for a
    realistic run sheet for this format.
  "speaker_profiles": array of 3-5 strings describing the KIND of speaker to invite
    (role and expertise, not invented names).
  "sponsor_targets": array of 3-5 categories of sponsor plausible for this event in
    {settings.chapter.country}, each with the ask in one clause.
  "success_metrics": array of 3-5 measurable targets to judge the event by.
  "watch_outs": array of 2-4 failure modes specific to THIS event, not generic advice.
Do not invent IEEE programmes, grants or figures."""
    data = llm.complete_json(
        "You plan technical events for IEEE student chapters. Be concrete and realistic "
        "about volunteer capacity and campus constraints.",
        prompt,
    )
    if not data:
        return None

    out: dict[str, Any] = {}
    agenda = data.get("agenda")
    if isinstance(agenda, list) and agenda:
        clean = []
        for row in agenda[:10]:
            if isinstance(row, dict) and row.get("item"):
                clean.append({"time": str(row.get("time", "")), "item": str(row["item"])})
        if clean:
            out["agenda"] = clean
    for key in ("speaker_profiles", "sponsor_targets", "success_metrics", "watch_outs"):
        val = data.get(key)
        if isinstance(val, list) and val:
            out[key] = [str(v).strip() for v in val if str(v).strip()][:6]
    return out or None


def to_markdown(plan: dict[str, Any]) -> str:
    """Render a plan as a document the committee can paste into a doc or email."""
    b = plan["budget"]
    lines = [
        f"# {plan['title']}",
        "",
        f"**Format:** {plan['format']['name']}  ",
        f"**Target date:** {plan['event_date']}  ",
        f"**Budget:** INR {b['total']:,} (typical range {b['range'][0]:,}-{b['range'][1]:,})  ",
    ]
    if plan.get("funding_programme"):
        lines.append(f"**Funding route:** {plan['funding_programme']}  ")
    if plan.get("co_societies"):
        lines.append(
            "**Suggested co-hosts:** "
            + ", ".join(f"{s['abbr']}" for s in plan["co_societies"])
            + "  "
        )

    if plan.get("agenda"):
        lines += ["", "## Run sheet", ""]
        lines += [f"- **{a['time']}** {a['item']}" for a in plan["agenda"]]

    lines += ["", "## Budget", "", "| Line item | Share | Amount (INR) |", "|---|---:|---:|"]
    lines += [f"| {l['item']} | {l['share']}% | {l['amount']:,} |" for l in b["lines"]]
    lines.append(f"| **Total** | **100%** | **{b['total']:,}** |")

    lines += ["", "## Timeline", "", "| Date | Milestone |", "|---|---|"]
    lines += [f"| {m['date']} | {m['task']} |" for m in plan["milestones"]]

    lines += ["", "## Committee roles", ""]
    lines += [f"- **{r['role']}** - {r['responsibility']}" for r in plan["roles"]]

    lines += ["", "## IEEE approvals and admin", ""]
    lines += [f"- {a}" for a in plan["approvals"]]

    if plan.get("speaker_profiles"):
        lines += ["", "## Speakers to target", ""]
        lines += [f"- {s}" for s in plan["speaker_profiles"]]
    if plan.get("sponsor_targets"):
        lines += ["", "## Sponsor targets", ""]
        lines += [f"- {s}" for s in plan["sponsor_targets"]]

    lines += ["", "## Promotion plan", ""]
    lines += [f"- **{p['date']}** {p['action']}" for p in plan["promotion"]]

    if plan.get("success_metrics"):
        lines += ["", "## Success metrics", ""]
        lines += [f"- {m}" for m in plan["success_metrics"]]

    risks = list(plan.get("watch_outs") or []) + list(plan.get("risks") or [])
    if risks:
        lines += ["", "## Risks", ""]
        lines += [f"- {r}" for r in dict.fromkeys(risks)]

    if plan.get("precedent"):
        lines += ["", "## Comparable past IEEE SPS events", ""]
        lines += [
            f"- [{p['title']}]({p['url']})" if p.get("url") else f"- {p['title']}"
            for p in plan["precedent"]
        ]
    return "\n".join(lines)
