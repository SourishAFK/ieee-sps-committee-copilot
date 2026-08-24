"""Chapter activity reporting.

Every year the chair has to account for what the chapter did - for the Section,
for Society awards, and for the annual chapter report. That is painful only
because nobody wrote things down at the time. If events are logged here as they
happen, the report is a query rather than an archaeology project.
"""
from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from . import llm, store
from .config import settings


def _year_of(iso: str | None) -> int | None:
    if not iso or len(str(iso)) < 4:
        return None
    try:
        return int(str(iso)[:4])
    except ValueError:
        return None


def collect_year(year: int) -> dict[str, Any]:
    """Aggregate everything the chapter did in one calendar year."""
    events = store.list_events(year)

    total_attendance = sum(int(e.get("attendance") or 0) for e in events)
    total_volunteers = sum(int(e.get("volunteers") or 0) for e in events)
    total_spend = sum(float(e.get("budget_spent") or 0) for e in events)

    by_format = Counter(e.get("format") or "unspecified" for e in events)
    partners: Counter = Counter()
    for e in events:
        for p in str(e.get("co_society") or "").split(","):
            p = p.strip()
            if p:
                partners[p] += 1

    by_month: dict[int, int] = defaultdict(int)
    for e in events:
        iso = e.get("event_date") or ""
        if len(iso) >= 7:
            try:
                by_month[int(iso[5:7])] += 1
            except ValueError:
                pass

    # Outreach that turned into something in this year.
    pipeline_wins = [
        row
        for row in store.list_outreach()
        if row.get("stage") == "committed" and _year_of(row.get("updated_at")) == year
    ]

    largest = max(events, key=lambda e: int(e.get("attendance") or 0), default=None)

    return {
        "year": year,
        "chapter": settings.chapter.name,
        "institution": settings.chapter.institution,
        "section": settings.chapter.section,
        "events": events,
        "event_count": len(events),
        "total_attendance": total_attendance,
        "total_volunteers": total_volunteers,
        "total_spend": round(total_spend, 2),
        "avg_attendance": round(total_attendance / len(events)) if events else 0,
        "by_format": dict(by_format),
        "by_month": dict(sorted(by_month.items())),
        "partners": dict(partners),
        "distinct_partners": len(partners),
        "largest_event": largest,
        "conferences_committed": len(pipeline_wins),
    }


def to_markdown(report: dict[str, Any], narrative: str | None = None) -> str:
    r = report
    lines = [
        f"# {r['chapter']} - Annual Activity Report {r['year']}",
        "",
        f"**Institution:** {r['institution']}  ",
        f"**Section:** {r['section']}  ",
        f"**Reporting period:** 1 January {r['year']} - 31 December {r['year']}  ",
        "",
        "## Summary",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Events held | {r['event_count']} |",
        f"| Total attendance | {r['total_attendance']} |",
        f"| Average attendance per event | {r['avg_attendance']} |",
        f"| Volunteers involved | {r['total_volunteers']} |",
        f"| Total expenditure (INR) | {r['total_spend']:,.0f} |",
        f"| Distinct partner societies | {r['distinct_partners']} |",
    ]
    if r["conferences_committed"]:
        lines.append(f"| Conferences secured for our campus | {r['conferences_committed']} |")

    if narrative:
        lines += ["", "## Year in review", "", narrative]

    if r["events"]:
        lines += ["", "## Events held", "", "| Date | Event | Format | Partner | Attendance |", "|---|---|---|---|---:|"]
        for e in sorted(r["events"], key=lambda x: str(x.get("event_date") or "")):
            lines.append(
                f"| {e.get('event_date') or '-'} | {e.get('title')} | "
                f"{e.get('format') or '-'} | {e.get('co_society') or '-'} | "
                f"{e.get('attendance') or 0} |"
            )
    else:
        lines += ["", "_No events logged for this year yet._"]

    if r["by_format"]:
        lines += ["", "## Activity mix", ""]
        lines += [f"- {fmt}: {n} event(s)" for fmt, n in sorted(r["by_format"].items(), key=lambda kv: -kv[1])]

    if r["partners"]:
        lines += ["", "## Collaborations", ""]
        lines += [f"- {p}: {n} joint event(s)" for p, n in sorted(r["partners"].items(), key=lambda kv: -kv[1])]

    if r["largest_event"]:
        le = r["largest_event"]
        lines += [
            "",
            "## Highlight",
            "",
            f"**{le.get('title')}** ({le.get('event_date') or 'date not recorded'}) drew "
            f"{le.get('attendance') or 0} attendees.",
        ]
        if le.get("outcomes"):
            lines.append(f"\n{le['outcomes']}")

    lines += [
        "",
        "---",
        "",
        f"_Generated by the SPS Committee Copilot on {date.today().isoformat()}. "
        "Figures come from events logged by the committee._",
    ]
    return "\n".join(lines)


def to_csv(report: dict[str, Any]) -> str:
    """Flat CSV of the year's events, for pasting into a reporting form."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["Date", "Title", "Format", "Co-society", "Speakers", "Attendance", "Volunteers", "Spend (INR)", "Outcomes"]
    )
    for e in sorted(report["events"], key=lambda x: str(x.get("event_date") or "")):
        writer.writerow(
            [
                e.get("event_date") or "",
                e.get("title") or "",
                e.get("format") or "",
                e.get("co_society") or "",
                e.get("speakers") or "",
                e.get("attendance") or 0,
                e.get("volunteers") or 0,
                e.get("budget_spent") or 0,
                (e.get("outcomes") or "").replace("\n", " "),
            ]
        )
    return buf.getvalue()


def narrative(report: dict[str, Any]) -> str | None:
    """Optional LLM-written year-in-review paragraph."""
    if not llm.available() or not report["events"]:
        return None

    listing = "\n".join(
        f"- {e.get('event_date') or '?'}: {e.get('title')} "
        f"({e.get('format') or 'event'}, {e.get('attendance') or 0} attendees"
        + (f", with {e['co_society']}" if e.get("co_society") else "")
        + ")"
        for e in sorted(report["events"], key=lambda x: str(x.get("event_date") or ""))
    )
    prompt = f"""Write the "year in review" section of an IEEE chapter's annual report.

CHAPTER: {report['chapter']}, {report['institution']}
YEAR: {report['year']}
Events held: {report['event_count']}
Total attendance: {report['total_attendance']}
Volunteers: {report['total_volunteers']}
Partner societies: {', '.join(report['partners']) or 'none'}

EVENTS
{listing}

Write 2 short paragraphs. State what the chapter did and what it achieved, using
only the figures above. No invented awards, membership numbers or quotes. Plain
professional prose - this goes to an IEEE Section."""
    return llm.complete(
        "You write concise, factual annual reports for IEEE chapters.", prompt
    )


def available_years() -> list[int]:
    years = {_year_of(e.get("event_date")) for e in store.list_events()}
    years.discard(None)
    return sorted(years, reverse=True) or [date.today().year]  # type: ignore[list-item]


def multi_year_trend() -> list[dict[str, Any]]:
    """Per-year headline figures, for a trend chart."""
    return [
        {
            "year": y,
            **{
                k: v
                for k, v in collect_year(y).items()
                if k in ("event_count", "total_attendance", "total_spend", "distinct_partners")
            },
        }
        for y in sorted(available_years())
    ]
