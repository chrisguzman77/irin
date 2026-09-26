"""The PIN gate. Every state-changing endpoint depends on require_pin;
the routes in contracts.FRESH_PIN_ENDPOINTS depend on require_fresh_pin.
One auth path only: the X-PIN header. No localhost exemption (cloudflared
also connects from localhost)."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from .config import config
from .contracts import FRESH_PIN_ENDPOINTS


def _check(pin: str | None) -> None:
    if not config.PIN:
        raise HTTPException(status_code=503, detail="PIN not configured")
    if pin is None or not hmac.compare_digest(pin.encode(), config.PIN.encode()):
        raise HTTPException(status_code=401, detail="bad PIN")


async def require_pin(x_pin: str | None = Header(default=None, alias="X-PIN")) -> None:
    """Dependency for every mutating route."""
    _check(x_pin)


async def require_fresh_pin(x_pin: str | None = Header(default=None, alias="X-PIN")) -> None:
    """The same check as require_pin, for the routes in FRESH_PIN_ENDPOINTS.

    The frontends must obtain this PIN from a fresh prompt, never storage:
    the kiosk re-prompts its touch keypad every time (cached PIN or not),
    the app re-prompts its PIN input. The server cannot tell the two
    apart, which is why the list lives in contracts.py and both frontends
    read it (GET /api/contracts/fresh_pin) instead of hand-copying it.
    """
    _check(x_pin)


__all__ = ["require_pin", "require_fresh_pin", "FRESH_PIN_ENDPOINTS"]
