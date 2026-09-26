"""The WebSocket hub. Sends a full state_snapshot on every new connection,
then broadcasts updates. The broadcaster polls the active datasource on a
timer driven by clock.py and emits reading_update when the reading changes.
Day one this is the echo stub; alarm, forecast, presence, and the sponsor
messages join as their modules land."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect

from .clock import clock
from .contracts import AlarmState, Reading, StateSnapshot, WSMessage
from .forecast import HISTORY_MINUTES

if TYPE_CHECKING:
    from .main import Runtime

log = logging.getLogger("irin.hub")
POLL_CLOCK_SECONDS = 60.0  # one clock minute between polls (1 s of wall time at 60x)


class Hub:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime
        self.clients: set[WebSocket] = set()
        self._last: Reading | None = None
        self._task: asyncio.Task | None = None

    async def snapshot(self) -> StateSnapshot:
        latest = await self.runtime.datasource.get_latest()
        fc = self.runtime.forecaster
        return StateSnapshot(
            latest_reading=latest,
            forecast=fc.last.forecast if fc and latest is not None and not latest.is_stale else None,
            alarm=self.runtime.alarm.state if self.runtime.alarm else AlarmState(),
            presence=self.runtime.presence.state if self.runtime.presence else None,
            settings=self.runtime.settings,
            mode=self.runtime.mode,
            clock_synced=self.runtime.scheduler.clock_synced if self.runtime.scheduler else True,
        )

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        snap = await self.snapshot()
        await ws.send_text(WSMessage(type="state_snapshot", payload=snap.model_dump(mode="json")).model_dump_json())

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, msg: WSMessage) -> None:
        text = msg.model_dump_json()
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def serve(self, ws: WebSocket) -> None:
        """Echo stub: accept, snapshot, then echo any client message back."""
        await self.connect(ws)
        try:
            while True:
                text = await ws.receive_text()
                try:
                    data: Any = json.loads(text)
                except json.JSONDecodeError:
                    data = {"raw": text}
                await ws.send_text(json.dumps({"echo": data}))
        except WebSocketDisconnect:
            self.disconnect(ws)

    # --- the timer ---

    async def _loop(self) -> None:
        while True:
            try:
                await self._poll()
            except Exception:  # the loop must outlive any one bad poll: alarms depend on it
                log.exception("hub poll failed; continuing")
            await clock.sleep(POLL_CLOCK_SECONDS)

    async def _poll(self) -> None:
        try:
            reading = await self.runtime.datasource.get_latest()
        except NotImplementedError:
            reading = None
        if reading is not None and reading != self._last:
            self._last = reading
            if self.runtime.alarm is not None:
                self.runtime.alarm.process_reading(reading)
            await self.broadcast(WSMessage(type="reading_update", payload=reading.model_dump(mode="json")))
            await self._forecast(reading)

    async def _forecast(self, reading: Reading) -> None:
        """Step 6: forecast on every new (or newly stale) reading; suspended on stale
        or gapped data. The polled reading's is_stale is the staleness verdict
        (history rows never carry it). Payload: ForecastResult.payload()."""
        fc = self.runtime.forecaster
        if fc is None:
            return
        if reading.is_stale:
            history: list[Reading] = []
        else:
            try:
                history = await self.runtime.datasource.history(HISTORY_MINUTES)
            except NotImplementedError:
                history = []
        result = fc.forecast(history, latest=reading)
        if self.runtime.alarm is not None:
            if result.forecast is not None:
                self.runtime.alarm.process_forecast(result.forecast)
            else:
                self.runtime.alarm.process_no_forecast()
        await self.broadcast(WSMessage(type="forecast_update", payload=result.payload(at=reading.timestamp)))

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
