"""george.md step 3: split by TIME (hold out the final 8 weeks); baselines FIRST (persistence, linear extrapolation of the last 15-min trend); XGBoost on the delta; save ONLY forecast_v1.json (never a pickle) with the pinned xgboost version."""

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    p.parse_args()
    raise NotImplementedError("george.md: train")


if __name__ == "__main__":
    main()
