"""End-to-end: spec -> approve-spec -> run (TESTS <-> TEST_REVIEW -> tests gate -> IMPLEMENT ->
REVIEW -> TRIAGE -> fixes -> final gates -> PR -> final gate -> RETRO -> DONE) in a tmp git repo
with a fake claude (tests/fake_claude.py via AGENTFLOW_CLAUDE)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from agentflow import procs
from agentflow.config import load_config
from agentflow.orchestrator import EXIT_GATE, EXIT_INTERRUPTED, Orchestrator
from conftest import DemoRepo, git

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"


def read(r: DemoRepo, rel: str) -> str:
    return (r.root / rel).read_text(encoding="utf-8")


def phases(r: DemoRepo) -> list[str]:
    return [c["phase"] for c in r.calls()]


def gate(r: DemoRepo) -> str | None:
    w = r.progress()["waiting"]
    return w["gate"] if w else None


def to_final_gate(r: DemoRepo) -> None:
    """approve-spec -> tests gate -> approve -> run to the final gate."""
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "tests"
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"


def finish_with_retro(r: DemoRepo) -> None:
    assert r.af("approve", "demo") == 0
    assert r.progress()["phase"] == "RETRO"
    assert r.af("run", "demo") == 0
    assert r.progress()["phase"] == "DONE"


def test_full_flow_human_mode(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    """(a) Human mode: 1 batched question at the tests gate, the test reviewer objects once,
    1 reject, answers; implementation logs 1 assumption and parks step 2 (no stop); a valid and an
    invalid review finding are triaged; final gate -> approve -> retro -> DONE. Real gates."""
    r = demo_repo
    r.two_steps()
    r.scenario(
        questions_in=["TESTS"],
        test_review_objections=1,
        assume=True,
        park_steps=[2],
        review_finding=True,
        review_invalid=True,
    )
    assert r.af("approve-spec", "demo") == 0

    # TESTS: writer (asks, continues) -> checks -> reviewer objects -> writer -> reviewer OK
    assert r.af("run", "demo") == EXIT_GATE
    assert phases(r) == ["TESTS", "TEST_REVIEW", "TESTS", "TEST_REVIEW"]
    assert not (r.root / ".phase").exists()
    assert "The test reviewer objected" in r.calls()[2]["prompt"]
    assert "also check the message" in r.calls()[2]["prompt"]
    assert "TEST REVIEWER" in r.calls()[1]["role"]
    assert "round 1 of at most 3" in r.calls()[1]["prompt"]
    tg = read(r, "out/demo/tests_gate.md")
    assert "## Open questions: 1" in tg and "1. [test writer] [TESTS] Should add()" in tg
    assert "## Test reviewer: OK, no objections" in tg
    assert "Rounds back to the test writer: 1 of at most 2" in tg
    assert "- Red against stubs: PASS" in tg and "- Traceability: PASS" in tg
    assert "| DEMO-R-002 | tests/test_demo.py::test_neg_negates |" in tg
    assert "## What each test checks" in tg and "test_add_returns_sum" in tg
    assert "Red against stubs: PASS" in read(r, "out/demo/test_checks.md")
    reviews = [e for e in r.events() if e["kind"] == "test_review"]
    assert [(e["round"], e["verdict"], e["sent_back"]) for e in reviews] == [
        (1, "OBJECTIONS", True),
        (2, "OK", False),
    ]
    capsys.readouterr()
    assert r.af("status", "demo") == EXIT_GATE
    assert "=== TESTS GATE (demo) ===" in capsys.readouterr().out
    assert r.af("run", "demo") == EXIT_GATE and len(r.calls()) == 4  # still waiting

    # reject: writer + reviewer re-run with the reason; the open question stays
    assert r.af("reject", "demo", "--reason", "Check the error message text too") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "tests"
    assert phases(r)[4:] == ["TESTS", "TEST_REVIEW"]
    assert "Check the error message text too" in r.calls()[4]["prompt"]
    assert "The human's note at the tests gate" in r.calls()[5]["prompt"]
    assert "Check the error message text too" in r.calls()[5]["prompt"]
    assert "## Open questions: 1" in read(r, "out/demo/tests_gate.md")

    # answer: the same, with the answers; the question moves to the archive
    assert r.af("answer", "demo") == 1  # answers.md missing
    (r.root / "out/demo/answers.md").write_text("No floats.\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "tests"
    assert phases(r)[6:] == ["TESTS", "TEST_REVIEW"]
    assert "answered the open questions" in r.calls()[6]["prompt"]
    assert "## Open questions: 0" in read(r, "out/demo/tests_gate.md")
    archive = read(r, "out/demo/questions_archive.md")
    assert "Should add() accept floats?" in archive and "No floats." in archive
    assert r.af("approve", "demo") == 0
    assert git(r.root, "tag", "--list", "tests-approved/demo").strip()

    # IMPLEMENT (step 1: assumption; step 2 parks) -> REVIEW -> TRIAGE -> fix -> final gates -> PR
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    assert phases(r)[8:] == ["IMPLEMENT", "IMPLEMENT", "REVIEW", "TRIAGE", "IMPLEMENT", "PR"]
    prog = r.progress()
    assert prog["phase"] == "FINAL" and prog["completed_steps"] == [1]
    parked = prog["parked"]
    assert [(p["kind"], p["label"]) for p in parked] == [("step", "step 2 - neg")]
    assert "should neg(0) be 0 or -0?" in parked[0]["question"] and parked[0]["attempts"] == 0
    # the parked step's half-done code is set aside (diff) and the sources restored
    assert "# partial" in read(r, "out/demo/parked/step-2-neg.diff")
    src = read(r, "src/demo/__init__.py")
    assert "# partial" not in src and "raise NotImplementedError" in src
    triage = [e for e in r.events() if e["kind"] == "triage"][0]["findings"]
    verdicts = {f["test"].split("::")[-1]: (f["verdict"], f["reason"]) for f in triage}
    assert verdicts["test_add_rejects_bool"] == ("VALID", "DEMO-E-001 says ints only")
    assert verdicts["test_add_rejects_negative"] == ("DISMISSED", "the spec allows negative ints")
    assert not (r.root / "tests/review/test_review_invalid.py").exists()
    fix = r.calls()[12]
    assert "currently failing tests" in fix["prompt"]
    # parked step 2's tests are red by design: the fix check leaves them out
    assert "--req DEMO-R-001,DEMO-E-001" in fix["prompt"]
    assert [e["kind"] for e in r.events()].count("findings_fixed") == 1
    final = read(r, "out/demo/final_gates.md")
    assert "FAILED" in final and "test_neg_negates" in final  # step 2 is parked
    assert "Time per check (s):" in final and "| frame diff |" in final
    facts = read(r, "out/demo/final_facts.md")
    assert "| step 2 - neg | PARKED (no reasonable assumption possible (question)) | 0 |" in facts
    assert "- [DEMO-E-001] bool counts as int (spec silent). (Step 1 — add)" in facts
    assert "test_add_rejects_negative: DISMISSED (the spec allows negative ints)" in facts
    summary = read(r, "out/demo/final_summary.md")
    assert summary.startswith("# Final summary")
    assert "## Cost, time, turns per phase (generated by agentflow)" in summary
    assert "| TESTS | 4 | " in summary and "| TEST_REVIEW | 4 |" in summary
    assert "| final gates (1 runs) |" in summary
    capsys.readouterr()
    assert r.af("status", "demo") == EXIT_GATE
    out = capsys.readouterr().out
    assert "=== FINAL GATE (demo) ===" in out and "1 parked: step 2 - neg" in out
    assert r.af("reject", "demo", "--reason", "x") == 1  # the final gate has no reject

    # approve -> RETRO -> DONE
    finish_with_retro(r)
    assert phases(r)[-1] == "RETRO"
    ri = read(r, "out/demo/retro_input.md")
    for line in (
        "questions 1 (1 round)",
        "human rejections 1",
        "review findings 2",
        "assumptions 1",
        "parked items 1",
        "### Q1 — TESTS (test writer)",
        "### R1 — tests gate rejected",
        "### A1 — Step 1 — add [DEMO-E-001]",
        "### P1 — step 2 - neg parked (0 failed attempts)",
        "Resolution: open (not redone)",
        "Triage (test reviewer): VALID",
        "Resolution: fixed by the implementer",
        "Triage (test reviewer): DISMISSED",
        "### G1 — final gates failed",
    ):
        assert line in ri, line
    metrics = read(r, "metrics/workflow_metrics.csv").splitlines()
    assert metrics[0].endswith(",parked,assumptions")
    assert metrics[1].split(",")[2:6] == ["1", "0", "1", "0"] and metrics[1].endswith(",1,1")
    assert not (r.root / ".phase").exists() and not (r.root / ".reqs").exists()
    log = git(r.root, "log", "--format=%s")
    for msg in (
        "demo: spec approved",
        "demo: tests approved",
        "demo: step 1 - add",
        "demo: review findings triaged (1 valid)",
        "demo: review fixes",
        "demo: workflow metrics",
    ):
        assert msg in log
    assert "demo: step 2 - neg" not in log
    cap = {"TESTS": 50, "TEST_REVIEW": 30, "IMPLEMENT": 80, "REVIEW": 40, "TRIAGE": 30}
    cap.update(PR=30, RETRO=30)
    for call in r.calls():
        argv = call["argv"]
        assert "-p" in argv and "--bare" not in argv
        assert argv[argv.index("--max-turns") + 1] == str(cap[call["phase"]])
    review_tools = r.calls()[1]["argv"][r.calls()[1]["argv"].index("--allowedTools") + 1]
    assert "Edit" not in review_tools.split(",") and "Write" in review_tools.split(",")


def test_finish_no_retro(demo_repo: DemoRepo) -> None:
    """(b) final gate -> finish --no-retro -> DONE, metrics row, no RETRO run."""
    r = demo_repo
    r.add_toml(FAST)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("finish", "demo", "--no-retro") == 1  # not at the final gate
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    with pytest.raises(SystemExit):
        r.af("finish", "demo")  # --no-retro is required
    (r.root / "out/demo/answers.md").write_text("x\n", encoding="utf-8")
    assert r.af("answer", "demo") == 1  # nothing parked
    assert r.af("finish", "demo", "--no-retro") == 0
    assert r.progress()["phase"] == "DONE" and r.progress()["waiting"] is None
    assert "RETRO" not in phases(r)
    assert not (r.root / "out/demo/retro_input.md").exists()
    assert ",demo," in read(r, "metrics/workflow_metrics.csv")
    assert [e for e in r.events() if e["kind"] == "finished"][0]["retro"] is False
    assert r.af("run", "demo") == 0  # nothing left


def test_answer_parked_redoes_only_parked_items(demo_repo: DemoRepo) -> None:
    """(c) A parked step: answer at the final gate -> the next run redoes only that step, then
    final gates, summary and the final gate again."""
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.scenario(park_steps=[2])
    to_final_gate(r)
    assert phases(r) == ["TESTS", "TEST_REVIEW", "IMPLEMENT", "IMPLEMENT", "REVIEW", "PR"]
    assert [p["label"] for p in r.progress()["parked"]] == ["step 2 - neg"]
    assert r.af("answer", "demo") == 1  # answers.md missing
    (r.root / "out/demo/answers.md").write_text("neg(0) is 0.\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    prog = r.progress()
    assert prog["phase"] == "IMPLEMENT" and prog["redo_mode"] and prog["parked"] == []
    archive = read(r, "out/demo/questions_archive.md")
    assert "[step 2 - neg]" in archive and "neg(0) is 0." in archive

    n = len(r.calls())
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    assert phases(r)[n:] == ["IMPLEMENT", "PR"]  # only the parked step, no review, no tests
    redo = r.calls()[n]
    assert "implement plan step 2" in redo["prompt"] and "redo of parked work" in redo["prompt"]
    prog = r.progress()
    assert prog["completed_steps"] == [1, 2] and prog["parked"] == [] and not prog["redo"]
    assert [e["passed"] for e in r.events() if e["kind"] == "redo_result"] == [True]
    assert "ALL GATES PASSED" in read(r, "out/demo/final_gates.md")
    assert "nothing parked" in r.progress()["waiting"]["message"]
    finish_with_retro(r)
    ri = read(r, "out/demo/retro_input.md")
    assert "### P1 — step 2 - neg" in ri
    assert "answered at the final gate and redone: passed" in ri


def test_max_attempts_parks_then_redo(demo_repo: DemoRepo) -> None:
    """A step failing max_attempts times is parked (no stop): review and the final gate still
    run; after an answer the redo passes."""
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(implement_fail=3)
    to_final_gate(r)
    expected = ["TESTS", "TEST_REVIEW", "IMPLEMENT", "IMPLEMENT", "IMPLEMENT", "REVIEW", "PR"]
    assert phases(r) == expected
    impl = [c for c in r.calls() if c["phase"] == "IMPLEMENT"]
    assert "Attempt 1 failed these gates" in impl[1]["prompt"]
    assert "Attempt 2 failed these gates" in impl[2]["prompt"]
    p = r.progress()["parked"][0]
    assert p["reason"] == "failed 3 attempts" and p["attempts"] == 3
    assert p["failed"] == ["tests (current + earlier steps)"]
    gates = [e for e in r.events() if e["kind"] == "gate"]
    assert [(g["attempt"], g["passed"]) for g in gates] == [(1, False), (2, False), (3, False)]
    assert "return a - b" in read(r, "out/demo/parked/step-1-add.diff")
    assert "raise NotImplementedError" in read(r, "src/demo/__init__.py")  # restored stub
    assert not (r.root / ".reqs").exists()
    assert "PARKED (failed 3 attempts) | 3 |" in read(r, "out/demo/final_facts.md")

    (r.root / "out/demo/answers.md").write_text("Subtraction is wrong; add.\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    assert r.progress()["completed_steps"] == [1]
    finish_with_retro(r)
    ri = read(r, "out/demo/retro_input.md")
    for item in ("### F1 — ", "### F2 — ", "### F3 — ", "### P1 — step 1 - add parked (3 "):
        assert item in ri
    assert "failed attempts 3" in ri and "redone: passed" in ri
    metrics = read(r, "metrics/workflow_metrics.csv").splitlines()
    assert metrics[1].split(",")[2:6] == ["0", "0", "0", "3"] and metrics[1].endswith(",1,0")


def test_implementer_question_parks_the_step(demo_repo: DemoRepo) -> None:
    """questions.md from the implementer counts as a park request (no stop, no attempt)."""
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(questions_in=["IMPLEMENT"])
    to_final_gate(r)
    p = r.progress()["parked"][0]
    assert p["label"] == "step 1 - add" and "accept floats" in p["question"]
    assert not [e for e in r.events() if e["kind"] == "gate"]  # no step gate ran


def test_questions_of_writer_and_reviewer_batched(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(questions_in=["TESTS", "TEST_REVIEW"])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert phases(r) == ["TESTS", "TEST_REVIEW"]  # no stop for either question
    tg = read(r, "out/demo/tests_gate.md")
    assert "## Open questions: 2" in tg
    assert "1. [test writer] [TESTS]" in tg and "2. [test reviewer] [TEST_REVIEW]" in tg
    q = [e for e in r.events() if e["kind"] == "questions"]
    assert [(e["phase"], e["who"]) for e in q] == [
        ("TESTS", "test writer"),
        ("TEST_REVIEW", "test reviewer"),
    ]
    assert r.af("approve", "demo") == 0  # approved with the questions open
    assert "Approved without an answer." in read(r, "out/demo/questions_archive.md")
    assert r.af("run", "demo") == EXIT_GATE
    finish_with_retro(r)
    ri = read(r, "out/demo/retro_input.md")
    assert "### Q2 — TEST_REVIEW (test reviewer)" in ri
    assert "(not answered: the tests gate was approved with it open)" in ri


def test_tests_gate_auto(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    """(d) [gates] tests = "auto": passes without a stop when clean."""
    r = demo_repo
    r.add_toml(FAST)
    r.set_toml("gates", "tests", '"auto"')
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    assert phases(r) == ["TESTS", "TEST_REVIEW", "IMPLEMENT", "REVIEW", "PR"]
    assert git(r.root, "tag", "--list", "tests-approved/demo").strip()
    auto = [e for e in r.events() if e["kind"] == "tests_gate_auto"]
    assert auto == [auto[0]] and auto[0]["passed"] is True and auto[0]["reasons"] == []
    assert not [e for e in r.events() if e["kind"] == "human_gate"]
    assert "Tests gate passed without a stop" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ({"questions_in": ["TESTS"]}, "1 open question(s)"),
        ({"test_review_objections": 9}, "test reviewer: 1 objection(s)"),
    ],
)
def test_tests_gate_auto_stops_when_not_clean(
    demo_repo: DemoRepo, scenario: dict[str, object], reason: str
) -> None:
    """(d) auto mode stops like human mode on a question or a remaining objection."""
    r = demo_repo
    r.add_toml(FAST)
    r.set_toml("agents", "test_review_rounds", "1")
    r.set_toml("gates", "tests", '"auto"')
    r.scenario(**scenario)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "tests"
    auto = [e for e in r.events() if e["kind"] == "tests_gate_auto"]
    assert auto[-1]["passed"] is False and reason in auto[-1]["reasons"]
    assert not git(r.root, "tag", "--list", "tests-approved/demo").strip()
    if "test_review_objections" in scenario:  # 1 round back to the writer, then the gate
        assert phases(r) == ["TESTS", "TEST_REVIEW", "TESTS", "TEST_REVIEW"]
    assert r.af("approve", "demo") == 0  # the human decides as in human mode
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"


def test_resume_after_interruption_at_each_gate(demo_repo: DemoRepo) -> None:
    """(e) An interruption on the way to each gate (TEST_REVIEW, IMPLEMENT, TRIAGE, PR) and in
    the retro: no attempt counted, state cleared, `run` resumes where it stopped."""
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(review_finding=True)
    assert r.af("approve-spec", "demo") == 0
    hit: set[str] = set()

    def run_interrupting(phase_to_break: str) -> int:
        orch = Orchestrator(load_config(r.root), echo=lambda s: None)
        real = orch.run_agent

        def interrupted(phase: str, *a: object, **k: object) -> object:
            if phase == phase_to_break and phase not in hit:
                hit.add(phase)
                raise KeyboardInterrupt
            return real(phase, *a, **k)  # type: ignore[arg-type]

        orch.run_agent = interrupted  # type: ignore[method-assign]
        return orch.run("demo")

    def cleared() -> None:
        assert not (r.root / ".phase").exists() and not (r.root / ".reqs").exists()
        assert not (r.root / "out/.run.lock").exists()
        assert r.progress()["waiting"] is None and r.progress()["attempt"] == 0

    assert run_interrupting("TEST_REVIEW") == EXIT_INTERRUPTED
    cleared()
    assert r.progress()["sub"] == "review"  # the writer's tests are kept
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "tests"
    assert phases(r) == ["TESTS", "TEST_REVIEW"]
    assert r.af("approve", "demo") == 0

    for phase in ("IMPLEMENT", "TRIAGE", "PR"):
        assert run_interrupting(phase) == EXIT_INTERRUPTED, phase
        cleared()
    assert r.progress()["phase"] == "PR"
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    expected = ["TESTS", "TEST_REVIEW", "IMPLEMENT", "REVIEW", "TRIAGE", "IMPLEMENT", "PR"]
    assert phases(r) == expected
    assert r.af("approve", "demo") == 0
    assert run_interrupting("RETRO") == EXIT_INTERRUPTED
    cleared()
    assert r.af("run", "demo") == 0 and r.progress()["phase"] == "DONE"
    ev = [e["phase"] for e in r.events() if e["kind"] == "interrupted"]
    assert ev == ["TESTS", "IMPLEMENT", "REVIEW", "PR", "RETRO"]
    assert [g["attempt"] for g in r.events() if g["kind"] == "gate"] == [1, 1]


def test_retro_input_question_and_failed_attempt(demo_repo: DemoRepo) -> None:
    """1 question + 1 failed attempt -> retro_input.md with Q1/F1; the fake retro leaves F1 out
    on its first run, so the coverage check re-runs it once with the missing ID."""
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(questions_in=["TESTS"], implement_fail=1, retro_omit=["F1"])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    (r.root / "out/demo/answers.md").write_text("No floats: ints only.\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    gates = [e for e in r.events() if e["kind"] == "gate"]
    assert [(g["attempt"], g["passed"]) for g in gates] == [(1, False), (2, True)]
    assert gates[0]["failed"] == ["tests (current + earlier steps)"] and gates[0]["excerpt"]
    finish_with_retro(r)

    ri = read(r, "out/demo/retro_input.md")
    assert "questions 1 (1 round); failed attempts 1; escalations 0" in ri
    assert "### Q1 — TESTS (test writer)" in ri
    assert "Question: [TESTS] Should add() accept floats?" in ri and "No floats: ints only." in ri
    assert "### F1 — IMPLEMENT / step 1 - add, attempt 1" in ri
    assert "### Q2" not in ri and "### F2" not in ri

    retro = [c for c in r.calls() if c["phase"] == "RETRO"]
    assert len(retro) == 2  # re-run once for the missing F1
    assert "does not address these items" in retro[1]["prompt"] and ": F1." in retro[1]["prompt"]
    assert "Every item ID must appear" in retro[0]["role"]
    cov = [e for e in r.events() if e["kind"] == "retro_coverage"]
    assert cov == [cov[0]] and cov[0]["missing_first"] == ["F1"] and cov[0]["missing"] == []
    assert cov[0]["items"] == 2 and cov[0]["rerun"] is True


def test_retro_coverage_warning_after_rerun(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(implement_fail=1, retro_omit=["F1"], retro_omit_runs=2)
    to_final_gate(r)
    finish_with_retro(r)  # a warning, not a stop
    assert len([c for c in r.calls() if c["phase"] == "RETRO"]) == 2  # only one re-run
    cov = [e for e in r.events() if e["kind"] == "retro_coverage"][0]
    assert cov["missing_first"] == ["F1"] and cov["missing"] == ["F1"]
    assert "WARNING: retro_proposals.md still doesn't address F1" in capsys.readouterr().out


def test_retro_no_items_no_rerun(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    to_final_gate(r)
    finish_with_retro(r)
    ri = read(r, "out/demo/retro_input.md")
    assert "questions 0 (0 rounds); failed attempts 0" in ri
    assert ri.count("None.") == 9  # one per item type
    assert len([c for c in r.calls() if c["phase"] == "RETRO"]) == 1


def test_permission_denials_reach_events_and_retro(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    denied = {"tool_name": "PowerShell", "tool_input": {"command": "cd src; python -m pytest"}}
    r.scenario(denials={"IMPLEMENT": [denied], "RETRO": [denied, denied]})
    to_final_gate(r)
    finish_with_retro(r)
    ev = [e for e in r.events() if e["kind"] == "permission_denials"]
    assert [e["phase"] for e in ev] == ["IMPLEMENT", "RETRO"]
    assert ev[0]["step"] == "step 1 - add"
    assert ev[0]["denials"] == [{"tool": "PowerShell", "input": "cd src; python -m pytest"}]
    ri = read(r, "out/demo/retro_input.md")
    # the retro input is written before the RETRO run: only the IMPLEMENT denial is in it
    assert "### D1 — IMPLEMENT / step 1 - add: PowerShell denied" in ri
    assert "### D2" not in ri and "permission denials 1" in ri


def test_turn_cap_retry_then_done(demo_repo: DemoRepo) -> None:
    """RETRO stops at its cap once: re-run once with a note. IMPLEMENT stops at its cap: no
    blind re-run, the step gate decides (the files are written, so it passes)."""
    r = demo_repo
    r.add_toml(FAST + "\n[phases.RETRO]\nmax_turns = 7\n")
    r.scenario(max_turns={"RETRO": 1, "IMPLEMENT": 1})
    to_final_gate(r)
    finish_with_retro(r)
    retro = [c for c in r.calls() if c["phase"] == "RETRO"]
    assert len(retro) == 2
    assert retro[0]["argv"][retro[0]["argv"].index("--max-turns") + 1] == "7"
    assert "stopped at the turn cap (7 turns)" in retro[1]["prompt"]
    assert len([c for c in r.calls() if c["phase"] == "IMPLEMENT"]) == 1
    capped = [e for e in r.events() if e["kind"] == "max_turns"]
    assert [(e["phase"], e["cap"], e["turns"]) for e in capped] == [
        ("IMPLEMENT", 80, 81),
        ("RETRO", 7, 8),
    ]
    assert r.progress().get("turn_cap_retries") == 0


def test_turn_cap_failed_attempt_note(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(max_turns={"IMPLEMENT": 1}, implement_fail=1)
    to_final_gate(r)
    impl = [c for c in r.calls() if c["phase"] == "IMPLEMENT"]
    assert len(impl) == 2 and "stopped at the turn cap (80 turns)" in impl[1]["prompt"]
    assert "Attempt 1 failed these gates" in impl[1]["prompt"]


def test_turn_cap_twice_continues_without_a_stop(demo_repo: DemoRepo) -> None:
    """The test writer stops at its cap twice: logged (E item), the run goes on to the gate."""
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(max_turns={"TESTS": 2})
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "tests"
    assert phases(r) == ["TESTS", "TESTS", "TEST_REVIEW"]
    ev = [e for e in r.events() if e["kind"] == "escalation"]
    assert ev[0]["failed"] == ["turn cap (50)"] and ev[0]["attempts"] == 2
    assert "continued without a stop" in ev[0]["resolution"]
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert "## Agent runs stopped at the turn cap twice: 1" in read(r, "out/demo/final_facts.md")
    finish_with_retro(r)
    assert "### E1 — TESTS stopped at the turn cap 2 times" in read(r, "out/demo/retro_input.md")


# ---------- stopping a run: no orphan agents, no stale phase lock ----------
def _hang_pids(r: DemoRepo) -> dict[str, int]:
    f = r.root.parent / "hang_pids.json"
    deadline = time.monotonic() + 60
    while not f.is_file():
        assert time.monotonic() < deadline, "the fake agent never started"
        time.sleep(0.1)
    pids: dict[str, int] = json.loads(f.read_text(encoding="utf-8"))
    return pids


def _all_dead(pids: dict[str, int], seconds: float = 20) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not any(procs.pid_alive(p) for p in pids.values()):
            return True
        time.sleep(0.1)
    return False


def _to_implement(r: DemoRepo) -> None:
    r.add_toml(FAST)
    r.scenario(hang="IMPLEMENT")
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0


def test_sigterm_mid_implement_stops_agents_then_resume(demo_repo: DemoRepo) -> None:
    """SIGTERM while the IMPLEMENT agent runs: the agent and its child are killed, .phase/.reqs
    and the run lock are cleared, the interruption is recorded; `run` resumes to the gate."""
    r = demo_repo
    _to_implement(r)
    seen: dict[str, int] = {}

    def stop() -> None:
        seen.update(_hang_pids(r))
        signal.raise_signal(signal.SIGTERM)

    threading.Thread(target=stop, daemon=True).start()
    t0 = time.monotonic()
    assert r.af("run", "demo") == EXIT_INTERRUPTED
    assert time.monotonic() - t0 < 60
    assert _all_dead(seen), f"orphans left: {seen}"
    for name in (".phase", ".reqs", "out/.run.lock"):
        assert not (r.root / name).exists(), name
    ev = [e for e in r.events() if e["kind"] == "interrupted"]
    assert len(ev) == 1 and ev[0]["how"] == "SIGTERM" and ev[0]["phase"] == "IMPLEMENT"
    assert r.progress()["attempt"] == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    finish_with_retro(r)


@pytest.mark.skipif(sys.platform != "win32", reason="the kill-on-close job is Windows-only")
def test_hard_kill_mid_implement_no_orphans_stale_lock_recovered(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """`taskkill /F` of the orchestrator (no handler runs): the job kills the agent tree; the
    stale .phase is reported by `status`, cleared by `unlock`, and `run` resumes."""
    r = demo_repo
    _to_implement(r)
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentflow", "run", "demo"],
        cwd=r.root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pids = _hang_pids(r)
        lock = json.loads((r.root / "out/.run.lock").read_text(encoding="utf-8"))
        subprocess.run(
            ["taskkill", "/F", "/PID", str(lock["pid"])], check=False, capture_output=True
        )
        assert _all_dead(pids), f"orphans left: {pids}"
        proc.wait(timeout=30)
    finally:
        procs.kill_tree(proc)
    assert (r.root / ".phase").read_text(encoding="utf-8") == "IMPLEMENT"
    capsys.readouterr()
    r.af("status", "demo")
    assert "WARNING: stale phase lock 'IMPLEMENT'" in capsys.readouterr().out
    assert r.af("unlock") == 0
    assert not (r.root / ".phase").exists() and not (r.root / "out/.run.lock").exists()
    ev = [e for e in r.events() if e["kind"] == "interrupted"]
    assert len(ev) == 1 and "found by unlock" in ev[0]["how"]
    assert r.af("unlock") == 0  # nothing left
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"


def test_stale_lock_taken_over_by_run_live_lock_refused(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.add_toml(FAST)
    assert r.af("approve-spec", "demo") == 0
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    lock = r.root / "out/.run.lock"
    lock.parent.mkdir(exist_ok=True)
    lock.write_text(json.dumps({"pid": dead.pid, "feature": "demo"}), encoding="utf-8")
    (r.root / ".phase").write_text("TESTS", encoding="utf-8")
    assert r.af("run", "demo") == EXIT_GATE  # stale lock recovered, run continues
    assert "Cleared stale state" in capsys.readouterr().out
    assert [e["kind"] for e in r.events()].count("interrupted") == 1
    # a live lock (another process) blocks a second run and plain unlock
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    lock.write_text(json.dumps({"pid": other.pid, "feature": "x"}), encoding="utf-8")
    (r.root / ".phase").write_text("TESTS", encoding="utf-8")
    orch = Orchestrator(load_config(r.root), echo=lambda s: None)
    with pytest.raises(Exception, match="already active"):
        orch.take_run_lock("demo")
    assert "agentflow run is active" in orch.stale_warning()
    assert r.af("unlock") == 1
    assert r.af("unlock", "--force") == 0 and not lock.exists()
    other.kill()
    # SPEC is set on purpose by `agentflow spec`: never reported as stale
    (r.root / ".phase").write_text("SPEC", encoding="utf-8")
    assert orch.stale_warning() == ""
