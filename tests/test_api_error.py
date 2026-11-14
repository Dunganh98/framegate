"""A run that did no work because the API could not be reached is repeated after a short
wait: no gate run, no attempt counted; still unreachable -> the run stops and can be resumed.

Seen live (da-argos): result "API Error: Can't reach the API server ... (ENOTFOUND)", one turn,
cost 0, subtype "success"; the step gate ran on the stub and the failure was "attempt 1 of 3"."""

from __future__ import annotations

import pytest

from conftest import DemoRepo
from framegate import api_error, orchestrator
from framegate.api_error import detect_api_error
from framegate.orchestrator import EXIT_GATE, EXIT_INTERRUPTED

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
LIVE = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "result": "API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)",
    "num_turns": 1,
    "total_cost_usd": 0,
}


def test_detection() -> None:
    assert detect_api_error(LIVE) == LIVE["result"]
    # also a run that worked for a while, then lost the API
    late = {**LIVE, "num_turns": 14, "total_cost_usd": 0.4}
    assert detect_api_error(late) is not None
    assert detect_api_error({**LIVE, "result": "API Error: 529 Overloaded"}) is not None
    # an error before any work, whatever claude calls it
    idle = {"is_error": True, "result": "Connection error.", "num_turns": 0, "total_cost_usd": 0}
    assert detect_api_error(idle) == "Connection error."
    assert detect_api_error({**idle, "result": ""}) == (
        "the run ended with an error before it did any work"
    )
    # not one: a normal run, a run that worked and then failed, a cap, text that only
    # mentions the words, a claude that did not start (no result object)
    done = {"is_error": False, "result": "done", "num_turns": 3, "total_cost_usd": 0.01}
    assert detect_api_error(done) is None
    assert detect_api_error({**done, "result": "API Error: handled now, see the diff."}) is None
    words = {**done, "is_error": True, "result": "API Error handling is not finished."}
    assert detect_api_error(words) is None
    assert detect_api_error({**done, "is_error": True, "result": "boom"}) is None
    capped = {"is_error": True, "subtype": "error_max_turns", "num_turns": 1, "total_cost_usd": 0}
    assert detect_api_error(capped) is None
    assert detect_api_error({"is_error": True, "result": "could not start claude: x"}) is None
    assert detect_api_error({"is_error": True, "result": "", "api_turns": 0}) is None


def to_implement(r: DemoRepo) -> None:
    r.add_toml(FAST)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0


def test_the_same_run_again_without_a_gate_or_an_attempt(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = demo_repo
    r.scenario(api_error={"IMPLEMENT": 2, "REVIEW": 1})
    waits: list[float] = []
    monkeypatch.setattr(
        "framegate.orchestrator.sleep_until",
        lambda until: waits.append(round((until - orchestrator.now_utc()).total_seconds())),
    )
    to_implement(r)
    assert r.af("run", "demo") == EXIT_GATE
    impl = [c for c in r.calls() if c["phase"] == "IMPLEMENT"]
    assert len(impl) == 3 and len({c["prompt"] for c in impl}) == 1  # the same task
    assert len([c for c in r.calls() if c["phase"] == "REVIEW"]) == 2  # every phase's runs
    assert waits == [30, 120, 30]  # per agent run: 30 s, 2 min (5 min would be next)
    # one gate run, one attempt: the two idle runs were neither
    gates = [e for e in r.events() if e["kind"] == "gate"]
    assert [(e["attempt"], e["passed"]) for e in gates] == [(1, True)]
    ev = [e for e in r.events() if e["kind"] == "api_error"]
    assert [(e["phase"], e["retry"], e["wait_s"]) for e in ev] == [
        ("IMPLEMENT", 1, 30),
        ("IMPLEMENT", 2, 120),
        ("REVIEW", 1, 30),
    ]
    assert ev[0]["step"] == "step 1 - add" and "ENOTFOUND" in ev[0]["message"]
    runs = [e for e in r.events() if e["kind"] == "agent_run" and e["phase"] == "IMPLEMENT"]
    assert [bool(e.get("api_error")) for e in runs] == [True, True, False]
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert "API error, no work done" in log and "retry 1 of 3, not a failed attempt" in log
    assert "the same run again in 2 min" in log
    assert "agent run reported an error" not in log and "attempt 1 of" not in log
    assert r.progress()["parked"] == []


def test_still_unreachable_stops_the_run_and_it_resumes(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = demo_repo
    n = len(api_error.RETRY_WAITS_S)
    r.scenario(api_error={"IMPLEMENT": n + 1})
    monkeypatch.setattr("framegate.orchestrator.sleep_until", lambda until: None)
    to_implement(r)
    assert r.af("run", "demo") == EXIT_INTERRUPTED  # like the usage-limit stop: no park
    prog = r.progress()
    assert prog["phase"] == "IMPLEMENT" and prog["attempt"] == 0 and prog["parked"] == []
    assert "API not reachable after 3 retries" in prog["interrupted"]["how"]
    assert not (r.root / "out/.run.lock").exists() and not (r.root / ".phase").exists()
    assert not [e for e in r.events() if e["kind"] == "gate"]
    ev = [e for e in r.events() if e["kind"] == "api_error"]
    assert [e["wait_s"] for e in ev] == [30, 120, 300, None]
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert "Resume with: framegate run demo" in log
    # the API is back: the same step, first attempt
    assert r.af("run", "demo") == EXIT_GATE and r.progress()["waiting"]["gate"] == "final"
    gates = [e for e in r.events() if e["kind"] == "gate"]
    assert [(e["attempt"], e["passed"]) for e in gates] == [(1, True)]
