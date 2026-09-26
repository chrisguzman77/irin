import pytest


@pytest.mark.skip(reason="R8: 5 clean nights passes, 4 does not; +30 does not fire, +31 does; 70% same direction passes, 69% does not; 3 near-misses flips the too-high direction; Follow-up with 2 clean nights on a side says 'not enough data yet'")
def test_placeholder():
    pass
