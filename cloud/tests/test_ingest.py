import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import app  # noqa: E402


def test_health():
    with TestClient(app) as c:
        r = c.get("/v1/health")
        assert r.status_code == 200 and r.json()["ok"] is True


@pytest.mark.skip(reason="C1: the same batch sent twice counts one row per reading; a bad device token gets 401")
def test_placeholder():
    pass
