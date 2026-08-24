"""Telegram push, via the bot HTTP API directly.

No library needed - it is one POST. Keeping it dependency-free means the
notifier cannot break when a bot framework changes its async model.
"""
from __future__ import annotations

from typing import Any

import requests

from ..config import settings

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 20
# Telegram rejects messages over 4096 characters.
MAX_LEN = 4000


def ready() -> bool:
    return settings.notify.telegram_ready


def _post(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = settings.notify.telegram_token
    if not token:
        return {"ok": False, "error": "no TELEGRAM_BOT_TOKEN set"}
    try:
        r = requests.post(API.format(token=token, method=method), json=payload, timeout=TIMEOUT)
        return r.json()
    except Exception as exc:  # noqa: BLE001 - a failed notification must not crash a crawl
        return {"ok": False, "error": str(exc)}


def send(text: str, chat_id: str | None = None, preview: bool = False) -> dict[str, Any]:
    """Send Markdown text, splitting anything too long for one message."""
    target = chat_id or settings.notify.telegram_chat_id
    if not target:
        return {"ok": False, "error": "no TELEGRAM_CHAT_ID set"}

    chunks = _split(text)
    results = []
    for chunk in chunks:
        results.append(
            _post(
                "sendMessage",
                {
                    "chat_id": target,
                    "text": chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": not preview,
                },
            )
        )
    ok = all(r.get("ok") for r in results)
    return {"ok": ok, "messages": len(results), "results": results if not ok else None}


def _split(text: str) -> list[str]:
    if len(text) <= MAX_LEN:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > MAX_LEN:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


def get_updates() -> dict[str, Any]:
    """Read recent updates - the easy way to discover your group's chat id.

    Add the bot to the committee group, post any message, then call this and
    read `chat.id` out of the result.
    """
    return _post("getUpdates", {"limit": 10})


def discover_chat_ids() -> list[dict[str, Any]]:
    data = get_updates()
    if not data.get("ok"):
        return []
    seen: dict[str, dict[str, Any]] = {}
    for update in data.get("result", []):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            seen[str(chat["id"])] = {
                "chat_id": str(chat["id"]),
                "type": chat.get("type"),
                "title": chat.get("title") or chat.get("username") or chat.get("first_name"),
            }
    return list(seen.values())
