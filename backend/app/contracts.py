"""Irin contracts — the treaty every lane imports.

NEVER modify this file unless the human explicitly asks. A contracts change
is its own tiny commit, announced in the group chat before merging.

Conventions:
- Every datetime is NAIVE, in the Pi's local time, and comes from
  app/clock.py (replay accelerates it; seek moves it). Dates are dates.
- Every record the demo-badging invariant touches carries is_demo.
- Sponsor models follow Appendix A of the updated Rounds spec
  (docs/Irin_Rounds.pdf, 2026-09-25) plus the two marked additions
  (is_demo everywhere, peer_kind on Pairing). See docs/plans/chris.md
  "Contracts additions".
- The app's TypeScript types are generated from /openapi.json (npm run
  types); nothing here is hand-copied on the frontend.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

Trend = str  # Nightscout direction names ("Flat", "FortyFiveDown", ...) or replay's


class Reading(BaseModel):
    timestamp: datetime
    glucose_mgdl: float
    trend: Trend
    source: Literal["nightscout", "replay"]
    is_stale: bool = False


TreatmentKind = Literal["bolus", "basal", "carbs", "note", "glp1_dose", "therapy_change"]


class Treatment(BaseModel):
    """A logged event. Invariant 2: insulin_units may only be stored with
    confirmed=True (echo-and-confirm). A parsed-but-unconfirmed voice entry
    lives in voice.py's pending state, never in a Treatment."""

    timestamp: datetime
    kind: TreatmentKind
    insulin_units: float | None = None
    carbs_g: float | None = None
    dose_label: str | None = None
    text: str | None = None
    confirmed: bool = False

    @model_validator(mode="after")
    def _insulin_requires_confirm(self) -> "Treatment":
        if self.insulin_units is not None and not self.confirmed:
            raise ValueError("insulin_units may only be stored with confirmed=True")
        return self


class Forecast(BaseModel):
    timestamp: datetime
    predicted_mgdl: float
    horizon_min: int = 30


AlarmStateName = Literal["idle", "pending", "active", "acknowledged", "rearmed"]
AlarmTrigger = Literal["predicted_low", "actual_low", "high", "stale"]


class AlarmState(BaseModel):
    state: AlarmStateName = "idle"
    trigger_type: AlarmTrigger | None = None
    started_at: datetime | None = None
    acknowledged_at: datetime | None = None


class PresenceState(BaseModel):
    mode: Literal["home", "away"] = "home"
    source: Literal["radar", "toggle"] = "radar"
    since: datetime


# --- Night Buddy opt-ins (a dict of four bools on Settings) ---


class NightBuddyOptIns(BaseModel):
    have_buddy: bool = False
    be_watcher: bool = False
    hub_watchable: bool = False
    hub_volunteer: bool = False


class EmergencyScript(BaseModel):
    steps: list[str] = Field(default_factory=list)


class FamilyRecipient(BaseModel):
    recipient_id: str
    name: str
    email: str
    level: Literal["story_only", "story_and_view"] = "story_only"
    send_mode: Literal["automatic", "approve_each"] = "approve_each"
    state: Literal["active", "paused", "revoked"] = "active"
    first_story_approved: bool = False


DEFAULT_LED_COLORS: dict[str, str] = {
    "ambient": "#1a1a40",
    "warning": "#ffb000",  # predicted low, amber
    "full": "#ff0000",  # actual low, red
    "stale": "#808080",
    "high": "#ffb000",
}


class Settings(BaseModel):
    low_threshold: float = 70
    high_threshold: float = 250
    predictive_enabled: bool = True
    predictive_lead_min: int = 30
    consecutive_predictions_n: int = 2
    night_window_start: str = "22:00"
    night_window_end: str = "07:00"
    high_alert_mode: Literal["oneshot", "remind"] = "oneshot"
    high_remind_hours: float | None = None
    led_colors: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_LED_COLORS))
    sound: str = "alarm_soft"
    volume: float = 0.8
    report_email: str | None = None
    basal_time: str | None = None
    iob_duration_hours: float = 4.0
    presence_override: Literal["auto", "home", "away"] = "auto"
    basal_units: float | None = None
    glucagon_on_hand: bool | None = None
    glucagon_expiry: date | None = None
    night_buddy: NightBuddyOptIns = Field(default_factory=NightBuddyOptIns)
    emergency_script: EmergencyScript | None = None
    family_recipients: list[FamilyRecipient] = Field(default_factory=list)


