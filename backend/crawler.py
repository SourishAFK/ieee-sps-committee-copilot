"""Crawl orchestration: collect, score, store, decide what deserves an alert."""
from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from . import scoring, store
from .sources import sps, wikicfp

Collector = Callable[..., list[dict[str, Any]]]

SOURCES: dict[str, Collector] = {
    "sps": sps.collect,
    "wikicfp": wikicfp.collect,
}


def run_crawl(use_cache: bool = False, only: list[str] | None = None) -> dict[str, Any]:
    """Fetch every source, rescore everything, persist. Returns a summary.

    A failing source is reported but never aborts the run - the SPS feed is the
    important one and must survive WikiCFP being down, and vice versa.
    """
    started = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "started": started.isoformat(timespec="seconds"),
        "sources": {},
        "new": 0,
        "updated": 0,
        "errors": [],
    }

    for name, collect in SOURCES.items():
        if only and name not in only:
            continue
        try:
            records = collect(use_cache=use_cache)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"{name}: {exc}")
            summary["sources"][name] = {"collected": 0, "error": str(exc)}
            traceback.print_exc()
            continue

        new = updated = 0
        for rec in records:
            try:
                scoring.apply(rec)
                _, is_new = store.upsert_conference(rec)
                new += is_new
                updated += not is_new
            except Exception as exc:  # noqa: BLE001 - one bad row is not fatal
                summary["errors"].append(f"{name} record: {exc}")

        summary["sources"][name] = {"collected": len(records), "new": new, "updated": updated}
        summary["new"] += new
        summary["updated"] += updated

    finished = datetime.now(timezone.utc)
    summary["finished"] = finished.isoformat(timespec="seconds")
    summary["seconds"] = round((finished - started).total_seconds(), 1)
    store.set_meta("last_crawl", summary["finished"])
    store.set_meta("last_crawl_summary", str(summary["sources"]))
    return summary


def rescore_all() -> int:
    """Re-apply scoring to everything already stored.

    Needed after the chapter profile changes - moving city or venue capacity
    changes proximity and scale for every record.
    """
    rows = store.list_conferences(limit=5000)
    for row in rows:
        scoring.apply(row)
        store.upsert_conference(
            {
                "uid": row["uid"],
                "source": row["source"],
                "title": row["title"],
                "fit_score": row["fit_score"],
                "fit_reasons": row["fit_reasons"],
            }
        )
    return len(rows)


def pending_alerts(threshold: int | None = None) -> list[dict[str, Any]]:
    from .config import settings

    return store.unalerted_above(threshold if threshold is not None else settings.alert_threshold)
