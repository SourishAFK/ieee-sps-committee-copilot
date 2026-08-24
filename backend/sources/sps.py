"""IEEE Signal Processing Society website collector.

Four feeds matter to a chapter that wants to bring events to campus:

  conference-call-proposals  Call for Organizers. SPS invites volunteers to bid
                             to run a conference. This is the highest-value feed
                             here - it is the literal front door to hosting one.
  attend-an-event            The forward calendar: conferences, workshops,
                             seasonal schools, webinars, chapter events.
  conference-call-papers     Open CFPs, feeds the deadline radar.
  event-archives             ~120 pages of past events. Not tracked as
                             opportunities; harvested as grounding for the
                             advisor so feedback cites real precedent.

The site is Drupal; every listing row is an <article> with an <h4 class=
"node__title"> and a body div carrying "<strong>Label:</strong> value" lines.
"""
from __future__ import annotations

import re
from typing import Any, Iterator

from .base import (
    clean_record,
    fetch,
    guess_acronym,
    is_future,
    make_uid,
    parse_date,
    split_range,
    strip_tags,
)

BASE = "https://signalprocessingsociety.org"
SOURCE = "sps"

# (path, kind, pages_to_walk)
PAGES: list[tuple[str, str, int]] = [
    ("/events/conference-call-proposals", "call_for_organizers", 2),
    ("/events/attend-an-event", "conference", 4),
    ("/events/conference-call-papers", "cfp", 2),
]

ARCHIVE_PATH = "/events/event-archives"

_ARTICLE = re.compile(r"<article\b.*?</article>", re.S | re.I)
_TITLE = re.compile(
    r'<h[234][^>]*class="[^"]*node__title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.S | re.I,
)
_BODY = re.compile(r"<div[^>]*field--name-body[^>]*>(.*?)</div>", re.S | re.I)
_LABEL = re.compile(r"<strong>\s*([^<:]{2,40}?)\s*:?\s*</strong>\s*([^<]*)", re.S | re.I)
_DATE_FIELD = re.compile(
    r'<(?:div|span|time)[^>]*(?:field--name-field-[a-z-]*date|datetime="[^"]*")[^>]*>(.*?)'
    r"</(?:div|span|time)>",
    re.S | re.I,
)
_DATETIME_ATTR = re.compile(r'datetime="(\d{4}-\d{2}-\d{2})', re.I)

DATE_KEYS = ("dates", "date", "conference dates", "when", "event date")
LOC_KEYS = ("location", "where", "venue", "city", "place")
PROP_KEYS = ("proposals due", "proposal due", "proposals deadline", "proposal deadline")
CFP_KEYS = (
    "paper deadline",
    "papers due",
    "submission deadline",
    "paper submission deadline",
    "deadline",
    "abstract deadline",
)


def _labels(fragment: str) -> dict[str, str]:
    """Pull '<strong>Proposals due:</strong> 30 September 2026' pairs."""
    out: dict[str, str] = {}
    for key, val in _LABEL.findall(fragment):
        k = re.sub(r"\s+", " ", key).strip().lower().rstrip(":")
        v = re.sub(r"\s+", " ", val).replace("\xa0", " ").strip(" : ")
        if k and v:
            out[k] = v
    return out


