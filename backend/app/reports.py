"""Morning report (chris.md step 11): overnight stats computed by code, a
matplotlib PNG, a plain-language narrative (the direct Claude call until R13,
when narrative.py's chain takes over; the deterministic template is the
no-network path and the fallback), stored in SQLite, emailed with the PNG
attached, and shown on the morning screen. Family Story rides this job (F3).

The narrative passes the no-invented-numbers validator: every numeric token
in the generated text (integers, decimals, percentages, clock times) must
exist in the computed stats, otherwise the template renders instead. Two
short paragraphs, plain language, no medical advice.

Demo-mode reports are badged DEMO, stored and shown, and NEVER emailed.
"""

from __future__ import annotations

import logging
import re
import smtplib
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Protocol

from . import store
from .clock import clock
from .config import BACKEND_DIR, config
from .contracts import MorningReport, Reading, Settings, Treatment
from .windows import parse_hhmm

log = logging.getLogger("irin.reports")

REPORTS_DIR = BACKEND_DIR / "reports"  # gitignored: PNGs of real nights
LOW = 70.0
HIGH = 180.0
DEFAULT_MODEL = "claude-sonnet-5"  # a Sonnet-class Claude for the morning report (chris.md R13+)

# --- stats (code, never a model) ---


def night_bounds(night_date: date, night_start: str, night_end: str) -> tuple[datetime, datetime]:
    """night_date is the MORNING the night ends on; the window starts the evening before."""
    end = datetime.combine(night_date, parse_hhmm(night_end))
    start = datetime.combine(night_date - timedelta(days=1), parse_hhmm(night_start))
    if parse_hhmm(night_start) <= parse_hhmm(night_end):  # a window that does not cross midnight
        start = datetime.combine(night_date, parse_hhmm(night_start))
    return start, end


def compute_stats(readings: list[Reading], treatments: list[Treatment], night_start: str, night_end: str) -> dict:
    rows = sorted((r for r in readings if not r.is_stale), key=lambda r: r.timestamp)
    if not rows:
        return {"readings": 0, "coverage_pct": 0.0, "low_mgdl": None, "low_at": None, "high_mgdl": None,
                "high_at": None, "tir_pct": None, "tbr_pct": None, "tar_pct": None, "minutes_below_70": 0,
                "carbs_g": 0.0, "insulin_units": 0.0}
    start, end = rows[0].timestamp, rows[-1].timestamp
    hours = max(1.0, (end - start).total_seconds() / 3600 + 5 / 60)
    expected = hours * 12
    values = [r.glucose_mgdl for r in rows]
    n = len(values)
    below = sum(1 for v in values if v < LOW)
    above = sum(1 for v in values if v > HIGH)
    lo = min(rows, key=lambda r: r.glucose_mgdl)
    hi = max(rows, key=lambda r: r.glucose_mgdl)
    return {
        "readings": n,
        "coverage_pct": round(min(100.0, 100.0 * n / expected), 1),
        "low_mgdl": round(lo.glucose_mgdl),
        "low_at": lo.timestamp.strftime("%H:%M"),
        "high_mgdl": round(hi.glucose_mgdl),
        "high_at": hi.timestamp.strftime("%H:%M"),
        "tir_pct": round(100.0 * (n - below - above) / n, 1),
        "tbr_pct": round(100.0 * below / n, 1),
        "tar_pct": round(100.0 * above / n, 1),
        "minutes_below_70": below * 5,
        "carbs_g": round(sum(t.carbs_g or 0.0 for t in treatments if t.kind == "carbs"), 1),
        "insulin_units": round(sum(t.insulin_units or 0.0 for t in treatments if t.kind in ("bolus", "basal")), 1),
    }


# --- graph ---


def render_graph(readings: list[Reading], path: Path, night_start: str, night_end: str) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    rows = sorted((r for r in readings if not r.is_stale), key=lambda r: r.timestamp)
    fig, ax = plt.subplots(figsize=(9, 3.2), dpi=110)
    ax.axhspan(LOW, HIGH, color="#3cb46e", alpha=0.12, lw=0)
    ax.axhline(LOW, color="#ff3b30", lw=0.8, alpha=0.6)
    if rows:
        ax.plot([r.timestamp for r in rows], [r.glucose_mgdl for r in rows], color="#222", lw=1.6)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    else:
        ax.text(0.5, 0.5, "Irin had no data last night", ha="center", va="center", transform=ax.transAxes)
    ax.set_ylim(40, max(300, max((r.glucose_mgdl for r in rows), default=0) + 20))
    ax.set_ylabel("mg/dL")
    ax.set_title(f"Overnight {night_start} to {night_end}")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


# --- narrative: template, validator, the direct Claude call ---


def template_narrative(s: dict) -> str:
    if not s["readings"]:
        return ("Irin didn't have data last night, so there is nothing to report on the overnight glucose. "
                "Check the sensor and the connection this morning.")
    p1 = (f"Overnight the lowest point was {s['low_mgdl']} mg/dL at {s['low_at']} and the highest was "
          f"{s['high_mgdl']} mg/dL at {s['high_at']}. Time in range was {s['tir_pct']}%, with {s['tbr_pct']}% "
          f"below 70 and {s['tar_pct']}% above 180.")
    if s["minutes_below_70"]:
        p2 = f"About {s['minutes_below_70']} minutes were spent below 70 mg/dL."
    else:
        p2 = "No time was spent below 70 mg/dL."
    if s["coverage_pct"] < 85:
        p2 += f" Sensor coverage was {s['coverage_pct']}%, so some of the night is missing."
    return p1 + "\n\n" + p2


