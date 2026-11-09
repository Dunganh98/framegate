"""End-to-end: spec -> approve-spec -> run (TESTS -> approve -> IMPLEMENT -> REVIEW -> PR -> RETRO
-> DONE) in a tmp git repo with a fake claude (tests/fake_claude.py via AGENTFLOW_CLAUDE)."""

from __future__ import annotations

import pytest

from agentflow.config import load_config
from agentflow.orchestrator import (
    EXIT_ESCALATION,
    EXIT_GATE,
    EXIT_QUESTIONS,
    EXIT_SPEC_CHANGE,
    Orchestrator,
)
from conftest import DemoRepo, git

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"


def test_full_flow(demo_repo: DemoRepo) -> None:
    """Happy path with real gates (ruff, mypy, pytest, coverage), plus a review finding."""
    r = demo_repo
    r.scenario(review_finding=True)
    assert r.af("approve-spec", "demo") == 0
    assert git(r.root, "tag", "--list", "spec-approved/demo").strip()
    assert not (r.root / ".phase").exists()

    # TESTS -> checks -> human gate
    assert r.af("run", "demo") == EXIT_GATE
    assert not (r.root / ".phase").exists()
    gate = (r.root / "out/demo/test_gate.md").read_text(encoding="utf-8")
    assert "Red against stubs: PASS" in gate
    assert "Traceability: PASS" in gate
    assert "Test smells: PASS" in gate
    assert r.af("run", "demo") == EXIT_GATE  # still waiting: no new agent run
    assert len(r.calls()) == 1
    assert r.af("approve", "demo") == 0
    assert git(r.root, "tag", "--list", "tests-approved/demo").strip()

    # IMPLEMENT -> REVIEW (finding) -> review gate
    assert r.af("run", "demo") == EXIT_GATE
    prog = r.progress()
    assert prog["phase"] == "REVIEW" and prog["waiting"]["gate"] == "review"
    assert prog["completed_steps"] == [1]
    assert r.af("approve", "demo") == 0

    # review fixes -> final gates -> PR -> RETRO -> DONE
    assert r.af("run", "demo") == 0
    prog = r.progress()
    assert prog["phase"] == "DONE" and prog["waiting"] is None
    final = (r.root / "out/demo/final_gates.md").read_text(encoding="utf-8")
    assert "ALL GATES PASSED" in final, final
    assert "[PASS] frame diff" in final
    assert "[PASS] diff coverage" in final
    assert "  Diff coverage 100% (" in final and "threshold 90%." in final
    assert (r.root / "out/demo/pr.md").is_file()
    assert (r.root / "out/demo/retro_proposals.md").is_file()
    retro_input = (r.root / "out/demo/retro_input.md").read_text(encoding="utf-8")
    assert "### V1 — " in retro_input and "test_add_rejects_bool" in retro_input
    assert "accepted: the implementer fixed it" in retro_input
    assert "review findings 1" in retro_input and "## Review notes" in retro_input
    findings = [e for e in r.events() if e["kind"] == "review_findings"]
    assert len(findings) == 1 and "test_add_rejects_bool" in findings[0]["tests"][0]
    metrics = (r.root / "metrics/workflow_metrics.csv").read_text(encoding="utf-8").splitlines()
    assert metrics[0].startswith("date,feature") and ",demo," in metrics[1]
    assert not (r.root / ".phase").exists() and not (r.root / ".reqs").exists()

    phases = [c["phase"] for c in r.calls()]
    assert phases == ["TESTS", "IMPLEMENT", "REVIEW", "IMPLEMENT", "PR", "RETRO"]
    fix_call = r.calls()[3]
    assert "currently failing tests" in fix_call["prompt"]
    for call in r.calls():
        argv = call["argv"]
        assert "-p" in argv and "--bare" not in argv
        assert argv[argv.index("--output-format") + 1] == "json"
        assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
        cap = {"TESTS": 50, "IMPLEMENT": 80, "REVIEW": 40, "PR": 30, "RETRO": 30}
        assert argv[argv.index("--max-turns") + 1] == str(cap[call["phase"]])
    tests_call = r.calls()[0]
    tools = tests_call["argv"][tests_call["argv"].index("--allowedTools") + 1].split(",")
    assert "Bash(python -m pytest *)" in tools and "Write" in tools
    assert "PowerShell(python -m pytest *)" in tools
    assert "exactly as written" in tests_call["prompt"]
    assert "one command per call" in tests_call["prompt"]
    assert "Conventions" in tests_call["prompt"] and "pytest.mark.req" in tests_call["prompt"]
    assert "TEST WRITER" in tests_call["role"] and "{feature}" not in tests_call["role"]
    impl_call = r.calls()[1]
    assert "--req DEMO-R-001,DEMO-E-001" in impl_call["prompt"]
    pr_tools = r.calls()[4]["argv"][r.calls()[4]["argv"].index("--allowedTools") + 1]
    assert "Edit" not in pr_tools.split(",")

    log = git(r.root, "log", "--format=%s")
    for msg in (
        "demo: spec approved",
        "demo: tests approved",
        "demo: step 1 - add",
        "demo: review findings accepted",
        "demo: review fixes",
        "demo: workflow metrics",
    ):
        assert msg in log
    kinds = [e["kind"] for e in r.events()]
    for k in ("spec_approved", "agent_run", "waiting", "human_gate", "gate", "final_gates"):
        assert k in kinds
    assert len(list((r.root / "out/runs").glob("*-demo-*.json"))) == 6


