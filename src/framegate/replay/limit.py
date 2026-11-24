"""A driver that died on the usage limit: no result for framegate. The run is "not run: usage
limit", never "bad"; the round waits for the reset and runs the candidate again."""

from __future__ import annotations

import datetime
import pathlib
import re
from collections.abc import Callable
from typing import Any

from framegate.replay.sandbox import read_json
from framegate.usage_limit import UsageLimit, detect_usage_limit, wait_until

#: the verdict and state of such a run
LIMITED = "not run: usage limit"
#: a reset further away than this ends the candidate for this round
WAIT_MAX_HOURS = 6.0
#: how often a candidate is run again in one round
RERUNS = 2
DEFAULT_WAIT_S = 15 * 60
MARGIN_S = 60
_HTTP_429 = re.compile(r"\b429\b|too many requests", re.I)


def detect(driver: pathlib.Path, now: datetime.datetime | None = None) -> UsageLimit | None:
    """The usage limit that ended the driver (its driver.json, else driver.err), or None."""
    try:
        answer: dict[str, Any] = read_json(driver / "driver.json")
    except ValueError:
        answer = {}
    if not answer:
        try:
            err = (driver / "driver.err").read_text(encoding="utf-8", errors="replace")
        except OSError:
            err = ""
        answer = {"is_error": True, "result": err[-2000:]}
    if not answer.get("is_error"):
        return None
    found = detect_usage_limit("", answer, now)
    text = str(answer.get("result") or "")
    if found is None and _HTTP_429.search(text):
        found = UsageLimit(None, " ".join(text.split())[:300])
    return found


def record(limit: UsageLimit | None) -> dict[str, Any]:
    """The limit as a part of the driver's result ({} = none)."""
    if limit is None:
        return {}
    reset = limit.reset_at.isoformat() if limit.reset_at else None
    return {"usage_limit": {"message": limit.message, "reset": reset}}


def wait_seconds(hit: dict[str, Any], now: datetime.datetime | None = None) -> float | None:
    """Seconds until the candidate runs again; None: the reset is beyond WAIT_MAX_HOURS."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        reset = datetime.datetime.fromisoformat(str(hit.get("reset")))
    except ValueError:
        reset = None
    until = wait_until(UsageLimit(reset, ""), now, DEFAULT_WAIT_S, MARGIN_S)
    seconds = (until - now).total_seconds()
    return seconds if seconds <= WAIT_MAX_HOURS * 3600 else None


def drive(
    start: Callable[[], dict[str, Any]],
    rebuild: Callable[[], None],
    wait: Callable[[float], bool],
    echo: Callable[[str], None],
    name: str,
) -> tuple[dict[str, Any], str]:
    """Run the driver, again after each usage limit: (its result, why "not run" or '')."""
    for again in range(RERUNS + 1):
        driven = start()
        hit = driven.get("usage_limit")
        if not hit or driven.get("stopped"):
            return driven, ""
        seconds = wait_seconds(hit) if again < RERUNS else None
        if seconds is None:
            return driven, f"{hit.get('message') or 'usage limit'}; the next round runs it again"
        echo(
            f"[{name}] {LIMITED}: waiting {seconds / 60:.0f} min for the reset, then the "
            "candidate runs again"
        )
        if wait(seconds):
            return {"stopped": "interrupted"}, ""
        rebuild()
    raise AssertionError("unreachable")
