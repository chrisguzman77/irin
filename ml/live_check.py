"""Watch a running Irin backend (the Pi, or a local one) and check, in real
time, that the forecaster is working. Read-only: it only GETs the device's
API; it never acknowledges, logs, or changes a setting.

Three checks, on every new forecast:
  1. PARITY: recompute the forecast on this laptop from the device's own
     readings (features.latest_window + predict, the exact path forecast.py
     takes) and compare; any difference over 0.001 mg/dL is a MISMATCH
     (different model file, xgboost version, or feature code on the device).
  2. CALIBRATION: forecast_v1 is a 20th-percentile forecast, so the actual
     reading 30 min later should come in AT OR ABOVE the forecast about 80%
     of the time. Well above 80%: too pessimistic (more false alarms); well
     below: too optimistic (missed lows). Plus the mean absolute error.
  3. HEALTH: the share of polls with status ok vs suspended (and why), and
     the alarm episodes seen (warnings, and whether they crossed into lows).
Times are the DEVICE's clock, so it works in replay (demo) and live alike.

Writes one row per forecast to ml/data/live_log.csv (gitignored: live
readings are medical data) and prints summaries only, never raw readings.

  python -m ml.live_check --url http://<pi>:8000 --interval 60        # live, one poll a minute
  python -m ml.live_check --url http://127.0.0.1:8011 --interval 3    # replay at 60x (127.0.0.1: see --url)
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from ml.models.features import HISTORY_MIN, latest_window
from ml.models.predict import PREDICTED_LOW_THRESHOLD, QUANTILE, predict

HORIZON = timedelta(minutes=30)
MATCH_TOL = timedelta(minutes=2, seconds=30)
PARITY_TOL = 1e-3
LOG_FIELDS = ["device_time", "forecast_time", "predicted", "local", "parity_ok", "actual", "status", "datasource"]


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.load(r)


def local_forecast(history: list[dict], at: datetime) -> float | None:
    """What the device should have forecast at `at`: forecast.py's exact path."""
    rows = [r for r in history if datetime.fromisoformat(r["timestamp"]) <= at]
    if not rows:
        return None
    ts = np.array([datetime.fromisoformat(r["timestamp"]).timestamp() for r in rows], dtype=float)
    mgdl = np.array([r["glucose_mgdl"] for r in rows], dtype=float)
    window = latest_window(ts, mgdl)
    if window is None:
        return None
    newest = datetime.fromisoformat(rows[-1]["timestamp"])
    return float(predict(window, newest.hour + newest.minute / 60))


