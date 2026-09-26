"""Nightscout datasource: interface-complete stub. The 60 s poller, the
SQLite cache, and monotonic stale marking are day-two work (chris.md step 3)."""

from __future__ import annotations

from datetime import datetime

from ..contracts import Reading
from .base import DataSource


class NightscoutDataSource(DataSource):
    name = "nightscout"

    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token

    async def get_latest(self) -> Reading | None:
        raise NotImplementedError("day-two work: Nightscout poller (chris.md step 3)")

    async def history(self, minutes: int) -> list[Reading]:
        raise NotImplementedError("day-two work: Nightscout poller (chris.md step 3)")

    async def seek(self, to: datetime) -> None:
        raise NotImplementedError("seek is replay-only")
