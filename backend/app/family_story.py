"""Family Story (docs/FAMILY_STORY.md; chris.md F-steps). The built half of
the Meta entry, riding the morning report.

F1 recipients and consent live in Settings.family_recipients (PIN-gated
add / edit / pause / resume / revoke in main.py). F2 the family prompt and
the INVERTED validator: a story_only story must contain ZERO glucose values
(clock times allowed); story_and_view keeps the ordinary no-invented-numbers
rule; any violation falls back to the deterministic family template; a
no-data night is never told as "fine". F3 the send hook on the morning
report job: one story per active recipient; approve_each and every FIRST
story wait for the patient's tap; automatic stories go over the existing
SMTP path with an unsubscribe line and the cached ElevenLabs clip attached;
demo stories are badged DEMO and NEVER reach SMTP (invariant 19). Save
celebrations are never part of the automatic story.

The words come from narrative.py's chain (task family_story: Muse Spark
through Backboard / the Meta Model API, then Claude, then this template, the
validator after every link), one family scope per recipient, memory Readonly.
build() runs in a worker thread (main._build_night), never on the event loop."""

from __future__ import annotations

import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from . import store
from .clock import clock
from .contracts import FamilyRecipient, FamilyStory, NarrativeScope, Settings
from .reports import Mailer, extract_numbers, validate_narrative
from .rounds import narrative

log = logging.getLogger("irin.family_story")

_CLOCK_TIME = re.compile(r"^\d{1,2}:\d{2}$")
_MGDL = re.compile(r"mg\s*/\s*dl|mmol", re.IGNORECASE)
_NUMBER_WORDS = re.compile(
    r"\b(zero|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    r"twenties|thirties|forties|fifties|sixties|seventies|eighties|nineties|hundreds|dozen)\b",
    re.IGNORECASE)  # "one of those nights", "half asleep", "a quarter past" stay allowed: no value hides there
_NO_DATA_PHRASE = re.compile(r"didn.t have data|no data|did not have data", re.IGNORECASE)
_NO_DATA_BANNED = re.compile(r"\b(fine|quiet|smooth|smoothly|uneventful)\b|went well|slept well", re.IGNORECASE)


def _clock_words(hhmm: str | None) -> str:
    """'04:00' -> 'around 4 AM' (family register, no leading zeros)."""
    if not hhmm:
        return "overnight"
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "AM" if h < 12 else "PM"
    return f"around {h % 12 or 12}:{m:02d} {suffix}"  # always H:MM, so no bare hour digit ever appears


# --- the deterministic family template (the no-network path and the fallback) ---


def template_story(stats: dict, level: str, name: str) -> str:
    """Two or three sentences in family register. story_only carries no
    glucose value at all; story_and_view may name the numbers."""
    if not stats.get("readings"):
        return (f"Irin didn't have data last night, so there is no story to tell about the night. "
                f"It may be worth asking how the sensor is doing.")
    minutes_low = stats.get("minutes_below_70") or 0
    treated = bool(stats.get("carbs_g"))
    if level == "story_and_view":
        if minutes_low:
            first = (f"Last night had a low {_clock_words(stats.get('low_at'))}, down to {stats['low_mgdl']} mg/dL "
                     f"for about {minutes_low} minutes; it was caught and treated, and the night settled again.")
        else:
            first = (f"Last night went smoothly: the lowest point was {stats['low_mgdl']} mg/dL "
                     f"{_clock_words(stats.get('low_at'))}, and {stats['tir_pct']}% of the night was in range.")
    else:
        if minutes_low:
            first = (f"Last night had a low {_clock_words(stats.get('low_at'))}; it was caught and "
                     f"{'treated' if treated else 'handled'}, and the night settled again.")
        else:
            first = "Last night went smoothly, with nothing that needed attention."
    if (stats.get("coverage_pct") or 0) < 85 and stats.get("readings"):
        first += " The sensor missed part of the night, so this is the part Irin could see."
    return first + " Something to ask about today that isn't glucose: how did they sleep?"


# --- the validator, inverted per level ---


def validate_family_text(text: str, level: str, stats: dict) -> bool:
    """story_only: zero glucose values (any number that is not a clock time is
    banned, spelled-out numbers and fractions too, and so is the unit);
    story_and_view: the ordinary no-invented-numbers rule. Both: a no-data
    night must say Irin had no data and is never told as fine or quiet."""
    if not stats.get("readings"):
        if not _NO_DATA_PHRASE.search(text) or _NO_DATA_BANNED.search(text):
            return False
    if level == "story_only":
        if _MGDL.search(text) or _NUMBER_WORDS.search(text):
            return False
        return all(_CLOCK_TIME.match(tok) for tok in extract_numbers(text))
    return validate_narrative(text, stats)


# --- the prompt (narrative.py's family_story task) ---

