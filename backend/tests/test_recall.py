"""R11 check: two low events make two questions and three make two (the
deepest); carbs at nadir + 20 min pre-fills treated; nothing answered by
noon reads no answer and never counts as felt; the unfelt-low rate divides
by answered, not total (2 of 3 answered = 67% with 1 no answer); a late
answer before noon updates the record without a second question and re-runs
the evaluation; after noon the answer is refused; the endpoints are
PIN-gated; the payload shape is pinned for the app."""

from datetime import date, datetime, timedelta

import pytest

from app import store
from app.clock import clock
from app.contracts import LowEvent, LowEventRecall, NightRecord, Settings, Treatment
from app.rounds.low_events import LowEventDetector
from app.rounds.nights_adapter import NightsAdapter
from app.rounds.evaluate import StandingEngine
from app.rounds.recall import ANSWER_UNTIL_HHMM, MAX_PER_MORNING, MorningRecall, RecallError, answer_deadline, deepest
from app.rounds.standing import evaluate_hypo_response
from tests.test_step_watch import FakeSender
from tests.test_ledger import NIGHT, START, Sources, basal
from tests.test_low_events import low_night

MORNING = datetime(2020, 1, 2, 7, 0)
H = {"X-PIN": "1234"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()
    clock.set(speed=60.0, start=MORNING)
    yield
    clock.reset()


def low(i, nadir, night=NIGHT, carbs=False, inferred=True):
    t = datetime.combine(night, datetime.min.time()) + timedelta(hours=26, minutes=10 * i)
    return LowEvent(low_event_id=f"low-{night.isoformat()}-{i}", night_date=night, started_at=t, nadir_mgdl=nadir, nadir_at=t,
                    minutes_below_70=25, auc_below_70=250.0, carbs_logged_within_30min=carbs, inferred_unfelt=inferred, is_demo=True)


def service(**kw):
    return MorningRecall(is_demo=lambda: True, **kw)


def test_two_events_make_two_questions_and_three_make_the_two_deepest(db):
    due = []
    rc = service(on_due=due.append)
    events = [low(0, 58.0), low(1, 62.0)]
    for e in events:
        store.upsert_low_event(e)
    rows = rc.create(NIGHT, events)
    assert [r.low_event_id for r in rows] == ["low-2020-01-01-0", "low-2020-01-01-1"] and all(r.answer is None for r in rows)
    assert all(abs((r.asked_at - MORNING).total_seconds()) < 1 and r.is_demo for r in rows)
    assert [i["recall"]["low_event_id"] for i in due[-1]["recalls"]] == ["low-2020-01-01-0", "low-2020-01-01-1"]
    three = [low(0, 58.0), low(1, 62.0), low(2, 49.0)]
    for e in three:
        store.upsert_low_event(e)
    assert [e.low_event_id for e in deepest(three)] == ["low-2020-01-01-2", "low-2020-01-01-0"] and MAX_PER_MORNING == 2
    rows = rc.create(NIGHT, three)  # the rebuilt night: the deepest two, the existing one kept (asked once)
    assert sorted(r.low_event_id for r in rows) == ["low-2020-01-01-0", "low-2020-01-01-2"]
    assert sorted(r.low_event_id for r in store.select_recalls(NIGHT)) == ["low-2020-01-01-0", "low-2020-01-01-2"]  # low 1 withdrawn
    assert len(rc.pending()) == 2 and rc.status() == {"recalls": rc.pending()}
    rc.answer("low-2020-01-01-0", "was_awake")
    store.upsert_low_event(low(3, 40.0))
    rc.create(NIGHT, [low(0, 58.0), low(2, 49.0), low(3, 40.0)])  # rebuilt again, a deeper low: the two open questions are
    # the deepest two (3 and 2); the answered one (0) is never withdrawn
    assert sorted(r.low_event_id for r in store.select_recalls(NIGHT)) == ["low-2020-01-01-0", "low-2020-01-01-2", "low-2020-01-01-3"]
    assert sorted(i["recall"]["low_event_id"] for i in rc.pending()) == ["low-2020-01-01-2", "low-2020-01-01-3"]


def test_carbs_at_nadir_plus_20_min_pre_fills_treated(db):
    """The R4 detector marks carbs within 30 min of the low; the question arrives pre-filled."""
    rows, nadir_t = low_night(5, slope=0.5)  # a short descent: carbs at nadir + 20 min are within 30 min of the start
    src = Sources(readings=rows, treatments=[basal(datetime(2020, 1, 1, 21, 30)),
                                            Treatment(timestamp=nadir_t + timedelta(minutes=20), kind="carbs", carbs_g=15.0)])
    adapter = NightsAdapter(settings=Settings(basal_time="21:30"), readings_for=src.readings_for, treatments_for=src.treatments_for,
                            alarm_events_for=src.alarm_events_for, presence_for=src.presence_for)
    [event] = LowEventDetector(adapter=adapter, is_demo=lambda: True).detect(NIGHT)
    assert event.carbs_logged_within_30min is True and event.inferred_unfelt is False
    [item] = service().create(NIGHT, [event]) and service().pending()
    assert item["prefill_treated"] is True and item["low_event"]["low_event_id"] == event.low_event_id
    assert item["answer_until"] == "2020-01-02T12:00:00" and item["recall"]["answer"] is None


def night(i):
    d = date(2020, 1, 1) + timedelta(days=i)
    return NightRecord(night_date=d, window_start=datetime.combine(d, datetime.min.time()).replace(hour=22),
                       window_end=datetime.combine(d + timedelta(days=1), datetime.min.time()).replace(hour=7),
                       coverage_pct=95.0, reason_codes=["clean"], code_source="logged", low_point_mgdl=60.0, is_demo=True)


def test_no_answer_by_noon_is_no_answer_and_the_rate_divides_by_answered(db):
    """4 nocturnal lows in 14 nights, 3 answered, 2 unfelt (67%), 1 no answer."""
    rc = service()
    lows = [low(0, 58.0, night=date(2020, 1, 1) + timedelta(days=k)) for k in range(4)]
    for e in lows:
        store.upsert_low_event(e)
    answers = ["felt_and_treated", "woke_no_symptoms", "dont_remember", None]
    for k, e in enumerate(lows):
        clock.set(speed=60.0, start=MORNING + timedelta(days=k))
        rc.create(e.night_date, [e])
        if answers[k] is not None:  # answered on its own morning, before noon
            clock.set(speed=60.0, start=MORNING + timedelta(days=k, hours=1))
            rc.answer(e.low_event_id, answers[k])
    clock.set(speed=60.0, start=MORNING + timedelta(days=3, hours=5))
    assert rc.close(date(2020, 1, 5)) == 1  # the fourth: nothing by noon
    recalls = store.select_recalls(date(2020, 1, 1))
    assert [r.answer for r in sorted(recalls, key=lambda r: r.asked_at)] == ["felt_and_treated", "woke_no_symptoms", "dont_remember", None]
    e = evaluate_hypo_response([night(i) for i in range(14)], lows, recalls, [], Settings())
    m = e.metrics
    assert (m["nocturnal_lows"], m["answered"], m["unfelt_lows"], m["no_answer"]) == (4, 3, 2, 1)
    assert m["unfelt_low_rate"] == pytest.approx(2 / 3) and m["inferred_unfelt_unanswered"] == 1
    assert e.status == "red" and "awareness" in e.flags  # Hypo Response fires on any reported unfelt low
    assert e.confidence["unfelt_lows"] == "reported" and e.confidence["no_answer"] == "reported"
    # nothing answered at all: no rate, never "felt", never fine
    none = [LowEventRecall(low_event_id=e_.low_event_id, asked_at=MORNING, is_demo=True) for e_ in lows]
    m = evaluate_hypo_response([night(i) for i in range(14)], lows, none, [], Settings()).metrics
    assert (m["answered"], m["unfelt_lows"], m["no_answer"], m["unfelt_low_rate"]) == (0, 0, 4, None)


def test_a_late_answer_before_noon_updates_without_a_second_question_and_reruns(db):
    reruns = []
    rc = service(on_answer=lambda r, e: reruns.append((r.low_event_id, r.answer, e.night_date)))
    event = low(0, 58.0)
    store.upsert_low_event(event)
    rc.create(NIGHT, [event])
    clock.advance(4 * 3600)  # 11:00
    r = rc.answer(event.low_event_id, "dont_remember")
    assert r.answer == "dont_remember" and abs((r.answered_at - datetime(2020, 1, 2, 11)).total_seconds()) < 1
    r = rc.answer(event.low_event_id, "was_awake")  # changed her mind before noon: the record updates
    assert r.answer == "was_awake" and len(store.select_recalls(NIGHT)) == 1 and rc.pending() == []
    assert reruns == [(event.low_event_id, "dont_remember", NIGHT), (event.low_event_id, "was_awake", NIGHT)]
    assert rc.create(NIGHT, [event]) == [r]  # the rebuilt night keeps the answered question
    assert [i["recall"]["answer"] for i in rc.morning(date(2020, 1, 2))] == ["was_awake"]
    clock.advance(3600 + 60)  # 12:01
    with pytest.raises(RecallError) as err:
        rc.answer(event.low_event_id, "felt_and_treated")
    assert err.value.status == 409 and store.select_recall(event.low_event_id).answer == "was_awake"
    with pytest.raises(RecallError) as err:
        rc.answer("nope", "was_awake")
    assert err.value.status == 404
    # the other world's question is invisible here
    live = low(1, 50.0).model_copy(update={"is_demo": False, "low_event_id": "live-1"})
    store.upsert_low_event(live)
    store.upsert_recall(LowEventRecall(low_event_id="live-1", asked_at=clock.now(), is_demo=False))
    assert rc.pending() == [] and rc.morning() == [i for i in rc.morning() if i["recall"]["is_demo"]]
    with pytest.raises(RecallError):
        rc.answer("live-1", "was_awake")


def test_app_asks_after_the_ledger_and_answers_are_pin_gated(monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth, main

    monkeypatch.setattr(auth.config, "PIN", "1234")
    with TestClient(main.app) as c:
        import inspect

        assert "recall.create" in inspect.getsource(main._build_night_record)  # the ledger job asks the questions
        today = main.runtime.ledger.night_ended_on(main.clock.now().date())
        event = low(0, 55.0, night=today)
        store.upsert_low_event(event)
        main.runtime.recall.create(today, [event])
        assert c.get("/api/rounds/recalls").status_code == 401
        items = c.get("/api/rounds/recalls", headers=H).json()
        assert [i["recall"]["low_event_id"] for i in items] == [event.low_event_id] and items[0]["low_event"]["nadir_mgdl"] == 55.0
        assert c.get("/api/rounds/checkin", headers=H).json()["recalls"] == items
        assert c.post(f"/api/rounds/recall/{event.low_event_id}", json={"answer": "dont_remember"}).status_code == 401
        assert c.post("/api/rounds/recall/nope", json={"answer": "dont_remember"}, headers=H).status_code == 404
        assert c.post(f"/api/rounds/recall/{event.low_event_id}", json={"answer": "fine"}, headers=H).status_code == 422
        r = c.post(f"/api/rounds/recall/{event.low_event_id}", json={"answer": "dont_remember"}, headers=H)
        assert r.status_code == 200 and r.json()["answer"] == "dont_remember"
        assert c.get("/api/rounds/checkin", headers=H).json()["recalls"] == []
        assert c.get("/api/rounds/recalls", headers=H).json()[0]["recall"]["answer"] == "dont_remember"
        assert "recall_close" in [j["name"] for j in c.get("/api/scheduler").json()["jobs"]]


def test_an_old_night_rebuilt_is_born_closed_and_the_deadline_is_the_next_noon(db):
    rc = service()
    event = low(0, 58.0)
    store.upsert_low_event(event)
    clock.set(speed=60.0, start=MORNING + timedelta(days=2, hours=1))  # 2020-01-04 08:00: the night is two days old
    [r] = rc.create(NIGHT, [event], asked_at=MORNING)  # asked AT the window end it belongs to (main passes record.window_end)
    assert r.asked_at == MORNING and rc.pending() == [] and rc.morning() == []
    with pytest.raises(RecallError) as err:
        rc.answer(event.low_event_id, "was_awake")
    assert err.value.status == 409 and store.select_recall(event.low_event_id).answer is None
    assert answer_deadline(datetime(2020, 1, 2, 7, 0)) == datetime(2020, 1, 2, 12, 0)
    assert answer_deadline(datetime(2020, 1, 2, 12, 0)) == datetime(2020, 1, 3, 12, 0)  # a window ending at noon or later
    assert answer_deadline(datetime(2020, 1, 1, 23, 0)) == datetime(2020, 1, 2, 12, 0)  # a window ending before midnight
    late = low(5, 44.0, night=date(2020, 1, 5))
    store.upsert_low_event(late)
    clock.set(speed=60.0, start=datetime(2020, 1, 6, 8, 0))
    rc.create(date(2020, 1, 5), [late], asked_at=datetime(2020, 1, 5, 23, 0))  # a window ending at 23:00: it is the 01-06 morning's question
    assert [i["recall"]["low_event_id"] for i in rc.pending()] == [late.low_event_id] and rc.morning(date(2020, 1, 6)) == rc.pending()
    assert rc.morning(date(2020, 1, 5)) == [] and rc.close(date(2020, 1, 6)) == 1
    assert ANSWER_UNTIL_HHMM == "12:00"
    unsynced = MorningRecall(is_demo=lambda: True, clock_synced=lambda: False)
    clock.set(speed=60.0, start=MORNING + timedelta(hours=1))
    with pytest.raises(RecallError) as err:
        unsynced.answer(event.low_event_id, "was_awake")
    assert err.value.status == 409 and "clock" in err.value.detail


def test_a_late_answer_reruns_hypo_response_and_sends_one_card(db):
    """The morning run: Hypo Response green (digest). The 11:00 answer "don't
    remember" makes it red: ONE card. Changing the answer sends nothing more."""
    sender = FakeSender()
    engine = StandingEngine(settings=Settings(), sender=sender, device_id="irin-test", is_demo=lambda: True)
    for i in range(14):
        store.upsert_night_record(night(i))
    event = low(0, 58.0, night=date(2020, 1, 14))
    store.upsert_low_event(event)
    rc = service(on_answer=lambda r, e: asyncio.run(engine.run(today=e.night_date)))
    clock.set(speed=60.0, start=datetime(2020, 1, 15, 7, 0))
    rc.create(event.night_date, [event])
    clock.set(speed=60.0, start=datetime(2020, 1, 15, 7, 5))
    out = {e["kind"]: e for e in asyncio.run(engine.run(today=event.night_date))}
    assert out["hypo_response"]["status"] == "green" and out["hypo_response"]["budget"] == "digest" and sender.sent == []
    clock.set(speed=60.0, start=datetime(2020, 1, 15, 11, 0))
    rc.answer(event.low_event_id, "dont_remember")
    assert [c.kind for c, _ in sender.sent] == ["hypo_response"] and sender.sent[0][0].status == "red"
    assert "1 of 1 answered lows reported unfelt" in sender.sent[0][0].headline
    rc.answer(event.low_event_id, "woke_no_symptoms")  # still an unfelt low: the same red, not a second card
    assert len(sender.sent) == 1
    assert sender.sent[0][0].metrics["unfelt_lows"] == 1 and sender.sent[0][0].metrics["answered"] == 1


import asyncio  # noqa: E402  (used by the engine test above)
