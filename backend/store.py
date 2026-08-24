"""Persistence, on SQLite locally and Postgres when deployed.

Set DATABASE_URL to a postgres:// URL and everything moves there; leave it unset
and it is a single SQLite file. The difference matters because free hosting
gives you a disposable disk - a committee that loses its outreach notes on every
restart would be worse off than with no tool at all.

Queries are written once with `?` placeholders and translated for Postgres, and
every insert uses RETURNING so neither dialect needs `lastrowid`.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import settings

_LOCAL = threading.local()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

# Dialect differences, kept to the two that actually matter.
_PK = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
_REAL = "DOUBLE PRECISION" if IS_PG else "REAL"


def _sql(query: str) -> str:
    """Translate `?` placeholders for Postgres. No query here has a literal `?`."""
    return query.replace("?", "%s") if IS_PG else query


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS conferences (
    id            {_PK},
    uid           TEXT UNIQUE NOT NULL,
    source        TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'conference',
    title         TEXT NOT NULL,
    acronym       TEXT,
    url           TEXT,
    homepage      TEXT,
    location      TEXT,
    country       TEXT,
    start_date    TEXT,
    end_date      TEXT,
    cfp_deadline  TEXT,
    proposal_deadline TEXT,
    society       TEXT,
    topics        TEXT,
    summary       TEXT,
    fit_score     INTEGER DEFAULT 0,
    fit_reasons   TEXT,
    status        TEXT DEFAULT 'new',
    alerted       INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach (
    id            {_PK},
    conference_id INTEGER NOT NULL REFERENCES conferences(id) ON DELETE CASCADE,
    stage         TEXT NOT NULL DEFAULT 'identified',
    contact_name  TEXT,
    contact_email TEXT,
    contact_role  TEXT,
    draft         TEXT,
    notes         TEXT,
    next_action   TEXT,
    next_action_date TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pitches (
    id            {_PK},
    title         TEXT NOT NULL,
    idea          TEXT NOT NULL,
    format        TEXT,
    audience      TEXT,
    co_society    TEXT,
    verdict       TEXT,
    score         INTEGER,
    feedback      TEXT,
    playbook      TEXT,
    status        TEXT DEFAULT 'draft',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapter_events (
    id            {_PK},
    pitch_id      INTEGER REFERENCES pitches(id) ON DELETE SET NULL,
    title         TEXT NOT NULL,
    event_date    TEXT,
    format        TEXT,
    speakers      TEXT,
    co_society    TEXT,
    attendance    INTEGER DEFAULT 0,
    volunteers    INTEGER DEFAULT 0,
    budget_spent  {_REAL} DEFAULT 0,
    outcomes      TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_conf_fit    ON conferences(fit_score DESC);
CREATE INDEX IF NOT EXISTS idx_conf_status ON conferences(status);
CREATE INDEX IF NOT EXISTS idx_out_conf    ON outreach(conference_id);
"""

JSON_FIELDS = {"topics", "fit_reasons"}


# Columns added after the first release. Applied on connect so an existing
# .db file keeps working without a manual migration step.
LATE_COLUMNS: list[tuple[str, str, str]] = [
    ("conferences", "homepage", "TEXT"),
]


def _migrate(conn: Any) -> None:
    """Add columns introduced after a database was first created."""
    for table, column, decl in LATE_COLUMNS:
        if IS_PG:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {decl}"
            )
            continue
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if cols and column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _new_connection() -> Any:
    if IS_PG:
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)
        with conn.cursor() as cur:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    cur.execute(statement)
            _migrate(cur)
        conn.commit()
        return conn

    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _conn() -> Any:
    conn = getattr(_LOCAL, "conn", None)
    if conn is None:
        conn = _new_connection()
        _LOCAL.conn = conn
    return conn


class _Cursor:
    """Thin wrapper that rewrites `?` placeholders for Postgres.

    Lets every query in this module stay written once, in SQLite style.
    """

    def __init__(self, cur: Any) -> None:
        self._cur = cur

    def execute(self, query: str, args: Any = ()) -> Any:
        return self._cur.execute(_sql(query), args)

    def executemany(self, query: str, seq: Any) -> Any:
        return self._cur.executemany(_sql(query), seq)

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> Any:
        return self._cur.fetchall()

    def close(self) -> None:
        self._cur.close()

    def __iter__(self) -> Any:
        return iter(self._cur)


