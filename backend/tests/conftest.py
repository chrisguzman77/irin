import os
import sys
from pathlib import Path

# backend/ on sys.path so `import app` works from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The app reads the repo-root .env on import (without overriding what is already
# set). On the Pi that .env is LIVE: nightscout, real hardware, the 1-minute idle
# test values. Pin what a test must never inherit, BEFORE anything imports app:
# replay data (else 26 tests fail and the suite polls the real Nightscout), mock
# hardware (a plain `pytest` on the Pi must never drive the real LEDs or speaker),
# and the spec's presence timings.
os.environ["DATASOURCE"] = "replay"
os.environ["IRIN_HW"] = "mock"
os.environ["AWAY_AFTER_MIN"] = "15"
os.environ["PRESENCE_SAMPLE_SECONDS"] = "30"


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
    monkeypatch.setattr(config, "VOICE_BACKEND", "none")  # never render a clip from a test
    from app.rounds import crypto

    monkeypatch.setattr(crypto, "KEYS_DIR", tmp_path / "keys")  # never the Pi's real keypair
    monkeypatch.setattr(config, "RELAY_URL", "http://relay.invalid")  # a test that pairs injects its own relay
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(config, "BACKBOARD_API_KEY", "")  # no narrative provider is ever called from a test
    monkeypatch.setattr(config, "META_MODEL_API_KEY", "")
    monkeypatch.setattr(config, "IRIN_DB", str(tmp_path / "irin-test.db"))
    try:
        from app import main

        monkeypatch.setattr(main.runtime.reports, "out_dir", tmp_path / "reports")
        monkeypatch.setattr(main.runtime.forwarder, "device_token", "")
    except Exception:  # a test that never imports the app
        pass


@pytest.fixture(autouse=True)
def _reset_pin_throttle():
    from app import auth

    auth._fails.clear()
    auth._locked_until.clear()
