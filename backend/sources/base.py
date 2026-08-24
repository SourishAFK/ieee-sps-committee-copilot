"""Shared plumbing for conference sources.

Collectors are deliberately dumb: fetch, parse, hand back plain dicts. Scoring
and persistence happen elsewhere so a broken parser can never corrupt the DB.
"""
from __future__ import annotations

import hashlib
import html
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from ..config import CACHE_DIR

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
TIMEOUT = 30
CACHE_TTL = 6 * 3600  # re-fetch a page at most every 6 hours

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}
MONTHS.update({m[:3].lower(): i for m, i in list(MONTHS.items())})


class SourceError(RuntimeError):
    """Raised when a source cannot be fetched at all."""


def _cache_path(url: str) -> Path:
    return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".html")


def fetch(url: str, use_cache: bool = True) -> str:
    """GET a page, with an on-disk cache and an automatic https->http retry.

    Some conference sites (WikiCFP notably) present a TLS chain that certain
    Windows/Python builds reject; falling back to http keeps the crawl alive
    rather than silently returning nothing.
    """
    cp = _cache_path(url)
    if use_cache and cp.exists() and (time.time() - cp.stat().st_mtime) < CACHE_TTL:
        return cp.read_text(encoding="utf-8", errors="ignore")

    candidates = [url]
    if url.startswith("https://"):
        candidates.append("http://" + url[len("https://"):])

    last: Exception | None = None
    for candidate in candidates:
        try:
            r = requests.get(
                candidate, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True
            )
            r.raise_for_status()
            text = r.text
            cp.write_text(text, encoding="utf-8", errors="ignore")
            return text
        except Exception as exc:  # noqa: BLE001 - any transport failure is retryable
            last = exc
            continue

    if cp.exists():  # stale cache beats nothing
        return cp.read_text(encoding="utf-8", errors="ignore")
    raise SourceError(f"could not fetch {url}: {last}")


def strip_tags(fragment: str) -> str:
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragment, flags=re.S | re.I)
    txt = re.sub(r"<br\s*/?>", "\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    txt = txt.replace("\xa0", " ")
    txt = re.sub(r"[ \t]+", " ", txt)
    return re.sub(r"\n\s*\n+", "\n", txt).strip()


def parse_date(raw: str | None) -> str | None:
    """Parse the handful of date shapes these sites emit into ISO yyyy-mm-dd.

    Handles 'Oct 16, 2026', '30 September 2026', 'Wednesday, 30 September 2026'
    and '2026-10-16'. Returns None when nothing usable is present.
    """
    if not raw:
        return None
    s = html.unescape(raw).replace("\xa0", " ").strip()
    s = re.sub(r"^\s*\w+day\s*,\s*", "", s, flags=re.I)  # drop weekday prefix

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)

    # "Oct 16, 2026" / "October 16 2026"
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s)
    if m and m.group(1).lower() in MONTHS:
        mon, day, yr = MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
        try:
            return date(yr, mon, day).isoformat()
        except ValueError:
            return None

    # "30 September 2026"
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
    if m and m.group(2).lower() in MONTHS:
        day, mon, yr = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
        try:
            return date(yr, mon, day).isoformat()
        except ValueError:
            return None
    return None


def split_range(raw: str) -> tuple[str | None, str | None]:
    """Parse a date range into ISO (start, end).

    'Oct 16, 2026 - Oct 18, 2026'   -> ('2026-10-16', '2026-10-18')
    'August 31 - September 4, 2026' -> ('2026-08-31', '2026-09-04')

    The second shape is common on IEEE event pages: the year is stated once, at
    the end, so an unparseable start borrows the year from the end date.
    """
    if not raw:
        return None, None

    # Compact same-month range: "May 4-9, 2027" / "May 4 - 9, 2027".
    m = re.search(
        r"([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*[-–—]\s*(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})", raw
    )
    if m and m.group(1).lower() in MONTHS:
        mon, d1, d2, yr = MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3)), int(m.group(4))
        try:
            return date(yr, mon, d1).isoformat(), date(yr, mon, d2).isoformat()
        except ValueError:
            pass

    parts = re.split(r"\s+-\s+|\s+to\s+|\s*[–—]\s*", raw, maxsplit=1)
    start = parse_date(parts[0])
    end = parse_date(parts[1]) if len(parts) > 1 else None

    if start is None and end and len(parts) > 1:
        # Retry the start with the year borrowed from the end.
        year = end[:4]
        start = parse_date(f"{parts[0].strip().rstrip(',')} {year}")

    return start, end or start


def make_uid(source: str, key: str) -> str:
    return f"{source}:{hashlib.sha1(key.lower().strip().encode()).hexdigest()[:16]}"


def guess_acronym(title: str) -> str | None:
    """Pull ICASSP out of 'IEEE International Conference ... (ICASSP) 2027'."""
    m = re.search(r"\(([A-Z][A-Za-z0-9\-]{1,12})\)", title)
    if m:
        return m.group(1)
    m = re.match(r"\s*([A-Z]{3,10})\s*[\-–:]?\s*\d{4}", title.strip())
    return m.group(1) if m else None


def guess_country(location: str | None) -> str | None:
    if not location:
        return None
    tail = location.split(",")[-1].strip()
    return tail or None


def is_future(iso: str | None, grace_days: int = 0) -> bool:
    if not iso:
        return True  # unknown dates stay in play rather than vanish
    try:
        d = datetime.fromisoformat(iso).date()
    except ValueError:
        return True
    return (d - date.today()).days >= -grace_days


def clean_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Trim strings and drop empty keys so the DB stays tidy."""
    out: dict[str, Any] = {}
    for k, v in rec.items():
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        if v is None:
            continue
        out[k] = v
    return out
