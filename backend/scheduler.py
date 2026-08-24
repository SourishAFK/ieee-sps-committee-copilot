"""Background jobs: crawl daily, digest weekly, refresh the archive monthly.

Runs in-process with APScheduler so `run.ps1` starts one thing, not three. If
the API is down nothing is crawled - which is the right tradeoff for a tool a
committee runs on a laptop or a small VPS.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import crawler, knowledge, store
from .config import settings
from .notify import digest

log = logging.getLogger("sps.scheduler")

_scheduler: BackgroundScheduler | None = None
_history: list[dict[str, Any]] = []


def _record(job: str, result: Any, error: str | None = None) -> None:
    _history.insert(
        0,
        {
            "job": job,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "result": result,
            "error": error,
        },
    )
    del _history[25:]


def job_crawl() -> dict[str, Any]:
    """Daily: refresh every source, then push anything newly worth knowing."""
    try:
        summary = crawler.run_crawl(use_cache=False)
        alerts = digest.send_alerts()
        out = {"crawl": summary, "alerts": alerts}
        _record("crawl", out)
        log.info("crawl done: %s new, %s alerted", summary["new"], alerts.get("sent", 0))
        return out
    except Exception as exc:  # noqa: BLE001 - a scheduled job must never die
        log.exception("crawl job failed")
        _record("crawl", None, str(exc))
        return {"error": str(exc)}


def job_digest() -> dict[str, Any]:
    try:
        result = digest.send_digest()
        _record("digest", result)
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("digest job failed")
        _record("digest", None, str(exc))
        return {"error": str(exc)}


def job_refresh_knowledge() -> dict[str, Any]:
    try:
        count = knowledge.refresh_past_events(max_pages=12)
        _record("knowledge", {"past_events": count})
        return {"past_events": count}
    except Exception as exc:  # noqa: BLE001
        log.exception("knowledge refresh failed")
        _record("knowledge", None, str(exc))
        return {"error": str(exc)}


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        job_crawl,
        CronTrigger(hour=settings.crawl_hour, minute=0),
        id="crawl",
        replace_existing=True,
        max_instances=1,
    )
    sched.add_job(
        job_digest,
        CronTrigger(day_of_week=settings.digest_day, hour=settings.digest_hour, minute=15),
        id="digest",
        replace_existing=True,
        max_instances=1,
    )
    sched.add_job(
        job_refresh_knowledge,
        CronTrigger(day=1, hour=3, minute=0),
        id="knowledge",
        replace_existing=True,
        max_instances=1,
    )
    sched.start()
    _scheduler = sched
    log.info(
        "scheduler started: crawl %02d:00 UTC daily, digest day %s %02d:15",
        settings.crawl_hour,
        settings.digest_day,
        settings.digest_hour,
    )
    return sched


def shutdown() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def status() -> dict[str, Any]:
    if not _scheduler or not _scheduler.running:
        return {"running": False, "jobs": [], "history": _history[:10]}
    return {
        "running": True,
        "jobs": [
            {
                "id": j.id,
                "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
            }
            for j in _scheduler.get_jobs()
        ],
        "history": _history[:10],
        "last_crawl": store.get_meta("last_crawl", "never"),
    }
