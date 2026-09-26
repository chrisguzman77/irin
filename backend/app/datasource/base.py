"""The DataSource interface. Replay and Nightscout sit behind the same
interface so POST /api/mode swaps one reference (chris.md step 12)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..contracts import Reading

STALE_AFTER_MIN = 15  # no new reading for 15+ clock minutes = stale


class DataSource(ABC):
    name: str

    @abstractmethod
    async def get_latest(self) -> Reading | None:
        """The most recent reading, is_stale set honestly. None before the first."""

    @abstractmethod
    async def history(self, minutes: int) -> list[Reading]:
        """Readings from the last `minutes` of clock time, oldest first."""

    @abstractmethod
    async def seek(self, to: datetime) -> None:
        """Replay only (R12). Nightscout raises NotImplementedError."""

    async def start(self) -> None:  # noqa: B027 - optional hook
        """Called when the source becomes active (start polling, set the clock)."""

    async def stop(self) -> None:  # noqa: B027 - optional hook
        """Called when the source is swapped out."""
