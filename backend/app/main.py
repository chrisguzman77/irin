"""Irin backend. Boots with IRIN_HW=mock and DATASOURCE=replay on any laptop.

Serves ../frontend/display at / (the kiosk page). The phone app is NOT
served here (it is hosted, decisions 27/28); CORS allows APP_ORIGIN and
Vite's dev server. Nothing here imports rounds/ or buddy/."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .alarm import AlarmEngine, Transition
from .auth import require_pin
from .clock import clock
from .config import REPO_ROOT, config
from .contracts import FRESH_PIN_ENDPOINTS, AlarmState, Reading, Settings, WSMessage
from .datasource.base import DataSource
from .datasource.nightscout import NightscoutDataSource
from .datasource.replay import ReplayDataSource
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


runtime = Runtime(mode="nightscout" if config.DATASOURCE == "nightscout" else "replay",
                  datasource=make_datasource("nightscout" if config.DATASOURCE == "nightscout" else "replay"))
hub = Hub(runtime)
runtime.hub = hub
runtime.alarm = AlarmEngine(runtime.settings)

ALARM_TICK_CLOCK_SECONDS = 30.0


def _broadcast_transition(t: Transition) -> None:
    """The ws observer on alarm.py's hook: every transition becomes alarm_state_change."""
    payload = {**runtime.alarm.state.model_dump(mode="json"), "old_state": t.old_state,
               "escalated": t.escalated, "ack_source": t.ack_source}
    try:
        asyncio.get_running_loop().create_task(hub.broadcast(WSMessage(type="alarm_state_change", payload=payload)))
    except RuntimeError:  # no running loop (a synchronous test driving the engine directly)
        pass


runtime.alarm.on_transition(_broadcast_transition)


async def _alarm_tick_loop() -> None:
    while True:
        runtime.alarm.tick()
        await clock.sleep(ALARM_TICK_CLOCK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    await runtime.datasource.start()
    hub.start()
    tick_task = asyncio.create_task(_alarm_tick_loop())
    yield
    tick_task.cancel()
    try:
        await tick_task
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


@app.get("/api/alarm", response_model=AlarmState)
async def alarm_state() -> AlarmState:
    return runtime.alarm.state


@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await hub.serve(ws)


# The kiosk page, mounted LAST so /api and /ws win.
if DISPLAY_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(DISPLAY_DIR), html=True), name="display")
