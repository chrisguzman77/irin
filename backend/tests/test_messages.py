import pytest


@pytest.mark.skip(reason="R9: never applies without confirm; decline and expiry apply nothing; a message from a non-paired key is rejected; silence changes nothing; a confirmed insulin_change writes exactly one therapy_change Treatment")
def test_placeholder():
    pass
