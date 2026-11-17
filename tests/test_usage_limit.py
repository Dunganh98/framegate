"""usage_limit: a `claude -p` run refused by the usage limit, when it lifts, how the wait ends."""

from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from conftest import DemoRepo
from framegate import orchestrator
from framegate.orchestrator import EXIT_GATE, LIMIT_WAIT_FILE
from framegate.usage_limit import clock_reset, detect_usage_limit

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"


def to_implement(r: DemoRepo) -> None:
    r.add_toml(FAST)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0


def implement_runs(r: DemoRepo) -> list[dict[str, object]]:
    return [e for e in r.events() if e["kind"] == "agent_run" and e["phase"] == "IMPLEMENT"]


def test_the_wait_ends_when_a_try_gets_through_or_the_user_wakes_the_run(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """IMPLEMENT: refused, then tried every 5 minutes; the try that gets through is the run
    itself. REVIEW: refused once, the user wakes the run."""
    r = demo_repo
    r.scenario(usage_limit={"IMPLEMENT": 3, "REVIEW": 1}, usage_limit_reset_s=3600)
    sleeps: list[float] = []
    woken: list[int] = []
    marker = r.root / "out" / "demo" / LIMIT_WAIT_FILE

    def sleep(until: datetime.datetime, wake: pathlib.Path | None = None) -> bool:
        assert wake == marker and marker.is_file()  # what `framegate wake` deletes
        if len(sleeps) < 3:
            sleeps.append((until - orchestrator.now_utc()).total_seconds())
            return False
        woken.append(r.af("wake", "demo"))  # from another window, while the run waits
        return not marker.is_file()

    monkeypatch.setattr("framegate.orchestrator.sleep_until", sleep)
    to_implement(r)
    assert r.af("run", "demo") == EXIT_GATE
    impl = [c for c in r.calls() if c["phase"] == "IMPLEMENT"]
    assert len(impl) == 4 and len({c["prompt"] for c in impl}) == 1  # refused, 2 tries, the run
    assert [round(s / 60) for s in sleeps] == [5, 10, 15]  # never the hour claude named
    # only the first refusal and the run that worked are on record
    assert [bool(e.get("usage_limit")) for e in implement_runs(r)] == [True, False]
    prompts = list((r.root / "out" / "runs").glob("*-demo-IMPLEMENT.prompt.md"))
    assert len(prompts) == 2
    assert len([e for e in r.events() if e["kind"] == "usage_limit"]) == 2
    gates = [e for e in r.events() if e["kind"] == "gate" and e.get("step")]
    assert [(e["attempt"], e["passed"]) for e in gates] == [(1, True)]  # no failed attempt
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert "a try every 5 min (not a failed attempt)" in log and "framegate wake demo" in log
    assert "try 3 got through" in log and "agent run reported an error" not in log

    assert woken == [0]
    assert "Woke demo: the run tries again" in capsys.readouterr().out
    assert len([c for c in r.calls() if c["phase"] == "REVIEW"]) == 2
    over = [e for e in r.events() if e["kind"] == "usage_limit_over"]
    assert [(e["how"], e["probes"], e["phase"]) for e in over] == [
        ("probe", 3, "IMPLEMENT"),
        ("wake", 0, "REVIEW"),
    ]
    assert "woken (framegate wake)" in log and not marker.exists()
    # nothing waits now: said so, exit 1; a file left by a killed run is removed
    marker.write_text("{}", encoding="utf-8")
    assert r.af("wake", "demo") == 1
    assert "not waiting for the usage limit" in capsys.readouterr().out
    assert not marker.exists()


def test_no_tries_when_switched_off(demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    """[agents] limit_probe_minutes = 0: one wait until the reset, as before."""
    r = demo_repo
    r.set_toml("agents", "limit_probe_minutes", "0")
    r.scenario(usage_limit={"IMPLEMENT": 1}, usage_limit_reset_s=3600)
    sleeps: list[float] = []
    monkeypatch.setattr(
        "framegate.orchestrator.sleep_until",
        lambda until, wake=None: sleeps.append((until - orchestrator.now_utc()).total_seconds()),
    )
    to_implement(r)
    assert r.af("run", "demo") == EXIT_GATE
    assert len(sleeps) == 1 and 3600 < sleeps[0] <= 3665
    assert len([c for c in r.calls() if c["phase"] == "IMPLEMENT"]) == 2
    over = [e for e in r.events() if e["kind"] == "usage_limit_over"]
    assert [(e["how"], e["probes"]) for e in over] == [("reset", 0)]
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert "a try every" not in log and "usage limit wait over; running again" in log


def test_sleep_until_ends_at_the_time_or_when_the_wake_file_is_gone(
    tmp_path: pathlib.Path,
) -> None:
    past = orchestrator.now_utc() - datetime.timedelta(seconds=1)
    later = orchestrator.now_utc() + datetime.timedelta(hours=1)
    marker = tmp_path / LIMIT_WAIT_FILE
    assert orchestrator.sleep_until(past) is False
    assert orchestrator.sleep_until(later, marker) is True  # no file: woken, at once
    marker.write_text("{}", encoding="utf-8")
    assert orchestrator.sleep_until(past, marker) is False


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
