"""Family Story checks (docs/FAMILY_STORY.md): a Level 1 story with a smuggled
glucose number falls back to the template; a demo-mode story never reaches
SMTP; a revoked or paused recipient receives nothing; a no-data night never
renders as "fine"; the first story waits for approval, then automatic; the
chip lists exactly the recipients that were sent; the clip is the cached
render of the sent text."""

from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth, main, store
from app.contracts import FamilyRecipient, Settings
from app.family_story import FamilyStoryService, family_prompt, template_story, validate_family_text
from tests.test_reports import NIGHT, WINDOW, the_save_readings

H = {"X-PIN": "1234"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store.config, "IRIN_DB", str(tmp_path / "t.db"))
    store.init_db()


@pytest.fixture
def stats():
    from app.reports import compute_stats

    return compute_stats(the_save_readings(), [], *WINDOW)


NO_DATA = {"readings": 0, "coverage_pct": 0.0, "low_mgdl": None, "low_at": None, "high_mgdl": None, "high_at": None,
           "tir_pct": None, "tbr_pct": None, "tar_pct": None, "minutes_below_70": 0, "carbs_g": 0.0, "insulin_units": 0.0}


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, attachments):
        self.sent.append({"to": to, "subject": subject, "body": body, "attachments": [Path(a).name for a in attachments]})


def recipients(*specs):
    return [FamilyRecipient(recipient_id=f"r{i}", name=n, email=f"{n.lower()}@example.com", level=level,
                            send_mode=mode, state=state, first_story_approved=approved)
            for i, (n, level, mode, state, approved) in enumerate(specs)]


def service(settings, mailer=None, **kw):
    return FamilyStoryService(settings=settings, mailer=mailer, narrative_backend=kw.pop("backend", "template"), **kw)


# --- F2: the inverted validator and the template ---


def test_level_1_bans_glucose_values_but_allows_clock_times(stats):
    assert validate_family_text("A low came around 2:18 AM; it was caught and treated.", "story_only", stats)
    assert not validate_family_text("A low of 50 around 2:18 AM, treated.", "story_only", stats)  # the true number, still banned
    assert not validate_family_text("Glucose dipped a little (mg/dL wise) but was handled.", "story_only", stats)
    assert not validate_family_text("About 20 minutes were low.", "story_only", stats)
    assert validate_family_text("A low of 50 mg/dL around 2:18 AM, treated.", "story_and_view", stats)
    assert not validate_family_text("A low of 48 mg/dL around 2:18 AM, treated.", "story_and_view", stats)  # invented


def test_no_data_night_is_never_fine():
    for level in ("story_only", "story_and_view"):
        text = template_story(NO_DATA, level, "Mom")
        assert "didn't have data" in text and "fine" not in text.lower()
        assert validate_family_text(text, level, NO_DATA)
        assert not validate_family_text("Everything was fine last night.", level, NO_DATA)


def test_templates_pass_their_own_validators(stats):
    for level in ("story_only", "story_and_view"):
        text = template_story(stats, level, "Mom")
        assert validate_family_text(text, level, stats), text
        assert "handled" in text or "treated" in text or "smoothly" in text
        assert "save" not in text.lower()  # save celebrations are never automatic
    assert "mg/dL" not in template_story(stats, "story_only", "Mom")
    assert "STORY ONLY" in family_prompt(stats, "story_only") and "ONLY the numbers" in family_prompt(stats, "story_and_view")


def test_smuggled_number_from_a_model_falls_back_to_the_template(db, stats):
    settings = Settings(family_recipients=recipients(("Mom", "story_only", "automatic", "active", True)))
    smuggled = lambda prompt, system: "A rough patch near 2:18 AM, down to 50, but they handled it. How's the garden?"
    svc = service(settings, FakeMailer(), backend="anthropic", model_call=smuggled)
    [story] = svc.build(NIGHT, stats, is_demo=False)
    assert story.text == template_story(stats, "story_only", "Mom") and story.status == "sent"
    good = lambda prompt, system: "A low came around 2:18 AM; they caught it and treated it. Ask them about the game."
    svc = service(settings, FakeMailer(), backend="anthropic", model_call=good)
    [story] = svc.build(NIGHT, stats, is_demo=False)
    assert story.text.startswith("A low came around 2:18 AM")


# --- F1 + F3: consent, the send hook, approval, demo ---


def test_paused_and_revoked_recipients_receive_nothing(db, stats):
    settings = Settings(family_recipients=recipients(
        ("Mom", "story_only", "automatic", "active", True), ("Dad", "story_only", "automatic", "paused", True),
        ("Sis", "story_and_view", "automatic", "revoked", True)))
    mailer = FakeMailer()
    stories = svc_build = service(settings, mailer).build(NIGHT, stats, is_demo=False)
    assert [s.recipient_id for s in stories] == ["r0"] and [m["to"] for m in mailer.sent] == ["mom@example.com"]