@contextmanager
def cursor():
    conn = _conn()
    cur = _Cursor(conn.cursor())
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()  # Postgres aborts the whole transaction on any error
        raise
    finally:
        cur.close()


def _insert(cur: _Cursor, table: str, payload: dict[str, Any]) -> int:
    """Insert a row and return its new id, in either dialect."""
    cols = list(payload)
    cur.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))}) RETURNING id",
        [payload[c] for c in cols],
    )
    row = cur.fetchone()
    return int(row["id"] if isinstance(row, dict) else row[0])


def init() -> None:
    _conn()


def backend_info() -> dict[str, Any]:
    """Which database is actually in use.

    Worth surfacing in the UI: if DATABASE_URL is missing or malformed on a
    hosted deployment, everything silently falls back to a file on a disposable
    disk and looks completely healthy right up until the data disappears.
    """
    if IS_PG:
        host = DATABASE_URL.split("@")[-1].split("/")[0] if "@" in DATABASE_URL else "configured"
        return {"engine": "postgres", "location": host, "persistent": True}
    return {
        "engine": "sqlite",
        "location": str(settings.db_path.name),
        "persistent": False,
    }


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for f in JSON_FIELDS:
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except (json.JSONDecodeError, TypeError):
                d[f] = []
    return d


def _rows(rs: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [_row(r) for r in rs]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# conferences
# ---------------------------------------------------------------------------
def upsert_conference(rec: dict[str, Any]) -> tuple[int, bool]:
    """Insert or refresh a conference. Returns (id, is_new).

    A re-crawl must never clobber committee-owned columns, so status and
    alerted are written on insert only.
    """
    payload = dict(rec)
    for f in JSON_FIELDS:
        if f in payload and not isinstance(payload[f], str):
            payload[f] = json.dumps(payload[f] or [])
    ts = now()

    with cursor() as cur:
        cur.execute("SELECT id FROM conferences WHERE uid = ?", (payload["uid"],))
        existing = cur.fetchone()
        if existing:
            cols = [k for k in payload if k not in ("uid", "status", "alerted", "created_at")]
            if cols:
                sets = ", ".join(f"{c} = ?" for c in cols)
                cur.execute(
                    f"UPDATE conferences SET {sets}, updated_at = ? WHERE uid = ?",
                    [payload[c] for c in cols] + [ts, payload["uid"]],
                )
            return existing["id"], False

        payload.setdefault("created_at", ts)
        payload["updated_at"] = ts
        return _insert(cur, "conferences", payload), True


def list_conferences(
    status: str | None = None,
    min_score: int = 0,
    kind: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    q = "SELECT * FROM conferences WHERE fit_score >= ?"
    args: list[Any] = [min_score]
    if status:
        q += " AND status = ?"
        args.append(status)
    if kind:
        q += " AND kind = ?"
        args.append(kind)
    q += " ORDER BY fit_score DESC, start_date ASC LIMIT ?"
    args.append(limit)
    with cursor() as cur:
        cur.execute(q, args)
        return _rows(cur.fetchall())


def get_conference(cid: int) -> dict[str, Any] | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM conferences WHERE id = ?", (cid,))
        return _row(cur.fetchone())


def set_conference_status(cid: int, status: str) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE conferences SET status = ?, updated_at = ? WHERE id = ?",
            (status, now(), cid),
        )


def mark_alerted(ids: list[int]) -> None:
    if not ids:
        return
    with cursor() as cur:
        cur.executemany("UPDATE conferences SET alerted = 1 WHERE id = ?", [(i,) for i in ids])


def unalerted_above(threshold: int) -> list[dict[str, Any]]:
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM conferences WHERE alerted = 0 AND fit_score >= ? "
            "AND status != 'dismissed' ORDER BY fit_score DESC",
            (threshold,),
        )
        return _rows(cur.fetchall())


