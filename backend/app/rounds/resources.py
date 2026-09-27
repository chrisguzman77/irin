"""R13: the resources handoff, the pharma moment of a card. A card names
CATEGORIES only (never a brand, never a number); the doctor picks category,
then brand, in the inbox, and the relay's POST /v0/resources/request is the
only thing a pharma-side system would ever see: {doctor_id, category, brand}
and no patient field, under the banner "No patient data shared with any
manufacturer". The seven categories are the relay's contract too
(relay/README.md); off-label use gets none (the plan has no on-label field
yet, so today every watch offers its categories: a contracts decision)."""

from __future__ import annotations

CATEGORIES = (
    "glucagon_access",
    "gi_side_effect_education",
    "copay_savings",
    "samples_next_pen",
    "bridge_supply",
    "prior_auth_hub",
    "ask_msl",
)

BANNER = "No patient data shared with any manufacturer"


def categories_for(kind: str, flags: list[str]) -> list[str]:
    """Which handoffs a card of this kind and colour may offer."""
    out: list[str] = []
    if kind == "hypo_response":
        out.append("glucagon_access")
    if kind == "early_check":  # the start of a therapy: coverage, access, and the first refills
        out += ["copay_savings", "prior_auth_hub", "bridge_supply"]
    if kind == "step_gate":  # the next pen strength is decided here
        out.append("samples_next_pen")
    if "tolerance" in flags and kind in ("early_check", "step_check", "step_gate"):
        out.append("gi_side_effect_education")
    if kind == "safety":
        out.append("ask_msl")
    return [c for c in dict.fromkeys(out) if c in CATEGORIES]
