"""B3: the hub. POST /v0/hub/listing (key-gated; first name, alarm state,
elapsed minutes, confidence, is_demo; any glucose, location, or phone field
REJECTED), GET /v0/hub/list (volunteer bearer; urgency then confidence; demo
only to demo volunteers), POST /v0/hub/claim (exclusive 3 min lease; 409 with
the holder's expiry), GET /v0/hub/claim/{id}/script (only the live
claim-holder, only while the lease is live), POST /v0/hub/call (brokered,
no numbers), POST /v0/hub/treating (clears, 20 min; returns at TOP urgency on
expiry), POST /v0/hub/resolve, GET /v0/hub/audit."""

from __future__ import annotations

FORBIDDEN_LISTING_FIELDS = ("mgdl", "glucose", "location", "lat", "lon", "phone", "number")


def create_listing(payload: dict) -> dict:
    raise NotImplementedError("B3: hub listing")
