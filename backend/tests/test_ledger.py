import pytest


@pytest.mark.skip(reason="R3: a record appears at the window end under 60x replay; each reason-code rule at its boundary (2 h 59 min is late_meal, 3 h 01 min is not; 61 min late is basal_late; 92 readings adequate, 91 stale); a rebuilt night is identical")
def test_placeholder():
    pass
