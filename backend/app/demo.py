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

from datetime import date as date_type, datetime, time, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import require_pin
from .clock import clock
from . import store
from .config import REPO_ROOT, config
from .contracts import WSMessage
from .datasource.replay import ReplayDataSource
from .rounds.catchup import Companion

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
    replay = _runtime.datasource if _runtime and isinstance(_runtime.datasource, ReplayDataSource) else None
    return {"scenarios": names, "current": Path(replay.path).stem if replay else None, "speed": clock.speed,
            "paused": bool(replay is not None and replay._paused_at is not None),
            "brain_only": bool(config.IRIN_BRAIN_ONLY), "clock": clock.now().isoformat(),
            "companion": Companion.load(replay.path).kind if replay and Companion.load(replay.path) else None}


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
    if getattr(_runtime, "alarm_events", None) is not None:
        _runtime.alarm_events.reset()
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


class SendCardRequest(BaseModel):
    fixture: str = Field(default="signal_card_standing", pattern=r"^signal_card_(standing|step)$")


@router.post("/send_card", dependencies=[Depends(require_pin), Depends(require_demo)])
async def send_fixture_card(req: SendCardRequest) -> dict:
    """R7: seal a fixture card (SYNTHETIC, badged DEMO) to every paired demo
    peer, so the inbox shell has something to list before R8 evaluates real
    nights. The sponsor controls (Bedside | Brain-only, Spark, jump) join at R12/R14."""
    import json as _json

    from .config import BACKEND_DIR
    from .contracts import SignalCard

    card = SignalCard.model_validate(_json.loads((BACKEND_DIR / "tests" / "fixtures" / f"{req.fixture}.json").read_text()))
    from .rounds.cards import pseudonym

    card = card.model_copy(update={"is_demo": True, "generated_at": clock.now(),
                                   "patient_pseudonym": pseudonym(_runtime.cards.device_id)})
    return await _runtime.cards.send(card)


# --- R12: the seek and the sponsor-tier controls (demo-only, PIN) ---


class SeekRequest(BaseModel):
    """Jump to step N (1-based) day D of the scenario's plan, or to day D of the
    scenario, or to a date; the clock lands at 09:00 of that day, after the
    morning's cards and before the recall questions close at noon."""

    step: int | None = Field(default=None, ge=1, le=20)
    day: int | None = Field(default=None, ge=1, le=400)
    date: date_type | None = None


def _seek_target(req: SeekRequest, replay: ReplayDataSource, comp) -> datetime:
    first, last = replay.span
    if req.date is not None:
        day = req.date
    elif req.step is not None:
        plan = comp.plan if comp is not None and comp.plan is not None else None
        if plan is None:
            raise HTTPException(status_code=409, detail="this scenario has no plan to seek by step")
        if req.step > len(plan.steps):
            raise HTTPException(status_code=422, detail=f"the plan has {len(plan.steps)} steps")
        day = plan.steps[req.step - 1].planned_start + timedelta(days=(req.day or 1) - 1)
    elif req.day is not None:
        day = first.date() + timedelta(days=req.day - 1)
    else:
        raise HTTPException(status_code=422, detail="give step + day, day, or date")
    to = datetime.combine(day, time(9, 0))
    if not (first <= to <= last):
        raise HTTPException(status_code=422, detail=f"{to.isoformat()} is outside the scenario ({first.date()} to {last.date()})")
    return to


@router.post("/seek", dependencies=[Depends(require_pin), Depends(require_demo)])
async def seek(req: SeekRequest) -> dict:
    """Jump the replay forward; the catch-up generates every night, question and
    card that should exist by then (idempotent: seeking twice sends nothing twice)."""
    replay = _replay()
    comp = Companion.load(replay.path)
    to = _seek_target(req, replay, comp)
    if to < clock.now():
        raise HTTPException(status_code=409, detail="the replay is already past that point; play the scenario from the start first")
    await replay.seek(to)
    _runtime.alarm.reset()  # the skipped readings never alarmed; the engine meets the new present cold
    if getattr(_runtime, "alarm_events", None) is not None:
        _runtime.alarm_events.reset()
    _runtime.forecaster.reset()
    _runtime.voice.reset()
    summary = await _runtime.catchup.run(replay.span[0], to, comp)
    if _runtime.scheduler is not None:
        for job in _runtime.scheduler.jobs:  # today's jobs already ran inside the catch-up
            job.last_fired = to.date()
    await _broadcast("mode_change", {"mode": "replay", "scenario": Path(replay.path).stem, "seek": to.isoformat()})
    return {"scenario": Path(replay.path).stem, "clock": clock.now().isoformat(), **summary}


class BrainOnlyRequest(BaseModel):
    brain_only: bool


@router.post("/brain_only", dependencies=[Depends(require_pin), Depends(require_demo)])
async def set_brain_only(req: BrainOnlyRequest) -> dict:
    """Irin Brain only: the Rounds adapter ignores presence, alarm hardware events
    and logged context; card rows change confidence label, never blank."""
    config.IRIN_BRAIN_ONLY = req.brain_only
    return {"brain_only": config.IRIN_BRAIN_ONLY}


SPARK_ID = "impiricus-spark"


@router.post("/spark_offer", dependencies=[Depends(require_pin), Depends(require_demo)])
async def spark_offer() -> dict:
    """Simulate Impiricus Spark offering the scenario's plan: a plan_create message
    from a simulated, demo-only peer, pending until the patient confirms it with
    a FRESH PIN like any doctor message (invariant 8). The peer's key is the
    device's own, so what is sealed to it never leaves the device readable."""
    from .contracts import DoctorMessage, Pairing

    comp = Companion.load(_replay().path)
    plan = comp.plan if comp is not None and comp.plan is not None else None
    if plan is None:
        raise HTTPException(status_code=409, detail="this scenario has no plan to offer")
    pairing = _runtime.pairing
    if SPARK_ID not in pairing.pairings or pairing.pairings[SPARK_ID].status != "paired":
        peer = Pairing(device_id=pairing.device_id, doctor_id=SPARK_ID, doctor_display_name="Impiricus Spark (simulated)",
                       doctor_pk=pairing.device_pk, status="paired", peer_kind="doctor", is_demo=True)
        pairing.pairings[SPARK_ID] = peer
        store.upsert_pairing(peer)
    now = clock.now()
    msg = DoctorMessage(message_id=f"spark-{plan.plan_id}-{now.date().isoformat()}", plan_id=plan.plan_id, kind="plan_create",
                        plan=plan.model_copy(update={"status": "pending_confirm"}), created_at=now,
                        text="Spark suggests a Step Watch for this plan (simulated offer)")
    if store.select_doctor_message(msg.message_id) is None:
        from .rounds.messages import EXPIRY

        doc = {"message": msg.model_dump(mode="json"), "sender_id": SPARK_ID, "doctor_display_name": "Impiricus Spark (simulated)",
               "is_demo": True, "received_at": now.isoformat(), "expires_at": (now + EXPIRY).isoformat(), "resolution_posted": True}
        store.upsert_doctor_message(doc)
        await _broadcast("doctor_message_received", {**doc, "status": "pending"})
    return {"message_id": msg.message_id, "plan_id": plan.plan_id, "status": "pending"}


@router.post("/buddy_rung", dependencies=[Depends(require_pin), Depends(require_demo)])
async def buddy_rung() -> dict:
    raise HTTPException(status_code=404, detail="the buddy rung arrives with the Night Buddy tier")


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
