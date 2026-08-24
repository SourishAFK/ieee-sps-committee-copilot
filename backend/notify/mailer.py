"""SMTP email sending for the weekly digest.

Named `mailer` rather than `email` so it cannot shadow the standard library's
`email` package, which it imports.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

from ..config import settings


def ready() -> bool:
    return settings.notify.email_ready


def send(
    subject: str,
    html_body: str,
    text_body: str | None = None,
    to: list[str] | None = None,
) -> dict[str, Any]:
    cfg = settings.notify
    recipients = to or cfg.digest_to
    if not recipients:
        return {"ok": False, "error": "no DIGEST_TO recipients configured"}
    if not (cfg.smtp_user and cfg.smtp_password):
        return {"ok": False, "error": "no SMTP credentials configured"}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_user
    msg["To"] = ", ".join(recipients)
    msg.set_content(text_body or _strip_html(html_body))
    msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
            server.starttls()
            server.login(cfg.smtp_user, cfg.smtp_password)
            server.send_message(msg)
        return {"ok": True, "recipients": len(recipients)}
    except Exception as exc:  # noqa: BLE001 - report, never raise into a scheduled job
        return {"ok": False, "error": str(exc)}


def _strip_html(html: str) -> str:
    import re

    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</tr>|</h[1-6]>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
