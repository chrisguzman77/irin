"""R7: the relay client. Outbound only (no new open port on the Pi): POST
sealed cards, poll GET /v0/device/{device_id}/messages every 60 s live and 5 s
in demo on clock.py (doctor messages, owner-pairing completions and
revocations, brokered buddy calls), POST resolutions. X-Source-Key on every
call."""

from __future__ import annotations


class RelayClient:
    async def poll(self) -> None:
        raise NotImplementedError("R7: relay client")
