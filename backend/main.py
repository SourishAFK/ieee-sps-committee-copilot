"""FastAPI backend for the IEEE SPS Committee Copilot."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import (
    advisor,
    crawler,
    knowledge,
    llm,
    outreach,
    playbook,
    radar,
    reporter,
    scheduler,
    scoring,
    store,
)
from .config import settings
from .notify import digest, telegram
from .scoring import band
from .sources.base import guess_acronym, make_uid


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    if not knowledge.past_events():
        # First boot: fill the advisor's grounding corpus in the background.
        try:
            knowledge.refresh_past_events(max_pages=6)
        except Exception:  # noqa: BLE001 - the app is still usable without it
            pass
    if settings.enable_scheduler:
        scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(
    title="IEEE SPS Committee Copilot",
    description="Conference tracking, event advice, outreach and reporting for an IEEE SPS chapter.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------
class PitchIn(BaseModel):
    title: str = ""
    idea: str = Field(..., min_length=1)
    format: str = ""
    audience: str = ""
    co_society: str = ""


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1)
    history: list[dict[str, str]] = []


class StageIn(BaseModel):
    stage: str
    note: str | None = None


class ContactIn(BaseModel):
    contact_name: str | None = None
    contact_email: str | None = None
    contact_role: str | None = None
    notes: str | None = None
    next_action: str | None = None
    next_action_date: str | None = None


class EventIn(BaseModel):
    title: str
    event_date: str
    format: str = ""
    speakers: str = ""
    co_society: str = ""
    attendance: int = 0
    volunteers: int = 0
    budget_spent: float = 0
    outcomes: str = ""
    pitch_id: int | None = None


class ConferenceIn(BaseModel):
    """A conference the committee heard about outside the crawled sources."""

    title: str = Field(..., min_length=3)
    acronym: str = ""
    url: str = ""
    homepage: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    cfp_deadline: str = ""
    proposal_deadline: str = ""
    society: str = ""
    summary: str = ""
    kind: str = "conference"
    topics: list[str] = []


class PlaybookIn(BaseModel):
    pitch_id: int | None = None
    title: str = ""
    idea: str = ""
    format: str = ""
    audience: str = ""
    event_date: str | None = None
    budget_total: int | None = None


# ---------------------------------------------------------------------------
# system
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "chapter": settings.chapter.name,
        "institution": settings.chapter.institution,
        "llm": llm.status(),
        "notify": digest.channel_status(),
        "stats": store.stats(),
        "knowledge": knowledge.corpus_stats(),
        "scheduler": scheduler.status(),
    }


@app.get("/config")
def get_config() -> dict[str, Any]:
    c = settings.chapter
    return {
        "chapter": {
            "name": c.name,
            "institution": c.institution,
            "city": c.city,
            "country": c.country,
            "region": c.region,
            "section": c.section,
            "chair_name": c.chair_name,
            "chair_email": c.chair_email,
            "venue_capacity": c.venue_capacity,
        },
        "alert_threshold": settings.alert_threshold,
        "llm": llm.status(),
    }


# ---------------------------------------------------------------------------
# conferences
# ---------------------------------------------------------------------------
@app.get("/conferences")
def list_conferences(
    status: str | None = None,
    min_score: int = 0,
    kind: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    rows = store.list_conferences(status=status, min_score=min_score, kind=kind, limit=limit)
    for r in rows:
        r["band"] = band(r["fit_score"])
    return {"count": len(rows), "conferences": rows}


@app.get("/conferences/{cid}")
def get_conference(cid: int) -> dict[str, Any]:
    conf = store.get_conference(cid)
    if not conf:
        raise HTTPException(404, "conference not found")
    conf["band"] = band(conf["fit_score"])
    conf["outreach"] = store.get_outreach(cid)
    return conf


@app.post("/conferences")
def add_conference(payload: ConferenceIn) -> dict[str, Any]:
    """Add a conference by hand.

    Automated discovery of regional IEEE conferences is patchy - IEEE's own
    conference search blocks scripted access - so most India-based leads will
    reach the committee through a Section mailing list or a colleague first.
    Once added they are scored, tracked and worked exactly like crawled ones.
    """
    data = payload.model_dump()
    rec = {k: v for k, v in data.items() if v not in ("", [], None)}
    rec["source"] = "manual"
    rec["uid"] = make_uid("manual", payload.title)
    rec.setdefault("acronym", guess_acronym(payload.title))
    rec.setdefault("topics", ["signal processing"])
    rec.setdefault("kind", "conference")

    scoring.apply(rec)
    cid, is_new = store.upsert_conference(rec)
    saved = store.get_conference(cid) or {}
    saved["band"] = band(saved.get("fit_score", 0))
    saved["created"] = is_new
    return saved


@app.post("/conferences/{cid}/status")
def set_status(cid: int, payload: dict[str, str]) -> dict[str, Any]:
    status = payload.get("status", "")
    if status not in ("new", "watching", "dismissed"):
        raise HTTPException(400, "status must be new, watching or dismissed")
    if not store.get_conference(cid):
        raise HTTPException(404, "conference not found")
    store.set_conference_status(cid, status)
    return {"ok": True, "id": cid, "status": status}


@app.post("/crawl")
def run_crawl(use_cache: bool = False) -> dict[str, Any]:
    return crawler.run_crawl(use_cache=use_cache)


@app.post("/rescore")
def rescore() -> dict[str, Any]:
    return {"rescored": crawler.rescore_all()}


# ---------------------------------------------------------------------------
# radar
# ---------------------------------------------------------------------------
@app.get("/radar")
def get_radar(days: int = 180, min_fit: int = 0, critical_only: bool = False) -> dict[str, Any]:
    items = radar.critical(days) if critical_only else radar.upcoming(days, min_fit=min_fit)
    return {"count": len(items), "summary": radar.summary(), "items": items}


# ---------------------------------------------------------------------------
# advisor
# ---------------------------------------------------------------------------
@app.post("/advisor/evaluate")
def evaluate_pitch(pitch: PitchIn, save: bool = True) -> dict[str, Any]:
    data = pitch.model_dump()
    result = advisor.evaluate(data)
    if save:
        result["pitch_id"] = store.save_pitch(
            {
                **data,
                "verdict": result["verdict"],
                "score": result["score"],
                "feedback": result,
            }
        )
    return result


@app.post("/advisor/chat")
def chat(payload: ChatIn) -> dict[str, Any]:
    reply = advisor.discuss(payload.message, payload.history)
    return {"reply": reply, "llm": llm.available()}


@app.get("/pitches")
def list_pitches(status: str | None = None) -> dict[str, Any]:
    return {"pitches": store.list_pitches(status)}


@app.get("/pitches/{pid}")
def get_pitch(pid: int) -> dict[str, Any]:
    p = store.get_pitch(pid)
    if not p:
        raise HTTPException(404, "pitch not found")
    return p


@app.post("/pitches/{pid}/status")
def set_pitch_status(pid: int, payload: dict[str, str]) -> dict[str, Any]:
    allowed = ("draft", "approved", "scheduled", "done", "shelved")
    status = payload.get("status", "")
    if status not in allowed:
        raise HTTPException(400, f"status must be one of {allowed}")
    if not store.get_pitch(pid):
        raise HTTPException(404, "pitch not found")
    store.update_pitch(pid, status=status)
    return {"ok": True, "id": pid, "status": status}


# ---------------------------------------------------------------------------
# playbook
# ---------------------------------------------------------------------------
@app.post("/playbook")
def make_playbook(payload: PlaybookIn) -> dict[str, Any]:
    pitch: dict[str, Any] = payload.model_dump()
    if payload.pitch_id:
        stored = store.get_pitch(payload.pitch_id)
        if not stored:
            raise HTTPException(404, "pitch not found")
        pitch = {**stored, **{k: v for k, v in pitch.items() if v}}

    if not (pitch.get("idea") or pitch.get("title")):
        raise HTTPException(400, "need at least a title or an idea")

    plan = playbook.generate(
        pitch, event_date=payload.event_date, budget_total=payload.budget_total
    )
    plan["markdown"] = playbook.to_markdown(plan)
    if payload.pitch_id:
        store.update_pitch(payload.pitch_id, playbook=plan, status="approved")
    return plan


# ---------------------------------------------------------------------------
# outreach
# ---------------------------------------------------------------------------
@app.get("/outreach")
def get_pipeline() -> dict[str, Any]:
    return {
        "stages": [{"id": s, "label": d} for s, d in outreach.STAGES],
        "pipeline": outreach.pipeline(),
        "funnel": outreach.funnel_stats(),
    }


@app.post("/outreach/{cid}/draft")
def make_draft(cid: int, payload: ContactIn | None = None) -> dict[str, Any]:
    if not store.get_conference(cid):
        raise HTTPException(404, "conference not found")
    name = payload.contact_name if payload else None
    return outreach.draft_email(cid, contact_name=name)


@app.post("/outreach/{cid}/stage")
def set_stage(cid: int, payload: StageIn) -> dict[str, Any]:
    if not store.get_conference(cid):
        raise HTTPException(404, "conference not found")
    try:
        return outreach.advance(cid, payload.stage, payload.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/outreach/{cid}/contact")
def set_contact(cid: int, payload: ContactIn) -> dict[str, Any]:
    if not store.get_conference(cid):
        raise HTTPException(404, "conference not found")
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    store.upsert_outreach(cid, **fields)
    return store.get_outreach(cid) or {}


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
@app.get("/events")
def list_events(year: int | None = None) -> dict[str, Any]:
    return {"events": store.list_events(year), "years": reporter.available_years()}


@app.post("/events")
def add_event(payload: EventIn) -> dict[str, Any]:
    eid = store.save_event(payload.model_dump())
    return {"ok": True, "id": eid}


@app.delete("/events/{eid}")
def remove_event(eid: int) -> dict[str, Any]:
    store.delete_event(eid)
    return {"ok": True}


@app.get("/report/{year}")
def get_report(year: int, with_narrative: bool = False) -> dict[str, Any]:
    data = reporter.collect_year(year)
    text = reporter.narrative(data) if with_narrative else None
    data["narrative"] = text
    data["markdown"] = reporter.to_markdown(data, text)
    data["csv"] = reporter.to_csv(data)
    return data


@app.get("/report/trend/all")
def get_trend() -> dict[str, Any]:
    return {"trend": reporter.multi_year_trend()}


# ---------------------------------------------------------------------------
# knowledge
# ---------------------------------------------------------------------------
@app.get("/knowledge")
def get_knowledge() -> dict[str, Any]:
    return {
        "stats": knowledge.corpus_stats(),
        "formats": knowledge.formats(),
        "societies": knowledge.societies(),
    }


@app.post("/knowledge/refresh")
def refresh_knowledge(pages: int = 12) -> dict[str, Any]:
    return {"past_events": knowledge.refresh_past_events(max_pages=pages)}


# ---------------------------------------------------------------------------
# notifications
# ---------------------------------------------------------------------------
@app.get("/notify/status")
def notify_status() -> dict[str, Any]:
    return digest.channel_status()


@app.post("/notify/digest")
def push_digest(dry_run: bool = True) -> dict[str, Any]:
    return digest.send_digest(dry_run=dry_run)


@app.post("/notify/alerts")
def push_alerts(dry_run: bool = True, threshold: int | None = None) -> dict[str, Any]:
    return digest.send_alerts(threshold=threshold, dry_run=dry_run)


@app.get("/notify/telegram/chats")
def telegram_chats() -> dict[str, Any]:
    """Discover the chat id of any group the bot has been added to."""
    return {"chats": telegram.discover_chat_ids()}


@app.get("/scheduler")
def scheduler_status() -> dict[str, Any]:
    return scheduler.status()


@app.post("/scheduler/run/{job}")
def run_job(job: str) -> dict[str, Any]:
    jobs = {
        "crawl": scheduler.job_crawl,
        "digest": scheduler.job_digest,
        "knowledge": scheduler.job_refresh_knowledge,
    }
    if job not in jobs:
        raise HTTPException(400, f"unknown job, expected one of {list(jobs)}")
    return {"job": job, "result": jobs[job]()}
