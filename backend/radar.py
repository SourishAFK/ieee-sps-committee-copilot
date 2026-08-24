"""Deadline radar.

The dates that actually bind a chapter are rarely the conference dates - they
are the proposal windows, CFP cutoffs and internal go/no-go points that close
quietly months earlier. This collects them into one ordered list and marks the
ones about to expire.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from . import store


def _days(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return (datetime.fromisoformat(iso).date() - date.today()).days
    except ValueError:
        return None


def urgency(days: int) -> str:
    if days < 0:
        return "passed"
    if days <= 7:
        return "critical"
    if days <= 30:
        return "urgent"
    if days <= 90:
        return "soon"
    return "later"


def upcoming(
    within_days: int = 180,
    include_passed: bool = False,
    min_fit: int = 0,
) -> list[dict[str, Any]]:
    """Every known deadline, soonest first.

    `min_fit` exists because aggregator sites give hundreds of unrelated
    conferences the same rolling CFP date; without a floor the radar becomes a
    wall of identical rows and stops being read.
    """
    items: list[dict[str, Any]] = []

    for conf in store.list_conferences(limit=2000):
        if conf.get("status") == "dismissed":
            continue
        watching = conf.get("status") == "watching"
        fit = conf.get("fit_score", 0)
        if not watching and fit < min_fit:
            continue

        for field, label, weight in (
            ("proposal_deadline", "Proposal / hosting bid", 3),
            ("cfp_deadline", "Call for papers", 2),
            ("start_date", "Event starts", 1),
        ):
            iso = conf.get(field)
            d = _days(iso)
            if d is None:
                continue
            if not include_passed and d < 0:
                continue
            if d > within_days:
                continue
            # An event we are not pursuing merely starting is not a deadline.
            if field == "start_date" and not watching and fit < 60:
                continue
            items.append(
                {
                    "date": iso,
                    "days": d,
                    "urgency": urgency(d),
                    "label": label,
                    "weight": weight,
                    "title": conf["title"],
                    "acronym": conf.get("acronym"),
                    "url": conf.get("url"),
                    "fit_score": fit,
                    "status": conf.get("status"),
                    "conference_id": conf["id"],
                    "kind": conf.get("kind"),
                }
            )

    # Outreach follow-ups the committee set themselves.
    for row in store.list_outreach():
        d = _days(row.get("next_action_date"))
        if d is None or (not include_passed and d < 0) or d > within_days:
            continue
        items.append(
            {
                "date": row["next_action_date"],
                "days": d,
                "urgency": urgency(d),
                "label": "Outreach follow-up",
                "weight": 3,
                "title": f"{row.get('title', 'Conference')}: {row.get('next_action') or 'follow up'}",
                "acronym": row.get("acronym"),
                "url": row.get("url"),
                "fit_score": row.get("fit_score", 0),
                "status": row.get("stage"),
                "conference_id": row["conference_id"],
                "kind": "outreach",
            }
        )

    items.sort(key=lambda i: (i["days"], -i["weight"], -i["fit_score"]))
    return items


def critical(within_days: int = 30, min_fit: int = 65) -> list[dict[str, Any]]:
    """Deadlines worth interrupting someone for.

    A hosting-bid deadline or a follow-up we committed to always qualifies.
    Everything else has to clear a high fit bar, because this is what goes out
    as a push notification.
    """
    out = []
    for item in upcoming(within_days, min_fit=0):
        binding = item["label"].startswith(("Proposal", "Outreach"))
        watched = item.get("status") == "watching"
        if binding or watched or item["fit_score"] >= min_fit:
            out.append(item)
    return out


def summary() -> dict[str, int]:
    items = upcoming(365, min_fit=50)
    out = {"critical": 0, "urgent": 0, "soon": 0, "later": 0}
    for i in items:
        if i["urgency"] in out:
            out[i["urgency"]] += 1
    return out
