"""R8 check: a second Basal Check inside 14 days is suppressed; a red inside
the window is still sent once (12 h cap, deduplicated per event); an active
watch suppresses Basal Check and absorbs Hypo Response; green goes to the
digest; insufficient never sends; never two programs on one day."""

from datetime import datetime, timedelta

from app.rounds.noise import Sent, allow, history_from_store

T = datetime(2020, 1, 15, 7, 5)


def test_one_standing_card_per_type_per_14_days():
    assert allow("basal_check", "standing", "amber", T, []).allowed
    hist = [Sent("basal_check", "standing", "amber", T)]
    assert allow("basal_check", "standing", "amber", T + timedelta(days=13), hist).reason == "interval"
    assert allow("basal_check", "standing", "amber", T + timedelta(days=14, minutes=1), hist).allowed
    assert allow("hypo_response", "standing", "amber", T + timedelta(days=1), hist).allowed  # another type


def test_red_bypasses_but_is_capped_and_deduplicated():
    hist = [Sent("hypo_response", "standing", "red", T, event_key="ae-1")]
    assert allow("hypo_response", "standing", "red", T + timedelta(hours=1), hist, event_key="ae-1").reason == "red_duplicate"
    assert allow("hypo_response", "standing", "red", T + timedelta(hours=1), hist, event_key="ae-2").reason == "red_cap"
    assert allow("hypo_response", "standing", "red", T + timedelta(hours=13), hist, event_key="ae-2").allowed
    assert allow("hypo_response", "standing", "red", T + timedelta(days=30), hist, event_key="ae-1").reason == "red_duplicate"


def test_green_goes_to_the_digest_and_insufficient_never_sends():
    assert allow("basal_check", "standing", "green", T, []).reason == "digest"
    assert allow("basal_check", "standing", "insufficient", T, []).reason == "insufficient"
    assert allow("safety", "step_watch", "insufficient", T, []).reason == "insufficient"


def test_active_watch_suspends_standing_cards():
    assert allow("basal_check", "standing", "amber", T, [], active_watch=True).reason == "watch"
    assert allow("hypo_response", "standing", "red", T, [], active_watch=True).reason == "watch"  # absorbed into safety
    assert allow("safety", "step_watch", "red", T, [], active_watch=True, event_key="ae-1").allowed


def test_one_step_check_and_gate_per_step_and_one_program_per_day():
    hist = [Sent("step_check", "step_watch", "amber", T, event_key="p1:1")]
    assert allow("step_check", "step_watch", "amber", T + timedelta(days=2), hist, event_key="p1:1").reason == "per_step"
    assert allow("step_check", "step_watch", "amber", T + timedelta(days=2), hist, event_key="p1:2").allowed
    assert allow("step_gate", "step_watch", "amber", T + timedelta(days=2), hist, event_key="p1:1").allowed
    assert allow("basal_check", "standing", "amber", T + timedelta(hours=2), hist).reason == "program_day"


def test_history_from_the_stored_cards():
    docs = [{"status": "sent", "stored_at": T.isoformat(), "card": {"kind": "step_check", "program": "step_watch", "status": "amber",
                                                                     "plan_id": "p1", "step_index": 1}},
            {"status": "unsent", "stored_at": T.isoformat(), "card": {"kind": "basal_check", "program": "standing", "status": "amber"}}]
    hist = history_from_store(docs)
    assert len(hist) == 2 and hist[0].event_key == "p1:1" and hist[0].sent_at == T  # unsent still counts: it is on its way
    red = [{"status": "sent", "stored_at": T.isoformat(), "event_key": "ae-7",
            "card": {"kind": "hypo_response", "program": "standing", "status": "red"}}]
    assert history_from_store(red)[0].event_key == "ae-7"  # the red's episode survives a restart
    assert allow("follow_up", "standing", "insufficient", T, []).allowed  # "not enough data yet" is a card
