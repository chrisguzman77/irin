"""george.md step 5: run the cleaned 22 months through nights.classify_night, night_metrics, and low_events with the shipped forecaster and the actual alarm rule to produce INFERRED reason codes and INFERRED alarm events per night; output ml/data/nights_labeled.csv (gitignored) plus a printed summary; never fabricate acks, presence, or morning answers."""

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    p.parse_args()
    raise NotImplementedError("george.md: label_history")


if __name__ == "__main__":
    main()