_NUM_TOKEN = re.compile(r"\d{1,2}:\d{2}|\d+(?:\.\d+)?")


def extract_numbers(text: str) -> set[str]:
    out = set()
    for m in _NUM_TOKEN.finditer(text):
        tok = m.group(0)
        if tok.endswith("%"):
            tok = tok[:-1]
        out.add(tok)
    return out


def _allowed_forms(s: dict) -> set[str]:
    forms: set[str] = {"70", "180", "54"}  # the thresholds every report may name
    for k, v in s.items():
        if v is None:
            continue
        if isinstance(v, str) and re.fullmatch(r"\d{2}:\d{2}", v):
            forms.add(v)
            forms.add(v.lstrip("0") if not v.startswith("00") else v)  # 04:00 and 4:00
            h, m = v.split(":")
            forms.add(f"{int(h) % 12 or 12}:{m}")  # 12-hour form
            forms.add(h)
            forms.add(str(int(h) % 12 or 12))
        elif isinstance(v, (int, float)):
            forms.add(f"{v:g}")
            forms.add(f"{round(v)}")
            forms.add(f"{v:.1f}")
            forms.add(f"{v:.0f}")
    return forms


def validate_narrative(text: str, s: dict) -> bool:
    """Every numeric token in the text must exist in the computed stats."""
    allowed = _allowed_forms(s)
    return all(tok in allowed for tok in extract_numbers(text))


SYSTEM_PROMPT = (
    "You write a two-paragraph morning note for an adult with type 1 diabetes about last night's glucose. "
    "Plain language, calm, no medical advice, no dose suggestions, no recommendations. Use ONLY the numbers "
    "given; never invent, round differently, or add a number that is not in the data. Clock times exactly as "
    "given. If there is no data, say Irin didn't have data last night and never say the night was fine."
)


def claude_narrative(prompt: str) -> str:
    """The direct Claude call (until R13). Raises on any failure; the caller falls back."""
    import anthropic

    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    routing = config.NARRATIVE_ROUTING.get("morning_report") or []
    model = routing[1] if len(routing) == 2 and routing[0] == "anthropic" else DEFAULT_MODEL
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=20.0, max_retries=1)
    response = client.messages.create(model=model, max_tokens=600, system=SYSTEM_PROMPT,
                                      messages=[{"role": "user", "content": prompt}])
    if response.stop_reason == "refusal":
        raise RuntimeError("refused")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise RuntimeError("empty narrative")
    return text


def narrative_prompt(night_date: date, s: dict) -> str:
    return f"Night ending {night_date.isoformat()}. Data (the only numbers you may use): {s}"


# --- mail ---


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str, attachments: list[Path]) -> None: ...


class SmtpMailer:
    """SMTP_HOST / SMTP_USER / SMTP_PASS from config; STARTTLS on 587."""

    def send(self, to: str, subject: str, body: str, attachments: list[Path]) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = config.SMTP_USER, to, subject
        msg.set_content(body)
        for p in attachments:
            msg.add_attachment(p.read_bytes(), maintype="image", subtype="png", filename=p.name)
        with smtplib.SMTP(config.SMTP_HOST, 587, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASS)
            smtp.send_message(msg)


# --- the builder ---


@dataclass
class ReportBuilder:
    readings_for: Callable[[datetime, datetime], list[Reading]]
    treatments_for: Callable[[datetime, datetime], list[Treatment]]
    mailer: Mailer | None = None
    out_dir: Path = REPORTS_DIR
    narrative_backend: str | None = None  # None = config.NARRATIVE_BACKEND
    model_call: Callable[[str], str] | None = None  # injectable; default claude_narrative
    settings: Settings = field(default_factory=Settings)  # the night window, read at build time

    def _narrative(self, night_date: date, s: dict) -> str:
        backend = self.narrative_backend or config.NARRATIVE_BACKEND
        fallback = template_narrative(s)
        if backend == "template":
            return fallback
        call = self.model_call or claude_narrative
        try:
            text = call(narrative_prompt(night_date, s))
        except Exception as e:  # no key, no network, refusal, timeout: the template stands
            log.warning("narrative backend failed (%s); using the template", type(e).__name__)
            return fallback
        if not validate_narrative(text, s):
            log.warning("narrative failed the no-invented-numbers validator; using the template")
            return fallback
        if not s["readings"] and "fine" in text.lower():
            return fallback
        return text

    def build(self, night_date: date, is_demo: bool) -> MorningReport:
        """night_date is the MORNING the night ends on. Runs in a worker thread
        from the scheduler job (matplotlib, the model call, and SMTP never
        block the event loop); every failure past the stats is logged, never raised."""
        night_start, night_end = self.settings.night_window_start, self.settings.night_window_end
        start, end = night_bounds(night_date, night_start, night_end)
        readings = self.readings_for(start, end)
        treatments = self.treatments_for(start, end)
        s = compute_stats(readings, treatments, night_start, night_end)
        png = render_graph(readings, self.out_dir / f"{night_date.isoformat()}.png", night_start, night_end)
        report = MorningReport(report_id=uuid.uuid4().hex, night_date=night_date, generated_at=clock.now(), stats=s,
                               narrative=self._narrative(night_date, s), graph_png_path=str(png), is_demo=is_demo)
        store.insert_report(report)
        if is_demo:
            log.info("demo report stored, not emailed (invariant 1)")
        elif self.mailer is not None and config.REPORT_EMAIL:
            try:
                self.mailer.send(config.REPORT_EMAIL, f"Irin morning report, {night_date.isoformat()}",
                                 report.narrative, [png])
            except Exception:
                log.exception("morning report email failed; the report is stored and shown")
        return report
