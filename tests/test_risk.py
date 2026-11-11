"""The engine's risk rating of a finished feature (risk.py), the brief line, and
`[gates] final = "auto"`: a low-risk feature passes the final gate without a stop."""

from __future__ import annotations

from typing import Any

import pytest

from agentflow import chat, risk
from agentflow.adapters import get_adapter
from agentflow.config import ConfigError, load_config, parse_config
from agentflow.orchestrator import EXIT_GATE, Orchestrator
from conftest import DemoRepo, git

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
CLEAN_GATES = {
    "kind": "final_gates",
    "passed": True,
    "failed": [],
    "results": {
        "regression: whole suite, no coverage": "PASS",
        "diff coverage >= 90% (feature tests)": "PASS: 100%",
        "mutation score (feature tests)": "PASS: 90%",
    },
    "structure": {"files": {"src/demo/__init__.py": 12}},
}
SMALL = {"changed_lines": 20, "changed_files": 1, "removed_lines": 0, "paths": ["src/demo/a.py"]}


@pytest.fixture
def rated(demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch) -> Any:
    """assess() on the demo repo with a small change and a track record, unless overridden."""
    state = {"stats": dict(SMALL), "done": 3}
    monkeypatch.setattr(risk, "change_stats", lambda a, f: state["stats"] and dict(state["stats"]))
    monkeypatch.setattr(risk, "finished_features", lambda a, f: state["done"])

    def run(
        events: list[dict[str, Any]] | None = None,
        prog: dict[str, Any] | None = None,
        items: int = 0,
        assumptions: int = 0,
        **over: Any,
    ) -> risk.Assessment:
        state.update(over)
        adapter = get_adapter(load_config(demo_repo.root))
        return risk.assess(
            adapter,
            "demo",
            prog or {},
            [CLEAN_GATES] if events is None else events,
            items,
            assumptions,
        )

    return run


def test_clean_small_measured_feature_is_low_risk(rated: Any) -> None:
    a = rated()
    assert a.low and a.reasons == []
    assert a.facts["changed_lines"] == 20 and a.facts["finished_features"] == 3
    assert '[gates] final = "auto"' in a.line(auto=False)
    assert "would not have stopped" not in a.line(auto=True)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"events": []}, "the final gates did not run"),
        (
            {"events": [dict(CLEAN_GATES, passed=False, failed=["typecheck"])]},
            "final gates failed: typecheck",
        ),
        (
            {
                "events": [
                    dict(
                        CLEAN_GATES,
                        results={"diff coverage >= 90%": "PASS", "mutation score": "SKIPPED: x"},
                    )
                ]
            },
            "mutation score was not measured",
        ),
        (
            {"events": [dict(CLEAN_GATES, results={"mutation score": "PASS"})]},
            "diff coverage was not measured",
        ),
        ({"prog": {"parked": [{"label": "step 2"}]}}, "1 parked item(s)"),
        ({"prog": {"provisional": [{"label": "step 1"}]}}, "provisional steps wait"),
        ({"items": 2}, "2 open item(s) to decide"),
        ({"events": [CLEAN_GATES, {"kind": "answer"}]}, "the human answered questions"),
        ({"events": [CLEAN_GATES, {"kind": "escalation"}]}, "cap twice"),
        ({"events": [CLEAN_GATES, {"kind": "parked"}]}, "work was parked during the run"),
        ({"assumptions": 4}, "4 assumptions (more than 3)"),
        ({"stats": dict(SMALL, changed_lines=201)}, "201 changed source lines (more than 200)"),
        ({"stats": dict(SMALL, changed_files=9)}, "9 changed source files (more than 8)"),
        ({"stats": dict(SMALL, removed_lines=31)}, "31 removed lines in existing code"),
        ({"stats": None}, "the size of the change could not be measured"),
        ({"done": 2}, "2 finished feature(s) in this repository (low risk needs 3)"),
        (
            {"events": [dict(CLEAN_GATES, structure={"files": {"src/big.py": 401}})]},
            "file(s) over 400 lines: src/big.py",
        ),
    ],
)
def test_each_broken_test_is_a_reason(rated: Any, kwargs: dict[str, Any], reason: str) -> None:
    a = rated(**kwargs)
    assert not a.low
    assert any(reason in r for r in a.reasons), a.reasons
    assert a.line(auto=False).startswith("Risk: elevated: ")


