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
    assert (r.root / "out/demo/pr.md").is_file()
    assert (r.root / "out/demo/retro_proposals.md").is_file()
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
        assert argv[argv.index("--max-turns") + 1] == "80"
    tests_call = r.calls()[0]
    tools = tests_call["argv"][tests_call["argv"].index("--allowedTools") + 1].split(",")
    assert "Bash(python -m pytest *)" in tools and "Write" in tools
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
    assert any(e["kind"] == "escalation" for e in r.events())
    assert "failed 3 times" in (r.root / "out/demo/escalation.md").read_text(encoding="utf-8")
    assert not (r.root / ".reqs").exists()

    assert r.af("answer", "demo") == 0
    assert r.progress()["attempt"] == 0
    assert r.af("run", "demo") == 0  # 4th run writes the right code -> through to DONE
    assert r.progress()["phase"] == "DONE"
    impl = [c for c in r.calls() if c["phase"] == "IMPLEMENT"]
    assert len(impl) == 4 and "The human looked at the escalation" in impl[3]["prompt"]


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