FAMILY_SYSTEM_PROMPT = (
    "You write two or three sentences to a family member of an adult with type 1 diabetes about last night, "
    "in a warm family register: how the night went, whether anything happened, and that the person handled it "
    "(always frame their agency, never alarm, never medical advice, never dose talk). Be honest: a rough night is "
    "told calmly but truthfully; if there is no data, say Irin didn't have data last night and never say it was fine. "
    "Clock times exactly as given. Optionally end with one conversation starter that is not about glucose."
)


def family_prompt(stats: dict, level: str) -> str:
    if level == "story_only":
        rule = ("STORY ONLY: do not state any glucose value or unit; no numbers at all except clock times, "
                "not even spelled out (no 'the fifties', no 'twenty minutes').")
    else:
        rule = "Use ONLY the numbers given; never invent or round differently."
    return f"{rule} Last night's data: {stats}"


# --- the service: build, send, approve ---


@dataclass
class FamilyStoryService:
    settings: Settings
    mailer: Mailer | None = None
    render_clip: Callable[[str], Path | None] = lambda text: None  # voice_out.render when a voice backend is set
    family_view_url: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _text(self, stats: dict, recipient: FamilyRecipient) -> str:
        """narrative.generate validates every link's words with validate_family_text at the recipient's
        level and falls back to template_story; the family scope keeps Backboard memory Readonly."""
        return narrative.generate("family_story", {
            "level": recipient.level, "name": recipient.name,
            "scope": NarrativeScope(kind="family", scope_id=recipient.recipient_id)}, stats)

    def build(self, night_date: date, stats: dict, is_demo: bool) -> list[FamilyStory]:
        """One story per active recipient (paused and revoked get nothing).
        Demo stories are stored with status demo and never sent. Automatic
        stories to an approved recipient are sent at once; the rest wait."""
        stories: list[FamilyStory] = []
        for r in self.settings.family_recipients:
            if r.state != "active":
                continue
            text = self._text(stats, r)
            story = FamilyStory(story_id=uuid.uuid4().hex, night_date=night_date, recipient_id=r.recipient_id,
                                level=r.level, text=text, is_demo=is_demo)
            clip = self.render_clip(text)  # cached by text hash, so the audio never carries a number the text does not
            if clip is not None:
                story.audio_url = f"/api/family/stories/{story.story_id}/audio.mp3"
            if is_demo:
                story.status = "demo"
            elif r.send_mode == "automatic" and r.first_story_approved:
                story = self._send(story, r)
            else:
                story.status = "pending_approval"
            store.upsert_family_story(story)
            stories.append(story)
        return stories

    def _recipient(self, recipient_id: str) -> FamilyRecipient | None:
        return next((r for r in self.settings.family_recipients if r.recipient_id == recipient_id), None)

    def _send(self, story: FamilyStory, recipient: FamilyRecipient) -> FamilyStory:
        """Over the morning report's SMTP path. NEVER for a demo story (invariant 19)."""
        if story.is_demo or recipient.state != "active":
            return story.model_copy(update={"status": "demo" if story.is_demo else "skipped"})
        if self.mailer is None:
            return story.model_copy(update={"status": "failed"})
        body = story.text
        if story.level == "story_and_view" and self.family_view_url:
            body += f"\n\nSee the night: {self.family_view_url}"
        body += ("\n\nYou get this because you were added as family in Irin. To stop these emails, reply to this "
                 "email and ask, or ask to be paused in Irin.")
        attachments = [Path(p) for p in [self.render_clip(story.text)] if p is not None]
        try:
            self.mailer.send(recipient.email, f"How last night went, {story.night_date.isoformat()}", body, attachments)
        except Exception:
            log.exception("family story email failed")
            return story.model_copy(update={"status": "failed"})
        return story.model_copy(update={"status": "sent", "sent_at": clock.now()})

    def approve(self, story_id: str) -> FamilyStory | None:
        """The patient's tap: send it (unless demo) and mark the recipient's
        first story approved. A story built at another level than the
        recipient now has is skipped, never sent (a downgrade to story_only
        must never deliver the story_and_view text)."""
        with self._lock:
            story = store.select_family_story(story_id)
            if story is None or story.status != "pending_approval":
                return story
            recipient = self._recipient(story.recipient_id)
            if recipient is None or recipient.level != story.level:
                story = story.model_copy(update={"status": "skipped"})
            else:
                story = self._send(story, recipient)
                if story.status == "sent":
                    recipient.first_story_approved = True
            store.upsert_family_story(story)
            return story

    def skip(self, story_id: str) -> FamilyStory | None:
        with self._lock:
            story = store.select_family_story(story_id)
            if story is None or story.status != "pending_approval":
                return story
            story = story.model_copy(update={"status": "skipped"})
            store.upsert_family_story(story)
            return story

    def skip_pending(self, recipient_id: str) -> int:
        """After an edit of a recipient's email or level: every pending story
        for them is skipped; the next morning builds a fresh one at the new level."""
        n = 0
        with self._lock:
            for story in store.select_family_stories(limit=500):
                if story.recipient_id == recipient_id and story.status == "pending_approval":
                    store.upsert_family_story(story.model_copy(update={"status": "skipped"}))
                    n += 1
        return n
