"""Composing and dispatching what the committee actually receives.

Two shapes:
  alert   a single high-fit opportunity, pushed the moment it is found
  digest  the weekly roundup: new opportunities, closing deadlines, stalled
          outreach

Both render to Telegram Markdown and to HTML email from one source of truth, so
the two channels can never drift apart.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .. import radar, store
from ..config import settings
from ..scoring import band
from . import mailer, telegram

MAX_ROWS = 8


def _fmt_date(iso: str | None) -> str:
    return iso or "date TBC"


def _conf_line(c: dict[str, Any]) -> str:
    bits = [f"*{c['fit_score']}* - {c['title'][:90]}"]
    where = c.get("location")
    when = c.get("start_date")
    meta = " | ".join(x for x in (when, where) if x)
    if meta:
        bits.append(f"  _{meta}_")
    if c.get("proposal_deadline"):
        bits.append(f"  *Proposal due: {c['proposal_deadline']}*")
    if c.get("url"):
        bits.append(f"  {c['url']}")
    return "\n".join(bits)


# ---------------------------------------------------------------------------
# instant alert
# ---------------------------------------------------------------------------
def alert_text(conferences: list[dict[str, Any]]) -> str:
    head = (
        f"*New hosting opportunity*\n"
        if len(conferences) == 1
        else f"*{len(conferences)} new hosting opportunities*\n"
    )
    body = "\n\n".join(_conf_line(c) for c in conferences[:MAX_ROWS])
    tail = f"\n\n_Flagged for {settings.chapter.name}_"
    return head + "\n" + body + tail


def send_alerts(threshold: int | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Push anything new that clears the alert threshold, once."""
    limit = threshold if threshold is not None else settings.alert_threshold
    pending = store.unalerted_above(limit)
    if not pending:
        return {"sent": 0, "reason": "nothing new above threshold"}

    text = alert_text(pending)
    result: dict[str, Any] = {"count": len(pending), "channels": {}}

    if dry_run:
        result["preview"] = text
        return result

    if telegram.ready():
        result["channels"]["telegram"] = telegram.send(text)
    if mailer.ready():
        result["channels"]["email"] = mailer.send(
            subject=f"[IEEE SPS] {len(pending)} new hosting opportunit"
            + ("y" if len(pending) == 1 else "ies"),
            html_body=_html_alert(pending),
        )

    # Only mark as alerted if at least one channel accepted the message,
    # otherwise a misconfigured bot would silently swallow the opportunity.
    delivered = any(r.get("ok") for r in result["channels"].values())
    if delivered:
        store.mark_alerted([c["id"] for c in pending])
    result["sent"] = len(pending) if delivered else 0
    result["delivered"] = delivered
    if not result["channels"]:
        result["reason"] = "no notification channel configured"
    return result


# ---------------------------------------------------------------------------
# weekly digest
# ---------------------------------------------------------------------------
def build_digest() -> dict[str, Any]:
    """Gather everything the weekly digest reports on."""
    top = [c for c in store.list_conferences(limit=200) if c.get("status") != "dismissed"]
    priority = [c for c in top if c["fit_score"] >= 65][:MAX_ROWS]
    deadlines = radar.critical(within_days=45)[:MAX_ROWS]

    stalled = []
    today = date.today().isoformat()
    for row in store.list_outreach():
        if row.get("stage") in ("committed", "declined", "identified"):
            continue
        if row.get("next_action_date") and row["next_action_date"] <= today:
            stalled.append(row)

    return {
        "generated": today,
        "priority": priority,
        "deadlines": deadlines,
        "stalled": stalled[:MAX_ROWS],
        "stats": store.stats(),
    }


def digest_text(d: dict[str, Any]) -> str:
    lines = [f"*IEEE SPS Committee digest - {d['generated']}*", ""]

    if d["priority"]:
        lines.append("*Top opportunities*")
        for c in d["priority"]:
            lines.append(f"- [{c['fit_score']}/{band(c['fit_score'])}] {c['title'][:80]}")
            if c.get("proposal_deadline"):
                lines.append(f"    proposal due {c['proposal_deadline']}")
        lines.append("")

    if d["deadlines"]:
        lines.append("*Closing soon*")
        for i in d["deadlines"]:
            lines.append(f"- {i['date']} ({i['days']}d) {i['label']}: {i['title'][:60]}")
        lines.append("")

    if d["stalled"]:
        lines.append("*Outreach needing a follow-up*")
        for r in d["stalled"]:
            lines.append(f"- {r.get('title', 'Conference')[:60]} - {r.get('next_action') or 'follow up'}")
        lines.append("")

    s = d["stats"]
    lines.append(
        f"_Tracking {s['conferences']} events | {s['watching']} watched | "
        f"{s['active_outreach']} in outreach | last crawl {s['last_crawl'][:10]}_"
    )
    if not d["priority"] and not d["deadlines"] and not d["stalled"]:
        lines.insert(2, "Nothing needs attention this week.\n")
    return "\n".join(lines)