# Route paths that require a PIN from a FRESH keypad or prompt entry, never
# from storage. Read by auth.py (require_fresh_pin), display.js, and the app's
# usePin hook via GET /api/contracts/fresh_pin. Declared ONCE, here.
FRESH_PIN_ENDPOINTS: tuple[str, ...] = (
    "/api/pair/confirm",
    "/api/rounds/messages/{message_id}/confirm",
    "/api/rounds/messages/{message_id}/decline",
)


WSMessageType = Literal[
    "reading_update",
    "forecast_update",
    "alarm_state_change",
    "acknowledge",
    "settings_change",
    "treatment_logged",
    "presence_change",
    "mode_change",
    "state_snapshot",
    "card_sent",
    "doctor_message_received",
    "doctor_message_resolved",
    "recall_due",
    "symptom_check_due",
    "pairing_state",
    "plan_state",
    "buddy_alert",
    "hub_update",
    "treating_set",
    "family_story_pending",
    "family_story_sent",
]


class WSMessage(BaseModel):
    type: WSMessageType
    payload: dict[str, Any] = Field(default_factory=dict)


class StateSnapshot(BaseModel):
    """The payload of a state_snapshot message, sent by the ws hub on every
    new connection. Clients render from snapshot + updates, never from
    assumed history."""

    latest_reading: Reading | None = None
    forecast: Forecast | None = None
    alarm: AlarmState = Field(default_factory=AlarmState)
    settings: Settings = Field(default_factory=Settings)
    presence: PresenceState | None = None
    mode: Literal["replay", "nightscout"] = "replay"
    active_plan: "TitrationPlan | None" = None
    pending_doctor_messages: list["DoctorMessage"] = Field(default_factory=list)
    todays_checkin_status: dict[str, Any] = Field(default_factory=dict)
    pairing_state: dict[str, Any] = Field(default_factory=dict)
    buddy_state: dict[str, Any] = Field(default_factory=dict)
    family_story_status: list[dict[str, Any]] = Field(default_factory=list)
    clock_synced: bool = True


# ---------------------------------------------------------------------------
# --- Rounds ---
# ---------------------------------------------------------------------------

ReasonCode = Literal[
    "late_meal",
    "late_correction",
    "basal_late",
    "basal_missed",
    "exercise",
    "treated_low",
    "stale",
    "away",
    "clean",
]
CodeSource = Literal["logged", "inferred", "unknown"]
Confidence = Literal["measured", "reported", "inferred"]


class NightRecord(BaseModel):
    night_date: date
    window_start: datetime
    window_end: datetime
    coverage_pct: float
    reason_codes: list[str]
    code_source: CodeSource
    rise_mgdl: float | None = None
    dawn_rise_mgdl: float | None = None
    low_point_mgdl: float | None = None
    tbr_pct: float | None = None
    minutes_below_70: int = 0
    near_miss_count: int = 0
    level2_count: int = 0
    alarm_event_ids: list[str] = Field(default_factory=list)
    is_demo: bool = False


class AlarmEvent(BaseModel):
    """One record per alarm episode, written by the R2 observer on
    alarm.py's on_transition hook. presence_during is the RAW radar verdict
    for the episode (R2 rule), never PresenceState.mode."""

    event_id: str
    tier: Literal["predicted_low", "actual_low", "stale", "high"]
    started_at: datetime
    acknowledged_at: datetime | None = None
    ack_source: Literal["device", "app"] | None = None
    escalated: bool = False
    rearm_count: int = 0
    crossed_actual: bool = False
    presence_during: Literal["home", "away", "unknown"] = "unknown"
    is_demo: bool = False


class LowEvent(BaseModel):
    low_event_id: str
    night_date: date
    started_at: datetime
    nadir_mgdl: float
    nadir_at: datetime
    minutes_below_70: int
    auc_below_70: float  # mg/dL-minutes
    recovery_slope: float | None = None  # mg/dL per min over the 30 min after the nadir
    carbs_logged_within_30min: bool = False
    inferred_unfelt: bool = False
    alarm_event_id: str | None = None
    is_demo: bool = False


