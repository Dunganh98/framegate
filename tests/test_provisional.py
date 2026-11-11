"""End-to-end (fake claude): work that waits for the human's answer at the final gate. A step
whose frozen tests are wrong keeps its code as provisional; parked steps' tests are expected red
at the final gate."""

from __future__ import annotations

import pytest

from agentflow.adapters import get_adapter
from agentflow.config import load_config
from agentflow.gates import gate_report
from agentflow.orchestrator import EXIT_GATE, contested_tests
from conftest import DemoRepo, git
from test_e2e import FAST, finish_with_retro, gate, phases, read

CONTESTED = "test_demo.test_add_refuses_bool_contested"


def contested_run_to_final_gate(r: DemoRepo, accept: bool) -> None:
    """Two steps (step 2 depends on step 1); step 1's implementer contests one frozen test."""
    r.add_toml(FAST)
    r.two_steps()
    r.defer_tests_gate()
    r.scenario(contest_steps=[1], contest_accept=accept)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"


def test_contested_tests_parsed_from_a_park_request_or_diagnosis() -> None:
    park = "Park kind: tests-wrong\nContested tests: `A.B`, C.D; none\n\nwhy..."
    assert contested_tests(park) == ["A.B", "C.D"]
    assert contested_tests("Contested tests: A.B\n(a plain question)") == []
    assert contested_tests("Park kind: tests-wrong\nno names") == []
    diagnosis = "Root cause: x\nFix owner: tests\nProposed fix: y\nContested tests: A.B\n"
    assert contested_tests(diagnosis, tests_owner=True) == ["A.B"]


