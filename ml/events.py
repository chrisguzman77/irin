"""george.md step 3.4: the reusable event replay. Runs a forecaster over a
stretch through the ACTUAL alarm rule (forecast crosses threshold, N = 2
consecutive) and returns detection rate, median lead time, and false alarms
per night. Step 5 runs the same thing over the whole history."""

from __future__ import annotations

import argparse


def replay_events(readings, predict, threshold: float = 70.0, n_consecutive: int = 2) -> dict:
    raise NotImplementedError("george.md step 3.4: event replay")


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    raise NotImplementedError("george.md step 3.4: event replay")


if __name__ == "__main__":
    main()