RecallAnswer = Literal["felt_and_treated", "woke_no_symptoms", "dont_remember", "was_awake"]


class LowEventRecall(BaseModel):
    """answer=None after the morning window is reported as "no answer",
    never fine; it never enters the unfelt-low denominator."""

    low_event_id: str
    asked_at: datetime
    answered_at: datetime | None = None
    answer: RecallAnswer | None = None
    is_demo: bool = False


class SymptomCheck(BaseModel):
    date: date
    gi: Literal["fine", "rough", "cant_eat"]
    is_demo: bool = False


class TitrationStep(BaseModel):
    index: int  # 0 = starting dose
    dose_label: str  # display only
    planned_start: date


class WatchOptions(BaseModel):
    ketone_prompts: bool = False
    step_week_vigilance: bool = False
    vigilance_offset_mgdl: float = 10.0  # predicted-low 70 -> 80


class TitrationPlan(BaseModel):
    plan_id: str
    drug_class: Literal["glp1", "gip_glp1", "weekly_basal"]
    drug_label: str  # display only
    steps: list[TitrationStep]
    started_at: date
    on_insulin: bool
    options: WatchOptions = Field(default_factory=WatchOptions)
    status: Literal["pending_confirm", "active", "graduated", "ended"] = "pending_confirm"
    is_demo: bool = False


CardProgram = Literal["standing", "step_watch"]
CardKind = Literal[
    "basal_check",
    "hypo_response",
    "follow_up",
    "early_check",
    "step_check",
    "step_gate",
    "safety",
    "graduation",
    "baseline_note",
]
CardStatus = Literal["green", "amber", "red", "insufficient"]
CardFlag = Literal[
    "lows",
    "highs",
    "awareness",
    "tolerance",
    "level2",
    "rearm",
    "ketone_risk",
    "baseline_thin",
    "dose_mismatch",
    "missed_injection",
    "rise_high",
    "rise_low",
]


class SignalCard(BaseModel):
    """Clinical Signal Card v0 (published in relay/README.md). One schema,
    two programs; `program` decides which sections a renderer shows. Every
    metric carries a confidence label; a card never shows a blank row; a
    card never contains a dose recommendation."""

    card_id: str
    schema_version: str = "0"
    program: CardProgram
    kind: CardKind
    status: CardStatus
    flags: list[CardFlag] = Field(default_factory=list)
    confidence: dict[str, Confidence] = Field(default_factory=dict)
    source: Literal["irin_bedside", "irin_brain"]
    patient_pseudonym: str
    plan_id: str | None = None
    step_index: int | None = None
    period_start: date
    period_end: date
    headline: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    nights: list[dict[str, Any]] = Field(default_factory=list)
    excluded_counts: dict[str, int] = Field(default_factory=dict)
    tolerance_days: list[dict[str, Any]] = Field(default_factory=list)
    narrative: str
    allowed_actions: list[str] = Field(default_factory=list)
    resource_categories: list[str] = Field(default_factory=list)
    is_demo: bool = False
    generated_at: datetime


class Pairing(BaseModel):
    """The doctor or buddy pairing (the crypto handshake, R5). The OWNER
    pairing is DevicePairing below, a separate model."""

    device_id: str
    doctor_id: str  # the peer's id
    doctor_display_name: str
    doctor_pk: str
    status: Literal["pending", "paired", "revoked"] = "pending"
    confirmed_at: datetime | None = None
    peer_kind: Literal["doctor", "buddy"] = "doctor"
    is_demo: bool = False


DoctorMessageKind = Literal[
    "plan_create",
    "plan_update",
    "proceed",
    "hold_step",
    "insulin_change",
    "note",
    "schedule_request",
    "end_watch",
    "dismiss",
]


class DoctorMessage(BaseModel):
    """Applies only after patient echo-and-confirm (invariant 8). Decline,
    24 h expiry, and silence apply nothing."""

    message_id: str
    plan_id: str | None = None
    kind: DoctorMessageKind
    hold_weeks: int | None = None  # 2 | 4 | 8
    insulin: Literal["basal", "bolus"] | None = None
    new_units: float | None = None
    start_date: date | None = None
    plan: TitrationPlan | None = None
    text: str | None = None
    created_at: datetime
    status: Literal["pending", "confirmed", "declined", "expired"] = "pending"


