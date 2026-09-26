"""R7: the relay client. Outbound only (no new open port on the Pi): POST
sealed cards, poll GET /v0/device/{device_id}/messages every 60 s live and
5 s in demo on clock.py (doctor messages, pairing revocations from the
inbox, later owner-pairing completions and brokered buddy calls), POST
resolutions. X-Source-Key on every call. Every failure is logged and
retried next tick; nothing here can take the device down (the Pi works
with no relay at all)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from ..clock import clock

log = logging.getLogger("irin.rounds.relay_client")

POLL_LIVE_S = 60.0  # WALL seconds: a relay is a real server, so the cadence is scaled by clock.speed
POLL_DEMO_S = 5.0


@dataclass
class RelayClient:
    relay_url: str
    source_key: str
    device_id: str
    is_demo: Callable[[], bool] = lambda: False
    transport: httpx.AsyncBaseTransport | None = None
    on_messages: Callable[[list[dict]], Any] | None = None  # R9 consumes doctor messages
    on_pairings: Callable[[list[dict]], Any] | None = None  # R5: revocations from the inbox
    on_tick: Callable[[], Any] | None = None  # R7: retry unsent cards
    polls: int = 0
    failures: int = 0
    last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.relay_url and self.source_key and self.device_id)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.relay_url.rstrip("/"), headers={"X-Source-Key": self.source_key},
                                 timeout=10.0, transport=self.transport)

    async def post_card(self, envelope: dict[str, Any]) -> bool:
        """True when the relay stored it. False (logged) on any failure."""
        if not self.enabled:
            return False
        try:
            async with self._client() as c:
                r = await c.post("/v0/cards", json=envelope)
            if r.status_code != 200:
                self.last_error = f"cards: {r.status_code} {r.text[:120]}"
                log.warning("relay refused a card: %s", self.last_error)
                return False
            return True
        except httpx.HTTPError as e:
            self.failures += 1
            self.last_error = f"cards: {type(e).__name__}"
            log.warning("relay unreachable for a card (%s)", type(e).__name__)
            return False

    async def post_resolution(self, message_id: str, status: str) -> bool:
        if not self.enabled:
            return False
        try:
            async with self._client() as c:
                r = await c.post(f"/v0/messages/{message_id}/resolution", json={"status": status})
            return r.status_code == 200
        except httpx.HTTPError as e:
            self.failures += 1
            self.last_error = f"resolution: {type(e).__name__}"
            return False

    async def poll(self) -> dict[str, Any] | None:
        """One poll; hands messages and pairing states to the observers."""
        if not self.enabled:
            return None
        try:
            async with self._client() as c:
                r = await c.get(f"/v0/device/{self.device_id}/messages")
            if r.status_code != 200:
                raise RuntimeError(f"relay answered {r.status_code}")
            body = r.json()
        except Exception as e:
            self.failures += 1
            self.last_error = f"poll: {type(e).__name__}"
            log.warning("relay poll failed (%s)", type(e).__name__)
            return None
        self.polls += 1
        self.last_error = None
        for observer, key in ((self.on_pairings, "pairings"), (self.on_messages, "messages")):
            if observer is not None and body.get(key):
                try:
                    result = observer(body[key])
                    if hasattr(result, "__await__"):
                        await result
                except Exception:
                    log.exception("relay %s observer failed", key)
        return body

    async def run(self) -> None:
        if not self.enabled:
            log.info("relay client disabled (RELAY_URL, RELAY_SOURCE_KEY, or DEVICE_ID unset)")
            return
        while True:
            try:
                await self.poll()
                if self.on_tick is not None:
                    result = self.on_tick()
                    if hasattr(result, "__await__"):
                        await result
            except Exception:
                log.exception("relay client tick raised")
            await clock.sleep((POLL_DEMO_S if self.is_demo() else POLL_LIVE_S) * clock.speed)  # wall seconds

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "polls": self.polls, "failures": self.failures, "last_error": self.last_error}
