import pytest


@pytest.mark.skip(reason="R8: a second Basal Check inside 14 days is suppressed; red inside the window is still sent once; an active watch suppresses Basal Check and merges Hypo Response")
def test_placeholder():
    pass