# ---------------------------------------------------------------------------
# --- Night Buddy (demo tier) ---
# ---------------------------------------------------------------------------

BuddyConfidence = Literal["device_confirmed", "unconfirmed"]


class BuddyLink(BaseModel):
    link_id: str
    peer_id: str
    mode: Literal["twin", "mirror"]
    state: str
    first_name: str


class BuddyAlert(BaseModel):
    """Never carries a glucose value, a location, or a contact (invariant 15)."""

    alert_id: str
    event_id: str
    urgency: int
    confidence: BuddyConfidence
    created_at: datetime
    status: str
    audio_url: str | None = None


class HubListing(BaseModel):
    listing_id: str
    first_name: str
    status: Literal["open", "claimed", "treating", "resolved"] = "open"
    confidence: BuddyConfidence
    elapsed_min: int
    urgency: int
    claim_expires_at: datetime | None = None
    treating_expires_at: datetime | None = None
    is_demo: bool = False


class HubClaim(BaseModel):
    claim_id: str
    listing_id: str
    volunteer_id: str
    claimed_at: datetime
    expires_at: datetime
    actions: list[str] = Field(default_factory=list)
    outcome: str | None = None


# ---------------------------------------------------------------------------
# --- Family Story ---
# ---------------------------------------------------------------------------


class FamilyStory(BaseModel):
    """Level story_only never contains a glucose value; a demo story is
    never emailed (invariant 19)."""

    story_id: str
    night_date: date
    recipient_id: str
    level: Literal["story_only", "story_and_view"]
    text: str
    status: Literal["pending_approval", "sent", "skipped", "failed", "demo"] = "pending_approval"
    sent_at: datetime | None = None
    is_demo: bool = False
    audio_url: str | None = None


# ---------------------------------------------------------------------------
# --- Owner pairing, directory, cloud, narrative ---
# ---------------------------------------------------------------------------


class DevicePairing(BaseModel):
    """The OWNER pairing (R5+): phone app <-> Pi through the relay. Separate
    from Pairing, whose peer_kind stays doctor | buddy. The token travels only
    in the QR or the typed code, never in a URL query."""

    device_id: str
    user_id: str
    device_url: str
    token: str
    paired_at: datetime | None = None
    state: Literal["pending", "paired", "revoked"] = "pending"


class AvailabilitySlot(BaseModel):
    weekday: int  # 0 = Monday
    start: str  # "HH:MM" local
    end: str


class CgmFeed(BaseModel):
    """Verified once at signup, never streamed; the relay holds no readings."""

    nightscout_url: str
    token: str


class UserProfile(BaseModel):
    user_id: str
    username: str
    first_name: str
    languages: list[str] = Field(default_factory=list)
    timezones: list[str] = Field(default_factory=list)  # IANA names
    availability: list[AvailabilitySlot] = Field(default_factory=list)
    sleep_window: tuple[str, str] = ("22:00", "08:00")  # locked
    cgm_feed: CgmFeed | None = None
    emergency_contact_ciphertext: str | None = None
    emergency_script: EmergencyScript = Field(default_factory=EmergencyScript)
    optins: NightBuddyOptIns = Field(default_factory=NightBuddyOptIns)
    created_at: datetime


class BuddyMatch(BaseModel):
    match_id: str
    user_id: str
    candidate_id: str
    score: float
    hours_covered: float
    mirror: bool
    shared_languages: list[str] = Field(default_factory=list)
    status: Literal["offered", "accepted", "declined"] = "offered"


class FamilyBearer(BaseModel):
    bearer_id: str
    recipient_id: str
    token_hash: str
    created_at: datetime
    revoked_at: datetime | None = None


class NarrativeScope(BaseModel):
    kind: Literal["patient", "doctor", "buddy", "family"]
    scope_id: str
    backboard_assistant_id: str | None = None
    thread_id: str | None = None


class SecondOpinion(BaseModel):
    invented_number: bool
    advice: bool


class MorningReport(BaseModel):
    report_id: str
    night_date: date
    generated_at: datetime
    stats: dict[str, Any] = Field(default_factory=dict)
    narrative: str
    graph_png_path: str | None = None
    audio_url: str | None = None
    is_demo: bool = False


StateSnapshot.model_rebuild()
