"""george.md step 1: parse only Event Type == EGV; concatenate all files and dedupe on timestamp FIRST; map Low -> 39 and High -> 401; the spike filter (drop x(t) when |x(t)-x(t-1)| > 30 AND |x(t+1)-x(t)| > 30 with opposite signs; the dropped reading becomes a hole, never interpolate); never auto-delete suspicious lows; gap = any interval > 30 min. Prints the cleaned count and the gap list only."""

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    p.parse_args()
    raise NotImplementedError("george.md: clean_clarity")


if __name__ == "__main__":
    main()