def test_never_auto_paths_and_limits_from_config(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo_repo.add_toml('[risk]\nnever_auto = ["src/demo/pay*", "db/"]\nmax_assumptions = 0\n')
    stats = dict(SMALL, paths=["src/demo/payment.py", "db/schema.sql", "src/demo/a.py"])
    monkeypatch.setattr(risk, "change_stats", lambda a, f: dict(stats))
    monkeypatch.setattr(risk, "finished_features", lambda a, f: 5)
    adapter = get_adapter(load_config(demo_repo.root))
    a = risk.assess(adapter, "demo", {}, [CLEAN_GATES], items=0, assumptions=50)
    assert a.reasons == [
        "touches paths marked never automatic: db/schema.sql, src/demo/payment.py"
    ]  # max_assumptions = 0 switches that test off


def test_risk_config_is_validated(demo_repo: DemoRepo) -> None:
    with pytest.raises(ConfigError, match=r"\[risk\] max_changed_lines must be >= 0"):
        parse_config(demo_repo.root, {"risk": {"max_changed_lines": -1}})
    with pytest.raises(ConfigError, match=r"\[gates\] final must be one of human, auto"):
        parse_config(demo_repo.root, {"gates": {"final": "always"}})


def test_change_stats_and_track_record_from_the_repository(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.defer_tests_gate()
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    adapter = get_adapter(load_config(r.root))
    stats = risk.change_stats(adapter, "demo")
    assert stats is not None
    assert stats["changed_files"] == 1 and stats["changed_lines"] >= 5
    assert stats["removed_lines"] == 0  # the stub file is new on the feature branch
    assert "src/demo/__init__.py" in stats["paths"]
    assert risk.finished_features(adapter, "demo") == 0
    metrics = r.root / "metrics"
    metrics.mkdir(exist_ok=True)
    (metrics / "workflow_metrics.csv").write_text(
        "date,feature,cost_usd\n2026-01-01,one,1\n2026-01-02,two,1\n2026-01-03,demo,1\n",
        encoding="utf-8",
    )
    assert risk.finished_features(adapter, "demo") == 2


def test_final_gate_shows_and_logs_the_rating(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """Default ([gates] final = "human"): the gate stops; the rating is on the brief, in
    progress.json and in the events, and the approval records it (shadow mode)."""
    r = demo_repo
    r.add_toml(FAST)
    r.defer_tests_gate()
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    rated = [e for e in r.events() if e["kind"] == "risk"]
    assert len(rated) == 1 and rated[0]["level"] == "elevated" and rated[0]["auto"] is False
    # the demo repo has no track record, and mutmut does not run on native Windows / unset
    assert any("finished feature(s)" in x for x in rated[0]["reasons"])
    assert r.progress()["risk"]["level"] == "elevated"
    assert "risk elevated" in r.progress()["waiting"]["message"]
    capsys.readouterr()
    orch = Orchestrator(load_config(r.root))
    assert chat.gate_summary(orch, "demo") == 0
    assert "Risk: elevated: " in capsys.readouterr().out
    assert r.af("approve", "demo") == 0
    gate = [e for e in r.events() if e["kind"] == "human_gate"][-1]
    assert gate["risk"] == "elevated" and "auto" not in gate


def test_low_risk_passes_the_final_gate_without_a_stop_in_auto_mode(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.defer_tests_gate()
    r.set_toml("gates", "final", '"auto"')
    monkeypatch.setattr(risk, "assess", lambda *a, **k: risk.Assessment(risk.LOW))
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == 0  # no stop: final gate -> retro -> DONE
    assert r.progress()["phase"] == "DONE"
    gate = [e for e in r.events() if e["kind"] == "human_gate"]
    assert len(gate) == 1 and gate[0]["auto"] is True and gate[0]["risk"] == "low"
    assert [c["phase"] for c in r.calls()][-2:] == ["PR", "RETRO"]
    assert "approved automatically (low risk" in (r.root / "out/demo/progress.log").read_text(
        "utf-8"
    )
    assert not git(r.root, "status", "--porcelain").strip()


def test_elevated_risk_still_stops_in_auto_mode(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.defer_tests_gate()
    r.set_toml("gates", "final", '"auto"')
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.progress()["waiting"]["gate"] == "final"
    assert [e for e in r.events() if e["kind"] == "risk"][0]["auto"] is True