class Tally:
    def __init__(self) -> None:
        self.polls = 0
        self.status = Counter()
        self.reasons = Counter()
        self.parity_checked = self.parity_bad = 0
        self.parity_max = 0.0
        self.resolved: list[tuple[float, float]] = []      # (predicted, actual)
        self.episodes = Counter()

    def summary(self) -> str:
        n = sum(self.status.values()) or 1
        lines = [f"polls {self.polls} | status: " + ", ".join(f"{k} {100 * v / n:.0f}%" for k, v in self.status.most_common())]
        if self.reasons:
            lines.append("  suspended/unavailable because: " + "; ".join(f"{k} ({v})" for k, v in self.reasons.most_common(4)))
        verdict = "OK" if self.parity_bad == 0 else f"{self.parity_bad} MISMATCH(ES)"
        lines.append(f"parity (device vs laptop): {self.parity_checked} forecasts checked, {verdict}, max |diff| {self.parity_max:.4f} mg/dL")
        if self.resolved:
            p = np.array([a for a, _ in self.resolved]); a = np.array([b for _, b in self.resolved])
            above = float((a >= p).mean())
            target = 1 - QUANTILE
            note = ("as trained" if abs(above - target) <= 0.10 else
                    "TOO PESSIMISTIC (expect extra false alarms)" if above > target else "TOO OPTIMISTIC (expect missed lows)")
            if len(p) < 50:
                note += f"; only {len(p)} pairs, too few to judge"
            lines.append(f"calibration: {len(p)} forecasts resolved, actual >= forecast {100 * above:.0f}% "
                         f"(target ~{100 * target:.0f}%: {note}); mean |error| {np.abs(a - p).mean():.1f} mg/dL")
        else:
            lines.append("calibration: no forecast is 30 min old yet")
        if self.episodes:
            lines.append("alarm episodes seen: " + ", ".join(f"{k} {v}" for k, v in self.episodes.items()))
        return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8011", help="use 127.0.0.1, not localhost: on Windows each localhost request stalls ~2 s on IPv6 first")
    p.add_argument("--interval", type=float, default=60.0, help="wall seconds between polls")
    p.add_argument("--duration", type=float, default=0.0, help="wall seconds to run (0 = until Ctrl+C)")
    p.add_argument("--summary-every", type=int, default=10, help="print a summary every N polls")
    p.add_argument("--log", default="ml/data/live_log.csv")
    args = p.parse_args()
    base = args.url.rstrip("/")

    health = get(base, "/api/health")
    print(f"watching {base}: datasource {health.get('datasource')}, hw {health.get('hw')}; "
          f"model = 20th-percentile forecast, warning threshold {PREDICTED_LOW_THRESHOLD:.0f}")
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not log_path.exists()
    logf = log_path.open("a", newline="")
    writer = csv.DictWriter(logf, fieldnames=LOG_FIELDS)
    if new_file:
        writer.writeheader()

    tally = Tally()
    pending: dict[datetime, float] = {}                    # forecast_time -> predicted, awaiting its actual
    seen: set[datetime] = set()
    last_alarm = None
    t_end = time.time() + args.duration if args.duration else None
    try:
        while t_end is None or time.time() < t_end:
            tally.polls += 1
            try:
                health = get(base, "/api/health")
                fc = get(base, "/api/forecast")
                history = get(base, f"/api/history?minutes={HISTORY_MIN + 45}")
                alarm = get(base, "/api/alarm")
            except Exception as e:
                tally.status["device unreachable"] += 1
                tally.reasons[type(e).__name__] += 1
                time.sleep(args.interval)
                continue

            status = fc.get("status", "?")
            tally.status[status] += 1
            if status != "ok" and fc.get("reason"):
                tally.reasons[fc["reason"]] += 1
            f = fc.get("forecast") or {}
            if status == "ok" and f.get("predicted_mgdl") is not None:
                ft = datetime.fromisoformat(f["timestamp"])
                if ft not in seen:
                    seen.add(ft)
                    predicted = float(f["predicted_mgdl"])
                    local = local_forecast(history, ft)
                    ok = local is not None and abs(local - predicted) <= PARITY_TOL
                    tally.parity_checked += 1
                    if local is not None:
                        tally.parity_max = max(tally.parity_max, abs(local - predicted))
                    if not ok:
                        tally.parity_bad += 1
                    pending[ft] = predicted
                    writer.writerow({"device_time": health.get("clock"), "forecast_time": ft.isoformat(),
                                     "predicted": round(predicted, 3), "local": None if local is None else round(local, 3),
                                     "parity_ok": ok, "actual": None, "status": status,
                                     "datasource": health.get("datasource")})

            # resolve forecasts whose 30-min target is now in the device's history
            readings = [(datetime.fromisoformat(r["timestamp"]), r["glucose_mgdl"]) for r in history]
            for ft in sorted(pending):
                target = ft + HORIZON
                if not readings or readings[-1][0] < target - MATCH_TOL:
                    break
                near = [(abs((t - target).total_seconds()), v) for t, v in readings if abs(t - target) <= MATCH_TOL]
                if near:
                    tally.resolved.append((pending[ft], float(min(near)[1])))
                    writer.writerow({"device_time": health.get("clock"), "forecast_time": ft.isoformat(),
                                     "predicted": round(pending[ft], 3), "local": None, "parity_ok": None,
                                     "actual": float(min(near)[1]), "status": "resolved",
                                     "datasource": health.get("datasource")})
                del pending[ft]                            # no reading at +30 (a gap): dropped, never guessed
            logf.flush()

            key = (alarm.get("state"), alarm.get("trigger_type"))
            if key != last_alarm and alarm.get("state") not in (None, "idle"):
                tally.episodes[f"{alarm.get('trigger_type')} {alarm.get('state')}"] += 1
            last_alarm = key

            if tally.polls % args.summary_every == 0:
                print(f"\n[{health.get('clock', '')[:16]} device time]\n{tally.summary()}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        logf.close()
    print(f"\nFINAL\n{tally.summary()}\nlog: {log_path}")


if __name__ == "__main__":
    main()
