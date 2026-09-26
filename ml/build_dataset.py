"""george.md step 2: one row per reading only when the full 60-min feature history AND the t+30 label sit inside one gap-free stretch; label = glucose(t+30) - glucose(t) (the DELTA); features via models/features.py ONLY; no future information; prints row count and NaN check."""

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    p.parse_args()
    raise NotImplementedError("george.md: build_dataset")


if __name__ == "__main__":
    main()
