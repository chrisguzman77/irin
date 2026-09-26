"""Family Story (docs/FAMILY_STORY.md; chris.md F-steps).
F1 recipients and consent (Settings.family_recipients, PIN-gated add/edit/
pause/revoke). F2 the family prompt and the INVERTED validator: a story_only
story must contain ZERO glucose values; story_and_view keeps the ordinary
no-invented-numbers rule; a violation falls back to the deterministic family
template; a no-data night is never told as "fine". F3 the send hook on the
morning-report job (approve_each and every first story wait for the
patient's tap; automatic sends over SMTP with an unsubscribe line; the
ElevenLabs clip attached via voice_out.py; demo stories are badged DEMO and
NEVER reach SMTP). F4 stretch: the story card via the relay. Invariant 19."""

from __future__ import annotations

from datetime import date

from .contracts import FamilyRecipient, FamilyStory


def build_story(night_date: date, recipient: FamilyRecipient) -> FamilyStory:
    raise NotImplementedError("F2: family prompt + inverted validator")


def validate_family_text(text: str, level: str, metrics: dict) -> bool:
    raise NotImplementedError("F2: level 1 bans glucose values; level 2 bans invented numbers")
