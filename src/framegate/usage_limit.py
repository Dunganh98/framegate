"""Detect a `claude -p` run refused because the subscription's usage limit is reached, and
when the limit lifts.

Only error results count, never ordinary agent text, so a task that merely mentions rate
limits is not mistaken for one:
- a `rate_limit_event` in the stream whose status is not "allowed..." (carries `resetsAt`);
- an error result (or claude's stderr) whose text names the limit, e.g. "You've hit your
  limit · resets 4pm (Europe/Berlin)" or "Claude AI usage limit reached|1762790400".

Ported from harnessOrch (https://github.com/kehuydietlatoi/harnessOrch,
packages/engine/src/adapters/usage-limit.ts), shared by its author.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass
from typing import Any

LIMIT_RE = re.compile(
    r"(usage|rate)[ _-]?limit|limit reached|hit your limit|out of (credits|usage)", re.IGNORECASE
)
#: "...limit reached|1762790400": the reset as epoch seconds
EPOCH_RE = re.compile(r"\|(\d{10})\b")
#: "resets 4pm", "resets at 4:30 PM", "try again at Nov 11th, 2026 12:07 AM", "(Europe/Berlin)"
CLOCK_RE = re.compile(
    r"(?:try again at|resets?(?: at)?)\s+(?:([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b(?:\s*\(([\w/+\-]+)\))?",
    re.IGNORECASE,
)
MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


@dataclass
class UsageLimit:
    #: when the limit is expected to lift (aware datetime), None when the output gave none
    reset_at: datetime.datetime | None
    #: claude's own message, for the progress line and the event log
    message: str


def _zone(name: str | None) -> datetime.tzinfo | None:
    """The named time zone, or None (= local time) when it is unknown here (Windows without
    the tzdata package has no zone database)."""
    if not name:
        return None
    if name.upper() in ("UTC", "GMT", "ETC/UTC"):
        return datetime.timezone.utc
    try:
        import zoneinfo

        return zoneinfo.ZoneInfo(name)
    except Exception:
        return None


def clock_reset(text: str, now: datetime.datetime) -> datetime.datetime | None:
    """The reset named as a clock time in `text`. A bare "4pm" is its next occurrence; an
    explicit date is kept as given. `now` must be aware."""
    m = CLOCK_RE.search(text)
    if not m:
        return None
    hour12, minute = int(m.group(4)), int(m.group(5) or 0)
    if not 1 <= hour12 <= 12 or minute > 59:
        return None
    hour = hour12 % 12 + (12 if m.group(6).lower() == "pm" else 0)
    tz = _zone(m.group(7)) or now.astimezone().tzinfo
    base = now.astimezone(tz)
    if m.group(1):
        name = m.group(1).lower()
        month = next((i for i, n in enumerate(MONTHS, 1) if name in (n, n[:3])), 0)
        try:
            return base.replace(
                year=int(m.group(3)),
                month=month,
                day=int(m.group(2)),
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
        except ValueError:  # month 0 or an impossible date
            return None
    at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return at if at > base else at + datetime.timedelta(days=1)


def _from_text(text: str, now: datetime.datetime) -> datetime.datetime | None:
    m = EPOCH_RE.search(text)
    if m:
        return datetime.datetime.fromtimestamp(int(m.group(1)), datetime.timezone.utc)
    return clock_reset(text, now)


def detect_usage_limit(
    stdout: str, result: dict[str, Any], now: datetime.datetime | None = None
) -> UsageLimit | None:
    """The usage limit that stopped this run, or None. `result` is the parsed result (an
    unparseable run's result text is claude's stderr)."""
    if not result.get("is_error"):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    event: UsageLimit | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{") or "rate_limit_event" not in line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        info = msg.get("rate_limit_info") if isinstance(msg, dict) else None
        if msg.get("type") != "rate_limit_event" or not isinstance(info, dict):
            continue
        status = str(info.get("status") or "allowed")
        if status.startswith("allowed"):
            continue
        resets = info.get("resetsAt")
        reset = (
            datetime.datetime.fromtimestamp(resets, datetime.timezone.utc)
            if isinstance(resets, (int, float)) and resets > 0
            else None
        )
        kind = info.get("rateLimitType") or "usage"
        event = UsageLimit(reset, f"Claude {kind} limit ({status})")
    errors = result.get("errors")
    text = " ".join(
        [str(result.get("result") or "")]
        + ([str(e) for e in errors] if isinstance(errors, list) else [])
    )
    if LIMIT_RE.search(text):
        # the event's epoch is exact; the text's clock time has minute precision at best
        reset = event.reset_at if event and event.reset_at else _from_text(text, now)
        return UsageLimit(reset, text.strip()[:300])
    return event
