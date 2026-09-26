"""Stub (George, docs/plans/george.md step 7): writes date-shifted real
scenarios and the SYNTHETIC titration scenario plus their companion JSON into
demo/scenarios/. Runs where the raw data lives, prints summaries only."""

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="demo/scenarios")
    p.parse_args()
    raise NotImplementedError("george.md step 7: make_scenarios")


if __name__ == "__main__":
    main()
