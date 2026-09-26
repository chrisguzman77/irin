import pytest


@pytest.mark.skip(reason="F2/F3: a Level 1 story with a smuggled glucose number falls back to the template; a no-data night never renders as 'fine'; a demo-mode story never reaches SMTP; the chip lists exactly the recipients that were sent")
def test_placeholder():
    pass
