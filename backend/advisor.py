"""The event-idea advisor.

Takes a pitch - "a joint workshop with the robotics chapter on SLAM" - and
returns structured feedback grounded in what SPS has actually run before.

Two paths produce the same shape:
  * with an LLM, the narrative parts are written by the model but the
    precedent, format economics and society matches are still supplied by our
    own knowledge base, so the model cannot invent a funding programme;
  * without one, everything is derived from rules. Terser, never wrong about
    budgets, and it costs nothing.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import knowledge as kb
from . import llm
from .config import settings

VERDICTS = ("strong", "promising", "needs work", "reconsider")

SYSTEM = """You advise an IEEE Signal Processing Society student branch chapter \
on whether to run a proposed event.

You will be given the pitch plus verified context: past IEEE SPS events that \
resemble it, the matching event format with real budget and lead-time figures, \
and candidate co-hosting societies. Treat that context as ground truth.

Rules:
- Never invent an IEEE programme, grant, deadline or budget figure. Use only the \
figures supplied.
- Be concrete and blunt. "Speaker cost will dominate; line up the DL application \
by March" beats "consider your budget carefully".
- Judge feasibility for a STUDENT chapter with volunteer labour, not a \
professional conference organiser.
- If the idea is weak, say so plainly and name the specific thing that would fix it.
"""


def _pitch_text(pitch: dict[str, Any]) -> str:
    return " ".join(
        str(pitch.get(k) or "")
        for k in ("title", "idea", "format", "audience", "co_society")
    ).strip()


def _rule_score(pitch: dict[str, Any], precedent: list[dict], fmt: dict | None) -> tuple[int, list[str], list[str]]:
    """Deterministic feasibility score with strengths and risks."""
    strengths: list[str] = []
    risks: list[str] = []
    score = 40

    idea = str(pitch.get("idea") or "")
    if len(idea) < 60:
        risks.append("The pitch is too thin to evaluate - say what attendees will actually do.")
        score -= 10
    else:
        score += 8
        strengths.append("Idea is described in enough detail to plan against.")

    if precedent:
        score += 14
        strengths.append(
            f"Clear precedent: SPS has run {len(precedent)} similar event(s), "
            f"closest being \"{precedent[0]['title'][:70]}\"."
        )
    else:
        risks.append("No close precedent in the SPS archive - you will be defining the format yourself.")

    if fmt:
        score += 10
        if fmt.get("sps_program"):
            score += 8
            strengths.append(f"Fits an existing SPS programme: {fmt['sps_program']}.")
        difficulty = fmt.get("difficulty", "medium")
        if difficulty in ("high", "very high"):
            score -= 8
            risks.append(
                f"{fmt['name']} is a {difficulty}-difficulty format needing about "
                f"{fmt['lead_weeks']} weeks and {fmt['volunteers']} volunteers."
            )
        else:
            strengths.append(
                f"{fmt['name']} is achievable in ~{fmt['lead_weeks']} weeks "
                f"with {fmt['volunteers']} volunteers."
            )
        risks.extend(fmt.get("risks", [])[:2])

    if pitch.get("co_society"):
        score += 8
        strengths.append("A co-hosting society is already identified - split the cost and the work.")

    if pitch.get("audience"):
        score += 5

    return max(0, min(100, score)), strengths, risks


def _timeline(fmt: dict | None) -> list[dict[str, str]]:
    """Working-backwards plan from today to delivery."""
    weeks = (fmt or {}).get("lead_weeks", 8)
    target = date.today() + timedelta(weeks=weeks)
    milestones = [
        (weeks, "Lock the theme, the faculty advisor and the co-hosting chapter"),
        (int(weeks * 0.8), "Confirm speakers and submit any IEEE/SPS funding request"),
        (int(weeks * 0.6), "Book the venue, open registration, publish the CFP or agenda"),
        (int(weeks * 0.35), "Sponsor follow-ups, print material, volunteer role assignment"),
        (int(weeks * 0.15), "Dry run of the technical setup; reminder mail to registrants"),
        (0, "Run the event; collect attendance and feedback the same day"),
    ]
    out = []
    for weeks_before, task in milestones:
        when = target - timedelta(weeks=weeks_before)
        out.append({"date": when.isoformat(), "weeks_before": str(weeks_before), "task": task})
    return out


def _next_steps(fmt: dict | None, societies: list[dict]) -> list[str]:
    steps = ["Put the idea to the committee and name one owner - not a group."]
    if fmt and fmt.get("sps_program"):
        steps.append(f"Check the current cycle and eligibility for {fmt['sps_program']}.")
    if societies:
        steps.append(
            f"Approach the {societies[0]['abbr']} chapter chair: {societies[0]['pitch_angle']}"
        )
    if fmt:
        steps.append(
            f"Draft a budget between INR {fmt['budget_min']:,} and {fmt['budget_max']:,} "
            "and get faculty advisor sign-off before announcing a date."
        )
    steps.append("Log the event here once approved so it lands in the annual activity report.")
    return steps


def _verdict(score: int) -> str:
    if score >= 78:
        return "strong"
    if score >= 60:
        return "promising"
    if score >= 42:
        return "needs work"
    return "reconsider"


def evaluate(pitch: dict[str, Any], use_llm: bool = True) -> dict[str, Any]:
    """Full structured feedback on a pitch."""
    text = _pitch_text(pitch)
    precedent = kb.find_similar(text, k=5)
    societies = kb.match_societies(text, k=3)

    audience = pitch.get("audience")
    try:
        audience_n = int(str(audience).strip()) if audience else None
    except ValueError:
        audience_n = None
    # A format the committee stated outright beats one inferred from prose.
    fmt = kb.resolve_format(str(pitch.get("format") or "")) or kb.suggest_format(text, audience_n)

    score, strengths, risks = _rule_score(pitch, precedent, fmt)

    result: dict[str, Any] = {
        "verdict": _verdict(score),
        "score": score,
        "summary": "",
        "strengths": strengths,
        "risks": risks,
        "precedent": precedent,
        "suggested_format": fmt,
        "co_societies": [
            {"abbr": s["abbr"], "name": s["name"], "pitch_angle": s["pitch_angle"]}
            for s in societies
        ],
        "next_steps": _next_steps(fmt, societies),
        "timeline": _timeline(fmt),
        "funding": [fmt["sps_program"]] if fmt and fmt.get("sps_program") else [],
        "source": "rules",
    }

    if use_llm and llm.available():
        enriched = _llm_pass(pitch, result)
        if enriched:
            result.update(enriched)
            result["source"] = f"rules + {settings.llm.provider}"

    if not result["summary"]:
        fmt_name = fmt["name"] if fmt else "event"
        result["summary"] = (
            f"Assessed as {result['verdict']} ({score}/100). Closest fit is a {fmt_name.lower()}; "
            f"{len(precedent)} comparable SPS event(s) found."
        )
    return result


def _llm_pass(pitch: dict[str, Any], base: dict[str, Any]) -> dict[str, Any] | None:
    fmt = base.get("suggested_format") or {}
    precedent_lines = "\n".join(
        f"- {p['title']} ({p.get('date') or 'date n/a'}) {p.get('url') or ''}"
        for p in base["precedent"]
    ) or "- none found"
    society_lines = "\n".join(
        f"- {s['abbr']} ({s['name']}): {s['pitch_angle']}" for s in base["co_societies"]
    ) or "- none matched"

    prompt = f"""PITCH
