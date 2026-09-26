"""george.md step 3: MAE overall and where true < 100; event metrics by replaying held-out weeks through the actual alarm rule (ml/events.py): detection rate (warning >= 10 min before crossing 70), median lead time, false alarms per night; threshold sweep 70/75/80; writes models/metrics.md."""

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    p.parse_args()
    raise NotImplementedError("george.md: evaluate")


if __name__ == "__main__":
    main()
