"""Irin backend. Boots with IRIN_HW=mock and DATASOURCE=replay on any laptop.

Serves ../frontend/display at / (the kiosk page). The phone app is NOT
served here (it is hosted, decisions 27/28); CORS allows APP_ORIGIN and
Vite's dev server. Nothing here imports rounds/ or buddy/."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .auth import require_pin
from .clock import clock
from .config import REPO_ROOT, config
from .contracts import FRESH_PIN_ENDPOINTS, Reading, Settings, WSMessage
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


runtime = Runtime(mode="nightscout" if config.DATASOURCE == "nightscout" else "replay",
                  datasource=make_datasource("nightscout" if config.DATASOURCE == "nightscout" else "replay"))
hub = Hub(runtime)
runtime.hub = hub


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    await runtime.datasource.start()
    hub.start()
    yield
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
    await hub.broadcast(WSMessage(type="mode_change", payload={"mode": runtime.mode}))
    return {"mode": runtime.mode, "changed": True}


@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await hub.serve(ws)


# The kiosk page, mounted LAST so /api and /ws win.
if DISPLAY_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(DISPLAY_DIR), html=True), name="display")