Title: {pitch.get('title') or '(none)'}
Idea: {pitch.get('idea')}
Proposed format: {pitch.get('format') or '(not specified)'}
Target audience: {pitch.get('audience') or '(not specified)'}
Desired co-society: {pitch.get('co_society') or '(none)'}

CHAPTER
{settings.chapter.name} at {settings.chapter.institution}, {settings.chapter.city},
{settings.chapter.country} (IEEE Region {settings.chapter.region}).
Largest venue seats about {settings.chapter.venue_capacity}.

VERIFIED CONTEXT - do not contradict this
Closest matching format: {fmt.get('name', 'unknown')}
  typical lead time: {fmt.get('lead_weeks', '?')} weeks
  budget range: INR {fmt.get('budget_min', '?')} - {fmt.get('budget_max', '?')}
  volunteers needed: {fmt.get('volunteers', '?')}
  supporting SPS programme: {fmt.get('sps_program') or 'none'}
  known risks: {'; '.join(fmt.get('risks', [])) or 'none recorded'}

Comparable past IEEE SPS events:
{precedent_lines}

Candidate co-hosting societies:
{society_lines}

Our rule-based feasibility score: {base['score']}/100 ({base['verdict']}).

TASK
Return JSON with these keys:
  "summary": 2-3 sentences giving your honest read on whether to run this.
  "strengths": array of 2-4 specific strengths.
  "risks": array of 2-4 specific risks, each naming what would go wrong.
  "next_steps": array of 3-5 concrete actions in order, each starting with a verb.
  "differentiator": one sentence on what would make this event distinctive rather
    than another generic campus workshop.
  "verdict": one of "strong", "promising", "needs work", "reconsider".
