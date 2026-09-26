import pytest


@pytest.mark.skip(reason="B2: full alarm, presence home, unacknowledged through two escalation cycles -> buddy alert fires; never fires when radar is absent; a claim on the listing leaves the T+20 timer untouched; alarm.py's state and the tier test are unchanged")
def test_placeholder():
    pass
