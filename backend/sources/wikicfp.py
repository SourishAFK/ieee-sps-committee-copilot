"""WikiCFP collector.

Widens the net past the SPS site: WikiCFP aggregates calls for papers across
the whole field, which is where you spot an IEEE conference in your region
that SPS itself has not listed, plus the adjacent-society events that make
good co-hosting targets.

Listing markup is a flat table of alternating row pairs:
    row A: [acronym, full title, ''] and a link to eventid
    row B: [dates, location, submission deadline]
Journals are interleaved with conferences and are filtered out - they have no
usable date range.
"""
from __future__ import annotations

import re
from typing import Any

from ..config import settings
from .base import (
    clean_record,
    fetch,
    guess_country,
    is_future,
    make_uid,
    parse_date,
    split_range,
    strip_tags,
)

BASE = "http://www.wikicfp.com"
SOURCE = "wikicfp"

# Topic browse and free-text search are different endpoints. `conference=` only
# accepts a topic category, so a query like "india" silently returns nothing -
# regional hunting has to go through tool.search.
BROWSE_URL = BASE + "/cfp/call?conference={q}"
SEARCH_URL = BASE + "/cfp/servlet/tool.search?q={q}&year=f"

# Search terms chosen to cover the SPS technical scope plus the neighbouring
# societies a chapter most often co-hosts with.
TERMS: list[str] = [
    "signal processing",
    "image processing",
    "speech processing",
    "machine learning",
    "computer vision",
    "wireless communications",
    "biomedical imaging",
]

_ROW = re.compile(r"<tr[^>]*>.*?</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_EVENT_LINK = re.compile(r'href="(/cfp/servlet/event\.showcfp\?eventid=\d+)', re.I)

# WikiCFP writes "N/A" and "TBD" where a value is missing.
_NULLISH = {"", "n/a", "na", "tbd", "tba", "-"}


def _cells(row: str) -> list[str]:
    return [re.sub(r"\s+", " ", strip_tags(c)).strip() for c in _CELL.findall(row)]


def _clean(value: str | None) -> str | None:
    if not value or value.strip().lower() in _NULLISH:
        return None
    return value.strip()


def parse_page(page_html: str, term: str) -> list[dict[str, Any]]:
    rows = _ROW.findall(page_html)
    out: list[dict[str, Any]] = []

    for i, row in enumerate(rows):
        link = _EVENT_LINK.search(row)
        if not link:
            continue
        head = _cells(row)
        if len(head) < 2:
            continue
        acronym, full_title = _clean(head[0]), _clean(head[1])
        if not full_title:
            continue

        # The detail row is the next <tr>; without it this is not a conference.
        if i + 1 >= len(rows):
            continue
        detail = _cells(rows[i + 1])
        if len(detail) < 3:
            continue

        when, where, deadline = (_clean(detail[0]), _clean(detail[1]), _clean(detail[2]))
        start, end = split_range(when or "")
        if not start:
            continue  # journals and open-ended calls: not venues we can host

        location = where
        url = BASE + link.group(1)
        is_ieee = bool(re.search(r"\bIEEE\b", full_title, re.I))

        out.append(
            clean_record(
                {
                    "uid": make_uid(SOURCE, url),
                    "source": SOURCE,
                    "kind": "cfp",
                    "title": full_title,
                    "acronym": acronym,
                    "url": url,
                    "location": location,
                    "country": guess_country(location),
                    "start_date": start,
                    "end_date": end,
                    "cfp_deadline": parse_date(deadline),
                    "society": "IEEE" if is_ieee else None,
                    "topics": [term],
                    "summary": f"{full_title} | {when or 'dates TBD'} | {location or 'venue TBD'}",
                }
            )
        )
    return out


def _quote(text: str) -> str:
    return text.strip().replace(" ", "%20")


def regional_queries() -> list[str]:
    """Free-text searches derived from where our chapter actually is.

    A conference already being held in our country is the strongest hosting
    argument there is, so these matter more than the topic browse - but they
    are invisible to the topic endpoint and have to be searched for by name.
    """
    chapter = settings.chapter
    country, city = chapter.country.strip(), chapter.city.strip()
    queries = []
    if country:
        queries += [country, f"signal processing {country}", f"IEEE {country}"]
    if city and city.lower() not in ("your city", ""):
        queries.append(city)
    return [q for q in queries if q]


def _harvest(url: str, label: str, use_cache: bool, merged: dict[str, dict[str, Any]]) -> None:
    try:
        page = fetch(url, use_cache=use_cache)
    except Exception:  # noqa: BLE001 - skip a query that will not load
        return
    for rec in parse_page(page, label):
        if not is_future(rec.get("start_date"), grace_days=3):
            continue
        prior = merged.get(rec["uid"])
        if prior:
            # Same event found under two queries: keep both topic labels.
            prior["topics"] = list(dict.fromkeys(list(prior["topics"]) + list(rec["topics"])))
        else:
            merged[rec["uid"]] = rec


def collect(
    use_cache: bool = True,
    terms: list[str] | None = None,
    regional: bool = True,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for term in terms or TERMS:
        _harvest(BROWSE_URL.format(q=_quote(term)), term, use_cache, merged)

    if regional and terms is None:
        for query in regional_queries():
            _harvest(SEARCH_URL.format(q=_quote(query)), query, use_cache, merged)

    return list(merged.values())
