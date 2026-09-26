import sys
from pathlib import Path

# backend/ on sys.path so `import app` works from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import pytest


@pytest.fixture(autouse=True)
def _no_network_no_real_db(monkeypatch, tmp_path):
    """Tests never reach the network or Chris's real irin.db: the narrative is the
    template, no key, no SMTP, the store and report PNGs go to a temp dir."""
    from app.config import config

    monkeypatch.setattr(config, "NARRATIVE_BACKEND", "template")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    monkeypatch.setattr(config, "DEVICE_TOKEN", "")  # the cloud forwarder stays disabled
    monkeypatch.setattr(config, "IRIN_DB", str(tmp_path / "irin-test.db"))
    try:
        from app import main

        monkeypatch.setattr(main.runtime.reports, "out_dir", tmp_path / "reports")
        monkeypatch.setattr(main.runtime.forwarder, "device_token", "")
    except Exception:  # a test that never imports the app
        pass
