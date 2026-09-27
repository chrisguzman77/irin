"""B3+: the Pi side of the buddy directory and matching (relay/README.md,
"Buddy directory (B3+)", PINNED; the relay's routes are called exactly as
pinned there).

Profile: the Pi verifies the CGM feed ITSELF (live: the Nightscout feed
answered with a recent, non-stale reading; demo: true) and sends the relay
only `cgm_verified`: the feed URL and token never leave the device. The
relay's user_bearer is kept in store.kv and never returned to the app.
Profile and bearer are kept per world (buddy_profile:demo / :live), so a
demo profile never uses, overwrites, or exposes the live one, and every
relay call carries this world's is_demo: a demo run never creates a real
directory entry (invariant 18); the relay only matches a demo user to demo
users.

Match: the relay scores (deterministic; a model never picks a buddy). The
Pi adds two lines beside each offer: `intro`, written by
narrative.generate("buddy_intro") in a worker thread (Meta when routed and
keyed, validated against hours_covered, the template otherwise; first names
only, never a glucose value), and `why`, the deterministic why-this-match
line built from the score parts.

Accept: once a match is accepted by BOTH sides (at accept time or later,
seen on the relay poll), the Pi starts a buddy pairing with the existing
PairingService (peer_kind "buddy") and posts its qr_url to POST
/v0/match/{id}/pair_link; a failure is retried on the next poll. The poll's
`matches` key becomes buddy_state.matches and a WS hub_update {event:
"match", match_id, status, pair_url} for every change."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from .. import store
from ..rounds import narrative
from ..rounds.relay_client import RelayClient

log = logging.getLogger("irin.buddy.directory")

_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
_TZ = re.compile(r"^(UTC|[A-Za-z_]+(/[A-Za-z0-9_+\-]+){1,2})$")
_CONTACT = re.compile(r"@|\d{7,}")  # an email or a phone number (invariant 15)


class DirectoryError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


# --- the app's shapes (the relay's are in relay/README.md) ---


class Availability(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start: str = Field(pattern=_HHMM)  # local "HH:MM"
    end: str = Field(pattern=_HHMM)


class Optins(BaseModel):
    """Four separate opt-ins, each revocable (invariant 16)."""
    have_buddy: bool = False
    be_watcher: bool = False
    hub_watchable: bool = False
    hub_volunteer: bool = False


class BuddyProfile(BaseModel):
    username: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z][A-Za-z0-9_.\-]*$")
    first_name: str = Field(min_length=1, max_length=40)
    languages: list[str] = Field(default_factory=list, max_length=10)
    timezones: list[str] = Field(default_factory=list, max_length=5)
    availability: list[Availability] = Field(default_factory=list, max_length=50)
    optins: Optins = Field(default_factory=Optins)

    @field_validator("username")
    @classmethod
    def _no_contact_username(cls, v: str) -> str:
        if _CONTACT.search(v):
            raise ValueError("a username never holds an email or a phone number")
        return v

    @field_validator("first_name")
    @classmethod
    def _first_name_only(cls, v: str) -> str:
        v = v.strip()
        if not v or re.search(r"[\d@]", v):
            raise ValueError("a first name only: no digits, no email")
        return v

    @field_validator("languages")
    @classmethod
    def _languages(cls, v: list[str]) -> list[str]:
        out = [s.strip() for s in v if s and s.strip()]
        if any(len(s) > 40 or re.search(r"\d", s) for s in out):
            raise ValueError("a language is a name")
        return out

    @field_validator("timezones")
    @classmethod
    def _timezones(cls, v: list[str]) -> list[str]:
        if any(not _TZ.match(s) for s in v):
            raise ValueError("timezones are IANA names such as America/New_York")
        return v


class StoredProfile(BuddyProfile):
    is_demo: bool = False


class ProfileResult(BaseModel):
    profile: StoredProfile
    cgm_verified: bool
    user_id: str
    is_demo: bool = False


class MatchOffer(BaseModel):
    match_id: str
    first_name: str
    hours_covered: float
    mirror: bool
    shared_languages: list[str]
    score: float
    intro: str
    why: str
    is_demo: bool = False


class MatchStatus(BaseModel):
    match_id: str
    status: Literal["offered", "accepted", "declined"]
    is_demo: bool = False


# --- the why-this-match line: the score parts in words, computed, never a model's ---


def _hours(v: Any) -> str:
    try:
        return f"{round(float(v), 1):g}"
    except (TypeError, ValueError):
        return "0"


def why_line(hours_covered: Any, mirror: bool, shared_languages: list[str]) -> str:
    parts = [f"Awake for {_hours(hours_covered)} of your night hours"]
    if mirror:
        parts.append("a mirror across time zones: their day is your night")
    if shared_languages:
        parts.append(f"you share {', '.join(shared_languages)}")
    return "; ".join(parts) + "."


def intro_line(first_name: str, shared_languages: list[str], mirror: bool, hours_covered: Any) -> str:
    """narrative.py's buddy_intro: validated, the template on any failure. Worker thread only."""
    return narrative.generate("buddy_intro", {"name": first_name, "shared_languages": list(shared_languages),
                                              "mirror": bool(mirror)}, {"hours_covered": hours_covered})