# ---------------------------------------------------------------------------
# outreach
# ---------------------------------------------------------------------------
def upsert_outreach(conference_id: int, **fields: Any) -> int:
    ts = now()
    with cursor() as cur:
        cur.execute("SELECT id FROM outreach WHERE conference_id = ?", (conference_id,))
        row = cur.fetchone()
        if row:
            if fields:
                sets = ", ".join(f"{k} = ?" for k in fields)
                cur.execute(
                    f"UPDATE outreach SET {sets}, updated_at = ? WHERE id = ?",
                    list(fields.values()) + [ts, row["id"]],
                )
            return row["id"]
        fields.update(conference_id=conference_id, created_at=ts, updated_at=ts)
        return _insert(cur, "outreach", fields)


def list_outreach() -> list[dict[str, Any]]:
    with cursor() as cur:
        cur.execute(
            "SELECT o.*, c.title, c.acronym, c.url, c.fit_score, c.start_date, c.location "
            "FROM outreach o JOIN conferences c ON c.id = o.conference_id "
            "ORDER BY c.fit_score DESC"
        )
        return _rows(cur.fetchall())


def get_outreach(conference_id: int) -> dict[str, Any] | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM outreach WHERE conference_id = ?", (conference_id,))
        return _row(cur.fetchone())


# ---------------------------------------------------------------------------
# pitches
# ---------------------------------------------------------------------------
def save_pitch(rec: dict[str, Any]) -> int:
    ts = now()
    payload = dict(rec, created_at=ts, updated_at=ts)
    for f in ("feedback", "playbook"):
        if f in payload and not isinstance(payload[f], (str, type(None))):
            payload[f] = json.dumps(payload[f])
    with cursor() as cur:
        return _insert(cur, "pitches", payload)


def update_pitch(pid: int, **fields: Any) -> None:
    for f in ("feedback", "playbook"):
        if f in fields and not isinstance(fields[f], (str, type(None))):
            fields[f] = json.dumps(fields[f])
    sets = ", ".join(f"{k} = ?" for k in fields)
    with cursor() as cur:
        cur.execute(
            f"UPDATE pitches SET {sets}, updated_at = ? WHERE id = ?",
            list(fields.values()) + [now(), pid],
        )


def list_pitches(status: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM pitches", []
    if status:
        q += " WHERE status = ?"
        args.append(status)
    q += " ORDER BY created_at DESC"
    with cursor() as cur:
        cur.execute(q, args)
        return _rows(cur.fetchall())


def get_pitch(pid: int) -> dict[str, Any] | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM pitches WHERE id = ?", (pid,))
        return _row(cur.fetchone())


# ---------------------------------------------------------------------------
# chapter events (activity reporting)
# ---------------------------------------------------------------------------
def save_event(rec: dict[str, Any]) -> int:
    ts = now()
    payload = dict(rec, created_at=ts, updated_at=ts)
    with cursor() as cur:
        return _insert(cur, "chapter_events", payload)


def list_events(year: int | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM chapter_events", []
    if year:
        q += " WHERE substr(event_date, 1, 4) = ?"
        args.append(str(year))
    q += " ORDER BY event_date DESC"
    with cursor() as cur:
        cur.execute(q, args)
        return _rows(cur.fetchall())


def delete_event(eid: int) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM chapter_events WHERE id = ?", (eid,))


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
def set_meta(key: str, value: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_meta(key: str, default: str = "") -> str:
    with cursor() as cur:
        cur.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default


def stats() -> dict[str, Any]:
    with cursor() as cur:
        out: dict[str, Any] = {}
        cur.execute("SELECT COUNT(*) n FROM conferences")
        out["conferences"] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) n FROM conferences WHERE status = 'watching'")
        out["watching"] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) n FROM outreach WHERE stage NOT IN ('identified','declined')")
        out["active_outreach"] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) n FROM pitches")
        out["pitches"] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) n FROM chapter_events")
        out["events_run"] = cur.fetchone()["n"]
        out["last_crawl"] = get_meta("last_crawl", "never")
        return out
