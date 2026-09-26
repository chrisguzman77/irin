import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import app  # noqa: E402


def test_health():
    with TestClient(app) as c:
        r = c.get("/v0/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True and "store" in r.json()


@pytest.mark.skip(reason="R6/R8: key gating, bearer gating, single-use pairing, is_demo mismatch rejected, /v0/log shows no plaintext; a document dump greps clean for mgdl")
def test_placeholder():
    pass