def test_questions_pause_and_answer(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(questions_in=["TESTS"])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_QUESTIONS
    assert r.progress()["waiting"]["kind"] == "questions"
    assert r.af("status", "demo") == EXIT_QUESTIONS
    assert r.af("run", "demo") == EXIT_QUESTIONS  # still waiting, no new agent run
    assert len(r.calls()) == 1
    assert r.af("approve", "demo") == 1  # not a gate
    assert r.af("answer", "demo") == 1  # answers.md missing
    (r.root / "out/demo/answers.md").write_text("No floats.\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    assert not (r.root / "out/demo/questions.md").exists()
    assert "floats" in (r.root / "out/demo/questions_archive.md").read_text(encoding="utf-8")
    assert r.af("run", "demo") == EXIT_GATE
    assert "answers.md" in r.calls()[-1]["prompt"]
    assert [e["kind"] for e in r.events()].count("questions") == 1


def test_answer_abort_requires_spec_change(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(questions_in=["TESTS"])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_QUESTIONS
    assert r.af("answer", "demo", "--abort") == EXIT_SPEC_CHANGE
    assert r.af("run", "demo") == EXIT_SPEC_CHANGE
    assert r.af("approve-spec", "demo") == 0  # spec edited -> re-approved, progress reset
    assert r.progress()["waiting"] is None and r.progress()["phase"] == "TESTS"
    assert r.af("run", "demo") == EXIT_GATE


def test_reject_reruns_tests_with_reason(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("reject", "demo", "--reason", "Check the error message text too") == 0
    assert r.progress()["waiting"] is None
    assert r.af("run", "demo") == EXIT_GATE
    tests_calls = [c for c in r.calls() if c["phase"] == "TESTS"]
    assert len(tests_calls) == 2
    assert "Check the error message text too" in tests_calls[1]["prompt"]
    assert "Check the error message text too" not in tests_calls[0]["prompt"]
    ev = [e for e in r.events() if e["kind"] == "human_gate"]
    assert ev[-1]["approved"] is False and ev[-1]["reason"] == "Check the error message text too"
    assert not git(r.root, "tag", "--list", "tests-approved/demo").strip()

    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == 0
    retro_input = (r.root / "out/demo/retro_input.md").read_text(encoding="utf-8")
    assert "### R1 — tests gate rejected" in retro_input
    assert "Reason: Check the error message text too" in retro_input
    assert "human rejections 1" in retro_input
    assert "- R1: one-off" in (r.root / "out/demo/retro_proposals.md").read_text(encoding="utf-8")


def test_implement_failure_retry_then_escalation(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(implement_fail=3)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_ESCALATION
    prog = r.progress()
    assert prog["phase"] == "IMPLEMENT" and prog["waiting"]["kind"] == "escalation"
    assert prog["completed_steps"] == []
    impl = [c for c in r.calls() if c["phase"] == "IMPLEMENT"]
    assert len(impl) == 3  # max_attempts
    assert "Attempt 1 failed these gates" in impl[1]["prompt"]
    assert "Attempt 2 failed these gates" in impl[2]["prompt"]
    gates = [e for e in r.events() if e["kind"] == "gate"]
    assert [g["passed"] for g in gates] == [False, False, False]
    assert [g["attempt"] for g in gates] == [1, 2, 3]
    for g in gates:
        assert g["phase"] == "IMPLEMENT" and g["step"] == "step 1 - add"
        assert g["failed"] == ["tests (current + earlier steps)"]
        assert (
            "[FAIL] tests (current + earlier steps)" in g["excerpt"] and len(g["excerpt"]) <= 2003
        )
    esc = [e for e in r.events() if e["kind"] == "escalation"]
    assert len(esc) == 1 and esc[0]["step"] == "step 1 - add" and esc[0]["attempts"] == 3
    assert esc[0]["phase"] == "IMPLEMENT" and esc[0]["failed"] == gates[-1]["failed"]
    assert "failed 3 times" in (r.root / "out/demo/escalation.md").read_text(encoding="utf-8")
    assert not (r.root / ".reqs").exists()

    (r.root / "out/demo/answers.md").write_text("Subtraction is wrong; add.\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    assert r.progress()["attempt"] == 0
    assert r.af("run", "demo") == 0  # 4th run writes the right code -> through to DONE
    assert r.progress()["phase"] == "DONE"
    impl = [c for c in r.calls() if c["phase"] == "IMPLEMENT"]
    assert len(impl) == 4 and "The human looked at the escalation" in impl[3]["prompt"]
    retro_input = (r.root / "out/demo/retro_input.md").read_text(encoding="utf-8")
    for item in ("### F1 — ", "### F2 — ", "### F3 — ", "### E1 — IMPLEMENT / step 1 - add"):
        assert item in retro_input
    assert "### F4" not in retro_input and "failed attempts 3" in retro_input
    assert "retried after the human's answer: Subtraction is wrong; add." in retro_input
    metrics = (r.root / "metrics/workflow_metrics.csv").read_text(encoding="utf-8").splitlines()
    assert metrics[1].split(",")[2:6] == ["0", "1", "0", "3"]  # rejections, esc., q., gate fails


def test_resume_after_interruption(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0

    orch = Orchestrator(load_config(r.root), echo=lambda s: None)
    real = orch.run_agent

    def interrupted(phase: str, *a: object, **k: object) -> object:
        if phase == "IMPLEMENT":
            raise KeyboardInterrupt
        return real(phase, *a, **k)  # type: ignore[arg-type]

    orch.run_agent = interrupted  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        orch.run("demo")
    assert not (r.root / ".phase").exists() and not (r.root / ".reqs").exists()
    prog = r.progress()
    assert prog["phase"] == "IMPLEMENT" and prog["completed_steps"] == []
    assert prog["waiting"] is None

    assert r.af("run", "demo") == 0
    assert r.progress()["phase"] == "DONE"
    assert [c["phase"] for c in r.calls()] == ["TESTS", "IMPLEMENT", "REVIEW", "PR", "RETRO"]


def test_retro_input_question_and_failed_attempt(demo_repo: DemoRepo) -> None:
    """1 question + 1 failed attempt -> retro_input.md with Q1/F1; the fake retro leaves F1 out
    on its first run, so the coverage check re-runs it once with the missing ID."""
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(questions_in=["IMPLEMENT"], implement_fail=1, retro_omit=["F1"])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_QUESTIONS
    q = [e for e in r.events() if e["kind"] == "questions"]
    assert len(q) == 1 and q[0]["phase"] == "IMPLEMENT" and q[0]["step"] == "step 1 - add"
    assert "Should add() accept floats?" in q[0]["questions"]
    assert r.progress()["attempt"] == 0  # a question is not an attempt
    (r.root / "out/demo/answers.md").write_text("No floats: ints only.\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    archive = (r.root / "out/demo/questions_archive.md").read_text(encoding="utf-8")
    assert "IMPLEMENT / step 1 - add" in archive and "No floats: ints only." in archive
    ans = [e for e in r.events() if e["kind"] == "answer"]
    assert ans[0]["step"] == "step 1 - add" and "No floats" in ans[0]["answer"]

    assert r.af("run", "demo") == 0
    gates = [e for e in r.events() if e["kind"] == "gate"]
    assert [(g["attempt"], g["passed"]) for g in gates] == [(1, False), (2, True)]
    assert gates[0]["failed"] == ["tests (current + earlier steps)"] and gates[0]["excerpt"]
    assert gates[1]["failed"] == [] and gates[1]["excerpt"] == ""

    retro_input = (r.root / "out/demo/retro_input.md").read_text(encoding="utf-8")
    assert "questions 1 (1 round); failed attempts 1; escalations 0" in retro_input
    assert "### Q1 — IMPLEMENT / step 1 - add" in retro_input
    assert "Question: [IMPLEMENT] Should add() accept floats?" in retro_input
    assert "No floats: ints only." in retro_input
    assert "### F1 — IMPLEMENT / step 1 - add, attempt 1" in retro_input
    assert "Failing checks: tests (current + earlier steps)" in retro_input
    assert "### Q2" not in retro_input and "### F2" not in retro_input

    retro = [c for c in r.calls() if c["phase"] == "RETRO"]
    assert len(retro) == 2  # re-run once for the missing F1
    assert "retro_input.md" in retro[0]["prompt"] and "F1" not in retro[0]["prompt"]
    assert "does not address these items" in retro[1]["prompt"] and ": F1." in retro[1]["prompt"]
    assert "Every item ID must appear" in retro[0]["role"]
    proposals = (r.root / "out/demo/retro_proposals.md").read_text(encoding="utf-8")
    assert "Q1" in proposals and "F1" in proposals
    cov = [e for e in r.events() if e["kind"] == "retro_coverage"]
    assert cov == [cov[0]] and cov[0]["missing_first"] == ["F1"] and cov[0]["missing"] == []
    assert cov[0]["items"] == 2 and cov[0]["rerun"] is True
    metrics = (r.root / "metrics/workflow_metrics.csv").read_text(encoding="utf-8").splitlines()
    assert metrics[1].split(",")[2:6] == ["0", "0", "1", "1"]


def test_retro_coverage_warning_after_rerun(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(implement_fail=1, retro_omit=["F1"], retro_omit_runs=2)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == 0  # a warning, not a stop
    assert r.progress()["phase"] == "DONE"
    assert len([c for c in r.calls() if c["phase"] == "RETRO"]) == 2  # only one re-run
    cov = [e for e in r.events() if e["kind"] == "retro_coverage"][0]
    assert cov["missing_first"] == ["F1"] and cov["missing"] == ["F1"]
    assert "WARNING: retro_proposals.md still doesn't address F1" in capsys.readouterr().out


def test_retro_no_items_no_rerun(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == 0
    retro_input = (r.root / "out/demo/retro_input.md").read_text(encoding="utf-8")
    assert "questions 0 (0 rounds); failed attempts 0" in retro_input
    assert retro_input.count("None.") == 7  # one per item type
    assert len([c for c in r.calls() if c["phase"] == "RETRO"]) == 1


def test_permission_denials_reach_events_and_retro(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    denied = {"tool_name": "PowerShell", "tool_input": {"command": "cd src; python -m pytest"}}
    r.scenario(denials={"IMPLEMENT": [denied], "RETRO": [denied, denied]})
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == 0
    ev = [e for e in r.events() if e["kind"] == "permission_denials"]
    assert [e["phase"] for e in ev] == ["IMPLEMENT", "RETRO"]
    assert ev[0]["step"] == "step 1 - add"
    assert ev[0]["denials"] == [{"tool": "PowerShell", "input": "cd src; python -m pytest"}]
    runs = [e for e in r.events() if e["kind"] == "agent_run"]
    assert [e["denials"] for e in runs if e["phase"] == "IMPLEMENT"] == [1]
    retro_input = (r.root / "out/demo/retro_input.md").read_text(encoding="utf-8")
    # the retro input is written before the RETRO run: only the IMPLEMENT denial is in it
    assert "### D1 — IMPLEMENT / step 1 - add: PowerShell denied" in retro_input
    assert "### D2" not in retro_input and "permission denials 1" in retro_input
    cov = [e for e in r.events() if e["kind"] == "retro_coverage"][0]
    assert cov["items"] == 1 and cov["missing"] == []


def test_turn_cap_retry_then_done(demo_repo: DemoRepo) -> None:
    """RETRO stops at its cap once: re-run once with a note. IMPLEMENT stops at its cap: no
    blind re-run, the step gate decides (the files are written, so it passes)."""
    r = demo_repo
    r.add_toml(FAST + "\n[phases.RETRO]\nmax_turns = 7\n")
    r.scenario(max_turns={"RETRO": 1, "IMPLEMENT": 1})
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == 0
    assert r.progress()["phase"] == "DONE"
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
    """An IMPLEMENT run that stops at the cap and fails the gate: a normal failed attempt whose
    feedback also names the turn cap."""
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(max_turns={"IMPLEMENT": 1}, implement_fail=1)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == 0
    impl = [c for c in r.calls() if c["phase"] == "IMPLEMENT"]
    assert len(impl) == 2 and "stopped at the turn cap (80 turns)" in impl[1]["prompt"]
    assert "Attempt 1 failed these gates" in impl[1]["prompt"]


def test_turn_cap_twice_escalates(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(max_turns={"TESTS": 2})
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_ESCALATION
    w = r.progress()["waiting"]
    assert (w["kind"], w["reason"], w["step"]) == ("escalation", "turn_cap", "TESTS")
    esc = (r.root / "out/demo/escalation.md").read_text(encoding="utf-8")
    assert "turn cap (50 turns) 2 times" in esc and "[phases.TESTS] max_turns" in esc
    ev = [e for e in r.events() if e["kind"] == "escalation"]
    assert ev[0]["failed"] == ["turn cap (50)"] and ev[0]["attempts"] == 2
    assert len(r.calls()) == 2
    assert r.af("answer", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE  # third TESTS run finishes normally
    assert len(r.calls()) == 3