def test_demo_story_never_reaches_smtp(db, stats):
    settings = Settings(family_recipients=recipients(("Mom", "story_only", "automatic", "active", True)))
    mailer = FakeMailer()
    svc = service(settings, mailer)
    [story] = svc.build(NIGHT, stats, is_demo=True)
    assert story.status == "demo" and story.is_demo and mailer.sent == []
    assert svc.approve(story.story_id).status == "demo" and mailer.sent == []  # a tap cannot email it either
    assert store.select_family_story(story.story_id).status == "demo"


def test_first_story_waits_for_approval_then_automatic(db, stats):
    settings = Settings(family_recipients=recipients(("Mom", "story_and_view", "automatic", "active", False)))
    mailer = FakeMailer()
    svc = service(settings, mailer, family_view_url="https://family.example.test")
    [story] = svc.build(date(2021, 3, 1), stats, is_demo=False)
    assert story.status == "pending_approval" and mailer.sent == []
    sent = svc.approve(story.story_id)
    assert sent.status == "sent" and sent.sent_at is not None and settings.family_recipients[0].first_story_approved
    assert mailer.sent[0]["to"] == "mom@example.com" and "https://family.example.test" in mailer.sent[0]["body"]
    assert "stop these emails" in mailer.sent[0]["body"].lower()
    assert svc.approve(story.story_id).status == "sent" and len(mailer.sent) == 1  # a second tap sends nothing
    [again] = svc.build(date(2021, 3, 2), stats, is_demo=False)  # the next morning: automatic
    assert again.status == "sent" and len(mailer.sent) == 2


def test_approve_each_always_waits_and_skip_sends_nothing(db, stats):
    settings = Settings(family_recipients=recipients(("Mom", "story_only", "approve_each", "active", True)))
    mailer = FakeMailer()
    svc = service(settings, mailer)
    [story] = svc.build(NIGHT, stats, is_demo=False)
    assert story.status == "pending_approval"
    assert svc.skip(story.story_id).status == "skipped" and mailer.sent == []
    assert svc.approve(story.story_id).status == "skipped" and mailer.sent == []


def test_chip_lists_exactly_the_recipients_sent_and_the_clip_is_the_cached_render(db, stats, tmp_path):
    settings = Settings(family_recipients=recipients(
        ("Mom", "story_only", "automatic", "active", True), ("Dad", "story_only", "approve_each", "active", True)))
    mailer = FakeMailer()
    renders = []

    def render(text):
        renders.append(text)
        p = tmp_path / f"{abs(hash(text))}.mp3"
        p.write_bytes(b"ID3clip")
        return p

    svc = service(settings, mailer, render_clip=render)
    stories = svc.build(NIGHT, stats, is_demo=False)
    chip = [(s.recipient_id, s.status) for s in store.select_family_stories(NIGHT)]
    assert sorted(chip) == [("r0", "sent"), ("r1", "pending_approval")]
    assert mailer.sent[0]["attachments"] == [Path(render(stories[0].text)).name]  # the clip for the sent text
    assert all(s.audio_url and s.audio_url.endswith("/audio.mp3") for s in stories)
    assert set(renders) == {s.text for s in stories}  # rendered from the validated text, nothing else


def test_no_voice_backend_means_no_clip(db, stats):
    from app import voice_out

    assert voice_out.render("hello") is None  # VOICE_BACKEND=none on every laptop
    settings = Settings(family_recipients=recipients(("Mom", "story_only", "automatic", "active", True)))
    [story] = service(settings, FakeMailer()).build(NIGHT, stats, is_demo=False)
    assert story.audio_url is None and story.status == "sent"


# --- the endpoints ---


@pytest.fixture
def client(monkeypatch, tmp_path):
    saved = main.runtime.settings.model_dump()
    monkeypatch.setattr(auth.config, "PIN", "1234")
    monkeypatch.setattr(main.runtime.reports, "out_dir", tmp_path)
    with TestClient(main.app) as c:
        yield c
    restored = Settings.model_validate(saved)
    for name in Settings.model_fields:
        setattr(main.runtime.settings, name, getattr(restored, name))


