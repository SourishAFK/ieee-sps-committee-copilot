"""The advisor's grounding: what IEEE SPS has actually done before.

Three bodies of knowledge:

  event_formats.json   curated archetypes with real budgets, lead times and the
                       SPS programme that funds each one
  ieee_societies.json  sister societies and the argument that wins each one over
  past_events.json     harvested from the SPS event archive, so feedback can
                       cite genuine precedent instead of inventing it

Retrieval is deliberately a small TF-IDF over titles and summaries. The corpus
is a few hundred short records; a vector database here would be ceremony.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .config import KNOWLEDGE_DIR

PAST_EVENTS_FILE = KNOWLEDGE_DIR / "past_events.json"

_STOP = {
    "the", "a", "an", "and", "or", "of", "for", "on", "in", "to", "with", "at",
    "ieee", "sps", "society", "conference", "workshop", "international", "2024",
    "2025", "2026", "2027", "chapter", "signal", "processing", "th", "st", "nd",
}

_cache: dict[str, Any] = {}


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def societies() -> list[dict[str, Any]]:
    if "societies" not in _cache:
        data = _load_json(KNOWLEDGE_DIR / "ieee_societies.json", {"societies": []})
        _cache["societies"] = data.get("societies", [])
    return _cache["societies"]


def formats() -> list[dict[str, Any]]:
    if "formats" not in _cache:
        data = _load_json(KNOWLEDGE_DIR / "event_formats.json", {"formats": []})
        _cache["formats"] = data.get("formats", [])
    return _cache["formats"]


def get_format(fmt_id: str) -> dict[str, Any] | None:
    return next((f for f in formats() if f["id"] == fmt_id), None)


def past_events() -> list[dict[str, Any]]:
    if "past" not in _cache:
        _cache["past"] = _load_json(PAST_EVENTS_FILE, [])
    return _cache["past"]


def refresh_past_events(max_pages: int = 12) -> int:
    """Re-harvest the SPS archive. Returns the number of events stored."""
    from .sources import sps

    events = sps.collect_archive(max_pages=max_pages)
    slim = [
        {
            "title": e.get("title"),
            "url": e.get("url"),
            "date": e.get("start_date"),
            "location": e.get("location"),
            "summary": (e.get("summary") or "")[:400],
        }
        for e in events
        if e.get("title")
    ]
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    PAST_EVENTS_FILE.write_text(json.dumps(slim, indent=1), encoding="utf-8")
    _cache.pop("past", None)
    _cache.pop("index", None)
    return len(slim)


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------
def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9\-]{2,}", (text or "").lower())
    return [w for w in words if w not in _STOP]


def _index() -> tuple[list[dict[str, Any]], list[Counter], dict[str, float]]:
    """Build (docs, per-doc term counts, idf) once per process."""
    if "index" in _cache:
        return _cache["index"]

    docs = past_events()
    tfs = [Counter(_tokens(f"{d.get('title','')} {d.get('summary','')}")) for d in docs]
    df: Counter = Counter()
    for tf in tfs:
        df.update(tf.keys())
    n = max(1, len(docs))
    idf = {term: math.log(1 + n / (1 + count)) for term, count in df.items()}

    _cache["index"] = (docs, tfs, idf)
    return _cache["index"]


def find_similar(query: str, k: int = 6) -> list[dict[str, Any]]:
    """Past SPS events most like `query`, each with a relevance score."""
    docs, tfs, idf = _index()
    if not docs:
        return []

    q_terms = Counter(_tokens(query))
    if not q_terms:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for doc, tf in zip(docs, tfs):
        if not tf:
            continue
        overlap = q_terms.keys() & tf.keys()
        if not overlap:
            continue
        num = sum(q_terms[t] * tf[t] * (idf.get(t, 1.0) ** 2) for t in overlap)
        norm = math.sqrt(sum(v * v for v in tf.values())) or 1.0
        scored.append((num / norm, doc))

    scored.sort(key=lambda s: -s[0])
    top = scored[:k]
    if not top:
        return []
    best = top[0][0] or 1.0
    return [dict(doc, relevance=round(score / best, 3)) for score, doc in top]


# Words that appear in almost every overlap phrase and so carry no signal about
# which society a pitch belongs to.
_GENERIC = {
    "signal", "signals", "processing", "analysis", "systems", "system", "based",
    "using", "advanced", "computational", "modern", "data", "theory", "design",
    "development", "career", "joint", "applications",
}


def _distinctive(phrase: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", phrase.lower()) if len(w) > 4 and w not in _GENERIC]


def match_societies(text: str, k: int = 3) -> list[dict[str, Any]]:
    """Rank sister societies by topical overlap with a pitch.

    Matches whole overlap phrases first, then falls back to distinctive single
    words. Generic words are excluded, otherwise every pitch that says "signal
    processing" matches every society at once.
    """
    blob = (text or "").lower()
    ranked: list[tuple[float, dict[str, Any]]] = []
    weight = {"high": 1.25, "medium": 1.0, "low": 0.8}

    for soc in societies():
        base = 0.0
        for phrase in soc.get("overlap", []):
            if phrase.lower() in blob:
                base += 3.0
                continue
            tokens = _distinctive(phrase)
            if not tokens:
                continue
            share = sum(1 for w in tokens if w in blob) / len(tokens)
            if share >= 0.6:
                base += 2.0  # near-miss phrasing, e.g. "joint" vs "integrated" sensing
            elif share > 0:
                base += 0.5

        # Society named outright, with or without the "IEEE " prefix.
        short_name = re.sub(r"^ieee\s+", "", soc["name"].lower())
        if soc["abbr"].lower() in blob or short_name in blob:
            base += 4.0

        if base <= 0:
            continue
        ranked.append((base * weight.get(soc.get("strength", "medium"), 1.0), soc))

    ranked.sort(key=lambda r: -r[0])
    return [dict(soc, match_score=round(score, 2)) for score, soc in ranked[:k]]


def resolve_format(explicit: str) -> dict[str, Any] | None:
    """Match a format the user named outright, by id or by name.

    Takes priority over keyword-guessing: if someone says "workshop", a stray
    "contest" in their description must not reclassify the whole pitch.
    """
    if not explicit:
        return None
    want = explicit.strip().lower()
    for fmt in formats():
        if want == fmt["id"] or want == fmt["name"].lower():
            return fmt
    for fmt in formats():
        name = fmt["name"].lower()
        if want in name or name.split(" / ")[0] in want:
            return fmt
    return None


def suggest_format(text: str, audience: int | None = None) -> dict[str, Any] | None:
    """Best-guess format for a free-text pitch, by keyword then by scale."""
    blob = (text or "").lower()
    keyed = [
        ("hackathon", ("hackathon", "datathon", "hack ")),
        ("seasonal_school", ("seasonal school", "summer school", "winter school")),
        ("sp_cup", ("sp cup", "signal processing cup", "vip cup", "competition", "contest")),
        ("webinar", ("webinar", "online talk", "virtual talk")),
        ("distinguished_lecture", ("distinguished lecturer", "distinguished lecture", "dlp")),
        ("paper_bootcamp", ("paper writing", "paper-writing", "reviewing", "publication")),
        ("industry_visit", ("industry visit", "industrial visit", "site visit", "panel")),
        ("conference_host", ("host a conference", "hosting", "bid", "conference on campus")),
        ("symposium", ("symposium", "colloquium")),
        ("chapter_initiative", ("outreach", "rural", "school children", "community")),
        ("workshop", ("workshop", "hands-on", "tutorial", "bootcamp")),
    ]
    for fmt_id, needles in keyed:
        if any(n in blob for n in needles):
            return get_format(fmt_id)

    if audience:
        for fmt_id, ceiling in (("webinar", 80), ("workshop", 150), ("symposium", 400)):
            if audience <= ceiling:
                return get_format(fmt_id)
        return get_format("conference_host")
    return get_format("workshop")


def corpus_stats() -> dict[str, Any]:
    docs = past_events()
    dated = [d for d in docs if d.get("date")]
    return {
        "past_events": len(docs),
        "dated": len(dated),
        "earliest": min((d["date"] for d in dated), default=None),
        "latest": max((d["date"] for d in dated), default=None),
        "formats": len(formats()),
        "societies": len(societies()),
    }
