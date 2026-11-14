"""Detect a `claude -p` run that did no work because the API could not be reached.

Seen live (da-argos, 2026-11-14): an implementer run ended after one turn with the result
"API Error: Can't reach the API server - check your internet or DNS (ENOTFOUND)", cost 0,
subtype "success". The step gate then ran on the untouched stub and the failure counted as
attempt 1 of 3. Such a run says nothing about the task: it is repeated after a short wait,
without a gate run and without counting an attempt (orchestrator.run_agent).

Two shapes count, both only on an error result (`is_error`; never ordinary agent text, so a
summary that begins with the words is not mistaken for one) and only on a real result object
(a `claude` that could not be started or printed no result is another problem, left to the
caller):
- the result text starts with "API Error:" / "API Error (" (claude's own wording for a
  failed API call: connection, DNS, 5xx, overloaded), also when the run had worked before
  it lost the API: its work is unfinished, the same task again picks it up;
- no cost and at most one turn, and not a turn / budget cap.
A run refused by the usage limit is detected before this (usage_limit.py) and waits for the
reset instead.
"""

from __future__ import annotations

import re
from typing import Any

#: seconds before the same run is started again; after the last one the run stops like
#: Ctrl+C (the state is kept, `framegate run` resumes it)
RETRY_WAITS_S = (30, 120, 300)
API_ERROR_RE = re.compile(r"^API Error\s*[:(]")


def detect_api_error(result: dict[str, Any]) -> str | None:
    """Claude's message when this run did no work because of an API / connection error, else
    None. `result` is the parsed result of the run (orchestrator.parse_result)."""
    if "num_turns" not in result and "total_cost_usd" not in result:
        return None  # no result object: claude did not start or printed none
    if not result.get("is_error") or str(result.get("subtype") or "").startswith("error_max"):
        return None
    text = " ".join(str(result.get("result") or "").split())
    if API_ERROR_RE.match(text):
        return text[:300]
    try:
        idle = (
            not float(result.get("total_cost_usd") or 0) and int(result.get("num_turns") or 0) <= 1
        )
    except (TypeError, ValueError):
        return None
    return (text[:300] or "the run ended with an error before it did any work") if idle else None
