import pytest


@pytest.mark.skip(reason="R11: two events make two cards and three make two; carbs at nadir + 20 min pre-fills treated; nothing answered by noon reads no answer and never counts as felt; the rate divides by answered, not total; a late answer updates the record without duplicating a card")
def test_placeholder():
    pass
