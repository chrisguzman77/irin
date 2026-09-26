"""The WebSocket hub. Sends a full state_snapshot on every new connection,
then broadcasts updates. The broadcaster polls the active datasource on a
timer driven by clock.py and emits reading_update when the reading changes.
Day one this is the echo stub; alarm, forecast, presence, and the sponsor
messages join as their modules land."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect

from .clock import clock
from .contracts import AlarmState, Reading, StateSnapshot, WSMessage

if TYPE_CHECKING:
    from .main import Runtime

POLL_CLOCK_SECONDS = 60.0  # one clock minute between polls (1 s of wall time at 60x)


class Hub:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime
        self.clients: set[WebSocket] = set()
        self._last: Reading | None = None
        self._task: asyncio.Task | None = None

    async def snapshot(self) -> StateSnapshot:
        latest = await self.runtime.datasource.get_latest()
        return StateSnapshot(
            latest_reading=latest,
            alarm=self.runtime.alarm.state if self.runtime.alarm else AlarmState(),
            settings=self.runtime.settings,
            mode=self.runtime.mode,
            clock_synced=True,  # the NTP guard (chris.md step 10) is bypassed under mock/replay
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
                reading = await self.runtime.datasource.get_latest()
            except NotImplementedError:
                reading = None
            if reading is not None and reading != self._last:
                self._last = reading
                if self.runtime.alarm is not None:
                    self.runtime.alarm.process_reading(reading)
                await self.broadcast(WSMessage(type="reading_update", payload=reading.model_dump(mode="json")))
            await clock.sleep(POLL_CLOCK_SECONDS)

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
