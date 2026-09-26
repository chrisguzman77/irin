"""B3+: the buddy directory and matching. POST /v0/users, GET
/v0/users/search, POST /v0/match, POST /v0/match/{id}/accept | decline. ONE
aggregation pipeline: opted-in watchers with language overlap and a timezone
in the requester's set; hours_covered (the requester's 22:00-08:00 sleep
window in UTC intersected with the candidate's availability), mirror (UTC
offset difference 10-14 h), shared_languages; score = hours_covered + 2 *
mirror + shared_languages; sort, limit 3; declined candidates never return.
The introduction and why-this-match lines are narrative text (Muse) beside
the match; the scorer never reads them and an LLM never picks the buddy."""

from __future__ import annotations


def match(user_id: str) -> list:
    raise NotImplementedError("B3+: directory matching")
