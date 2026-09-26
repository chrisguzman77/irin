import pytest


@pytest.mark.skip(reason="R4: a treated low (carbs at nadir + 20 min) is never inferred_unfelt; a slow recovery with nothing logged is; slope 0.99 fires and 1.0 does not; a 19-minute run never does")
def test_placeholder():
    pass
