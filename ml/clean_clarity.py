"""george.md step 1: parse only Event Type == EGV; concatenate all files and dedupe on timestamp FIRST; map Low -> 39 and High -> 401; the spike filter (drop x(t) when |x(t)-x(t-1)| > 30 AND |x(t+1)-x(t)| > 30 with opposite signs; the dropped reading becomes a hole, never interpolate); never auto-delete suspicious lows; gap = any interval > 30 min. Prints the cleaned count and the gap list only."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

TS_COL = "Timestamp (YYYY-MM-DDThh:mm:ss)"
GLUCOSE_COL = "Glucose Value (mg/dL)"
SENSOR_CAPS = {"Low": 39, "High": 401}
SPIKE_MGDL = 30
NEIGHBOR_MIN = 10   # a spike's neighbours must be adjacent readings, not across a hole
GAP_MIN = 30
LONG_GAP_H = 2      # gaps at least this long are listed one by one; shorter ones are counted


def read_egv(path: Path) -> tuple[pd.DataFrame, dict]:
    """One Clarity export -> (timestamp, mgdl) for EGV rows only, plus per-file counts."""
    raw = pd.read_csv(path, low_memory=False)
    raw.columns = raw.columns.str.strip()
    egv = raw[raw["Event Type"] == "EGV"]
    text = egv[GLUCOSE_COL].astype(str).str.strip()
    mgdl = pd.to_numeric(text.replace(SENSOR_CAPS), errors="coerce")
    out = pd.DataFrame({"timestamp": pd.to_datetime(egv[TS_COL], errors="coerce"), "mgdl": mgdl})
    bad = out.isna().any(axis=1)
    counts = {
        "rows": len(raw),
        "egv": len(egv),
        "low": int((text == "Low").sum()),
        "high": int((text == "High").sum()),
        "unparseable": int(bad.sum()),
    }
    return out[~bad], counts


def spike_mask(ts: pd.Series, mgdl: pd.Series) -> pd.Series:
    """True where x(t) is a single-reading spike: both neighbouring moves exceed 30 mg/dL
    with opposite signs. A continuing crash (same sign) never fires. Expects sorted input."""
    before = mgdl - mgdl.shift(1)
    after = mgdl.shift(-1) - mgdl
    dt_before = (ts - ts.shift(1)).dt.total_seconds() / 60
    dt_after = (ts.shift(-1) - ts).dt.total_seconds() / 60
    adjacent = (dt_before <= NEIGHBOR_MIN) & (dt_after <= NEIGHBOR_MIN)
    return (
        adjacent
        & (before.abs() > SPIKE_MGDL)
        & (after.abs() > SPIKE_MGDL)
        & (np.sign(before) != np.sign(after))
    )


def find_gaps(ts: pd.Series) -> pd.DataFrame:
    """Every interval between consecutive readings longer than GAP_MIN."""
    step = ts.diff()
    is_gap = step > pd.Timedelta(minutes=GAP_MIN)
    return pd.DataFrame({"start": ts.shift(1)[is_gap], "end": ts[is_gap], "length": step[is_gap]})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    args = p.parse_args()
    data = Path(args.data)
    files = sorted((data / "raw").glob("*.csv"))
    if not files:
        raise SystemExit(f"no CSVs in {data / 'raw'}")

    parts = []
    print("FILES")
    for f in files:
        df, c = read_egv(f)
        parts.append(df)
        print(f"  {f.name}: rows={c['rows']} egv={c['egv']} Low={c['low']} High={c['high']} unparseable={c['unparseable']}")

    all_egv = pd.concat(parts, ignore_index=True)
    deduped = all_egv.drop_duplicates("timestamp").sort_values("timestamp", ignore_index=True)
    print(f"\nEGV rows across files: {len(all_egv)}")
    print(f"after dedupe on timestamp: {len(deduped)} (dropped {len(all_egv) - len(deduped)} overlap rows)")
    conflicts = all_egv.groupby("timestamp")["mgdl"].nunique().gt(1).sum()
    print(f"overlap timestamps whose files disagree on the value: {conflicts}")
    print(f"sensor caps kept: Low->39 x{(deduped.mgdl == 39).sum()}, High->401 x{(deduped.mgdl == 401).sum()}")

    spikes = spike_mask(deduped.timestamp, deduped.mgdl)
    clean = deduped[~spikes].reset_index(drop=True)
    print(f"spike filter dropped: {int(spikes.sum())} (left as holes, not interpolated)")
    print(f"\nCLEAN READINGS: {len(clean)}")
    print(f"span: {clean.timestamp.min():%Y-%m-%d} -> {clean.timestamp.max():%Y-%m-%d}")
    print(f"readings under 70: {(clean.mgdl < 70).sum()}")

    gaps = find_gaps(clean.timestamp)
    long_gaps = gaps[gaps.length >= pd.Timedelta(hours=LONG_GAP_H)]
    short = gaps[gaps.length < pd.Timedelta(hours=LONG_GAP_H)]
    print(f"\nGAPS > {GAP_MIN} min: {len(gaps)} total; {len(short)} are {GAP_MIN} min - {LONG_GAP_H} h (not listed)")
    print(f"gaps >= {LONG_GAP_H} h ({len(long_gaps)}), LONG = over 3 days:")
    for g in long_gaps.itertuples():
        hours = g.length.total_seconds() / 3600
        flag = "  LONG" if hours > 72 else ""
        print(f"  {g.start:%Y-%m-%d %H:%M} -> {g.end:%Y-%m-%d %H:%M}  {hours:7.1f} h{flag}")

    out = data / "clean.csv"
    clean.assign(mgdl=clean.mgdl.astype(int)).to_csv(out, index=False, date_format="%Y-%m-%dT%H:%M:%S")
    print(f"\nwrote {out} (timestamp, mgdl)")


if __name__ == "__main__":
    main()
