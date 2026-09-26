"""Step 11 check: overnight stats computed by code, a matplotlib PNG, a
narrative that passes the no-invented-numbers validator (template fallback
otherwise), stored + emailed with the PNG attached; a demo-mode report is
never emailed; the no-network path is the template."""

import asyncio
from datetime import date, datetime, timedelta

import pytest

from app import store
from app.clock import clock
from app.config import config
from app.contracts import Reading, Treatment
from app.datasource.replay import ReplayDataSource
from app.reports import (
    ReportBuilder,
    compute_stats,
    extract_numbers,
    night_bounds,
    render_graph,
    template_narrative,
    validate_narrative,
)

# The shipped scenario, whatever George cuts it to: the expectations below are
# computed from its rows by hand (min, max, count), never hard-coded.
_ROWS = ReplayDataSource(config.scenario_path, speed=60.0).rows
NIGHT = _ROWS[-1][0].date()  # the morning the scenario's night ends on
WINDOW = night_bounds(NIGHT, "22:00", "07:00")
EXPECTED = 9 * 12 + 1  # readings expected in a 22:00-07:00 window


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(store.config, "IRIN_DB", str(path))
    store.init_db(path)
    monkeypatch.setattr(config, "REPORT_EMAIL", "you@example.com")
    return path


def the_save_readings() -> list[Reading]:
    return [Reading(timestamp=t, glucose_mgdl=g, trend=tr, source="replay") for t, g, tr in _ROWS]


def test_stats_from_the_save():
    s = compute_stats(the_save_readings(), [], *WINDOW)
    lo = min(_ROWS, key=lambda r: r[1])
    hi = max(_ROWS, key=lambda r: r[1])
    assert s["low_mgdl"] == round(lo[1]) and s["low_at"] == lo[0].strftime("%H:%M")
    assert s["high_mgdl"] == round(hi[1]) and s["readings"] == len(_ROWS)
    assert s["tbr_pct"] == pytest.approx(100 * sum(1 for r in _ROWS if r[1] < 70) / len(_ROWS), abs=0.1)
    assert s["minutes_below_70"] == 5 * sum(1 for r in _ROWS if r[1] < 70) > 0  # The Save has its low
    assert s["coverage_pct"] == pytest.approx(min(100.0, 100 * len(_ROWS) / EXPECTED), abs=0.1)
    assert s["tir_pct"] + s["tbr_pct"] + s["tar_pct"] == pytest.approx(100.0)


def test_no_data_night_is_honest():
    s = compute_stats([], [], *WINDOW)
    assert s["readings"] == 0 and s["low_mgdl"] is None and s["coverage_pct"] == 0.0


def test_coverage_is_over_the_night_window_not_the_readings_present():
    """A sensor that dies after the first hour is a ~11% night, never a 100% one."""
    first_hour = [r for r in the_save_readings() if r.timestamp < _ROWS[0][0] + timedelta(hours=1)]
    s = compute_stats(first_hour, [], *WINDOW)
    assert s["readings"] == len(first_hour) and s["coverage_pct"] == pytest.approx(100 * len(first_hour) / EXPECTED, abs=0.1)
    assert s["coverage_pct"] < 15 and "coverage" in template_narrative(s).lower()


def test_graph_png_is_written(tmp_path):
    p = render_graph(the_save_readings(), tmp_path / "night.png", night_start="22:00", night_end="07:00")
    assert p.exists() and p.stat().st_size > 1000 and p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_validator_accepts_numbers_in_stats_and_rejects_invented_ones():
    s = compute_stats(the_save_readings(), [], *WINDOW)
    assert validate_narrative(template_narrative(s), s)
    assert not validate_narrative("You dipped to 48 at 13:10 and recovered.", s)  # 48 and 13:10 are invented
    h, m = s["low_at"].split(":")
    assert not validate_narrative(f"You had {int(h)} lows overnight.", s)  # the hour of the low is not a count
    assert not validate_narrative(f"You had {int(m) or 22} alarms.", s)
    assert validate_narrative(f"The low came at {int(h) % 12 or 12}:{m}.", s)
    assert validate_narrative(f"Your lowest point was {s['low_mgdl']} mg/dL at {s['low_at']}; time in range was "
                              f"{s['tir_pct']:.0f}%.", s)
    assert extract_numbers("2 alarms, 55 mg/dL at 4:00 AM, 88.5% in range") == {"2", "55", "4:00", "88.5"}


