"""Step 5 check: the decay reaches exactly zero at the configured duration,
sums across doses, ignores everything that is not a confirmed bolus, and
refuses negative or absurd inputs."""

import math
from datetime import datetime, timedelta

import pytest

from app.contracts import Treatment
from app.iob import dose_iob, fraction_remaining, iob

T0 = datetime(2020, 1, 1, 12, 0)


def bolus(units: float, at: datetime = T0) -> Treatment:
    return Treatment(timestamp=at, kind="bolus", insulin_units=units, confirmed=True)


def test_curve_starts_full_and_reaches_exactly_zero_at_duration():
    assert fraction_remaining(0.0) == 1.0
    assert fraction_remaining(4.0) == 0.0
    assert fraction_remaining(3.999) > 0.0
    assert fraction_remaining(10.0) == 0.0
    assert fraction_remaining(2.0, duration_hours=2.0) == 0.0  # configurable duration


def test_curve_is_monotonically_decreasing():
    samples = [fraction_remaining(h / 10) for h in range(0, 41)]
    assert all(a > b for a, b in zip(samples, samples[1:]))
    assert fraction_remaining(2.0) == pytest.approx(0.25)  # halfway: (1 - 0.5)^2


def test_total_iob_sums_boluses_and_hits_exactly_zero():
    doses = [bolus(4.0, T0), bolus(2.0, T0 + timedelta(hours=1))]
    assert iob(doses, T0) == 4.0  # the second dose has not happened yet
    assert iob(doses, T0 + timedelta(hours=1)) == pytest.approx(4.0 * 0.5625 + 2.0)
    assert iob(doses, T0 + timedelta(hours=5)) == 0.0  # both fully decayed, exactly zero


def test_only_confirmed_boluses_count():
    at = T0 + timedelta(minutes=30)
    others = [
        Treatment(timestamp=T0, kind="basal", insulin_units=22.0, confirmed=True),
        Treatment(timestamp=T0, kind="carbs", carbs_g=30.0),
        Treatment(timestamp=T0, kind="glp1_dose", dose_label="2.5 mg"),
        Treatment(timestamp=T0, kind="therapy_change", text="basal 22"),
        Treatment(timestamp=T0, kind="bolus", insulin_units=None),
    ]
    assert iob(others, at) == 0.0
    assert iob(others + [bolus(3.0)], at) == pytest.approx(3.0 * fraction_remaining(0.5))


def test_configured_duration_applies_to_the_total():
    doses = [bolus(5.0)]
    assert iob(doses, T0 + timedelta(hours=3), duration_hours=3.0) == 0.0
    assert iob(doses, T0 + timedelta(hours=3), duration_hours=4.0) > 0.0


@pytest.mark.parametrize("units", [-1.0, math.nan, math.inf])
def test_absurd_units_raise(units):
    with pytest.raises(ValueError):
        dose_iob(units, 1.0)


@pytest.mark.parametrize("duration", [0.0, -4.0, math.nan, math.inf])
def test_absurd_duration_raises(duration):
    with pytest.raises(ValueError):
        fraction_remaining(1.0, duration_hours=duration)


def test_non_finite_elapsed_raises_and_future_dose_is_zero():
    with pytest.raises(ValueError):
        fraction_remaining(math.nan)
    assert fraction_remaining(-0.5) == 0.0


def test_contract_refuses_unconfirmed_insulin_so_iob_never_sees_it():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Treatment(timestamp=T0, kind="bolus", insulin_units=4.0, confirmed=False)
