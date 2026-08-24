"""Fit scoring: how worth pursuing is this event for our chapter?

The score answers one question - "should we contact these organisers about
bringing this to our campus?" - and every point is explained in `reasons`,
because the chair has to justify the shortlist to a committee and to faculty.

Five weighted components, 100 points total, plus a quality penalty:

    opportunity  35   is there an actual opening to host or co-host
    topic        25   is it really signal processing / SPS scope
    proximity    20   can our people and our Section realistically reach it
    lead_time    12   is there enough runway left to bid and organise
    scale         8   can our venue physically hold it
    quality     -25   penalty for predatory / aggregator-spam listings

Opportunity outweighs topic on purpose. A perfectly on-topic conference that
has already chosen its venue is not actionable; an open Call for Organizers is,
even if its theme is only adjacent. The shortlist has to be ranked by what we
can actually act on.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .config import settings

# --- topic vocabulary -------------------------------------------------------
CORE_SP = {
    "signal processing": 12, "speech": 10, "audio": 9, "acoustic": 9,
    "image processing": 10, "video processing": 9, "image": 6, "vision": 6,
    "radar": 8, "sonar": 8, "biomedical imaging": 9, "medical imaging": 8,
    "compression": 7, "coding": 5, "multimedia": 6, "sensor": 6,
    "machine learning": 6, "deep learning": 6, "artificial intelligence": 5,
    "pattern recognition": 6, "information forensics": 8, "biometrics": 7,
    "wireless": 5, "communications": 4, "remote sensing": 7, "geoscience": 5,
    "spectral": 6, "filtering": 6, "estimation": 5, "array processing": 8,
}
OFF_TOPIC = {
    "operations research", "software engineering", "civil engineering",
    "management", "tourism", "accounting", "linguistics", "education policy",
    "bioinformatics journal", "supply chain",
}

# --- geography --------------------------------------------------------------
ASIA_PACIFIC = {
    "india", "singapore", "malaysia", "indonesia", "thailand", "vietnam",
    "philippines", "china", "japan", "south korea", "korea", "taiwan",
    "hong kong", "australia", "new zealand", "bangladesh", "sri lanka",
    "nepal", "pakistan", "uae", "united arab emirates", "qatar",
}

# Events a student-branch chapter can realistically host, in ascending order
# of how much infrastructure they demand.
SMALL_FORMATS = ("webinar", "seasonal school", "workshop", "symposium", "school", "summer school")
MEGA_CONFERENCES = {"ICASSP", "ICIP", "ICC", "GLOBECOM", "CVPR", "INTERSPEECH", "NeurIPS"}


def _text(rec: dict[str, Any]) -> str:
    parts = [
        str(rec.get("title") or ""),
        str(rec.get("summary") or ""),
        " ".join(rec.get("topics") or []),
    ]
    return " ".join(parts).lower()


def _days_out(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return (datetime.fromisoformat(iso).date() - date.today()).days
    except ValueError:
        return None


def _topic_hits(blob: str) -> list[tuple[str, int]]:
    """Matched topic terms with substring duplicates removed.

    Without this, "image processing" also fires "image", and a single subject
    is counted two or three times - which is how keyword-stuffed listings used
    to outrank genuine SPS opportunities.
    """
    matched = [(k, w) for k, w in CORE_SP.items() if k in blob]
    matched.sort(key=lambda kv: -len(kv[0]))
    kept: list[tuple[str, int]] = []
    for term, weight in matched:
        if any(term in longer for longer, _ in kept):
            continue
        kept.append((term, weight))
    kept.sort(key=lambda kv: -kv[1])
    return kept


def _score_topic(rec: dict[str, Any], reasons: list[str]) -> int:
    blob = _text(rec)
    hits = _topic_hits(blob)
    if not hits:
        reasons.append("No clear signal-processing topic match")
        return 3
    score = min(25, sum(w for _, w in hits[:3]))
    if any(bad in blob for bad in OFF_TOPIC):
        score = max(0, score - 10)
        reasons.append("Contains off-scope subject matter")
    reasons.append("Topic match: " + ", ".join(k for k, _ in hits[:3]))
    return score


def _score_opportunity(rec: dict[str, Any], reasons: list[str]) -> int:
    kind = rec.get("kind") or "conference"
    blob = _text(rec)

    if kind == "call_for_organizers":
        reasons.append("OPEN CALL FOR ORGANIZERS - IEEE is actively seeking a host institution")
        return 35
    if rec.get("proposal_deadline"):
        reasons.append("Open proposal deadline - a bid can still be submitted")
        return 28
    if any(w in blob for w in ("call for proposals", "seeking host", "host institution", "bids")):
        reasons.append("Listing mentions hosting or proposals")
        return 22
    if kind == "cfp":
        reasons.append("Open call for papers - approach for a satellite or local edition")
        return 12
    reasons.append("Listed event - approach organisers directly")
    return 9


def _score_proximity(rec: dict[str, Any], reasons: list[str]) -> int:
    chapter = settings.chapter
    loc = f"{rec.get('location') or ''} {rec.get('country') or ''}".lower()
    if not loc.strip():
        reasons.append("Location unknown")
        return 8  # unknown is not a reason to discard
    if "virtual" in loc or "online" in loc:
        reasons.append("Virtual event - easy to co-host remotely")
        return 12
    if chapter.country.lower() in loc:
        reasons.append(f"Already held in {chapter.country} - strong hosting case")
        return 20
    if chapter.city.lower() and chapter.city.lower() in loc:
        reasons.append("In our city")
        return 20
    if any(c in loc for c in ASIA_PACIFIC):
        reasons.append("Within IEEE Region 10 (Asia-Pacific)")
        return 14
    reasons.append("Outside our region - harder to win a bid")
    return 4


def _score_lead_time(rec: dict[str, Any], reasons: list[str]) -> int:
    """Enough runway to bid, get approvals and organise - but not so far out
    that nobody is deciding yet."""
    deadline = rec.get("proposal_deadline") or rec.get("cfp_deadline")
    d_days = _days_out(deadline)
    if d_days is not None:
        if d_days < 0:
            reasons.append("Deadline has passed")
            return 0
        if d_days <= 21:
            reasons.append(f"Deadline in {d_days} days - act now")
            return 12
        if d_days <= 90:
            reasons.append(f"Deadline in {d_days} days")
            return 10

    days = _days_out(rec.get("start_date"))
    if days is None:
        reasons.append("No date announced yet - early enough to influence")
        return 7
    if days < 60:
        reasons.append("Too soon to bid for hosting")
        return 1
    if 180 <= days <= 900:
        reasons.append("Good runway to prepare a hosting bid")
        return 12
    if days < 180:
        reasons.append("Tight but feasible timeline")
        return 6
    reasons.append("Very far out - track, revisit later")
    return 5


def _score_scale(rec: dict[str, Any], reasons: list[str]) -> int:
    blob = _text(rec)
    acronym = (rec.get("acronym") or "").upper()
    if acronym in MEGA_CONFERENCES:
        reasons.append(f"{acronym} is a flagship conference - beyond a single campus venue")
        return 1
    if any(f in blob for f in SMALL_FORMATS):
        reasons.append("Workshop/school format - a good fit for a campus venue")
        return 8
    if settings.chapter.venue_capacity >= 800:
        reasons.append("Large venue available")
        return 6
    reasons.append("Standard conference scale")
    return 5


# Markers of aggregator spam and predatory venues. These listings flood WikiCFP
# and are exactly what a chapter must not put its name on.
SPAM_PATTERNS = [
    (re.compile(r"^\s*[A-Z]{2,6}\s*--", re.I), 12, "Aggregator-style '--' title prefix"),
    (re.compile(r"international journal", re.I), 25, "This is a journal, not a conference"),
    (re.compile(r"\b(?:submit|publication).{0,20}\b(?:scopus|ei compendex)\b", re.I), 15,
     "Indexing-led marketing copy"),
]
# Subject words that, combined with a signal-processing keyword, indicate one of
# those catch-all conferences that claims to cover everything.
CATCH_ALL = ("materials science", "operations research", "tourism", "supply chain",
             "civil engineering", "accounting")


def _score_quality(rec: dict[str, Any], reasons: list[str]) -> int:
    """Negative points. Returns a penalty in the range -25..0."""
    title = str(rec.get("title") or "")
    blob = _text(rec)
    penalty = 0

    for pattern, cost, why in SPAM_PATTERNS:
        if pattern.search(title):
            penalty += cost
            reasons.append(f"Quality flag: {why}")

    hits = [c for c in CATCH_ALL if c in blob]
    if hits:
        penalty += 12
        reasons.append(f"Quality flag: catch-all scope ({hits[0]})")

    if rec.get("society") and "ieee" in str(rec["society"]).lower():
        penalty = max(0, penalty - 8)  # IEEE affiliation is real reassurance

    return -min(25, penalty)


def score(rec: dict[str, Any]) -> tuple[int, list[str]]:
    """Return (0-100 fit score, human-readable reasons)."""
    reasons: list[str] = []
    total = (
        _score_opportunity(rec, reasons)
        + _score_topic(rec, reasons)
        + _score_proximity(rec, reasons)
        + _score_lead_time(rec, reasons)
        + _score_scale(rec, reasons)
        + _score_quality(rec, reasons)
    )
    return max(0, min(100, total)), reasons


def apply(rec: dict[str, Any]) -> dict[str, Any]:
    """Attach fit_score/fit_reasons to a record in place."""
    s, reasons = score(rec)
    rec["fit_score"] = s
    rec["fit_reasons"] = reasons
    return rec


def band(score_value: int) -> str:
    if score_value >= 75:
        return "priority"
    if score_value >= 55:
        return "promising"
    if score_value >= 35:
        return "watch"
    return "low"