def test_template_never_says_fine_for_a_no_data_night():
    s = compute_stats([], [], *WINDOW)
    text = template_narrative(s)
    assert "didn't have data" in text.lower()
    assert "fine" not in text.lower()


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, attachments):
        self.sent.append((to, subject, body, [a.name for a in attachments]))


def test_build_stores_emails_with_png_and_uses_the_template_offline(db, tmp_path):
    readings = the_save_readings()
    mailer = FakeMailer()
    b = ReportBuilder(readings_for=lambda a, c: readings, treatments_for=lambda a, c: [],
                      mailer=mailer, out_dir=tmp_path, narrative_backend="template")
    r = b.build(NIGHT, is_demo=False)
    assert r.stats["low_mgdl"] == round(min(g for _, g, _ in _ROWS)) and r.graph_png_path and r.night_date == NIGHT
    assert validate_narrative(r.narrative, r.stats) and r.is_demo is False
    assert mailer.sent and mailer.sent[0][0] == "you@example.com" and mailer.sent[0][3][0].endswith(".png")
    assert store.select_report(NIGHT).report_id == r.report_id
    assert [x.night_date for x in store.select_reports()] == [NIGHT]


def test_demo_report_is_stored_and_shown_but_never_emailed(db, tmp_path):
    mailer = FakeMailer()
    b = ReportBuilder(readings_for=lambda a, c: the_save_readings(), treatments_for=lambda a, c: [],
                      mailer=mailer, out_dir=tmp_path)
    r = b.build(NIGHT, is_demo=True)
    assert r.is_demo and mailer.sent == [] and store.select_report(NIGHT) is not None


def test_invented_number_from_a_model_falls_back_to_the_template(db, tmp_path):
    b = ReportBuilder(readings_for=lambda a, c: the_save_readings(), treatments_for=lambda a, c: [],
                      mailer=FakeMailer(), out_dir=tmp_path,
                      narrative_backend="anthropic", model_call=lambda prompt: "You dropped to 41 and slept through it.")
    r = b.build(NIGHT, is_demo=True)
    assert r.narrative == template_narrative(r.stats)


def test_model_error_or_missing_key_falls_back_to_the_template(db, tmp_path):
    def boom(prompt):
        raise RuntimeError("no network")

    b = ReportBuilder(readings_for=lambda a, c: the_save_readings(), treatments_for=lambda a, c: [],
                      mailer=FakeMailer(), out_dir=tmp_path, narrative_backend="anthropic", model_call=boom)
    r = b.build(NIGHT, is_demo=True)
    assert r.narrative == template_narrative(r.stats)


def test_valid_model_text_is_used(db, tmp_path):
    lo = min(_ROWS, key=lambda r: r[1])
    good = f"Your lowest point was {round(lo[1])} mg/dL around {lo[0].strftime('%H:%M')}. Nothing else stood out."
    b = ReportBuilder(readings_for=lambda a, c: the_save_readings(), treatments_for=lambda a, c: [],
                      mailer=FakeMailer(), out_dir=tmp_path, narrative_backend="anthropic", model_call=lambda p: good)
    r = b.build(NIGHT, is_demo=True)
    assert r.narrative == good


def test_rebuild_replaces_the_stored_report(db, tmp_path):
    b = ReportBuilder(readings_for=lambda a, c: the_save_readings(), treatments_for=lambda a, c: [],
                      mailer=FakeMailer(), out_dir=tmp_path)
    b.build(NIGHT, is_demo=True)
    b.build(NIGHT, is_demo=True)
    assert len(store.select_reports()) == 1