def test_recipient_endpoints_and_the_demo_morning(client):
    c = client
    assert c.post("/api/family/recipients", json={"name": "Mom", "email": "mom@example.com"}).status_code == 401
    r = c.post("/api/family/recipients", json={"name": "Mom", "email": "mom@example.com", "send_mode": "automatic"}, headers=H)
    assert r.status_code == 200 and r.json()["state"] == "active" and r.json()["first_story_approved"] is False
    rid = r.json()["recipient_id"]
    assert c.post("/api/family/recipients", json={"name": "X", "email": "not-an-email"}, headers=H).status_code == 422
    assert c.post(f"/api/family/recipients/{rid}", json={"level": "story_and_view"}, headers=H).json()["level"] == "story_and_view"
    assert c.post(f"/api/family/recipients/{rid}/pause", headers=H).json()["state"] == "paused"
    assert c.post(f"/api/family/recipients/{rid}/resume", headers=H).json()["state"] == "active"
    assert [x["recipient_id"] for x in c.get("/api/family/recipients").json()] == [rid]
    # a demo morning: the story is built, badged DEMO, shown on the chip, never emailed
    night = main.runtime.datasource.rows[-1][0].date().isoformat()
    assert c.post("/api/reports/build", json={"night_date": night}, headers=H).status_code == 200
    stories = c.get(f"/api/family/stories?night_date={night}").json()
    assert len(stories) == 1 and stories[0]["status"] == "demo" and stories[0]["is_demo"] is True
    assert stories[0]["recipient_id"] == rid and c.get(f"/api/family/stories/{stories[0]['story_id']}/audio.mp3").status_code == 404
    assert c.post(f"/api/family/stories/{stories[0]['story_id']}/approve", headers=H).json()["status"] == "demo"
    assert c.post(f"/api/family/recipients/{rid}/revoke", headers=H).json()["state"] == "revoked"
    assert c.post(f"/api/family/recipients/{rid}", json={"name": "M"}, headers=H).status_code == 409
    assert c.post("/api/family/stories/nope/approve", headers=H).status_code == 404


# --- review follow-ups ---


def test_spelled_out_numbers_and_quiet_no_data_nights_are_rejected(stats):
    assert not validate_family_text("They dipped into the fifties around 3:00 AM but treated it.", "story_only", stats)
    assert not validate_family_text("About twenty minutes were low near 3:00 AM.", "story_only", stats)
    assert not validate_family_text("Half the night was a bit low, all handled.", "story_only", stats)
    assert validate_family_text("A low came around 3:00 AM; they caught it. How was the garden?", "story_only", stats)
    for text in ("All quiet last night, nothing to report. How was the weekend?", "Last night went well.",
                 "Irin didn't have data last night, but it was probably fine."):
        assert not validate_family_text(text, "story_only", NO_DATA), text
        assert not validate_family_text(text, "story_and_view", NO_DATA), text
    assert validate_family_text("Irin didn't have data last night, so there is no story to tell.", "story_only", NO_DATA)
    on_the_hour = {**stats, "low_at": "03:00"}
    assert validate_family_text(template_story(on_the_hour, "story_only", "Mom"), "story_only", on_the_hour)


def test_a_level_downgrade_after_build_never_delivers_the_higher_level_text(db, stats):
    settings = Settings(family_recipients=recipients(("Mom", "story_and_view", "approve_each", "active", True)))
    mailer = FakeMailer()
    svc = service(settings, mailer)
    [story] = svc.build(NIGHT, stats, is_demo=False)
    assert "mg/dL" in story.text and story.status == "pending_approval"
    settings.family_recipients[0].level = "story_only"  # the patient downgrades before tapping
    assert svc.approve(story.story_id).status == "skipped" and mailer.sent == []
    assert svc.skip_pending("r0") == 0  # nothing pending is left


def test_editing_email_or_level_resets_consent_and_drops_pending_stories(client):
    c = client
    r = c.post("/api/family/recipients", json={"name": "Mom", "email": "mom@example.com", "send_mode": "automatic"}, headers=H).json()
    rid = r["recipient_id"]
    main.runtime.settings.family_recipients[0].first_story_approved = True
    night = main.runtime.datasource.rows[-1][0].date().isoformat()
    from app import store as st

    st.upsert_family_story(main.FamilyStory(story_id="s-pending", night_date=night, recipient_id=rid, level="story_only",
                                            text="A low came around 2:18 AM; they handled it.", status="pending_approval"))
    r = c.post(f"/api/family/recipients/{rid}", json={"email": "other@example.com"}, headers=H).json()
    assert r["first_story_approved"] is False and r["email"] == "other@example.com"
    assert st.select_family_story("s-pending").status == "skipped"
    assert c.post("/api/settings", json={"family_recipients": []}, headers=H).status_code == 400  # only the family routes


def test_settings_and_consent_survive_a_restart(monkeypatch, tmp_path):
    saved = main.runtime.settings.model_dump()
    monkeypatch.setattr(auth.config, "PIN", "1234")
    try:
        with TestClient(main.app) as c:
            c.post("/api/settings", json={"night_window_end": "08:00"}, headers=H)
            rid = c.post("/api/family/recipients", json={"name": "Mom", "email": "mom@example.com"}, headers=H).json()["recipient_id"]
        for name in Settings.model_fields:  # the process restarts with defaults in memory
            setattr(main.runtime.settings, name, getattr(Settings(), name))
        with TestClient(main.app) as c:  # the same database (conftest's temp db for this test)
            assert c.get("/api/settings").json()["night_window_end"] == "08:00"
            assert [x["recipient_id"] for x in c.get("/api/family/recipients").json()] == [rid]
    finally:
        restored = Settings.model_validate(saved)
        for name in Settings.model_fields:
            setattr(main.runtime.settings, name, getattr(restored, name))