def _html_alert(conferences: list[dict[str, Any]]) -> str:
    rows = "".join(
        f"<tr><td style='padding:8px;border-bottom:1px solid #eee'>"
        f"<strong>{c['fit_score']}</strong></td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>"
        f"<a href='{c.get('url') or '#'}'>{c['title']}</a><br>"
        f"<small>{_fmt_date(c.get('start_date'))}"
        f"{' &middot; ' + c['location'] if c.get('location') else ''}"
        f"{'<br><b>Proposal due: ' + c['proposal_deadline'] + '</b>' if c.get('proposal_deadline') else ''}"
        f"</small></td></tr>"
        for c in conferences
    )
    return _wrap_html(
        "New hosting opportunities",
        f"<p>{len(conferences)} event(s) scored above your alert threshold.</p>"
        f"<table style='border-collapse:collapse;width:100%'>{rows}</table>",
    )


def digest_html(d: dict[str, Any]) -> str:
    parts: list[str] = []

    if d["priority"]:
        rows = "".join(
            f"<tr><td style='padding:6px;border-bottom:1px solid #eee'><b>{c['fit_score']}</b></td>"
            f"<td style='padding:6px;border-bottom:1px solid #eee'>"
            f"<a href='{c.get('url') or '#'}'>{c['title']}</a><br>"
            f"<small>{_fmt_date(c.get('start_date'))}"
            f"{' &middot; ' + c['location'] if c.get('location') else ''}</small></td></tr>"
            for c in d["priority"]
        )
        parts.append(f"<h3>Top opportunities</h3><table style='border-collapse:collapse;width:100%'>{rows}</table>")

    if d["deadlines"]:
        items = "".join(
            f"<li><b>{i['date']}</b> ({i['days']} days) - {i['label']}: {i['title']}</li>"
            for i in d["deadlines"]
        )
        parts.append(f"<h3>Closing soon</h3><ul>{items}</ul>")

    if d["stalled"]:
        items = "".join(
            f"<li>{r.get('title', 'Conference')} - {r.get('next_action') or 'follow up'}</li>"
            for r in d["stalled"]
        )
        parts.append(f"<h3>Outreach needing a follow-up</h3><ul>{items}</ul>")

    if not parts:
        parts.append("<p>Nothing needs attention this week.</p>")

    s = d["stats"]
    parts.append(
        f"<hr><p style='color:#777;font-size:12px'>Tracking {s['conferences']} events &middot; "
        f"{s['watching']} watched &middot; {s['active_outreach']} in outreach &middot; "
        f"last crawl {s['last_crawl'][:10]}</p>"
    )
    return _wrap_html(f"Committee digest - {d['generated']}", "".join(parts))


def _wrap_html(title: str, body: str) -> str:
    return (
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:640px;margin:0 auto;color:#1a1a1a\">"
        f"<h2 style='color:#00629B'>{title}</h2>{body}"
        f"<p style='color:#777;font-size:12px'>{settings.chapter.name} &middot; "
        f"{settings.chapter.institution}</p></div>"
    )


def send_digest(dry_run: bool = False) -> dict[str, Any]:
    d = build_digest()
    text = digest_text(d)
    if dry_run:
        return {"preview": text, "html": digest_html(d), "data": d}

    result: dict[str, Any] = {"channels": {}}
    if telegram.ready():
        result["channels"]["telegram"] = telegram.send(text)
    if mailer.ready():
        result["channels"]["email"] = mailer.send(
            subject=f"[IEEE SPS] Committee digest - {d['generated']}",
            html_body=digest_html(d),
            text_body=text,
        )
    if not result["channels"]:
        result["reason"] = "no notification channel configured"
    result["ok"] = any(r.get("ok") for r in result["channels"].values())
    return result


def channel_status() -> dict[str, Any]:
    return {
        "telegram": {"configured": telegram.ready()},
        "email": {
            "configured": mailer.ready(),
            "recipients": len(settings.notify.digest_to),
        },
    }
