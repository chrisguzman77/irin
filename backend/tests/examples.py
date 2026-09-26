"""One valid example of every model in contracts.py. Used by test_contracts.py
and by tests/fixtures/generate.py, so the JSON fixtures handed to Justin and
George are produced by the models and cannot drift from them. Values are
SYNTHETIC (2020 dates)."""

from __future__ import annotations

from datetime import date, datetime

from app import contracts as c

T0 = datetime(2020, 1, 1, 22, 0)
D0 = date(2020, 1, 1)


def examples() -> dict[str, c.BaseModel]:
    reading = c.Reading(timestamp=T0, glucose_mgdl=128.0, trend="Flat", source="replay", is_stale=False)
    forecast = c.Forecast(timestamp=T0, predicted_mgdl=96.0, horizon_min=30)
    alarm = c.AlarmState(state="pending", trigger_type="predicted_low", started_at=T0)
    presence = c.PresenceState(mode="home", source="radar", since=T0)
    recipient = c.FamilyRecipient(recipient_id="r1", name="Mom", email="mom@example.com")
    settings = c.Settings(
        report_email="you@example.com", basal_time="21:30", basal_units=22.0, glucagon_on_hand=True,
        glucagon_expiry=date(2021, 6, 1), emergency_script=c.EmergencyScript(steps=["call", "juice"]),
        family_recipients=[recipient],
    )
    step0 = c.TitrationStep(index=0, dose_label="2.5 mg", planned_start=D0)
    step1 = c.TitrationStep(index=1, dose_label="5 mg", planned_start=date(2020, 1, 29))
    plan = c.TitrationPlan(plan_id="p1", drug_class="gip_glp1", drug_label="tirzepatide", steps=[step0, step1],
                           started_at=D0, on_insulin=True,
                           options=c.WatchOptions(ketone_prompts=True, step_week_vigilance=True), status="active",
                           is_demo=True)
    message = c.DoctorMessage(message_id="m1", kind="insulin_change", insulin="basal", new_units=24.0,
                              start_date=date(2020, 1, 2), created_at=T0)
    alarm_event = c.AlarmEvent(event_id="e1", tier="actual_low", started_at=T0, acknowledged_at=datetime(2020, 1, 1, 22, 8),
                               ack_source="device", escalated=True, rearm_count=1, crossed_actual=True,
                               presence_during="home", is_demo=True)
    low_event = c.LowEvent(low_event_id="l1", night_date=D0, started_at=datetime(2020, 1, 2, 2, 40), nadir_mgdl=58.0,
                           nadir_at=datetime(2020, 1, 2, 2, 47), minutes_below_70=25, auc_below_70=210.0,
                           recovery_slope=0.8, carbs_logged_within_30min=False, inferred_unfelt=True,
                           alarm_event_id="e1", is_demo=True)
    # R1: the two card shapes Justin's inbox renders from. metrics and confidence
    # mirror ml/models/nights.py standing_window / step_window_metrics KEY FOR KEY
    # (George's values dict + parallel confidence dict), so R8's builder can pass
    # them straight through; every metric row is labeled, none is blank.
    standing = c.SignalCard(
        card_id="standing:basal_check:2020-01-01:2020-01-14", program="standing", kind="basal_check", status="amber",
        flags=["rise_high"],
        confidence={"clean_nights": "inferred", "rise_median_clean": "inferred", "same_direction_share": "inferred",
                    "near_misses": "measured", "escalated_warnings": "measured", "rearms": "measured",
                    "median_ack_min": "measured", "nocturnal_lows": "measured", "answered": "reported",
                    "unfelt_lows": "reported", "no_answer": "reported", "unfelt_low_rate": "reported",
                    "inferred_unfelt_unanswered": "inferred"},
        source="irin_bedside", patient_pseudonym="Patient 7", period_start=D0, period_end=date(2020, 1, 14),
        headline="8 clean nights of 14; median overnight rise +42 mg/dL, 6 of 8 in the same direction.",
        metrics={"nights": 14, "clean_nights": 8, "rise_median_clean": 42.0, "same_direction_share": 0.75,
                 "near_misses": 1, "escalated_warnings": 0, "rearms": 0, "median_ack_min": 1.5, "nocturnal_lows": 1,
                 "answered": 1, "unfelt_lows": 0, "no_answer": 0, "unfelt_low_rate": 0.0,
                 "inferred_unfelt_unanswered": 0,
                 "excluded_nights": [{"night_date": "2020-01-03", "reasons": ["late_meal"]},
                                     {"night_date": "2020-01-05", "reasons": ["stale"]},
                                     {"night_date": "2020-01-06", "reasons": ["late_meal"]},
                                     {"night_date": "2020-01-08", "reasons": ["treated_low"]},
                                     {"night_date": "2020-01-10", "reasons": ["stale"]},
                                     {"night_date": "2020-01-12", "reasons": ["late_meal"]}]},
        nights=[{"night_date": "2020-01-01", "reason_codes": ["clean"], "code_source": "inferred", "rise_mgdl": 40.0,
                 "low_point_mgdl": 98.0, "coverage_pct": 94.4},
                {"night_date": "2020-01-02", "reason_codes": ["clean"], "code_source": "inferred", "rise_mgdl": 45.0,
                 "low_point_mgdl": 104.0, "coverage_pct": 100.0},
                {"night_date": "2020-01-03", "reason_codes": ["late_meal"], "code_source": "inferred", "rise_mgdl": 61.0,
                 "low_point_mgdl": 130.0, "coverage_pct": 98.6}],
        excluded_counts={"late_meal": 3, "stale": 2, "treated_low": 1},
        narrative=("Eight clean nights of fourteen. On the clean nights the median overnight rise was 42 mg/dL, and "
                   "6 of 8 rose in the same direction. Six nights were left out (3 late meals, 2 sensor gaps, 1 treated "
                   "low); their reasons are inferred from the trace, not logged."),
        allowed_actions=["adjust_basal", "schedule_visit", "ask_patient", "dismiss"], resource_categories=[],
        is_demo=True, generated_at=datetime(2020, 1, 15, 7, 5),
    )
    step_card = c.SignalCard(
        card_id="step_watch:step_check:p1:1:2020-01-31:2020-02-04", program="step_watch", kind="step_check",
        status="amber", flags=["lows", "awareness", "tolerance"],
        confidence={"low_point_shift": "measured", "baseline_nights": "measured", "coverage_pct": "measured",
                    "tbr_pct": "measured", "near_misses": "measured", "ketone_risk_episodes": "measured",
                    "tolerance": "reported", "adherence": "reported", "unfelt_lows": "reported"},
        source="irin_bedside", patient_pseudonym="Patient 7", plan_id="p1", step_index=1,
        period_start=date(2020, 1, 31), period_end=date(2020, 2, 4),
        headline=("Step 2 (5 mg), days 3 to 7. Overnight low point down 22 mg/dL from baseline, 2 near-misses, "
                  "time below range 4.0%. 1 low not remembered. Rough stomach 3 of 5 days."),
        metrics={"low_point_shift": -22.0, "window_low_point": 76.0, "baseline_low_point": 98.0, "baseline_nights": 7,
                 "coverage_pct": 96.5, "insufficient": False, "tbr_pct": 4.0, "near_misses": 2,
                 "ketone_risk_episodes": 0,
                 "tolerance": {"fine": 2, "rough": 3, "cant_eat": 0, "missing": 0},
                 "adherence": {"logged": 1, "expected": 1, "missed": 0, "dose_mismatch": False},
                 "unfelt_lows": 1},
        nights=[{"night_date": "2020-01-31", "reason_codes": ["clean"], "code_source": "logged", "low_point_mgdl": 74.0,
                 "near_miss_count": 1, "coverage_pct": 97.2}],
        excluded_counts={},
        tolerance_days=[{"date": "2020-01-31", "gi": "rough"}, {"date": "2020-02-01", "gi": "rough"},
                        {"date": "2020-02-02", "gi": "fine"}, {"date": "2020-02-03", "gi": "rough"},
                        {"date": "2020-02-04", "gi": "fine"}],
        narrative=("Days 3 to 7 on 5 mg. The overnight low point sat 22 mg/dL below the baseline weeks, with 2 "
                   "near-misses and 4.0% of readings below range; 1 low was not remembered the next morning. "
                   "Stomach was rough on 3 of 5 days; the weekly dose was logged as planned."),
        allowed_actions=["proceed", "hold_step", "adjust_insulin", "message", "end_watch"],
        resource_categories=["gi_side_effect_education"], is_demo=True, generated_at=datetime(2020, 2, 5, 7, 5),
    )
    snapshot = c.StateSnapshot(latest_reading=reading, forecast=forecast, alarm=alarm, settings=settings,
                               presence=presence, mode="replay", active_plan=plan, pending_doctor_messages=[message],
                               todays_checkin_status={"pending_recalls": 1, "symptom_check_due": True},
                               pairing_state={"status": "paired"}, buddy_state={}, family_story_status=[],
                               clock_synced=True)
    return {
        "reading": reading,
        "treatment": c.Treatment(timestamp=T0, kind="bolus", insulin_units=4.0, confirmed=True),
        "treatment_carbs": c.Treatment(timestamp=T0, kind="carbs", carbs_g=30.0),
        "forecast": forecast,
        "alarm_state": alarm,
        "presence_state": presence,
        "night_buddy_optins": c.NightBuddyOptIns(),
        "emergency_script": c.EmergencyScript(steps=["Call me", "Juice is in the fridge"]),
        "family_recipient": recipient,
        "settings": settings,
        "ws_message": c.WSMessage(type="reading_update", payload=reading.model_dump(mode="json")),
        "state_snapshot": snapshot,
        "night_record": c.NightRecord(night_date=D0, window_start=T0, window_end=datetime(2020, 1, 2, 7, 0),
                                      coverage_pct=94.4, reason_codes=["clean"], code_source="inferred", rise_mgdl=40.0,
                                      dawn_rise_mgdl=18.0, low_point_mgdl=98.0, tbr_pct=0.0, alarm_event_ids=["e1"],
                                      is_demo=True),
        "alarm_event": alarm_event,
        "low_event": low_event,
        "low_event_recall": c.LowEventRecall(low_event_id="l1", asked_at=datetime(2020, 1, 2, 7, 5), answer=None, is_demo=True),
        "symptom_check": c.SymptomCheck(date=D0, gi="rough", is_demo=True),
        "titration_step": step0,
        "watch_options": c.WatchOptions(),
        "titration_plan": plan,
        "signal_card_standing": standing,
        "signal_card_step": step_card,
        "pairing": c.Pairing(device_id="irin-dev-0001", doctor_id="d1", doctor_display_name="Dr. Patel", doctor_pk="pk",
                             status="paired", confirmed_at=T0, peer_kind="doctor", is_demo=True),
        "doctor_message": message,
        "buddy_link": c.BuddyLink(link_id="b1", peer_id="u2", mode="mirror", state="active", first_name="Sam"),
        "buddy_alert": c.BuddyAlert(alert_id="a1", event_id="e1", urgency=3, confidence="device_confirmed", created_at=T0,
                                    status="open", audio_url=None),
        "hub_listing": c.HubListing(listing_id="h1", first_name="Chris", confidence="device_confirmed", elapsed_min=12,
                                    urgency=3, is_demo=True),
        "hub_claim": c.HubClaim(claim_id="c1", listing_id="h1", volunteer_id="u3", claimed_at=T0,
                                expires_at=datetime(2020, 1, 1, 22, 3), actions=["call"]),
        "family_story": c.FamilyStory(story_id="s1", night_date=D0, recipient_id="r1", level="story_only",
                                      text="A quiet night. Chris handled a dip on his own around three and was fine by morning.",
                                      status="demo", is_demo=True),
        "device_pairing": c.DevicePairing(device_id="irin-dev-0001", user_id="chris", device_url="http://localhost:8000",
                                          token="tok", paired_at=T0, state="paired"),
        "user_profile": c.UserProfile(user_id="u1", username="chris", first_name="Chris", languages=["en"],
                                      timezones=["America/New_York"],
                                      availability=[c.AvailabilitySlot(weekday=0, start="21:00", end="23:00")],
                                      created_at=T0),
        "buddy_match": c.BuddyMatch(match_id="mt1", user_id="u1", candidate_id="u2", score=14.0, hours_covered=10.0,
                                    mirror=True, shared_languages=["en"]),
        "family_bearer": c.FamilyBearer(bearer_id="fb1", recipient_id="r1", token_hash="sha256:...", created_at=T0),
        "narrative_scope": c.NarrativeScope(kind="family", scope_id="r1"),
        "second_opinion": c.SecondOpinion(invented_number=False, advice=False),
        "morning_report": c.MorningReport(report_id="rp1", night_date=D0, generated_at=datetime(2020, 1, 2, 7, 10),
                                          stats={"low": 58, "high": 142, "tir_pct": 88.0}, narrative="Template.",
                                          is_demo=True),
    }
