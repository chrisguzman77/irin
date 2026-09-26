"""george.md step 6: the three measured numbers from the real 22 months, never from the synthetic scenario: (1) retrospective lead time for Basal Check per listed therapy change, (2) false-alarm rate per signal over stable-therapy stretches, (3) detection lag of the low-point-shift rule; with the leakage guard and the n = 1, observational, retrospective caveats."""

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="ml/data")
    p.parse_args()
    raise NotImplementedError("george.md: evaluate_rounds")


if __name__ == "__main__":
    main()