# --- the service ---


def _kv_json(key: str) -> dict | None:
    raw = store.get_kv(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


@dataclass
class BuddyDirectory:
    relay_url: str
    source_key: str
    is_demo: Callable[[], bool]
    verify_cgm: Callable[[], Awaitable[bool]]  # live: the Nightscout feed answered with a recent reading
    start_pairing: Callable[[], dict]  # PairingService.start("buddy"): blocking, run in a worker thread
    on_update: Callable[[dict], None] | None = None  # hub_update {event: "match", ...}
    transport: httpx.AsyncBaseTransport | None = None  # tests inject a MockTransport
    matches: dict[str, dict] = field(default_factory=dict)  # match_id -> {match_id, first_name, status, pair_url}
    _linking: set = field(default_factory=set, repr=False)

    # --- state ---

    def _world(self) -> str:
        return "demo" if self.is_demo() else "live"

    def _user(self) -> dict | None:
        return _kv_json(f"buddy_user:{self._world()}")

    def _linked(self) -> set[str]:
        return set((_kv_json(f"buddy_pair_links:{self._world()}") or {}).get("ids", []))

    def _mark_linked(self, match_id: str) -> None:
        ids = self._linked() | {match_id}
        store.set_kv(f"buddy_pair_links:{self._world()}", json.dumps({"ids": sorted(ids)}))

    def profile(self) -> StoredProfile | None:
        raw = _kv_json(f"buddy_profile:{self._world()}")
        return StoredProfile.model_validate(raw) if raw else None

    def snapshot(self) -> list[dict]:
        return [dict(m) for m in self.matches.values()]

    def reset(self) -> None:
        """A mode switch: the other world's matches are never shown in this one."""
        self.matches.clear()

    # --- the relay ---

    def _client(self, bearer: str | None = None) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {"X-Source-Key": self.source_key}
        return httpx.AsyncClient(base_url=self.relay_url.rstrip("/"), headers=headers, timeout=10.0,
                                 transport=self.transport)

    async def _call(self, method: str, path: str, body: dict, bearer: str | None = None,
                    refused: str = "the relay refused this request") -> Any:
        if not self.relay_url or not self.source_key:
            raise DirectoryError(503, "the relay is not configured on this device")
        try:
            async with self._client(bearer) as c:
                r = await c.request(method, path, json=body)
        except httpx.HTTPError as e:
            raise DirectoryError(502, f"relay unreachable ({type(e).__name__})")
        if r.status_code == 422:
            raise DirectoryError(422, "the relay refused the fields (no email, phone, location, or glucose value)")
        if r.status_code == 403:
            raise DirectoryError(403, refused)
        if r.status_code == 404:
            raise DirectoryError(404, "no such match for this device")
        if r.status_code == 409:
            raise DirectoryError(409, refused)
        if r.status_code != 200:
            raise DirectoryError(502, f"relay answered {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise DirectoryError(502, "relay answered without JSON")

    def _bearer(self) -> str:
        user = self._user()
        if not user or not user.get("user_bearer"):
            raise DirectoryError(409, "set up the buddy profile first")
        return str(user["user_bearer"])

    # --- profile ---

    async def save_profile(self, profile: BuddyProfile) -> ProfileResult:
        demo = self.is_demo()
        if demo:
            verified = True  # the replay feed is the demo's CGM
        else:
            try:
                verified = bool(await self.verify_cgm())
            except Exception:
                log.exception("CGM feed check failed")
                verified = False
        body = {**profile.model_dump(mode="json"), "cgm_verified": verified, "is_demo": demo}  # never the feed
        out = await self._call("POST", "/v0/users", body)
        user_id, bearer = out.get("user_id"), out.get("user_bearer")
        if not isinstance(user_id, str) or not user_id or not isinstance(bearer, str) or not bearer:
            raise DirectoryError(502, "relay answered without a user")
        world = "demo" if demo else "live"
        stored = StoredProfile(**profile.model_dump(), is_demo=demo)
        store.set_kv(f"buddy_profile:{world}", stored.model_dump_json())
        store.set_kv(f"buddy_user:{world}", json.dumps({"user_id": user_id, "user_bearer": bearer,
                                                        "cgm_verified": verified}))
        return ProfileResult(profile=stored, cgm_verified=verified, user_id=user_id, is_demo=demo)

    # --- match ---

    async def find_matches(self) -> list[MatchOffer]:
        demo = self.is_demo()
        rows = await self._call("POST", "/v0/match", {}, bearer=self._bearer(),
                               refused="no matches until the CGM feed is verified and 'have a buddy' is on")
        if not isinstance(rows, list):
            raise DirectoryError(502, "relay answered without a match list")
        rows = [r for r in rows if isinstance(r, dict) and r.get("match_id")][:3]

        async def offer(r: dict) -> MatchOffer:
            name = str(r.get("first_name") or "")[:40]
            langs = [str(x) for x in (r.get("shared_languages") or [])]
            mirror, hours = bool(r.get("mirror")), r.get("hours_covered") or 0
            intro = await asyncio.to_thread(intro_line, name, langs, mirror, hours)  # never on the event loop
            return MatchOffer(match_id=str(r["match_id"]), first_name=name, hours_covered=float(hours),
                              mirror=mirror, shared_languages=langs, score=float(r.get("score") or 0),
                              intro=intro, why=why_line(hours, mirror, langs), is_demo=demo)

        offers = list(await asyncio.gather(*(offer(r) for r in rows)))
        for o, r in zip(offers, rows):
            self._set(o.match_id, o.first_name, str(r.get("status") or "offered"),
                      self.matches.get(o.match_id, {}).get("pair_url"))
        return offers

    async def respond(self, match_id: str, action: Literal["accept", "decline"]) -> MatchStatus:
        out = await self._call("POST", f"/v0/match/{match_id}/{action}", {}, bearer=self._bearer(),
                               refused=("a declined match cannot be accepted" if action == "accept"
                                        else "an accepted match is ended by revoking the buddy pairing"))
        status = str(out.get("status") or "")
        if status not in ("offered", "accepted", "declined"):
            raise DirectoryError(502, "relay answered without a match status")
        prev = self.matches.get(match_id, {})
        self._set(match_id, str(out.get("first_name") or prev.get("first_name") or ""), status, prev.get("pair_url"))
        if status == "accepted":
            await self._ensure_pair_link(match_id)
        return MatchStatus(match_id=match_id, status=status, is_demo=self.is_demo())

    async def _ensure_pair_link(self, match_id: str) -> None:
        """Once per accepted match: a buddy pairing, its qr_url handed to the other side. Failures retry on the poll."""
        if match_id in self._linking or match_id in self._linked():
            return
        self._linking.add(match_id)
        try:
            started = await asyncio.to_thread(self.start_pairing)
            qr_url = started.get("qr_url") if isinstance(started, dict) else None
            if not qr_url:
                raise DirectoryError(502, "the pairing service returned no link")
            await self._call("POST", f"/v0/match/{match_id}/pair_link", {"pair_url": qr_url},
                             refused="the other side has not accepted yet")
            self._mark_linked(match_id)
        except Exception as e:
            log.warning("buddy pair link for %s not posted (%s); retried on the next poll", match_id,
                        getattr(e, "detail", type(e).__name__))
        finally:
            self._linking.discard(match_id)

    # --- the relay poll ---

    def _set(self, match_id: str, first_name: str, status: str, pair_url: str | None) -> None:
        new = {"match_id": match_id, "first_name": first_name, "status": status, "pair_url": pair_url}
        if self.matches.get(match_id) == new:
            return
        self.matches[match_id] = new
        if self.on_update is not None:
            try:
                self.on_update({"event": "match", "match_id": match_id, "status": status, "pair_url": pair_url})
            except Exception:
                log.exception("hub_update observer failed")

    async def on_matches(self, rows: list[dict]) -> None:
        """The poll's `matches` key: the relay's view of this user's matches is the truth."""
        seen = set()
        for r in rows or []:
            if not isinstance(r, dict) or not r.get("match_id"):
                continue
            mid = str(r["match_id"])
            seen.add(mid)
            pair_url = r.get("pair_url") if isinstance(r.get("pair_url"), str) else None
            self._set(mid, str(r.get("first_name") or "")[:40], str(r.get("status") or "offered"), pair_url)
            if r.get("status") == "accepted":
                await self._ensure_pair_link(mid)
        for mid in [m for m in self.matches if m not in seen]:
            del self.matches[mid]


@dataclass
class DirectoryRelayClient(RelayClient):
    """The relay client plus the poll's B3+ `matches` key (every other key is handled unchanged)."""
    on_matches: Callable[[list[dict]], Any] | None = None

    async def poll(self) -> dict[str, Any] | None:
        body = await super().poll()
        if body is not None and self.on_matches is not None and isinstance(body.get("matches"), list):
            try:
                result = self.on_matches(body["matches"])
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                log.exception("relay matches observer failed")
        return body
