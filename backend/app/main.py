"""Irin backend. Boots with IRIN_HW=mock and DATASOURCE=replay on any laptop.

Serves ../frontend/display at / (the kiosk page). The phone app is NOT
served here (it is hosted, decisions 27/28); CORS allows APP_ORIGIN and
Vite's dev server. Nothing here imports rounds/ or buddy/."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
from .alarm import AlarmEngine, Transition
from .auth import require_fresh_pin, require_pin
from .clock import clock
from .config import REPO_ROOT, config
from .contracts import (FRESH_PIN_ENDPOINTS, AlarmEvent, AlarmState, FamilyRecipient, FamilyStory, LowEvent,
                        MorningReport, NightRecord, Pairing, Reading, Settings, Treatment, WSMessage)
from .datasource.base import DataSource
from .datasource.nightscout import NightscoutDataSource
from .datasource.replay import ReplayDataSource
from .demo import bind as bind_demo, live_basal_time, restore_live_settings, router as demo_router
from .family_story import FamilyStoryService
from .forecast import Forecaster
from .forward import Forwarder, from_config as forwarder_from_config
from .outputs import GatedOutputs
from .presence import PresenceMachine, PresenceState
from .reports import ReportBuilder, SmtpMailer
from .rounds import crypto
from .rounds.alarm_events import AlarmEventRecorder
from .rounds.ledger import Ledger
from .rounds.low_events import LowEventDetector
from .rounds.cards import CardSender
from .rounds.pairing import PairingError, PairingService, RelayPairing
from .rounds.relay_client import RelayClient
from .rounds.nights_adapter import NightsAdapter
from .scheduler import Scheduler, basal_logged_on, timedatectl_synced
from . import voice_out
from .voice import MAX_CARBS_G, MAX_UNITS, VoiceLogger
from .windows import parse_hhmm
from .ws import Hub

Mode = Literal["replay", "nightscout"]

DISPLAY_DIR = REPO_ROOT / "frontend" / "display"


def make_datasource(mode: Mode) -> DataSource:
    if mode == "replay":
        return ReplayDataSource(config.scenario_path, speed=config.REPLAY_SPEED)
    return NightscoutDataSource(config.NIGHTSCOUT_URL, config.NIGHTSCOUT_TOKEN)


@dataclass
class Runtime:
    """Everything the app holds at runtime; the mode switch swaps `datasource`."""

    mode: Mode
    datasource: DataSource
    settings: Settings = field(default_factory=Settings)
    hub: "Hub | None" = None
    alarm: "AlarmEngine | None" = None
    forecaster: "Forecaster | None" = None
    voice: "VoiceLogger | None" = None
    presence: "PresenceMachine | None" = None
    outputs: "GatedOutputs | None" = None
    scheduler: "Scheduler | None" = None
    reports: "ReportBuilder | None" = None
    forwarder: "Forwarder | None" = None
    family: "FamilyStoryService | None" = None
    alarm_events: "AlarmEventRecorder | None" = None
    ledger: "Ledger | None" = None
    low_events: "LowEventDetector | None" = None
    pairing: "PairingService | None" = None
    relay_client: "RelayClient | None" = None
    cards: "CardSender | None" = None


runtime = Runtime(mode="nightscout" if config.DATASOURCE == "nightscout" else "replay",
                  datasource=make_datasource("nightscout" if config.DATASOURCE == "nightscout" else "replay"))
hub = Hub(runtime)
runtime.hub = hub


def _hal():
    from hardware.hal import get_hal  # Slavik's boundary; the mock under IRIN_HW=mock

    return get_hal()

runtime.presence = PresenceMachine(runtime.settings)
runtime.outputs = GatedOutputs(_hal(), runtime.presence)  # Away gates room outputs only, here
runtime.alarm = AlarmEngine(runtime.settings, hal=runtime.outputs)
runtime.forecaster = Forecaster()
runtime.voice = VoiceLogger(is_demo=lambda: runtime.mode == "replay")


def _sync_check() -> bool:
    """The NTP guard on real hardware; bypassed in replay (the clock is the scenario's)."""
    return True if runtime.mode == "replay" else timedatectl_synced()


runtime.scheduler = Scheduler(runtime.settings, sync_check=None if config.IRIN_HW == "mock" else _sync_check)
runtime.scheduler.basal_logged_today = lambda d: basal_logged_on(
    store.select_treatments(datetime.combine(d, time.min)), d)
runtime.scheduler.on_synced = lambda: clock.resync() if runtime.mode != "replay" else None


# --- the morning report (step 11): built from stored readings (live) or the scenario rows (replay) ---


def _readings_from(ds: DataSource, start: datetime, end: datetime) -> list[Reading]:
    if isinstance(ds, ReplayDataSource):
        return [ds._to_reading(r) for r in ds.rows if start <= r[0] <= end]
    return [r for r in store.select_readings(start) if r.timestamp <= end]


def _readings_between(start: datetime, end: datetime) -> list[Reading]:
    return _readings_from(runtime.datasource, start, end)


def _treatments_between(start: datetime, end: datetime) -> list[Treatment]:
    return [t for t in store.select_treatments(start) if t.timestamp <= end]


runtime.reports = ReportBuilder(readings_for=_readings_between, treatments_for=_treatments_between,
                                settings=runtime.settings)


_mailer = SmtpMailer() if config.SMTP_HOST else None
runtime.reports.mailer = _mailer
runtime.family = FamilyStoryService(
    settings=runtime.settings, mailer=_mailer, render_clip=voice_out.render,
    family_view_url=config.APP_ORIGIN.replace("://", "://family.", 1) if config.APP_ORIGIN.startswith("https://") else "")


def _build_night(night_date: date, scheduled: bool = False) -> tuple[MorningReport | None, list[FamilyStory]]:
    """The morning report, then one Family Story per active recipient (F3).
    The datasource is read ONCE: is_demo and the readings come from the same
    source, so a mode switch mid-build can never email scenario rows as a real
    night (invariant 1). The scheduled job never rebuilds a night that already
    has a report (a restart inside the catch-up window would re-email it);
    POST /api/reports/build always rebuilds."""
    ds = runtime.datasource
    if scheduled and store.select_report(night_date) is not None:
        logging.getLogger("irin.main").info("morning report for %s already exists; not rebuilt", night_date)
        return None, []
    builder = dataclasses.replace(runtime.reports, readings_for=lambda a, b: _readings_from(ds, a, b))
    is_demo = isinstance(ds, ReplayDataSource)
    try:
        report = builder.build(night_date, is_demo=is_demo)
    except Exception:
        logging.getLogger("irin.main").exception("morning report build failed")
        return None, []
    try:
        stories = runtime.family.build(night_date, report.stats, is_demo)
    except Exception:
        logging.getLogger("irin.main").exception("family stories failed; the report stands")
        stories = []
    return report, stories


def _build_report(night_date: date, scheduled: bool = False) -> MorningReport | None:
    return _build_night(night_date, scheduled)[0]


async def _announce_stories(stories: list[FamilyStory]) -> None:
    for story in stories:
        if story.status == "pending_approval":
            await hub.broadcast(WSMessage(type="family_story_pending", payload=story.model_dump(mode="json")))
        elif story.status in ("sent", "demo"):
            await hub.broadcast(WSMessage(type="family_story_sent", payload=story.model_dump(mode="json")))


async def _night_job(night_date: date) -> None:
    _, stories = await asyncio.to_thread(_build_night, night_date, True)
    await _announce_stories(stories)


def _morning_report_job(night_date: date) -> None:
    """The scheduler job at night-window end: the build runs in a worker thread."""
    task = asyncio.get_running_loop().create_task(_night_job(night_date))
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


runtime.scheduler.register("morning_report", lambda: runtime.settings.night_window_end, _morning_report_job)


# --- the forwarder to Irin Cloud (C1): outbound, batched, its own task, never in the alarm path ---


def _replay_rows(cursor: datetime | None) -> tuple[object, list[dict]]:
    """The replay source's served rows newer than the cursor, keyed by the source
    object (a mode switch or scenario select makes a new one and the forwarder
    starts that run from its first row). Live readings come from the store."""
    ds = runtime.datasource
    if not isinstance(ds, ReplayDataSource):
        return None, []
    rows = [ds._to_reading(r) for r in ds._available(clock.now()) if cursor is None or r[0] > cursor]
    return ds, [r.model_dump(mode="json") for r in rows]


runtime.forwarder = forwarder_from_config(_replay_rows)


# --- the night ledger (R3): George's nights.py over the device's own stores, at night-window end ---

runtime.ledger = Ledger(
    adapter=NightsAdapter(settings=runtime.settings, readings_for=_readings_between,
                          treatments_for=_treatments_between, alarm_events_for=store.select_alarm_events,
                          presence_for=store.select_presence_transitions,
                          brain_only=lambda: config.IRIN_BRAIN_ONLY),
    is_demo=lambda: runtime.mode == "replay")


runtime.low_events = LowEventDetector(adapter=runtime.ledger.adapter, is_demo=lambda: runtime.mode == "replay")


def _ledger_job(morning: date) -> None:
    """At night-window end: the night that just ended (keyed by its evening date)."""
    task = asyncio.get_running_loop().create_task(
        asyncio.to_thread(_build_night_record, runtime.ledger.night_ended_on(morning)))
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


def _build_night_record(night_date: date) -> NightRecord | None:
    """The ledger row, then the night's low events (R4) from the same inputs."""
    try:
        record = runtime.ledger.build_night(night_date)
    except Exception:
        logging.getLogger("irin.main").exception("night ledger failed for %s", night_date)
        return None
    try:
        runtime.low_events.detect(night_date)
    except Exception:
        logging.getLogger("irin.main").exception("low events failed for %s; the ledger row stands", night_date)
    return record


runtime.scheduler.register("ledger", lambda: runtime.settings.night_window_end, _ledger_job)


# --- doctor / buddy pairing (R5): the QR handshake through the relay, confirmed with a FRESH PIN ---


def _role_url(configured: str, sub: str) -> str:
    """INBOX_URL / WATCH_URL from .env, else derived from APP_ORIGIN (doctor. / watch.)."""
    if configured:
        return configured
    return config.APP_ORIGIN.replace("://", f"://{sub}.", 1) if config.APP_ORIGIN.startswith("https://") else config.APP_ORIGIN


def _broadcast_pairing_state(state: dict) -> None:
    try:
        task = asyncio.get_running_loop().create_task(hub.broadcast(WSMessage(type="pairing_state", payload=state)))
    except RuntimeError:
        return
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


def _broadcast_card_sent(payload: dict) -> None:
    try:
        task = asyncio.get_running_loop().create_task(hub.broadcast(WSMessage(type="card_sent", payload=payload)))
    except RuntimeError:
        return
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


def _make_relay_client() -> RelayClient:
    return RelayClient(relay_url=config.RELAY_URL, source_key=config.RELAY_SOURCE_KEY,
                       device_id=config.DEVICE_ID or "irin-dev", is_demo=lambda: runtime.mode == "replay",
                       on_pairings=lambda states: runtime.pairing.apply_remote_states(states),
                       on_tick=lambda: runtime.cards.flush())


def _make_pairing() -> PairingService:
    return PairingService(relay=RelayPairing(config.RELAY_URL, config.RELAY_SOURCE_KEY),
                          device_id=config.DEVICE_ID or "irin-dev", device_pk_fn=crypto.device_public_key,
                          inbox_url=_role_url(config.INBOX_URL, "doctor"), watch_url=_role_url(config.WATCH_URL, "watch"),
                          relay_url=config.RELAY_URL, is_demo=lambda: runtime.mode == "replay",
                          on_state=_broadcast_pairing_state)

ALARM_TICK_CLOCK_SECONDS = 30.0
_broadcast_tasks: set[asyncio.Task] = set()  # references held so a broadcast is never GC'd mid-flight


def _broadcast_transition(t: Transition) -> None:
    """The ws observer on alarm.py's hook: every transition becomes alarm_state_change."""
    payload = {**runtime.alarm.state.model_dump(mode="json"), "old_state": t.old_state,
               "escalated": t.escalated, "ack_source": t.ack_source}
    try:
        task = asyncio.get_running_loop().create_task(hub.broadcast(WSMessage(type="alarm_state_change", payload=payload)))
    except RuntimeError:  # no running loop (a synchronous test driving the engine directly)
        return
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


runtime.alarm.on_transition(_broadcast_transition)
runtime.alarm_events = AlarmEventRecorder(is_demo=lambda: runtime.mode == "replay",
                                          brain_only=lambda: config.IRIN_BRAIN_ONLY)
runtime.alarm.on_transition(runtime.alarm_events)  # R2 observes; it never calls back into alarm.py


def _broadcast_presence(state: PresenceState) -> None:
    try:
        store.insert_presence_transition(state)  # the toggle history the night ledger reads (R3)
    except Exception:
        logging.getLogger("irin.main").exception("presence transition not stored")
    try:
        task = asyncio.get_running_loop().create_task(
            hub.broadcast(WSMessage(type="presence_change", payload=state.model_dump(mode="json"))))
    except RuntimeError:
        return
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


runtime.presence.on_change(_broadcast_presence)


async def _alarm_tick_loop() -> None:
    """Every 30 s of clock time: the alarm deadlines and one raw radar sample
    for the presence machine (the same cadence R2 samples presence_during)."""
    while True:
        try:
            runtime.alarm.tick()
            raw = runtime.outputs.get_presence()
            runtime.presence.sample(raw)
            runtime.alarm_events.sample(raw)  # the same raw radar sample, aggregated per episode (B7)
        except Exception:
            logging.getLogger("irin.main").exception("tick failed; continuing")
        await clock.sleep(ALARM_TICK_CLOCK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    _load_settings()
    runtime.pairing = _make_pairing()  # after init_db: it loads the stored pairings
    runtime.relay_client = _make_relay_client()
    runtime.cards = CardSender(recipients=runtime.pairing.recipients, post=runtime.relay_client.post_card,
                               device_id=config.DEVICE_ID or "irin-dev", on_sent=_broadcast_card_sent)
    await runtime.datasource.start()
    hub.start()
    tick_task = asyncio.create_task(_alarm_tick_loop())
    sched_task = asyncio.create_task(runtime.scheduler.run())
    forward_task = asyncio.create_task(runtime.forwarder.run())
    relay_task = asyncio.create_task(runtime.relay_client.run())
    yield
    for task in (tick_task, sched_task, forward_task, relay_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await hub.stop()
    await runtime.datasource.stop()


app = FastAPI(title="Irin device API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.APP_ORIGIN, "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["X-PIN", "Authorization", "Content-Type"],
    allow_credentials=True,
)


# --- API ---


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "datasource": runtime.mode, "hw": config.IRIN_HW, "clock": clock.now().isoformat()}


@app.get("/api/latest", response_model=Reading)
async def latest() -> Reading:
    try:
        reading = await runtime.datasource.get_latest()
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    if reading is None:
        raise HTTPException(status_code=404, detail="no reading yet")
    return reading


@app.get("/api/history", response_model=list[Reading])
async def history(minutes: int = 180) -> list[Reading]:
    """Readings from the last `minutes` of clock time, oldest first (the
    display's graph after a reload; Justin's request, no contracts change)."""
    minutes = max(1, min(minutes, 24 * 60))
    try:
        return await runtime.datasource.history(minutes)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.get("/api/forecast")
async def forecast_status() -> dict:
    """The last forecast result: forecast, status (ok | suspended | unavailable), reason."""
    return runtime.forecaster.last.payload() if runtime.forecaster else {"forecast": None, "status": "unavailable", "reason": "no forecaster"}


@app.get("/api/contracts/fresh_pin")
async def fresh_pin() -> dict:
    """The one list of fresh-PIN endpoints, read by display.js and the app's usePin hook."""
    return {"endpoints": list(FRESH_PIN_ENDPOINTS)}


class ModeRequest(BaseModel):
    mode: Mode


@app.post("/api/mode", dependencies=[Depends(require_pin)])
async def set_mode(req: ModeRequest) -> dict:
    """Swap the active datasource at runtime (chris.md step 12). Day one: the
    swap mechanism; alarm reset is wired here as the alarm engine lands. On
    return to live the poller serves the cached reading marked stale until a
    fresh poll lands."""
    if req.mode == runtime.mode:
        return {"mode": runtime.mode, "changed": False}
    new = make_datasource(req.mode)
    await runtime.datasource.stop()
    runtime.datasource = new
    runtime.mode = req.mode
    await new.start()
    runtime.alarm.reset()  # switch semantics (step 12): alarm state back to idle
    runtime.alarm_events.reset()  # an episode cut by the switch is dropped, never written
    runtime.pairing.cancel()  # a pending QR token belongs to the old clock and the old is_demo
    runtime.forecaster.reset()
    runtime.voice.reset()
    hub._last = None
    if req.mode == "nightscout" and restore_live_settings():  # the demo basal-time button never reaches live
        await _settings_changed()
    await hub.broadcast(WSMessage(type="mode_change", payload={"mode": runtime.mode}))
    return {"mode": runtime.mode, "changed": True}


class AckRequest(BaseModel):
    source: Literal["device", "app"]


@app.post("/api/acknowledge", dependencies=[Depends(require_pin)])
async def acknowledge(req: AckRequest) -> dict:
    """Acknowledge the current alarm. source records which screen answered
    (the kiosk's big button = device, the app = app) for the R2 recorder."""
    changed = runtime.alarm.acknowledge(req.source)
    return {"acknowledged": changed, "alarm": runtime.alarm.state.model_dump(mode="json")}


# --- logging (step 7): every path is PIN-gated; insulin is never stored without confirm ---


async def _announce(stored: list[dict]) -> None:
    for t in stored:
        await hub.broadcast(WSMessage(type="treatment_logged", payload=t))


class VoiceText(BaseModel):
    text: str = Field(max_length=200)


@app.post("/api/log/voice", dependencies=[Depends(require_pin)])
async def log_voice(req: VoiceText) -> dict:
    """Parse spoken text. Carbs-only entries are stored at once; any insulin
    comes back as needs_confirm with an echo and a pending_id (10 s on clock.py)."""
    result = runtime.voice.submit(req.text)
    if result["status"] == "stored":
        await _announce(result["stored"])
    return result


@app.post("/api/log/voice/{pending_id}/confirm", dependencies=[Depends(require_pin)])
async def log_voice_confirm(pending_id: str) -> dict:
    result = runtime.voice.confirm(pending_id)
    if result["status"] == "stored":
        await _announce(result["stored"])
    return result


@app.post("/api/log/voice/{pending_id}/cancel", dependencies=[Depends(require_pin)])
async def log_voice_cancel(pending_id: str) -> dict:
    return runtime.voice.cancel(pending_id)


@app.post("/api/log", dependencies=[Depends(require_pin)])
async def log_treatment(t: Treatment) -> dict:
    """Structured logging from the app's forms (basal taken, carbs + units,
    notes). The Treatment contract refuses insulin_units without
    confirmed=True (422); `confirmed` is the client's assertion that its
    echo-and-confirm screen was passed, which the server cannot see, so the
    PIN gate is what makes that assertion trustworthy."""
    ts = t.timestamp
    if ts.tzinfo is not None:  # JS toISOString() sends Z; the Pi keeps naive local time
        ts = ts.astimezone().replace(tzinfo=None)
    if ts > clock.now() + timedelta(minutes=5):
        raise HTTPException(status_code=400, detail="timestamp is in the future")
    if t.insulin_units is not None:
        if t.kind not in ("bolus", "basal"):
            raise HTTPException(status_code=400, detail="insulin_units only on a bolus or basal")
        if not (math.isfinite(t.insulin_units) and 0 < t.insulin_units <= MAX_UNITS):
            raise HTTPException(status_code=400, detail=f"insulin_units must be in (0, {MAX_UNITS}]")
    if t.carbs_g is not None:
        if t.kind != "carbs":
            raise HTTPException(status_code=400, detail="carbs_g only on a carbs entry")
        if not (math.isfinite(t.carbs_g) and 0 < t.carbs_g <= MAX_CARBS_G):
            raise HTTPException(status_code=400, detail=f"carbs_g must be in (0, {MAX_CARBS_G}]")
    if t.kind in ("bolus", "basal") and t.insulin_units is None:
        raise HTTPException(status_code=400, detail=f"a {t.kind} entry needs insulin_units")
    if t.kind == "carbs" and t.carbs_g is None:
        raise HTTPException(status_code=400, detail="a carbs entry needs carbs_g")
    row = t.model_copy(update={"timestamp": ts})
    store.insert_treatment(row, is_demo=runtime.mode == "replay")
    payload = row.model_dump(mode="json")
    await _announce([payload])
    return {"status": "stored", "stored": [payload]}


@app.get("/api/treatments", response_model=list[Treatment])
async def treatments(hours: int = 24) -> list[Treatment]:
    hours = max(1, min(hours, 24 * 14))
    return store.select_treatments(clock.now() - timedelta(hours=hours))


class PresenceOverride(BaseModel):
    override: Literal["auto", "home", "away"]


@app.post("/api/presence", dependencies=[Depends(require_pin)], response_model=PresenceState)
async def set_presence(req: PresenceOverride) -> PresenceState:
    """The manual Home/Away toggle (Settings.presence_override). It always beats
    the radar; it gates room outputs only and never touches alarm logic."""
    state = runtime.presence.set_override(req.override)
    await _settings_changed()
    return state


@app.get("/api/presence", response_model=PresenceState)
async def presence_state() -> PresenceState:
    return runtime.presence.state


@app.get("/api/forwarder")
async def forwarder_state() -> dict:
    """The cloud forwarder: enabled, cursor, batches sent, failures (the under-the-hood panel)."""
    return runtime.forwarder.status()


@app.get("/api/scheduler")
async def scheduler_state() -> dict:
    """clock_synced (the NTP guard), display_mode (detail | night | morning, from
    backend state), and the basal nudge (none | visual | email)."""
    sch = runtime.scheduler
    return {"clock_synced": sch.clock_synced, "display_mode": sch.display_mode(),
            "basal_nudge": {"level": sch.nudge.level, "since": sch.nudge.since.isoformat() if sch.nudge.since else None},
            "jobs": [{"name": j.name, "at": j.at_hhmm(), "last_fired": j.last_fired.isoformat() if j.last_fired else None}
                     for j in sch.jobs]}


@app.get("/api/alarm", response_model=AlarmState)
async def alarm_state() -> AlarmState:
    return runtime.alarm.state


# --- pairing endpoints (R5) ---


class PairStartRequest(BaseModel):
    peer_kind: Literal["doctor", "buddy"] = "doctor"


def _pairing_error(e: PairingError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail)


@app.post("/api/pair/start", dependencies=[Depends(require_pin)])
async def pair_start(req: PairStartRequest) -> dict:
    """A single-use 10-minute token and the QR URL (everything after # stays off every server)."""
    try:
        return await asyncio.to_thread(runtime.pairing.start, req.peer_kind)
    except PairingError as e:
        raise _pairing_error(e)


@app.get("/api/pair/status", dependencies=[Depends(require_pin)])
async def pair_status() -> dict:
    """Polls the relay once: awaiting_scan | awaiting_confirm (with code4) | idle, plus the pairings."""
    try:
        return await asyncio.to_thread(runtime.pairing.poll)
    except PairingError as e:
        raise _pairing_error(e)


@app.post("/api/pair/confirm", dependencies=[Depends(require_fresh_pin)], response_model=Pairing)
async def pair_confirm() -> Pairing:
    """The patient's confirmation on the device, with a PIN typed fresh (FRESH_PIN_ENDPOINTS)."""
    try:
        pairing = await asyncio.to_thread(runtime.pairing.confirm)
    except PairingError as e:
        raise _pairing_error(e)
    return pairing.model_copy(update={"doctor_pk": ""})  # the peer key stays on the device


@app.post("/api/pair/{doctor_id}/revoke", dependencies=[Depends(require_pin)], response_model=Pairing)
async def pair_revoke(doctor_id: str) -> Pairing:
    """Instant and final on both sides: sharing ended."""
    try:
        return await asyncio.to_thread(runtime.pairing.revoke, doctor_id)
    except PairingError as e:
        raise _pairing_error(e)


@app.get("/api/pairings", response_model=list[Pairing])
async def pairings() -> list[Pairing]:
    return [p.model_copy(update={"doctor_pk": ""}) for p in runtime.pairing.pairings.values()]


@app.get("/api/rounds/cards")
async def rounds_cards(limit: int = 50) -> list[dict]:
    """The device's own record of the cards it sealed (R7): card, delivery status, recipients."""
    return store.select_cards(max(1, min(limit, 500)))


@app.get("/api/relay")
async def relay_state() -> dict:
    """The relay client: polls, failures, undelivered cards (the under-the-hood panel)."""
    return {**runtime.relay_client.status(), "pending_cards": sorted(runtime.cards.pending)}


@app.get("/api/nights", response_model=list[NightRecord])
async def nights(days: int = 14) -> list[NightRecord]:
    """The night ledger (R3), oldest first."""
    days = max(1, min(days, 365))
    return store.select_night_records(clock.now().date() - timedelta(days=days))


class BuildNightRequest(BaseModel):
    night_date: date | None = None


@app.post("/api/nights/build", dependencies=[Depends(require_pin)], response_model=NightRecord)
async def build_night(req: BuildNightRequest) -> NightRecord:
    """Build or rebuild one night now (the demo panel, the seek's catch-up).
    night_date is the EVENING the night starts on; default: the night that ended this morning."""
    record = await asyncio.to_thread(_build_night_record,
                                     req.night_date or runtime.ledger.night_ended_on(clock.now().date()))
    if record is None:
        raise HTTPException(status_code=500, detail="night ledger failed; see the log")
    return record


@app.get("/api/low_events", response_model=list[LowEvent])
async def low_events(days: int = 14) -> list[LowEvent]:
    """Nocturnal lows (R4), oldest first."""
    days = max(1, min(days, 365))
    return store.select_low_events(clock.now().date() - timedelta(days=days))


@app.get("/api/alarm_events", response_model=list[AlarmEvent])
async def alarm_events(hours: int = 24) -> list[AlarmEvent]:
    """Finished episodes (R2), oldest first: the under-the-hood panel and Justin's timeline."""
    hours = max(1, min(hours, 24 * 30))
    return store.select_alarm_events(clock.now() - timedelta(hours=hours))


# --- settings (the app's settings form): one shared Settings object, updated in place ---


@app.get("/api/settings", response_model=Settings)
async def get_settings() -> Settings:
    return runtime.settings


def _deep_merge(base: dict, patch: dict) -> dict:
    """Nested objects (led_colors, night_buddy, emergency_script) merge field by
    field so patching one buddy opt-in never resets the other three; lists
    (family_recipients) are replaced whole."""
    out = dict(base)
    for k, v in patch.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


SETTINGS_BOUNDS = {  # (low, high) inclusive; every numeric setting must be finite and inside
    "low_threshold": (54, 100),
    "high_threshold": (120, 400),
    "predictive_lead_min": (5, 60),
    "consecutive_predictions_n": (1, 5),
    "high_remind_hours": (0.5, 24),
    "volume": (0.0, 1.0),
    "iob_duration_hours": (1.0, 12.0),
    "basal_units": (0.5, 200),
}


def _check_settings(new: Settings) -> None:
    """Raises ValueError. A NaN threshold would make every comparison in the
    alarm engine False and the actual-low alarm never fire (invariant 3)."""
    for name, (lo, hi) in SETTINGS_BOUNDS.items():
        value = getattr(new, name)
        if value is None:
            continue
        if not (math.isfinite(value) and lo <= value <= hi):
            raise ValueError(f"{name} must be a number in [{lo}, {hi}]")
    for name in ("night_window_start", "night_window_end", "basal_time"):
        value = getattr(new, name)
        if value is not None:
            parse_hhmm(value)  # "HH:MM" only; time() rejects out-of-range fields
    if new.low_threshold >= new.high_threshold:
        raise ValueError("low_threshold must be below high_threshold")


@app.post("/api/settings", dependencies=[Depends(require_pin)], response_model=Settings)
async def update_settings(patch: dict) -> Settings:
    """Merge the given fields into the live settings (a partial body is fine;
    nested objects merge field by field, lists replace whole). The alarm
    engine, presence machine, scheduler, and report builder all hold the
    same Settings object, so they see the change at once."""
    if not isinstance(patch, dict):
        raise HTTPException(status_code=400, detail="body must be an object of settings fields")
    unknown = set(patch) - set(Settings.model_fields)
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown settings: {sorted(unknown)}")
    try:
        new = Settings.model_validate(_deep_merge(runtime.settings.model_dump(), patch))
        _check_settings(new)
        if "family_recipients" in patch:  # the app's Family section replaces the list whole; consent rules hold
            new.family_recipients = _reconcile_recipients(runtime.settings.family_recipients, new.family_recipients)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"invalid settings: {e}")
    dropped = [r.recipient_id for r in runtime.settings.family_recipients
               if r.recipient_id not in {n.recipient_id for n in new.family_recipients}]
    reset = [n.recipient_id for n in new.family_recipients if not n.first_story_approved
             and any(o.recipient_id == n.recipient_id and o.first_story_approved for o in runtime.settings.family_recipients)]
    override_changed = new.presence_override != runtime.settings.presence_override
    for name in Settings.model_fields:
        setattr(runtime.settings, name, getattr(new, name))
    if override_changed:
        runtime.presence.set_override(new.presence_override)
    for rid in dropped + reset:  # removed, or re-consented: nothing pending at the old terms goes out
        await asyncio.to_thread(runtime.family.skip_pending, rid)
    await _settings_changed()
    return runtime.settings


def _reconcile_recipients(current: list[FamilyRecipient], incoming: list[FamilyRecipient]) -> list[FamilyRecipient]:
    """The list from the app, held to the consent rules the dedicated routes
    enforce: a valid email; a NEW recipient's first story waits for a tap; a
    changed email or level is a new consent (first story waits again); a
    revoked recipient stays revoked; a recipient missing from the list is
    revoked (never silently forgotten). Raises ValueError."""
    by_id = {r.recipient_id: r for r in current}
    out: list[FamilyRecipient] = []
    seen: set[str] = set()
    for r in incoming:
        if not re.fullmatch(_EMAIL, r.email) or not r.name.strip() or not r.recipient_id.strip():
            raise ValueError(f"recipient {r.recipient_id or '?'}: a name, an id, and a valid email are required")
        if r.recipient_id in seen:
            raise ValueError(f"recipient {r.recipient_id} listed twice")
        seen.add(r.recipient_id)
        old = by_id.get(r.recipient_id)
        r = r.model_copy()
        if old is None:
            r.first_story_approved = False
        else:
            if old.state == "revoked":
                r.state = "revoked"
            r.first_story_approved = old.first_story_approved and old.email == r.email and old.level == r.level
        out.append(r)
    for old in current:  # dropped from the list = revoked, kept on record
        if old.recipient_id not in seen:
            out.append(old.model_copy(update={"state": "revoked"}))
    return out


# --- morning reports (step 11): read by the morning screen and the app ---


@app.get("/api/reports", response_model=list[MorningReport])
async def list_reports(limit: int = 30) -> list[MorningReport]:
    """Newest night first; every report carries is_demo."""
    return store.select_reports(max(1, min(limit, 365)))


@app.get("/api/reports/latest", response_model=MorningReport)
async def latest_report() -> MorningReport:
    reports = store.select_reports(1)
    if not reports:
        raise HTTPException(status_code=404, detail="no report yet")
    return reports[0]


@app.get("/api/reports/{night_date}", response_model=MorningReport)
async def get_report(night_date: date) -> MorningReport:
    report = store.select_report(night_date)
    if report is None:
        raise HTTPException(status_code=404, detail="no report for that night")
    return report


@app.get("/api/reports/{night_date}/graph.png")
async def report_graph(night_date: date) -> FileResponse:
    report = store.select_report(night_date)
    if report is None or not report.graph_png_path or not Path(report.graph_png_path).is_file():
        raise HTTPException(status_code=404, detail="no graph for that night")
    return FileResponse(report.graph_png_path, media_type="image/png")


class BuildReportRequest(BaseModel):
    night_date: date | None = None  # default: the night ending this clock morning


@app.post("/api/reports/build", dependencies=[Depends(require_pin)], response_model=MorningReport)
async def build_report(req: BuildReportRequest) -> MorningReport:
    """Build (or rebuild) a report now: the demo panel's button and the
    pre-generated fallback for the no-network demo."""
    report, stories = await asyncio.to_thread(_build_night, req.night_date or clock.now().date())
    if report is None:
        raise HTTPException(status_code=500, detail="report build failed; see the log")
    await _announce_stories(stories)
    return report


# --- Family Story (F1, F3): recipients live in Settings; stories ride the morning report ---

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RecipientRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(pattern=_EMAIL, max_length=120)
    level: Literal["story_only", "story_and_view"] = "story_only"
    send_mode: Literal["automatic", "approve_each"] = "approve_each"


class RecipientPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    email: str | None = Field(default=None, pattern=_EMAIL, max_length=120)
    level: Literal["story_only", "story_and_view"] | None = None
    send_mode: Literal["automatic", "approve_each"] | None = None


def _recipient_or_404(recipient_id: str) -> FamilyRecipient:
    for r in runtime.settings.family_recipients:
        if r.recipient_id == recipient_id:
            return r
    raise HTTPException(status_code=404, detail="no such recipient")


SETTINGS_KEY = "settings"


NOT_PERSISTED = ("presence_override",)  # a reboot starts in auto: a power blip at night never leaves the room Away


def _persist_settings() -> None:
    data = runtime.settings.model_dump(mode="json")
    stash = live_basal_time()
    if stash is not None:
        data["basal_time"] = stash[0]  # the demo button's value never reaches the saved live settings
    for name in NOT_PERSISTED:
        data[name] = getattr(Settings(), name)
    store.set_kv(SETTINGS_KEY, Settings.model_validate(data).model_dump_json())


def _load_settings() -> None:
    """At boot: the last saved Settings (thresholds, windows, family consent) come
    back from the store; a fresh Pi starts with the defaults. An unreadable blob
    is kept aside as settings.unreadable, never silently overwritten."""
    raw = store.get_kv(SETTINGS_KEY)
    if not raw:
        return
    try:
        saved = Settings.model_validate_json(raw)
    except Exception:
        logging.getLogger("irin.main").exception("saved settings unreadable; defaults kept, blob saved aside")
        store.set_kv(SETTINGS_KEY + ".unreadable", raw)
        return
    for name in Settings.model_fields:
        if name not in NOT_PERSISTED:
            setattr(runtime.settings, name, getattr(saved, name))


async def _settings_changed() -> None:
    _persist_settings()
    await hub.broadcast(WSMessage(type="settings_change", payload=runtime.settings.model_dump(mode="json")))


@app.get("/api/family/recipients", response_model=list[FamilyRecipient])
async def list_recipients() -> list[FamilyRecipient]:
    return runtime.settings.family_recipients


@app.post("/api/family/recipients", dependencies=[Depends(require_pin)], response_model=FamilyRecipient)
async def add_recipient(req: RecipientRequest) -> FamilyRecipient:
    """A new recipient's first story always waits for the patient's approval."""
    r = FamilyRecipient(recipient_id=uuid.uuid4().hex[:12], **req.model_dump())
    runtime.settings.family_recipients.append(r)
    await _settings_changed()
    return r


@app.post("/api/family/recipients/{recipient_id}", dependencies=[Depends(require_pin)], response_model=FamilyRecipient)
async def edit_recipient(recipient_id: str, req: RecipientPatch) -> FamilyRecipient:
    r = _recipient_or_404(recipient_id)
    if r.state == "revoked":
        raise HTTPException(status_code=409, detail="a revoked recipient cannot be edited; add them again")
    changes = req.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(r, k, v)
    if "email" in changes or "level" in changes:
        # a new address or a new disclosure level is a new consent: the next story waits for a tap,
        # and anything already pending at the old level is dropped
        r.first_story_approved = False
        await asyncio.to_thread(runtime.family.skip_pending, r.recipient_id)
    await _settings_changed()
    return r


@app.post("/api/family/recipients/{recipient_id}/pause", dependencies=[Depends(require_pin)], response_model=FamilyRecipient)
async def pause_recipient(recipient_id: str) -> FamilyRecipient:
    r = _recipient_or_404(recipient_id)
    if r.state == "active":
        r.state = "paused"
        await _settings_changed()
    return r


@app.post("/api/family/recipients/{recipient_id}/resume", dependencies=[Depends(require_pin)], response_model=FamilyRecipient)
async def resume_recipient(recipient_id: str) -> FamilyRecipient:
    r = _recipient_or_404(recipient_id)
    if r.state == "paused":
        r.state = "active"
        await _settings_changed()
    return r


@app.post("/api/family/recipients/{recipient_id}/revoke", dependencies=[Depends(require_pin)], response_model=FamilyRecipient)
async def revoke_recipient(recipient_id: str) -> FamilyRecipient:
    """Instant and final: a revoked recipient receives nothing (invariant 19)."""
    r = _recipient_or_404(recipient_id)
    r.state = "revoked"
    await _settings_changed()
    return r


@app.get("/api/family/stories", response_model=list[FamilyStory])
async def list_stories(night_date: date | None = None, limit: int = 50) -> list[FamilyStory]:
    """The morning chip ("Sent to Mom"): the stories of one morning, or the latest."""
    return store.select_family_stories(night_date, max(1, min(limit, 500)))


@app.get("/api/family/stories/{story_id}/audio.mp3")
async def story_audio(story_id: str) -> FileResponse:
    story = store.select_family_story(story_id)
    if story is None or story.audio_url is None or not voice_out.clip_path(story.text).is_file():
        raise HTTPException(status_code=404, detail="no clip for that story")
    return FileResponse(voice_out.clip_path(story.text), media_type="audio/mpeg")


@app.post("/api/family/stories/{story_id}/approve", dependencies=[Depends(require_pin)], response_model=FamilyStory)
async def approve_story(story_id: str) -> FamilyStory:
    story = await asyncio.to_thread(runtime.family.approve, story_id)
    if story is None:
        raise HTTPException(status_code=404, detail="no such story")
    if story.status == "sent":
        await _settings_changed()  # first_story_approved may have flipped
        await hub.broadcast(WSMessage(type="family_story_sent", payload=story.model_dump(mode="json")))
    return story


@app.post("/api/family/stories/{story_id}/skip", dependencies=[Depends(require_pin)], response_model=FamilyStory)
async def skip_story(story_id: str) -> FamilyStory:
    story = await asyncio.to_thread(runtime.family.skip, story_id)  # the lock may be held by a send in a worker
    if story is None:
        raise HTTPException(status_code=404, detail="no such story")
    return story


@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await hub.serve(ws)


# The demo panel (step 12): PIN-gated, and every control 404s outside demo mode.
bind_demo(runtime)
app.include_router(demo_router)


# The kiosk page, mounted LAST so /api and /ws win.
if DISPLAY_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(DISPLAY_DIR), html=True), name="display")
