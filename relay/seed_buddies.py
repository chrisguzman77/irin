"""Buddy onboarding v2: 150 seeded sample profiles for the buddy directory.

    python seed_buddies.py          # insert or replace the 150 seeds
    python seed_buddies.py --wipe   # delete every seed (and the matches that name one)

Runs inside the relay container against ATLAS_URI through store.py, like the
app. Every seed is flagged `seed: true`, never a real person: usernames end in
_sample, first names only, cgm_verified true, be_watcher true, is_demo false
(so a live Pi can match them), and each has its own random source_key_hash, so
no device's key ever resolves to one. The profiles come from a fixed random
seed, so a re-run rewrites the same 150 in place (user_ids stay stable).
Every seed but Sam gets a single slot of 2-5 hours a day, so sam_sample
(Tokyo, English + Japanese, 08:00-18:00 every day) is always the top match
for a New York night that prefers Tokyo."""

from __future__ import annotations

import random
import secrets
import sys

import store
from directory import UserIn

COUNT = 150
SAM = {"username": "sam_sample", "first_name": "Sam", "languages": ["English", "Japanese"],
       "timezones": ["Asia/Tokyo"],
       "availability": [{"weekday": d, "start": "08:00", "end": "18:00"} for d in range(7)],
       "optins": {"have_buddy": True, "be_watcher": True, "hub_watchable": False, "hub_volunteer": True}}

# (zone, weight, first names, languages beside the zone's first): extra density in Tokyo, London, Sydney, LA
REGIONS = [
    ("Asia/Tokyo", 14, ["Aiko", "Haruto", "Yuki", "Ren", "Sakura", "Kenji", "Mei", "Sora", "Hina", "Takumi"],
     ["Japanese", "English"]),
    ("Europe/London", 14, ["Oliver", "Amelia", "Harry", "Isla", "George", "Freya", "Jack", "Poppy", "Alfie", "Ruby"],
     ["English", "Welsh", "Polish", "Urdu"]),
    ("Australia/Sydney", 14, ["Liam", "Charlotte", "Noah", "Matilda", "Lachlan", "Chloe", "Cooper", "Zoe"],
     ["English", "Mandarin", "Vietnamese", "Greek"]),
    ("America/Los_Angeles", 14, ["Mateo", "Ava", "Ethan", "Sofia", "Jayden", "Camila", "Ryan", "Maya", "Diego"],
     ["English", "Spanish", "Tagalog", "Korean"]),
    ("America/New_York", 6, ["Olivia", "James", "Emma", "Marcus", "Grace", "Andre"], ["English", "Spanish"]),
    ("America/Chicago", 4, ["Caleb", "Harper", "Luis", "Addison"], ["English", "Spanish"]),
    ("America/Denver", 2, ["Wyatt", "Brooke"], ["English"]),
    ("America/Toronto", 3, ["Owen", "Chloe", "Samir"], ["English", "French", "Punjabi"]),
    ("America/Mexico_City", 3, ["Santiago", "Valentina", "Ximena"], ["Spanish", "English"]),
    ("America/Sao_Paulo", 4, ["Gabriel", "Beatriz", "Rafael", "Larissa"], ["Portuguese", "English", "Spanish"]),
    ("America/Bogota", 2, ["Andrés", "Mariana"], ["Spanish", "English"]),
    ("America/Argentina/Buenos_Aires", 2, ["Tomás", "Lucía"], ["Spanish", "Italian", "English"]),
    ("Europe/Dublin", 2, ["Cian", "Aoife"], ["English", "Irish"]),
    ("Europe/Paris", 3, ["Louis", "Chloé", "Hugo"], ["French", "English", "Arabic"]),
    ("Europe/Berlin", 3, ["Lukas", "Hannah", "Felix"], ["German", "English", "Turkish"]),
    ("Europe/Madrid", 2, ["Pablo", "Lucía"], ["Spanish", "Catalan", "English"]),
    ("Europe/Rome", 2, ["Matteo", "Giulia"], ["Italian", "English"]),
    ("Europe/Amsterdam", 2, ["Daan", "Sanne"], ["Dutch", "English", "German"]),
    ("Europe/Stockholm", 2, ["Elias", "Astrid"], ["Swedish", "English"]),
    ("Europe/Warsaw", 2, ["Jakub", "Zofia"], ["Polish", "English"]),
    ("Europe/Athens", 1, ["Nikos"], ["Greek", "English"]),
    ("Europe/Istanbul", 2, ["Emre", "Elif"], ["Turkish", "English"]),
    ("Africa/Lagos", 3, ["Chinedu", "Amara", "Tunde"], ["English", "Yoruba", "Igbo"]),
    ("Africa/Nairobi", 2, ["Wanjiru", "Kamau"], ["Swahili", "English"]),
    ("Africa/Johannesburg", 2, ["Thabo", "Lerato"], ["English", "Zulu", "Afrikaans"]),
    ("Africa/Cairo", 2, ["Omar", "Nour"], ["Arabic", "English"]),
    ("Asia/Dubai", 2, ["Ahmed", "Layla"], ["Arabic", "English", "Hindi"]),
    ("Asia/Kolkata", 4, ["Aarav", "Priya", "Rohan", "Ananya"], ["Hindi", "English", "Tamil", "Bengali"]),
    ("Asia/Singapore", 3, ["Wei", "Siti", "Arjun"], ["English", "Mandarin", "Malay", "Tamil"]),
    ("Asia/Manila", 2, ["Miguel", "Andrea"], ["Tagalog", "English"]),
    ("Asia/Shanghai", 2, ["Hao", "Xin"], ["Mandarin", "English"]),
    ("Asia/Seoul", 3, ["Minjun", "Jiwoo", "Seoyeon"], ["Korean", "English"]),
    ("Pacific/Auckland", 2, ["Nikau", "Aroha"], ["English", "Māori"]),
]


