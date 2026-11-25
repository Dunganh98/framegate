"""An existing test that is red at the final gate: no default when the task turned it red, a
follow-up when it was red before (newly_red.py)."""

from __future__ import annotations

import json
import pathlib
from typing import Any

from framegate import defaults, gates, newly_red
from framegate.adapters.base import TestCase, TestRun
from framegate.orchestrator import NOTHING_BUILT_ASK, Orchestrator
from framegate.replay import score
from test_checks import adapter_for, write
from test_replay import event, scored

NEW = "tests.test_old.TestRange::test_stale"
OLD = "tests/test_debt.py::test_todo"


def test_the_final_regression_records_new_and_old_red_tests(
    tmp_path: pathlib.Path, monkeypatch: Any
) -> None:
    write(tmp_path, "specs/f/requirements.md", "Mode: bugfix\n")
    write(tmp_path, f"out/f/{gates.BASELINE_FILE}", json.dumps({"failing": [OLD]}))
    adapter = adapter_for(tmp_path)
    cases = [TestCase(NEW, "failed"), TestCase(OLD, "failed"), TestCase("t::ok", "passed")]
    run = TestRun("failed", 1, "1 failed", cases)
    monkeypatch.setattr(gates.suite_run, "whole_suite", lambda a: run)
    res = gates.regression_expecting(adapter, gates.ExpectedRed(), {OLD}, "f")
    assert res.status == "FAIL" and NEW in res.output
    assert newly_red.read(adapter.cfg, "f") == ([NEW], [OLD])
    newly_red.clear(adapter.cfg, "f")
    assert newly_red.read(adapter.cfg, "f") == ([], [])


def test_a_newly_red_test_is_a_gate_item_without_a_default() -> None:
    orch = Orchestrator.__new__(Orchestrator)
    parked = {"label": newly_red.REPAIR_LABEL, "reason": "question"}
    prog = {"completed_steps": [1], "newly_red": [NEW], "parked": [parked]}
    ask = orch.parked_ask(prog, parked)
    assert NEW in ask and defaults.no_default(ask) and defaults.options(ask) == ["A", "B"]
    assert defaults.unclear(ask, "ok") and defaults.takes_default(ask, "A")
    assert not defaults.takes_default(ask, "B: approved")
    assert orch.parked_ask(prog, {"label": "step 2"}) == ""
    assert orch.parked_ask({**prog, "newly_red": []}, parked) == ""
    assert orch.parked_ask({"newly_red": [NEW]}, parked) == NOTHING_BUILT_ASK
    q = newly_red.question([NEW])
    assert newly_red.is_question(f"[final gates] {q}") and defaults.unclear(q, "ok")
    assert defaults.takes_default(q, "A") and not newly_red.is_question("[x] another")


def test_a_test_red_before_the_task_is_a_follow_up_not_a_gate_item() -> None:
    assert newly_red.section([]) == [] and newly_red.deferred_items([]) == []
    lines = newly_red.section([OLD])
    assert lines[0] == "## Tests red before the task: 1" and f"- {OLD}" in lines
    (item,) = newly_red.deferred_items([OLD])
    assert (item.kind, item.files) == ("bugfix", ["tests/test_debt.py"]) and OLD in item.text


def test_the_scorecard_names_a_test_left_red() -> None:
    events = [
        event("final_gates", passed=False, failed=["regression"], newly_red=[NEW]),
        event("human_gate", gate="final", approved=True),
    ]
    assert newly_red.left(events) == [NEW] and newly_red.left(events[:1]) == []
    green = [event("final_gates", passed=True), event("human_gate", gate="final", approved=True)]
    assert newly_red.left([*events, *green]) == []
    cells = score._cells(scored("a", red_left=[NEW]))
    assert cells[2] == "good, existing test red"
    assert cells[-1] == f"existing test(s) left red: {NEW}"
    assert score._cells(scored("a", red_left=[]))[2] == "good"
    assert score._cells(scored("a", verdict="bad", red_left=[NEW]))[2] == "bad"
