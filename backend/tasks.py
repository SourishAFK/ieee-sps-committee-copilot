"""Command-line jobs, for running the schedule outside the web process.

A hosted UI on a free tier sleeps when nobody is looking at it, so an
in-process scheduler quietly stops firing. These commands are run by CI on a
cron instead, writing to the same shared database the UI reads:

    python -m backend.tasks crawl      # refresh sources, push new opportunities
    python -m backend.tasks digest     # send the weekly roundup
    python -m backend.tasks knowledge  # re-harvest the SPS event archive
    python -m backend.tasks status     # print what is in the database

Exit code is non-zero only when the job genuinely failed, so a red CI run means
something needs attention rather than "no new conferences today".
"""
from __future__ import annotations

import json
import sys
from typing import Any

from . import crawler, knowledge, store
from .notify import digest


def _print(label: str, payload: Any) -> None:
    print(f"--- {label} ---")
    print(json.dumps(payload, indent=2, default=str))


def cmd_crawl() -> int:
    store.init()
    summary = crawler.run_crawl(use_cache=False)
    _print("crawl", summary)

    alerts = digest.send_alerts()
    _print("alerts", alerts)

    print(
        f"\n{summary['new']} new, {summary['updated']} updated, "
        f"{alerts.get('sent', 0)} alerted in {summary['seconds']}s"
    )
    # Source failures are worth a red build; finding nothing new is not.
    return 1 if summary["errors"] else 0


def cmd_digest() -> int:
    store.init()
    result = digest.send_digest()
    _print("digest", result)
    if result.get("reason") == "no notification channel configured":
        print("No Telegram or SMTP credentials set - nothing to send.")
        return 0
    return 0 if result.get("ok") else 1


def cmd_knowledge() -> int:
    store.init()
    count = knowledge.refresh_past_events(max_pages=12)
    print(f"stored {count} past events")
    return 0 if count else 1


def cmd_status() -> int:
    store.init()
    db = store.backend_info()
    _print("database", db)
    if not db["persistent"]:
        print(
            "\nWARNING: running against a local SQLite file, not the shared database.\n"
            "         DATABASE_URL is unset or misspelled, so this job is writing\n"
            "         somewhere the app will never read. Check the repository secret.\n"
        )
    _print("stats", store.stats())
    _print("knowledge", knowledge.corpus_stats())
    _print("channels", digest.channel_status())
    top = store.list_conferences(min_score=65, limit=10)
    print(f"\n--- {len(top)} opportunities at fit >= 65 ---")
    for c in top:
        print(f"  {c['fit_score']:3d}  {c['title'][:70]}")
    return 0


COMMANDS = {
    "crawl": cmd_crawl,
    "digest": cmd_digest,
    "knowledge": cmd_knowledge,
    "status": cmd_status,
}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in COMMANDS:
        print(f"usage: python -m backend.tasks [{' | '.join(COMMANDS)}]")
        return 2
    try:
        return COMMANDS[args[0]]()
    except Exception as exc:  # noqa: BLE001 - report clearly, fail the CI run
        print(f"job '{args[0]}' failed: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
