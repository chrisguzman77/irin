from datetime import datetime

import numpy as np

from ml.events import replay_events
from ml.label_history import alarm_events_from_replay, night_windows


def test_night_windows_cover_every_evening():
    w = night_windows(datetime(2020, 1, 1, 3, 0), datetime(2020, 1, 3, 12, 0))
    assert w[0] == (datetime(2019, 12, 31, 22, 0), datetime(2020, 1, 1, 7, 0))
    assert w[-1] == (datetime(2020, 1, 3, 22, 0), datetime(2020, 1, 4, 7, 0))
    assert len(w) == 4


def test_replayed_alarm_events_carry_no_human_fields():
    # SYNTHETIC: a warning that escalates into a crossing
    ts = np.datetime64("2020-01-01T23:00:00") + np.arange(30) * np.timedelta64(300, "s")
    x = np.array([120.0] * 15 + [110, 100, 90, 80, 72, 65, 60] + [60.0] * 8)
    preds = np.where(np.arange(30) >= 16, 60.0, 120.0)
    ev = alarm_events_from_replay(replay_events(ts, x, preds, threshold=85.0))
    tiers = [e["tier"] for e in ev]
    assert tiers == ["predicted_low", "actual_low"]
    assert ev[0]["crossed_actual"] is True
    for e in ev:
        assert e["inferred"] and e["presence_during"] == "unknown"
        assert "acknowledged_at" not in e and "ack_source" not in e and "escalated" not in e