def _slots(rng: random.Random) -> list[dict]:
    """One daily slot of 2-5 hours on 3-7 weekdays; late slots run past midnight."""
    start, length = rng.choice([6, 7, 8, 9, 12, 13, 17, 18, 19, 20, 21, 22, 23]), rng.randint(2, 5)
    days = sorted(rng.sample(range(7), rng.randint(3, 7)))
    end = (start + length) % 24
    return [{"weekday": d, "start": f"{start:02d}:00", "end": f"{end:02d}:00"} for d in days]


def profiles() -> list[dict]:
    """The 150 seeds (Sam first), deterministic, each validated by the same model POST /v0/users uses."""
    rng = random.Random(2026)
    zones = [r for r in REGIONS for _ in range(r[1])]
    out, used = [dict(SAM)], {"sam_sample"}
    while len(out) < COUNT:
        zone, _, names, langs = rng.choice(zones)
        first = rng.choice(names)
        username = f"{first.lower().translate(str.maketrans('áéíóúā', 'aeioua'))}{rng.randint(10, 99)}_sample"
        if username in used or not username.isascii():
            continue
        used.add(username)
        spoken = [langs[0]] + rng.sample(langs[1:], rng.randint(0, min(2, len(langs) - 1)))
        out.append({"username": username, "first_name": first, "languages": spoken, "timezones": [zone],
                    "availability": _slots(rng),
                    "optins": {"have_buddy": rng.random() < 0.7, "be_watcher": True,
                               "hub_watchable": rng.random() < 0.3, "hub_volunteer": rng.random() < 0.4}})
    # validated as any user, except the reserved _sample suffix (real users may never take it)
    return [{**UserIn(**{**p, "username": p["username"][:-len("_sample")] + "_s"}, cgm_verified=True,
                      is_demo=False).model_dump(), "username": p["username"], "seed": True} for p in out]


def seed() -> int:
    """Insert or replace every seed in place (user_id and source_key_hash kept); drop any seed no longer listed."""
    users, now = store.db()["users"], store.now()
    rows = profiles()
    for p in rows:
        users.update_one({"username": p["username"], "seed": True},
                         {"$set": {**p, "updated_at": now},
                          "$setOnInsert": {"user_id": f"u-{secrets.token_hex(6)}", "created_at": now,
                                           "source_key_hash": store.bearer_hash(secrets.token_hex(32))}},
                         upsert=True)
    users.delete_many({"seed": True, "username": {"$nin": [p["username"] for p in rows]}})
    store.audit("directory.seed", count=len(rows))
    return len(rows)


def wipe() -> int:
    """Delete every seed and every match that names one."""
    ids = [u["user_id"] for u in store.db()["users"].find({"seed": True}, {"user_id": 1})]
    store.db()["matches"].delete_many({"users": {"$in": ids}})
    n = store.db()["users"].delete_many({"seed": True}).deleted_count
    store.audit("directory.seed_wipe", count=n)
    return n


if __name__ == "__main__":
    store.ensure_indexes()
    if "--wipe" in sys.argv[1:]:
        print(f"wiped {wipe()} sample profiles")
    else:
        print(f"seeded {seed()} sample profiles")
