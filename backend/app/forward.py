"""C1: the outbound forwarder to Irin Cloud. Every 5 minutes on clock.py,
POST {CLOUD_URL}/v1/ingest {device_id, readings[], alarm_events[],
low_events[], treatments[]} with everything new since the last acknowledged
batch: outbound only, batched, never blocking the poller or an alarm; on
failure it keeps its cursor and retries next tick (the Pi works with no
cloud at all, invariant 21). Demo readings carry is_demo and land in a
separate demo device_id."""

from __future__ import annotations


class Forwarder:
    async def tick(self) -> None:
        raise NotImplementedError("C1: batched outbound forwarder")
