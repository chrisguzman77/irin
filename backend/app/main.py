"""Irin backend. Boots with IRIN_HW=mock and DATASOURCE=replay on any laptop.

Serves ../frontend/display at / (the kiosk page). The phone app is NOT
served here (it is hosted, decisions 27/28); CORS allows APP_ORIGIN and
Vite's dev server. Nothing here imports rounds/ or buddy/."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
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
from .auth import require_pin
from .clock import clock
from .config import REPO_ROOT, config
from .contracts import FRESH_PIN_ENDPOINTS, AlarmState, MorningReport, Reading, Settings, Treatment, WSMessage
from .datasource.base import DataSource
from .datasource.nightscout import NightscoutDataSource
from .datasource.replay import ReplayDataSource
from .forecast import Forecaster
from .outputs import GatedOutputs
from .presence import PresenceMachine, PresenceState
from .reports import ReportBuilder, SmtpMailer
from .scheduler import Scheduler, basal_logged_on, timedatectl_synced
from .voice import MAX_CARBS_G, MAX_UNITS, VoiceLogger
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
runtime.voice = VoiceLogger()


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
                                mailer=SmtpMailer() if config.SMTP_HOST else None, settings=runtime.settings)


def _build_report(night_date: date, scheduled: bool = False) -> MorningReport | None:
    """The datasource is read ONCE: is_demo and the readings come from the same
    source, so a mode switch mid-build can never email scenario rows as a real
    night (invariant 1). The scheduled job never rebuilds a night that already
    has a report (a restart inside the catch-up window would re-email it);
    POST /api/reports/build always rebuilds."""
    ds = runtime.datasource
    if scheduled and store.select_report(night_date) is not None:
        logging.getLogger("irin.main").info("morning report for %s already exists; not rebuilt", night_date)
        return None
    builder = dataclasses.replace(runtime.reports, readings_for=lambda a, b: _readings_from(ds, a, b))
    try:
        return builder.build(night_date, is_demo=isinstance(ds, ReplayDataSource))
    except Exception:
        logging.getLogger("irin.main").exception("morning report build failed")
        return None


def _morning_report_job(night_date: date) -> None:
    """The scheduler job at night-window end: the build runs in a worker thread."""
    task = asyncio.get_running_loop().create_task(asyncio.to_thread(_build_report, night_date, True))
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


runtime.scheduler.register("morning_report", runtime.settings.night_window_end, _morning_report_job)

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


def _broadcast_presence(state: PresenceState) -> None:
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
            runtime.presence.sample(runtime.outputs.get_presence())
        except Exception:
            logging.getLogger("irin.main").exception("tick failed; continuing")
        await clock.sleep(ALARM_TICK_CLOCK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    await runtime.datasource.start()
    hub.start()
    tick_task = asyncio.create_task(_alarm_tick_loop())
    sched_task = asyncio.create_task(runtime.scheduler.run())
    yield
    for task in (tick_task, sched_task):
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
    runtime.forecaster.reset()
    runtime.voice.reset()
    hub._last = None
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
    store.insert_treatment(row)
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
    await hub.broadcast(WSMessage(type="settings_change", payload=runtime.settings.model_dump(mode="json")))
    return state


@app.get("/api/presence", response_model=PresenceState)
async def presence_state() -> PresenceState:
    return runtime.presence.state


@app.get("/api/scheduler")
async def scheduler_state() -> dict:
    """clock_synced (the NTP guard), display_mode (detail | night | morning, from
    backend state), and the basal nudge (none | visual | email)."""
    sch = runtime.scheduler
    return {"clock_synced": sch.clock_synced, "display_mode": sch.display_mode(),
            "basal_nudge": {"level": sch.nudge.level, "since": sch.nudge.since.isoformat() if sch.nudge.since else None},
            "jobs": [{"name": j.name, "at": j.at, "last_fired": j.last_fired.isoformat() if j.last_fired else None}
                     for j in sch.jobs]}


@app.get("/api/alarm", response_model=AlarmState)
async def alarm_state() -> AlarmState:
    return runtime.alarm.state


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
    report = await asyncio.to_thread(_build_report, req.night_date or clock.now().date())
    if report is None:
        raise HTTPException(status_code=500, detail="report build failed; see the log")
    return report


@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await hub.serve(ws)


# The kiosk page, mounted LAST so /api and /ws win.
if DISPLAY_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(DISPLAY_DIR), html=True), name="display")