"""
    data = llm.complete_json(SYSTEM, prompt)
    if not data:
        return None

    out: dict[str, Any] = {}
    if isinstance(data.get("summary"), str):
        out["summary"] = data["summary"].strip()
    for key in ("strengths", "risks", "next_steps"):
        val = data.get(key)
        if isinstance(val, list) and val:
            out[key] = [str(v).strip() for v in val if str(v).strip()][:5]
    if isinstance(data.get("differentiator"), str):
        out["differentiator"] = data["differentiator"].strip()
    if data.get("verdict") in VERDICTS:
        out["verdict"] = data["verdict"]
    return out or None


# ---------------------------------------------------------------------------
# follow-up conversation
# ---------------------------------------------------------------------------
def discuss(message: str, history: list[dict[str, str]] | None = None) -> str:
    """Free-form follow-up, still grounded in the knowledge base."""
    history = history or []
    precedent = kb.find_similar(message, k=4)
    societies = kb.match_societies(message, k=2)
    fmt = kb.suggest_format(message)

    if not llm.available():
        return _rule_reply(message, precedent, societies, fmt)

    context = []
    if precedent:
        context.append(
            "Relevant past IEEE SPS events:\n"
            + "\n".join(f"- {p['title']} ({p.get('date') or 'n/a'})" for p in precedent)
        )
    if fmt:
        context.append(
            f"Closest format: {fmt['name']} - {fmt['lead_weeks']} weeks lead, "
            f"INR {fmt['budget_min']:,}-{fmt['budget_max']:,}, "
            f"{fmt['volunteers']} volunteers, programme: {fmt.get('sps_program') or 'none'}"
        )
    if societies:
        context.append(
            "Co-hosting candidates:\n"
            + "\n".join(f"- {s['abbr']}: {s['pitch_angle']}" for s in societies)
        )

    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    prompt = (
        f"{'CONVERSATION SO FAR' + chr(10) + convo + chr(10) if convo else ''}"
        f"VERIFIED CONTEXT\n{chr(10).join(context) or 'nothing specific matched'}\n\n"
        f"CHAPTER: {settings.chapter.name}, {settings.chapter.institution}, "
        f"{settings.chapter.city}, {settings.chapter.country}\n\n"
        f"QUESTION\n{message}\n\n"
        "Answer in at most 200 words. Be specific and practical. Do not invent "
        "IEEE programmes, deadlines or figures beyond the verified context."
    )
    reply = llm.complete(SYSTEM, prompt, use_cache=False)
    return reply or _rule_reply(message, precedent, societies, fmt)


def _rule_reply(
    message: str, precedent: list[dict], societies: list[dict], fmt: dict | None
) -> str:
    lines = ["*(rules-only mode - add an API key for a fuller discussion)*", ""]
    if fmt:
        lines.append(
            f"**Closest format:** {fmt['name']} - about {fmt['lead_weeks']} weeks lead time, "
            f"INR {fmt['budget_min']:,}-{fmt['budget_max']:,}, ~{fmt['volunteers']} volunteers."
        )
        if fmt.get("sps_program"):
            lines.append(f"**Supporting programme:** {fmt['sps_program']}")
        if fmt.get("notes"):
            lines.append(f"**Note:** {fmt['notes']}")
    if precedent:
        lines.append("\n**Past SPS events like this:**")
        lines += [f"- {p['title']}" + (f" ({p['date']})" if p.get("date") else "") for p in precedent]
    if societies:
        lines.append("\n**Worth co-hosting with:**")
        lines += [f"- **{s['abbr']}** - {s['pitch_angle']}" for s in societies]
    if len(lines) <= 2:
        lines.append("Nothing in the knowledge base matched. Try naming the topic and the format.")
    return "\n".join(lines)
