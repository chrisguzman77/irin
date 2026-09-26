"""C1: POST /v1/ingest {device_id, readings[], alarm_events[], low_events[],
treatments[]}: validates the device token, upserts on (device_id, timestamp)
so a retry never duplicates, writes to the hypertables with psycopg."""


def ingest(batch: dict) -> dict:
    raise NotImplementedError("C1: ingest")
