"""The PIN gate. Every state-changing endpoint depends on require_pin;
the routes in contracts.FRESH_PIN_ENDPOINTS depend on require_fresh_pin.
One auth path only: the X-PIN header. No localhost exemption (cloudflared
also connects from localhost).

A2: a PIN-gated request that arrived through the tunnel (header
Cf-Connecting-Ip present, set by cloudflared) must ALSO carry
`Authorization: Bearer <owner token>` matching this device's paired owner
token (app/owner.py); requests without that header (the kiosk on localhost,
the LAN, tests) are unchanged."""

from __future__ import annotations

import hmac
import time

from fastapi import Header, HTTPException, Request

from .config import config
from .contracts import FRESH_PIN_ENDPOINTS
from .owner import token_matches


# PIN throttle: 5 wrong PINs from one client inside 10 minutes lock that client out for
# 10 minutes. Wall (monotonic) time on purpose: replay speed must not shorten it.
THROTTLE_MAX, THROTTLE_WINDOW_S = 5, 600.0
_LOOPBACK = {"127.0.0.1", "::1"}
_fails: dict[str, list[float]] = {}  # client -> monotonic times of wrong PINs
_locked_until: dict[str, float] = {}


def _client(request: Request) -> str:
    return request.headers.get("Cf-Connecting-Ip") or (request.client.host if request.client else "")


def _check(pin: str | None, request: Request) -> None:
    client = _client(request)
    throttled = client not in _LOOPBACK
    now = time.monotonic()
    if throttled and _locked_until.get(client, 0.0) > now:
        raise HTTPException(status_code=429, detail="too many PIN attempts; wait 10 minutes")
    if not config.PIN:
        raise HTTPException(status_code=503, detail="PIN not configured")
    if pin is None or not hmac.compare_digest(pin.encode(), config.PIN.encode()):
        if throttled and pin is not None:  # a missing header is not a guess
            recent = [t for t in _fails.get(client, []) if now - t < THROTTLE_WINDOW_S] + [now]
            _fails[client] = recent
            if len(recent) >= THROTTLE_MAX:
                _locked_until[client] = now + THROTTLE_WINDOW_S
                _fails.pop(client, None)
        raise HTTPException(status_code=401, detail="bad PIN")
    _fails.pop(client, None)
    _locked_until.pop(client, None)


def _check_owner(request: Request) -> None:
    if request.headers.get("Cf-Connecting-Ip") is None:
        return
    auth = request.headers.get("Authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not token_matches(token):
        raise HTTPException(status_code=401, detail="pair this phone with your Irin")


async def require_pin(request: Request, x_pin: str | None = Header(default=None, alias="X-PIN")) -> None:
    """Dependency for every mutating route."""
    _check(x_pin, request)
    _check_owner(request)


async def require_fresh_pin(request: Request, x_pin: str | None = Header(default=None, alias="X-PIN")) -> None:
    """The same check as require_pin, for the routes in FRESH_PIN_ENDPOINTS.

    The frontends must obtain this PIN from a fresh prompt, never storage:
    the kiosk re-prompts its touch keypad every time (cached PIN or not),
    the app re-prompts its PIN input. The server cannot tell the two
    apart, which is why the list lives in contracts.py and both frontends
    read it (GET /api/contracts/fresh_pin) instead of hand-copying it.
    """
    _check(x_pin, request)
    _check_owner(request)


__all__ = ["require_pin", "require_fresh_pin", "FRESH_PIN_ENDPOINTS"]
