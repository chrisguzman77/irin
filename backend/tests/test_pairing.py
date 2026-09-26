import pytest


@pytest.mark.skip(reason="R5: expired and reused tokens rejected; not paired until device confirmation; revoke stops sends; demo pairings are is_demo; R5+ owner path: no code while presence is False, a used code is refused, the token never appears in a URL, unpair stops listings")
def test_placeholder():
    pass
