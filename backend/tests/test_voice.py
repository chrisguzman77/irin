import pytest


@pytest.mark.skip(reason="step 7: malformed input cannot store a number the user never confirmed; a partial parse asks for the missing piece; 10 s timeout discards")
def test_placeholder():
    pass
