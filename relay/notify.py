"""B4+: the WhatsApp Cloud API channel (the second channel beside the
watcher page's voice loop). POST https://graph.facebook.com/<version>/
{WHATSAPP_PHONE_NUMBER_ID}/messages with WHATSAPP_TOKEN: the buddy text
("Irin: your buddy <first name> is in trouble. The alarm has been
unacknowledged for N minutes. Open <watch URL>") then the same ElevenLabs
clip as an audio message (link on cloud.<domain>). NEVER a glucose value in a
payload (/review and /audit grep this file for mgdl). Free-form messages only
inside the 24-hour window; outside it, an approved template."""

from __future__ import annotations

import os

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")


async def send_buddy_alert(to_wa_id: str, first_name: str, minutes: int, watch_url: str, audio_url: str | None) -> None:
    raise NotImplementedError("B4+: WhatsApp channel")
