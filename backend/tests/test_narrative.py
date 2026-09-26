import pytest


@pytest.mark.skip(reason="R13: a text with an invented number falls back to the template; a matching text passes; a stub second opinion that says advice forces the template; a timeout falls through to the next link; a routing row naming meta reaches the Meta stub and never the scorer")
def test_placeholder():
    pass
