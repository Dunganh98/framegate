"""usage_limit: detect a `claude -p` run refused by the usage limit, and when it lifts."""

from __future__ import annotations

import datetime
import json

from framegate.usage_limit import clock_reset, detect_usage_limit

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 10, 17, 0, tzinfo=UTC)


def stream(*msgs: dict[str, object]) -> str:
    return "\n".join(json.dumps(m) for m in msgs)


def limit_event(status: str = "rejected", resets: int | None = 1762794000) -> dict[str, object]:
    info: dict[str, object] = {"status": status, "rateLimitType": "five_hour"}
    if resets is not None:
        info["resetsAt"] = resets
    return {"type": "rate_limit_event", "rate_limit_info": info}


ERR = {"type": "result", "is_error": True, "result": "You've hit your limit · resets 4pm (UTC)"}


def test_successful_run_is_never_a_limit() -> None:
    ok = {"type": "result", "is_error": False, "result": "I added a rate limit check."}
    assert detect_usage_limit(stream(limit_event(), ok), ok, NOW) is None


def test_other_errors_are_not_a_limit() -> None:
    capped = {"type": "result", "is_error": True, "subtype": "error_max_turns"}
    assert detect_usage_limit(stream(capped), capped, NOW) is None
    budget = {"is_error": True, "errors": ["Reached maximum budget ($1.5)"]}
    assert detect_usage_limit("", budget, NOW) is None


def test_rejected_event_gives_the_exact_reset() -> None:
    lim = detect_usage_limit(stream(limit_event(), ERR), ERR, NOW)
    assert lim is not None
    assert lim.reset_at == datetime.datetime.fromtimestamp(1762794000, UTC)
    assert "hit your limit" in lim.message


def test_allowed_warning_event_is_ignored_text_still_counts() -> None:
    lim = detect_usage_limit(stream(limit_event("allowed_warning"), ERR), ERR, NOW)
    assert lim is not None
    # from the text: 4pm UTC, already past at 17:00 -> tomorrow
    assert lim.reset_at == datetime.datetime(2026, 11, 11, 16, 0, tzinfo=UTC)


def test_rejected_event_without_limit_text() -> None:
    err = {"type": "result", "is_error": True, "result": "API Error"}
    lim = detect_usage_limit(stream(limit_event(), err), err, NOW)
    assert lim is not None and lim.message == "Claude five_hour limit (rejected)"


def test_epoch_in_the_text() -> None:
    err = {"is_error": True, "result": "Claude AI usage limit reached|1762794000"}
    lim = detect_usage_limit("", err, NOW)
    assert lim is not None and lim.reset_at == datetime.datetime.fromtimestamp(1762794000, UTC)


def test_limit_without_any_reset_time() -> None:
    err = {"is_error": True, "result": "Usage limit reached."}
    lim = detect_usage_limit("", err, NOW)
    assert lim is not None and lim.reset_at is None


def test_clock_times() -> None:
    assert clock_reset("resets 6:30pm (UTC)", NOW) == datetime.datetime(
        2026, 11, 10, 18, 30, tzinfo=UTC
    )
    assert clock_reset("try again at Nov 12th, 2026 12:07 AM (UTC)", NOW) == datetime.datetime(
        2026, 11, 12, 0, 7, tzinfo=UTC
    )
    assert clock_reset("resets 13pm", NOW) is None
    assert clock_reset("try again at Foo 12th, 2026 1 AM", NOW) is None
    assert clock_reset("no time here", NOW) is None


def test_unknown_zone_falls_back_to_local_time() -> None:
    now = datetime.datetime(2026, 11, 10, 17, 0).astimezone()
    at = clock_reset("resets 4pm (Nowhere/Atlantis)", now)
    assert at is not None and at.tzinfo is not None
    assert (at.hour, at.minute, at.date()) == (16, 0, datetime.date(2026, 11, 11))
