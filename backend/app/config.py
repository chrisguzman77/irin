"""Configuration from the environment. Every name is spelled exactly as in
.env.example. A repo-root .env is read once (without overriding variables
already set) so the dev loop needs no extra tooling."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"

# The backend imports hardware/hal.py (Slavik) and ml/models/ (George) as packages.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split("  #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive(value: str | None, default: float, floor: float) -> float:
    """A finite float >= floor, else the default (a typo never yields 0 or NaN)."""
    try:
        x = float(value) if value not in (None, "") else default
    except ValueError:
        return default
    return x if x == x and x != float("inf") and x >= floor else default


@dataclass
class Config:
    IRIN_HW: str = "mock"  # mock | real
    DATASOURCE: str = "replay"  # replay | nightscout
    SCENARIO: str = "demo/scenarios/the_save.csv"  # relative to the repo root
    REPLAY_SPEED: float = 60.0
    NIGHTSCOUT_URL: str = ""
    NIGHTSCOUT_TOKEN: str = ""
    PIN: str = ""
    ANTHROPIC_API_KEY: str = ""
    SMTP_HOST: str = ""
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    REPORT_EMAIL: str = ""
    # sponsor tiers
    RELAY_URL: str = "http://localhost:8100"
    CLOUD_URL: str = "http://localhost:8200"
    RELAY_SOURCE_KEY: str = ""
    DEVICE_ID: str = ""
    DEVICE_TOKEN: str = ""
    DEVICE_URL: str = "http://localhost:8000"
    APP_ORIGIN: str = "https://irin-out-of-sleep-at-hackgt.tech"
    INBOX_URL: str = ""
    WATCH_URL: str = ""
    IRIN_BRAIN_ONLY: bool = False
    NARRATIVE_BACKEND: str = "template"  # template | anthropic | backboard (first link)
    BACKBOARD_API_KEY: str = ""
    META_MODEL_API_KEY: str = ""
    NARRATIVE_ROUTING: dict = field(default_factory=dict)  # task -> [provider, model]
    VOICE_BACKEND: str = "none"  # none | elevenlabs
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_DEVICE: str = ""
    ELEVENLABS_VOICE_ALERT: str = ""
    IRIN_DB: str = str(BACKEND_DIR / "irin.db")
    # Presence (Away gates room outputs only, invariant 6). Defaults are the
    # spec's; the Pi may shorten them for testing the idle screen.
    AWAY_AFTER_MIN: float = 15.0  # sustained daytime absence before Away (never at night)
    PRESENCE_SAMPLE_SECONDS: float = 30.0  # wall seconds between raw radar samples (30 = the alarm tick)

    @property
    def scenario_path(self) -> Path:
        p = Path(self.SCENARIO)
        return p if p.is_absolute() else REPO_ROOT / p


def default_routing(meta_key: str) -> dict:
    """NARRATIVE_ROUTING when the env var is unset (chris.md R13+). Cards and the
    morning report stay on Claude (they carry glucose values); the second opinion
    is OpenAI through Backboard, never a writer's own family. Muse Spark writes
    the family story and the buddy lines only when the Meta key is set; without
    it those rows are absent and they fall back to Claude as before. Model
    names match reports.DEFAULT_MODEL and narrative.META_MODEL (tested)."""
    routing = {"clinician_card": ["anthropic", "claude-sonnet-5"], "morning_report": ["anthropic", "claude-sonnet-5"],
               "second_opinion": ["openai", "gpt-5-mini"]}
    if meta_key:
        for task in ("family_story", "buddy_line", "match_explanation"):
            routing[task] = ["meta", "muse-spark-1.3"]
    return routing


def load_config() -> Config:
    _load_dotenv()
    env = os.environ
    routing: dict = default_routing(env.get("META_MODEL_API_KEY", ""))
    raw = env.get("NARRATIVE_ROUTING", "")
    if raw:
        try:
            routing = json.loads(raw)
        except json.JSONDecodeError:
            routing = {}
    return Config(
        IRIN_HW=env.get("IRIN_HW", "mock"),
        DATASOURCE=env.get("DATASOURCE", "replay"),
        SCENARIO=env.get("SCENARIO", "demo/scenarios/the_save.csv"),
        REPLAY_SPEED=float(env.get("REPLAY_SPEED", "60.0")),
        NIGHTSCOUT_URL=env.get("NIGHTSCOUT_URL", ""),
        NIGHTSCOUT_TOKEN=env.get("NIGHTSCOUT_TOKEN", ""),
        PIN=env.get("PIN", ""),
        ANTHROPIC_API_KEY=env.get("ANTHROPIC_API_KEY", ""),
        SMTP_HOST=env.get("SMTP_HOST", ""),
        SMTP_USER=env.get("SMTP_USER", ""),
        SMTP_PASS=env.get("SMTP_PASS", ""),
        REPORT_EMAIL=env.get("REPORT_EMAIL", ""),
        RELAY_URL=env.get("RELAY_URL", "http://localhost:8100"),
        CLOUD_URL=env.get("CLOUD_URL", "http://localhost:8200"),
        RELAY_SOURCE_KEY=env.get("RELAY_SOURCE_KEY", ""),
        DEVICE_ID=env.get("DEVICE_ID", ""),
        DEVICE_TOKEN=env.get("DEVICE_TOKEN", ""),
        DEVICE_URL=env.get("DEVICE_URL", "http://localhost:8000"),
        APP_ORIGIN=env.get("APP_ORIGIN", "https://irin-out-of-sleep-at-hackgt.tech"),
        INBOX_URL=env.get("INBOX_URL", ""),
        WATCH_URL=env.get("WATCH_URL", ""),
        IRIN_BRAIN_ONLY=_bool(env.get("IRIN_BRAIN_ONLY", "false")),
        NARRATIVE_BACKEND=env.get("NARRATIVE_BACKEND", "template"),
        BACKBOARD_API_KEY=env.get("BACKBOARD_API_KEY", ""),
        META_MODEL_API_KEY=env.get("META_MODEL_API_KEY", ""),
        NARRATIVE_ROUTING=routing,
        VOICE_BACKEND=env.get("VOICE_BACKEND", "none"),
        ELEVENLABS_API_KEY=env.get("ELEVENLABS_API_KEY", ""),
        ELEVENLABS_VOICE_DEVICE=env.get("ELEVENLABS_VOICE_DEVICE", ""),
        ELEVENLABS_VOICE_ALERT=env.get("ELEVENLABS_VOICE_ALERT", ""),
        IRIN_DB=env.get("IRIN_DB", str(BACKEND_DIR / "irin.db")),
        AWAY_AFTER_MIN=_positive(env.get("AWAY_AFTER_MIN"), 15.0, floor=0.5),
        PRESENCE_SAMPLE_SECONDS=_positive(env.get("PRESENCE_SAMPLE_SECONDS"), 30.0, floor=0.5),
    )


config = load_config()
