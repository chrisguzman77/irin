import pytest


@pytest.mark.skip(reason="R2: The Save yields one event with escalated=True and crossed_actual=True; a warning-only run yields crossed_actual=False; presence flicker both ways; an undriven mock reads unknown")
def test_placeholder():
    pass
