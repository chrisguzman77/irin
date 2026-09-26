"""SQLite persistence. Readings and treatments are live from day one; the
other tables are defined empty for the tiers that fill them (R2 alarm_events,
R3 night_records, R4 low_events, R11 low_event_recalls, R10 symptom_checks
and plans, R7 cards, R5 pairings, R9 doctor_messages, F3 family_stories)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from .config import config
from .contracts import AlarmEvent, FamilyStory, MorningReport, NightRecord, PresenceState, Reading, Treatment

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    timestamp TEXT PRIMARY KEY, glucose_mgdl REAL NOT NULL, trend TEXT NOT NULL,
    source TEXT NOT NULL, is_stale INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS treatments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, kind TEXT NOT NULL,
    insulin_units REAL, carbs_g REAL, dose_label TEXT, text TEXT,
    confirmed INTEGER NOT NULL DEFAULT 0, is_demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS alarm_events (event_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS night_records (night_date TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS low_events (low_event_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS low_event_recalls (low_event_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS symptom_checks (date TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cards (card_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plans (plan_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pairings (id INTEGER PRIMARY KEY AUTOINCREMENT, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS doctor_messages (message_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS family_stories (story_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports (night_date TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS presence_transitions (since TEXT PRIMARY KEY, json TEXT NOT NULL);
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or config.IRIN_DB))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str | Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        # columns added after the first Pi database was created (ALTER is idempotent by try)
        try:
            conn.execute("ALTER TABLE treatments ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:  # duplicate column: already there
            pass


def insert_reading(reading: Reading, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO readings VALUES (?, ?, ?, ?, ?)",
            (reading.timestamp.isoformat(), reading.glucose_mgdl, reading.trend,
             reading.source, int(reading.is_stale)),
        )
    if own:
        conn.close()


def select_readings(since: datetime, conn: sqlite3.Connection | None = None) -> list[Reading]:
    own = conn is None
    conn = conn or connect()
    rows = conn.execute(
        "SELECT * FROM readings WHERE timestamp >= ? ORDER BY timestamp", (since.isoformat(),)
    ).fetchall()
    if own:
        conn.close()
    return [
        Reading(timestamp=datetime.fromisoformat(r["timestamp"]), glucose_mgdl=r["glucose_mgdl"],
                trend=r["trend"], source=r["source"], is_stale=bool(r["is_stale"]))
        for r in rows
    ]


def select_latest_reading(conn: sqlite3.Connection | None = None) -> Reading | None:
    own = conn is None
    conn = conn or connect()
    r = conn.execute("SELECT * FROM readings ORDER BY timestamp DESC LIMIT 1").fetchone()
    if own:
        conn.close()
    if r is None:
        return None
    return Reading(timestamp=datetime.fromisoformat(r["timestamp"]), glucose_mgdl=r["glucose_mgdl"],
                   trend=r["trend"], source=r["source"], is_stale=bool(r["is_stale"]))


def insert_treatment(t: Treatment, conn: sqlite3.Connection | None = None, is_demo: bool = False) -> int:
    """Invariant 2, enforced again at the storage boundary. is_demo marks an
    entry logged while the replay source was active: stored locally like any
    other, but the forwarder files it under the demo device, never the real one."""
    if t.insulin_units is not None and not t.confirmed:
        raise ValueError("insulin_units may only be stored with confirmed=True")
    own = conn is None
    conn = conn or connect()
    with conn:
        cur = conn.execute(
            "INSERT INTO treatments (timestamp, kind, insulin_units, carbs_g, dose_label, text, confirmed, is_demo)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (t.timestamp.isoformat(), t.kind, t.insulin_units, t.carbs_g, t.dose_label, t.text,
             int(t.confirmed), int(is_demo)),
        )
        rowid = int(cur.lastrowid or 0)
    if own:
        conn.close()
    return rowid


def select_treatments(since: datetime, conn: sqlite3.Connection | None = None) -> list[Treatment]:
    own = conn is None
    conn = conn or connect()
    rows = conn.execute(
        "SELECT * FROM treatments WHERE timestamp >= ? ORDER BY timestamp", (since.isoformat(),)
    ).fetchall()
    if own:
        conn.close()
    return [
        Treatment(timestamp=datetime.fromisoformat(r["timestamp"]), kind=r["kind"],
                  insulin_units=r["insulin_units"], carbs_g=r["carbs_g"], dose_label=r["dose_label"],
                  text=r["text"], confirmed=bool(r["confirmed"]))
        for r in rows
    ]


def insert_report(report: MorningReport, conn: sqlite3.Connection | None = None) -> None:
    """One report per night; a rebuild replaces it."""
    own = conn is None
    conn = conn or connect()
    with conn:
        conn.execute("INSERT OR REPLACE INTO reports VALUES (?, ?)",
                     (report.night_date.isoformat(), report.model_dump_json()))
    if own:
        conn.close()


def select_report(night_date: date, conn: sqlite3.Connection | None = None) -> MorningReport | None:
    own = conn is None
    conn = conn or connect()
    r = conn.execute("SELECT json FROM reports WHERE night_date = ?", (night_date.isoformat(),)).fetchone()
    if own:
        conn.close()
    return MorningReport.model_validate_json(r["json"]) if r else None


def select_reports(limit: int = 30, conn: sqlite3.Connection | None = None) -> list[MorningReport]:
    """Newest night first."""
    own = conn is None
    conn = conn or connect()
    rows = conn.execute("SELECT json FROM reports ORDER BY night_date DESC LIMIT ?", (limit,)).fetchall()
    if own:
        conn.close()
    return [MorningReport.model_validate_json(r["json"]) for r in rows]


def get_kv(key: str, conn: sqlite3.Connection | None = None) -> str | None:
    own = conn is None
    conn = conn or connect()
    r = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    if own:
        conn.close()
    return r["value"] if r else None


def set_kv(key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    with conn:
        conn.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))
    if own:
        conn.close()


# --- the cloud forwarder's readers (C1): insertion order, so a backfilled older
# reading is still forwarded, and a replaced row (INSERT OR REPLACE gives it a new
# rowid) is forwarded again and upserted ---


def select_reading_rows(after_rowid: int, limit: int, conn: sqlite3.Connection | None = None) -> list[tuple[int, dict]]:
    own = conn is None
    conn = conn or connect()
    rows = conn.execute("SELECT rowid AS rid, * FROM readings WHERE rowid > ? ORDER BY rowid LIMIT ?",
                        (after_rowid, limit)).fetchall()
    if own:
        conn.close()
    return [(int(r["rid"]), {"timestamp": r["timestamp"], "glucose_mgdl": r["glucose_mgdl"], "trend": r["trend"],
                                "source": r["source"], "is_stale": bool(r["is_stale"]), "is_demo": False}) for r in rows]


def select_treatment_rows(after_rowid: int, limit: int, conn: sqlite3.Connection | None = None) -> list[tuple[int, dict]]:
    own = conn is None
    conn = conn or connect()
    rows = conn.execute("SELECT rowid AS rid, * FROM treatments WHERE rowid > ? ORDER BY rowid LIMIT ?",
                        (after_rowid, limit)).fetchall()
    if own:
        conn.close()
    return [(int(r["rid"]), {"timestamp": r["timestamp"], "kind": r["kind"], "insulin_units": r["insulin_units"],
                                "carbs_g": r["carbs_g"], "dose_label": r["dose_label"], "text": r["text"],
                                "confirmed": bool(r["confirmed"]), "is_demo": bool(r["is_demo"])}) for r in rows]


# --- Family Story (F3): one row per story, replaced on status change ---


def upsert_family_story(story: FamilyStory, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    with conn:
        conn.execute("INSERT OR REPLACE INTO family_stories VALUES (?, ?)", (story.story_id, story.model_dump_json()))
    if own:
        conn.close()


def select_family_story(story_id: str, conn: sqlite3.Connection | None = None) -> FamilyStory | None:
    own = conn is None
    conn = conn or connect()
    r = conn.execute("SELECT json FROM family_stories WHERE story_id = ?", (story_id,)).fetchone()
    if own:
        conn.close()
    return FamilyStory.model_validate_json(r["json"]) if r else None


def select_family_stories(night_date: date | None = None, limit: int = 100,
                          conn: sqlite3.Connection | None = None) -> list[FamilyStory]:
    """Newest night first; filtered to one morning when night_date is given."""
    own = conn is None
    conn = conn or connect()
    rows = conn.execute("SELECT json FROM family_stories").fetchall()
    if own:
        conn.close()
    stories = [FamilyStory.model_validate_json(r["json"]) for r in rows]
    if night_date is not None:
        stories = [s for s in stories if s.night_date == night_date]
    stories.sort(key=lambda s: (s.night_date, s.story_id), reverse=True)
    return stories[:limit]


# --- AlarmEvents (R2): one row per episode, replaced on rewrite ---


def upsert_alarm_event(event: AlarmEvent, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    with conn:
        conn.execute("INSERT OR REPLACE INTO alarm_events VALUES (?, ?)", (event.event_id, event.model_dump_json()))
    if own:
        conn.close()


def select_alarm_events(since: datetime, until: datetime | None = None,
                        conn: sqlite3.Connection | None = None) -> list[AlarmEvent]:
    """Episodes that started in [since, until], oldest first."""
    own = conn is None
    conn = conn or connect()
    rows = conn.execute("SELECT json FROM alarm_events").fetchall()
    if own:
        conn.close()
    events = [AlarmEvent.model_validate_json(r["json"]) for r in rows]
    events = [e for e in events if e.started_at >= since and (until is None or e.started_at <= until)]
    events.sort(key=lambda e: e.started_at)
    return events


# --- the presence TOGGLE history (R3's "away" reason code reads it; the radar never lands here) ---


def insert_presence_transition(state: PresenceState, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    with conn:
        conn.execute("INSERT OR REPLACE INTO presence_transitions VALUES (?, ?)",
                     (state.since.isoformat(), state.model_dump_json()))
    if own:
        conn.close()


def select_presence_transitions(start: datetime, end: datetime,
                                conn: sqlite3.Connection | None = None) -> list[PresenceState]:
    """The transitions in [start, end] plus the last one before start (the state in force)."""
    own = conn is None
    conn = conn or connect()
    rows = conn.execute("SELECT json FROM presence_transitions WHERE since <= ? ORDER BY since",
                        (end.isoformat(),)).fetchall()
    if own:
        conn.close()
    states = [PresenceState.model_validate_json(r["json"]) for r in rows]
    before = [s for s in states if s.since < start]
    return (before[-1:] if before else []) + [s for s in states if s.since >= start]


# --- night records (R3): one per night_date, replaced on rebuild ---


def upsert_night_record(record: NightRecord, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    with conn:
        conn.execute("INSERT OR REPLACE INTO night_records VALUES (?, ?)",
                     (record.night_date.isoformat(), record.model_dump_json()))
    if own:
        conn.close()


def select_night_record(night_date: date, conn: sqlite3.Connection | None = None) -> NightRecord | None:
    own = conn is None
    conn = conn or connect()
    r = conn.execute("SELECT json FROM night_records WHERE night_date = ?", (night_date.isoformat(),)).fetchone()
    if own:
        conn.close()
    return NightRecord.model_validate_json(r["json"]) if r else None


def select_night_records(since: date, until: date | None = None,
                         conn: sqlite3.Connection | None = None) -> list[NightRecord]:
    """Oldest first, by night_date in [since, until]."""
    own = conn is None
    conn = conn or connect()
    rows = conn.execute("SELECT json FROM night_records WHERE night_date >= ? AND night_date <= ? ORDER BY night_date",
                        (since.isoformat(), (until or date.max).isoformat())).fetchall()
    if own:
        conn.close()
    return [NightRecord.model_validate_json(r["json"]) for r in rows]