def _first(labels: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if k in labels:
            return labels[k]
    return None


def parse_page(page_html: str, kind: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for block in _ARTICLE.findall(page_html):
        tm = _TITLE.search(block)
        if not tm:
            continue
        href = tm.group(1)
        title = re.sub(r"\s+", " ", strip_tags(tm.group(2))).strip()
        if not title:
            continue

        bm = _BODY.search(block)
        body_frag = bm.group(1) if bm else ""
        labels = _labels(body_frag)
        body_text = strip_tags(body_frag)
        url = href if href.startswith("http") else BASE + href

        start, end = split_range(_first(labels, DATE_KEYS) or "")
        if not start:
            # Drupal often renders the date in a field div or a <time datetime="">
            dm = _DATETIME_ATTR.search(block)
            if dm:
                start = dm.group(1)
            else:
                for frag in _DATE_FIELD.findall(block):
                    start, end = split_range(strip_tags(frag))
                    if start:
                        break

        acronym = _first(labels, ("conference",)) or guess_acronym(title)
        if acronym:
            acronym = acronym.strip()[:20]

        summary = " | ".join(f"{k.title()}: {v}" for k, v in labels.items()) or body_text
        records.append(
            clean_record(
                {
                    "uid": make_uid(SOURCE, url or title),
                    "source": SOURCE,
                    "kind": kind,
                    "title": title,
                    "acronym": acronym,
                    "url": url,
                    "location": _first(labels, LOC_KEYS),
                    "start_date": start,
                    "end_date": end,
                    "cfp_deadline": parse_date(_first(labels, CFP_KEYS)),
                    "proposal_deadline": parse_date(_first(labels, PROP_KEYS)),
                    "society": "IEEE Signal Processing Society",
                    "summary": summary[:800],
                    "topics": ["signal processing"],
                }
            )
        )
    return records


def _walk(path: str, kind: str, pages: int, use_cache: bool) -> Iterator[dict[str, Any]]:
    for page in range(pages):
        url = f"{BASE}{path}" + (f"?page={page}" if page else "")
        try:
            html_text = fetch(url, use_cache=use_cache)
        except Exception:  # noqa: BLE001 - a dead page must not kill the crawl
            break
        rows = parse_page(html_text, kind)
        if not rows:
            break
        yield from rows


_TEXT_LABEL = re.compile(r"^\s*(Dates?|Location|Venue|Deadline[^:]*|Proposals due)\s*:\s*(.+)$", re.I | re.M)
_EXT_LINK = re.compile(r'href="(https?://(?!signalprocessingsociety\.org)[^"]+)"', re.I)


def enrich(rec: dict[str, Any], use_cache: bool = True) -> dict[str, Any]:
    """Fill blanks from an event's own detail page.

    Listing rows often omit dates and location; the detail page states them as
    plain "Dates: August 31 - September 4, 2026" lines. Only called for records
    that are actually missing something, since it costs one request each.
    """
    url = rec.get("url")
    if not url or "signalprocessingsociety.org" not in url:
        return rec
    try:
        page = fetch(url, use_cache=use_cache)
    except Exception:  # noqa: BLE001
        return rec

    body = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    main = re.search(r"<main.*?</main>", body, re.S | re.I)
    seg = main.group(0) if main else body
    text = strip_tags(seg)

    found = {k.strip().lower(): v.strip() for k, v in _TEXT_LABEL.findall(text)}

    if not rec.get("start_date"):
        for key in ("dates", "date"):
            if key in found:
                start, end = split_range(found[key])
                if start:
                    rec["start_date"], rec["end_date"] = start, end
                break
    if not rec.get("location"):
        rec["location"] = found.get("location") or found.get("venue") or rec.get("location")
    if not rec.get("proposal_deadline") and "proposals due" in found:
        rec["proposal_deadline"] = parse_date(found["proposals due"])
    if not rec.get("cfp_deadline"):
        for key, val in found.items():
            if key.startswith("deadline") or "submission" in key:
                rec["cfp_deadline"] = parse_date(val)
                break

    # The conference's own site is the address outreach actually needs.
    if not rec.get("homepage"):
        link = _EXT_LINK.search(seg)
        if link and not any(
            d in link.group(1) for d in ("ieee.org", "twitter", "facebook", "linkedin", "youtube")
        ):
            rec["homepage"] = link.group(1)

    return clean_record(rec)


def collect(use_cache: bool = True, enrich_limit: int = 25) -> list[dict[str, Any]]:
    """Upcoming opportunities worth tracking.

    `enrich_limit` caps how many incomplete records get a detail-page lookup,
    so a crawl stays bounded even if the listing markup changes upstream.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, kind, pages in PAGES:
        for rec in _walk(path, kind, pages, use_cache):
            if rec["uid"] in seen:
                continue
            seen.add(rec["uid"])
            out.append(rec)

    budget = enrich_limit
    for rec in out:
        if budget <= 0:
            break
        if not rec.get("start_date") or not rec.get("location"):
            enrich(rec, use_cache=use_cache)
            budget -= 1

    # Past-dated rows linger on the calendar; drop them only once we have
    # tried to learn the real date.
    return [r for r in out if r["kind"] != "conference" or is_future(r.get("start_date"), 3)]


def collect_archive(max_pages: int = 12, use_cache: bool = True) -> list[dict[str, Any]]:
    """Past SPS events, used as advisor grounding rather than opportunities."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in _walk(ARCHIVE_PATH, "past", max_pages, use_cache):
        if rec["uid"] in seen:
            continue
        seen.add(rec["uid"])
        out.append(rec)
    return out
