"""george.md step 9.4 (C5): load the cleaned history (ml/data/clean.csv) into
Irin Cloud's `readings` hypertable under Chris's device id, so the My Irin
dashboards have the real 22 months behind them. Run ONCE, from the laptop
where the data lives: never from the Pi, never from the repo.

This uploads personal medical data to a cloud database, so it is a
deliberate, human-authorized step: without --yes it only prints what it
would do (a dry run). Idempotent: rows go through a temp table and
INSERT ... ON CONFLICT (device_id, time) DO NOTHING, so a rerun adds
nothing. Not demo data (is_demo false); source "clarity". Needs the tables:
run cloud/migrate.py against TIGER_URI first. After loading, the continuous
aggregates are refreshed once over the whole history. Prints counts only.

  python -m ml.load_history --device-id <DEVICE_ID from .env>          # dry run
  python -m ml.load_history --device-id <DEVICE_ID from .env> --yes    # upload"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ml.models.features import drop_collisions

ROOT = Path(__file__).resolve().parents[1]
CAGGS = ("daily_stats", "overnight_profile", "hourly_heatmap", "alarms_weekly")


def env_value(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def prepare(clean: pd.DataFrame) -> pd.DataFrame:
    """Sorted, collision-free readings (the same readings everything else in
    ml/ uses), as the readings table's columns minus device_id."""
    clean = clean.sort_values("timestamp", ignore_index=True)
    keep = drop_collisions(clean.timestamp.to_numpy("datetime64[s]").astype(np.int64))
    out = clean[keep]
    return pd.DataFrame({"time": out.timestamp.to_numpy(), "mgdl": out.mgdl.astype(float).to_numpy()})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="ml/data")
    p.add_argument("--device-id", default=env_value("DEVICE_ID"))
    p.add_argument("--yes", action="store_true", help="actually upload (otherwise a dry run)")
    args = p.parse_args()
    if not args.device_id:
        raise SystemExit("no device id: pass --device-id or set DEVICE_ID in .env")
    rows = prepare(pd.read_csv(Path(args.data) / "clean.csv", parse_dates=["timestamp"]))
    print(f"history: {len(rows)} readings, {rows.time.min():%Y-%m} .. {rows.time.max():%Y-%m}, device {args.device_id}")
    if not args.yes:
        print("dry run: nothing uploaded (add --yes to load)")
        return

    import psycopg

    uri = env_value("TIGER_URI")
    if not uri:
        raise SystemExit("TIGER_URI not set")
    t0 = time.perf_counter()
    with psycopg.connect(uri, connect_timeout=15, autocommit=True) as conn:
        before = conn.execute("SELECT count(*) FROM readings WHERE device_id = %s", (args.device_id,)).fetchone()[0]
        with conn.transaction():
            conn.execute("CREATE TEMP TABLE load_readings (time timestamp, mgdl real) ON COMMIT DROP")
            with conn.cursor() as cur, cur.copy("COPY load_readings (time, mgdl) FROM STDIN") as cp:
                for t, v in zip(rows.time, rows.mgdl):
                    cp.write_row((t.to_pydatetime(), float(v)))
            conn.execute(
                "INSERT INTO readings (device_id, time, mgdl, trend, source, is_demo)"
                " SELECT %s, time, mgdl, NULL, 'clarity', false FROM load_readings"
                " ON CONFLICT (device_id, time) DO NOTHING", (args.device_id,))
        after = conn.execute("SELECT count(*) FROM readings WHERE device_id = %s", (args.device_id,)).fetchone()[0]
        for view in CAGGS:
            conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")
    print(f"uploaded: {after - before} new rows ({after} total for the device); aggregates refreshed"
          f"  ({time.perf_counter() - t0:.0f} s)")


if __name__ == "__main__":
    main()