def test_a_step_whose_tests_are_wrong_keeps_its_code(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """Seen live: a correct step 1 parked only for two wrong tests, so the dependent steps
    waited and their own wrong test showed only one round (~30 min, ~$2) later. Now step 1's
    code is kept as provisional, step 2 builds on it, the contested test stays out of the step
    and final checks, and the final gate lists the proposed fix; one answer approves it, the
    test writer applies it, and the contested test must then pass (here: it is gone)."""
    r = demo_repo
    contested_run_to_final_gate(r, accept=True)
    assert phases(r) == ["TESTS", "TEST_REVIEW", "IMPLEMENT", "IMPLEMENT", "REVIEW", "PR"]
    prog = r.progress()
    assert prog["completed_steps"] == [1, 2] and prog["parked"] == []
    (rec,) = prog["provisional"]
    assert rec["label"] == "step 1 - add" and rec["contested"] == [CONTESTED]
    assert "Fix: delete the test." in rec["question"]
    log = git(r.root, "log", "--format=%s")
    assert f"demo: step 1 - add (PROVISIONAL: tests contested: {CONTESTED})" in log
    # step 2 knew, and its gate left the contested test out
    step2 = r.calls()[3]["prompt"]
    assert "Provisional steps (code kept and committed" in step2 and CONTESTED in step2
    gates = [(e["step"], e["passed"]) for e in r.events() if e["kind"] == "gate"]
    assert gates == [("step 2 - neg", True)]
    # final gates: the contested test is expected red; coverage and mutation wait for the redo
    final = read(r, "out/demo/final_gates.md")
    assert f"contested tests, left out: {CONTESTED}" in final
    assert "[PASS] regression: whole suite, no coverage" in final
    assert "contested (provisional, left out): 1; no other failure" in final
    assert "[SKIPPED] " in final
    assert "SKIPPED (provisional steps, tests contested: step 1 - add)" in final
    assert "ALL GATES PASSED" in final
    assert "Final gates PASSED; nothing parked; 1 provisional" in prog["waiting"]["message"]
    facts = read(r, "out/demo/final_facts.md")
    assert "## Provisional steps (code kept, test fixes proposed): 1" in facts
    assert "| step 1 - add | done, PROVISIONAL (1 contested tests) | 0 |" in facts
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    out = capsys.readouterr().out
    assert "Provisional steps (one answer approves all test fixes):\n  1. Proposed test" in out
    assert "Open questions: none" in out
    # the gate cannot be approved (or finished) past the open fix
    assert r.af("approve", "demo") == 1 and r.af("finish", "demo", "--no-retro") == 1
    (r.root / "out/demo/answers.md").write_text(
        "1. Q: Proposed test fixes\n   A: Approved: apply all proposed test fixes as written.\n",
        "utf-8",
    )
    assert r.af("answer", "demo") == 0
    archive = read(r, "out/demo/questions_archive.md")
    assert "Proposed test fixes (the code of these steps" in archive
    n = len(r.calls())
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    assert phases(r)[n:] == ["SPEC_UPDATE", "TESTS", "TEST_REVIEW", "PR"]
    assert "provisional step's contested tests" in r.calls()[n + 1]["prompt"]
    settled = [e for e in r.events() if e["kind"] == "provisional_settled"]
    assert [(e["label"], e["accepted"]) for e in settled] == [("step 1 - add", True)]
    prog = r.progress()
    assert prog["completed_steps"] == [1, 2] and not prog.get("provisional")
    assert "test_add_refuses_bool_contested" not in read(r, "tests/test_demo.py")
    final = read(r, "out/demo/final_gates.md")
    assert "ALL GATES PASSED" in final and "SKIPPED (" not in final
    assert "nothing parked" in prog["waiting"]["message"]
    assert "provisional" not in prog["waiting"]["message"]
    finish_with_retro(r)
    ri = read(r, "out/demo/retro_input.md")
    assert "### P1 — step 1 - add provisional (tests contested)" in ri
    assert "Resolution: test fixes approved: the contested tests pass, the code stayed" in ri
    metrics = read(r, "metrics/workflow_metrics.csv").splitlines()
    assert metrics[-1].split(",")[4] == "1"  # questions: the provisional step's


def test_a_contested_test_the_human_keeps_reverts_the_provisional_code(
    demo_repo: DemoRepo,
) -> None:
    """The human does not approve the proposed test fix: the test stands, so the provisional
    commit is reverted and the step redone against it (the dependent step stays done)."""
    r = demo_repo
    contested_run_to_final_gate(r, accept=False)
    (r.root / "out/demo/answers.md").write_text(
        "1. Q: Proposed test fixes\n   A: No: bools must be rejected, the test is right.\n",
        "utf-8",
    )
    assert r.af("answer", "demo") == 0
    n = len(r.calls())
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    assert phases(r)[n:] == ["SPEC_UPDATE", "TESTS", "IMPLEMENT", "PR"]
    (settled,) = [e for e in r.events() if e["kind"] == "provisional_settled"]
    assert not settled["accepted"] and settled["reverted"]
    assert [s.split("::")[-1] for s in settled["still_failing"]] == [
        "test_add_refuses_bool_contested"
    ]
    log = git(r.root, "log", "--format=%s").splitlines()
    assert any(s.startswith('Revert "demo: step 1 - add (PROVISIONAL') for s in log)
    redo = r.calls()[n + 2]["prompt"]
    assert "implement plan step 1" in redo
    assert "did not approve changing its contested tests" in redo and "was reverted" in redo
    assert "out/demo/parked/step-1-add.diff" in redo
    prog = r.progress()
    assert sorted(prog["completed_steps"]) == [1, 2] and not prog.get("provisional")
    redone = [(e["label"], e["passed"]) for e in r.events() if e["kind"] == "redo_result"]
    assert redone == [("step 1 - add", True)]
    assert "type(a) is not int" in read(r, "src/demo/__init__.py")
    assert "ALL GATES PASSED" in read(r, "out/demo/final_gates.md")
    assert "nothing parked" in prog["waiting"]["message"]


def test_parked_steps_are_expected_red_at_the_final_gate(demo_repo: DemoRepo) -> None:
    """Seen live: final gates FAILED twice with 456 / 7 failures that were all a parked step's
    own tests (a real regression would have hidden among them) and a 253 s coverage run that
    gave nothing usable. Now the parked IDs' tests count as expected red, coverage, diff
    coverage and mutation are skipped with the reason, and only other failures fail."""
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.defer_tests_gate()
    r.scenario(park_steps=[2])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    final = read(r, "out/demo/final_gates.md")
    assert "Waiting for the human's answer (parked steps: step 2 - neg):" in final
    assert "- expected red: the tests of DEMO-R-002" in final
    assert "[PASS] regression: whole suite, no coverage" in final
    assert "expected red (parked): 1; no other failure" in final
    assert "SKIPPED (parked steps: step 2 - neg): runs again after the redo" in final
    assert "ALL GATES PASSED" in final
    assert "Final gates PASSED; 1 parked: step 2 - neg" in r.progress()["waiting"]["message"]
    # a real regression outside the parked step still fails the gate, named on its own
    (r.root / "tests/test_other.py").write_text(
        "def test_unrelated() -> None:\n    assert 1 == 2\n", "utf-8"
    )
    rep = gate_report(get_adapter(load_config(r.root)), "demo", "final")
    name = "regression: whole suite, no coverage"
    assert name in rep.failed  # (the frame diff fails too: tests are frozen)
    out = rep.full_outputs[name]
    assert "UNEXPECTED failures (neither a parked step's test nor a contested test): 1" in out
    assert "test_unrelated" in out
