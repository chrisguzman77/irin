"""The demo panel (chris.md step 12): the controls below the LIVE/DEMO switch.
All PIN-gated, and ALL demo-only: in live mode every one of them 404s, so
nothing can inject into live data. Injections are an overlay; the scenario
CSV replays clean. Treatments logged in demo mode are stored locally and
never posted to the real Nightscout. While replay is active every screen
badges DEMO (invariant 1); the badge is driven by the mode in the snapshot
and the mode_change message.

The sponsor controls (send card Bedside | Brain-only, brain_only toggle,
simulate Spark offer, start watch, jump to step N day D, trigger buddy rung)
join this router at R12/R14/B5.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import require_pin
from .clock import clock
from .config import REPO_ROOT
from .contracts import WSMessage
from .datasource.replay import ReplayDataSource

SCENARIOS_DIR = REPO_ROOT / "demo" / "scenarios"

router = APIRouter(prefix="/api/demo", tags=["demo"])
_runtime = None  # set by main.py: the Runtime (mode, datasource, settings, alarm, hub)
_live_basal_time: tuple[str | None] | None = None  # the real basal_time while the demo button overrides it


def bind(runtime) -> None:
    global _runtime
    _runtime = runtime


def live_basal_time() -> tuple[str | None] | None:
    """The stashed live basal_time while the demo button overrides it (None otherwise),
    so persistence writes the live value, never the demo one."""
    return _live_basal_time


def restore_live_settings() -> bool:
    """Called by the mode switch on the way back to live: the basal-time button's
    value never reaches the live nudge ladder. Returns True when something changed."""
    global _live_basal_time
    if _live_basal_time is None:
        return False
    (_runtime.settings.basal_time,), _live_basal_time = _live_basal_time, None
    return True


def require_demo() -> None:
    """Demo-only: 404 in live mode, so the controls cannot touch live data."""
    if _runtime is None or _runtime.mode != "replay" or not isinstance(_runtime.datasource, ReplayDataSource):
        raise HTTPException(status_code=404, detail="demo controls exist only in demo mode")


def _replay() -> ReplayDataSource:
    return _runtime.datasource  # type: ignore[return-value]


async def _broadcast(msg_type: str, payload: dict) -> None:
    if _runtime is not None and _runtime.hub is not None:
        await _runtime.hub.broadcast(WSMessage(type=msg_type, payload=payload))


@router.get("/scenarios")
async def list_scenarios() -> dict:
    """Read-only, any mode: the CSVs a demo may play."""
    names = sorted(p.stem for p in SCENARIOS_DIR.glob("*.csv"))
    current = Path(_runtime.datasource.path).stem if _runtime and isinstance(_runtime.datasource, ReplayDataSource) else None
    return {"scenarios": names, "current": current, "speed": clock.speed}


class ScenarioRequest(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")


@router.post("/scenario", dependencies=[Depends(require_pin), Depends(require_demo)])
async def select_scenario(req: ScenarioRequest) -> dict:
    """Play another scenario CSV from its first row (clock reset to its start)."""
    path = SCENARIOS_DIR / f"{req.name}.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no scenario named {req.name}")
    new = ReplayDataSource(path, speed=clock.speed)
    await _runtime.datasource.stop()
    _runtime.datasource = new
    await new.start()
    _runtime.alarm.reset()
    _runtime.forecaster.reset()
    _runtime.voice.reset()
    _runtime.hub._last = None
    await _broadcast("mode_change", {"mode": "replay", "scenario": req.name})
    return {"scenario": req.name, "speed": clock.speed}


class SpeedRequest(BaseModel):
    speed: float = Field(gt=0, le=600)


@router.post("/speed", dependencies=[Depends(require_pin), Depends(require_demo)])
async def set_speed(req: SpeedRequest) -> dict:
    """Change the replay multiplier without moving the clock."""
    clock.set(speed=req.speed, start=clock.now())
    _replay().speed = req.speed
    return {"speed": clock.speed}


class PauseRequest(BaseModel):
    paused: bool


@router.post("/pause", dependencies=[Depends(require_pin), Depends(require_demo)])
async def pause_feed(req: PauseRequest) -> dict:
    """Pause the FEED (the sensor stops), not the clock: after 15 clock
    minutes the reading is shown stale, the honest outcome."""
    _replay().set_paused(req.paused)
    return {"paused": req.paused}


class InjectRequest(BaseModel):
    glucose_mgdl: float = Field(ge=39, le=401)
    trend: str = Field(default="SingleDown", pattern=r"^[A-Za-z]{1,20}$")  # a Nightscout direction name


@router.post("/inject_low", dependencies=[Depends(require_pin), Depends(require_demo)])
async def inject_low(req: InjectRequest) -> dict:
    """Overlay one reading at the current clock time (at the pause moment while
    paused, so it shows at once); the CSV stays clean."""
    r = _replay().inject(req.glucose_mgdl, req.trend)
    return {"injected": r.model_dump(mode="json")}


@router.post("/basal_time", dependencies=[Depends(require_pin), Depends(require_demo)])
async def basal_time_button() -> dict:
    """Set basal_time to 61 clock minutes ago so the basal nudge shows at once.
    The live value is stashed and restored on the switch back to live."""
    global _live_basal_time
    t = (clock.now() - timedelta(minutes=61)).strftime("%H:%M")
    if _live_basal_time is None:
        _live_basal_time = (_runtime.settings.basal_time,)
    _runtime.settings.basal_time = t
    await _broadcast("settings_change", _runtime.settings.model_dump(mode="json"))
    return {"basal_time": t}
