"""Chat flow support: feature branch per spec, progress lines (live view), watch, gate-summary,
status --brief, toast notifications, cleanup."""

from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Any

import pytest

from conftest import DemoRepo, git
from framegate import chat, notify, procs
from framegate.config import Config, load_config
from framegate.orchestrator import EXIT_GATE, Orchestrator

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
LINE_RE = re.compile(r"^\d\d:\d\d:\d\d \S.* \[\$\d+\.\d\d\]$")


def branch(r: DemoRepo) -> str:
    return git(r.root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def log_lines(r: DemoRepo) -> list[str]:
    return (r.root / "out/demo/progress.log").read_text(encoding="utf-8").splitlines()


def orch(r: DemoRepo) -> Orchestrator:
    return Orchestrator(load_config(r.root))


def to_tests_gate(r: DemoRepo) -> None:
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE


def to_final_gate(r: DemoRepo) -> None:
    to_tests_gate(r)
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE


# ---------- feature branch ----------
def test_spec_creates_the_feature_branch_from_base(demo_repo: DemoRepo) -> None:
    r = demo_repo  # the fixture ran `framegate spec demo` on a clean main
    assert branch(r) == "feat/demo"
    assert git(r.root, "rev-parse", "feat/demo") == git(r.root, "rev-parse", "main")
    assert git(r.root, "config", "--get", "branch.feat/demo.framegateFeature").strip() == "demo"
    assert (r.root / ".phase").read_text(encoding="utf-8") == "SPEC"
    # again on its own branch, with uncommitted spec edits: fine, stays
    assert r.af("spec", "demo") == 0 and branch(r) == "feat/demo"


def test_spec_refuses_with_dirty_tracked_files(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    git(r.root, "checkout", "-q", "main")
    (r.root / "untracked.txt").write_text("fine", encoding="utf-8")
    (r.root / "pyproject.toml").write_text("# changed\n", encoding="utf-8")
    capsys.readouterr()
    assert r.af("spec", "other") == 1
    err = capsys.readouterr().err
    assert "Uncommitted changes to tracked files on main" in err and "pyproject.toml" in err
    assert branch(r) == "main" and not (r.root / "specs/other").exists()
    git(r.root, "checkout", "--", "pyproject.toml")
    assert r.af("spec", "other") == 0  # untracked files don't block
    assert branch(r) == "feat/other" and (r.root / "untracked.txt").is_file()


def test_spec_switches_to_its_existing_branch_only(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "spec draft")
    git(r.root, "checkout", "-q", "main")
    assert r.af("spec", "demo") == 0 and branch(r) == "feat/demo"  # its own branch: switch
    git(r.root, "checkout", "-q", "main")
    git(r.root, "branch", "feat/foreign")  # made by hand: no mark, no specs/foreign/
    capsys.readouterr()
    assert r.af("spec", "foreign") == 1
    assert "exists but is not framegate's branch for 'foreign'" in capsys.readouterr().err
    assert branch(r) == "main"


def test_spec_branch_options(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    r = demo_repo
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "spec draft")
    r.set_toml("git", "base", '"develop"')
    capsys.readouterr()
    assert r.af("spec", "x") == 1
    assert "Base branch 'develop' not found; set [git] base" in capsys.readouterr().err
    toml = r.root / "framegate.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace('base = "develop"', "branch = false"),
        encoding="utf-8",
    )
    assert r.af("spec", "x") == 0 and branch(r) == "feat/demo"  # stays


def test_spec_refused_during_a_run_and_noted_after_approval(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    assert r.af("approve-spec", "demo") == 0
    procs.write_lock(r.root / "out", "demo")  # this test process = a live run
    capsys.readouterr()
    assert r.af("spec", "demo") == 1
    assert "a framegate run is active (feature demo" in capsys.readouterr().err
    procs.remove_lock(r.root / "out")
    assert r.af("spec", "demo") == 0
    assert "NOTE: the spec of demo is already approved" in capsys.readouterr().out
    assert not (r.root / ".phase").exists()


# ---------- progress lines ----------
def test_progress_lines_through_a_feature(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.scenario(park_steps=[2], assume=True, implement_fail=1)
    to_tests_gate(r)
    lines = log_lines(r)
    assert all(LINE_RE.match(x) for x in lines), lines
    text = "\n".join(lines)
    for part in (
        "TESTS start",
        "[TESTS] checks: Red against stubs PASS, Traceability PASS, Test smells PASS",
        "[TESTS] test reviewer round 1: OK, 0 objection(s) left",
        "TESTS GATE: waiting for you. State: all clean.",
    ):
        assert part in text
    assert "TESTS GATE: waiting for you." in capsys.readouterr().out  # also on stdout
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    text = "\n".join(log_lines(r))
    for part in (
        "IMPLEMENT start",
        "[step 1 - add] start (2 steps; DEMO-R-001, DEMO-E-001)",
        "[step 1 - add] attempt 1 of 3 failed: tests (current + earlier steps)",
        "assumption [DEMO-E-001]: bool counts as int (spec silent).",
        "[step 1 - add] green (attempt 2), committed",
        "[step 2 - neg] PARKED: no reasonable assumption possible",
        "IMPLEMENT done",
        # the parked step's tests are expected red; coverage and mutation wait for the redo
        "[REVIEW] final gates: PASSED; skipped: ",
        "FINAL GATE: waiting for you. Final gates PASSED; 1 parked: step 2 - neg; risk elevated.",
    ):
        assert part in text, part
    assert 10 <= len(log_lines(r)) <= 30  # the live view stays short
    costs = [float(x.rsplit("[$", 1)[1].rstrip("]")) for x in log_lines(r)]
    assert costs == sorted(costs) and costs[-1] > 0
    assert r.af("finish", "demo", "--no-retro") == 0


def test_done_and_error_lines(demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    r = demo_repo
    r.add_toml(FAST)
    to_final_gate(r)
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == 0
    text = "\n".join(log_lines(r))
    assert "RETRO start" in text and "Retro proposals: out/demo/retro_proposals.md" in text
    assert " DONE [$" in log_lines(r)[-1]

    def boom(self: Orchestrator, feature: str, prog: dict[str, Any]) -> None:
        raise RuntimeError("disk full")

    prog_file = r.root / "out/demo/progress.json"
    prog_file.write_text(prog_file.read_text().replace('"DONE"', '"RETRO"'), encoding="utf-8")
    monkeypatch.setattr(Orchestrator, "phase_retro", boom)
    with pytest.raises(RuntimeError):
        r.af("run", "demo")
    assert "ERROR in RETRO: RuntimeError: disk full" in log_lines(r)[-1]
    assert procs.read_lock(r.root / "out") is None


# ---------- watch ----------
def test_watch_prints_new_lines_once_and_ends_without_a_run(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    to_tests_gate(r)
    capsys.readouterr()
    o = orch(r)
    assert chat.watch(o, "demo", start_wait=0) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:-1] == log_lines(r)
    assert out[-1] == "watch ended: waiting at the tests gate -> framegate gate-summary demo"
    o.progress("demo", "one more")
    capsys.readouterr()
    assert chat.watch(o, "demo", start_wait=0) == 0  # re-armed: only what is new
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and "one more" in out[0]


def test_watch_follows_a_live_run(demo_repo: DemoRepo) -> None:
    """A real `framegate watch` process sees lines written while the run holds the lock and
    ends once the lock is gone."""
    r = demo_repo
    to_tests_gate(r)
    (r.root / "out/demo/progress.cursor").write_text(
        str((r.root / "out/demo/progress.log").stat().st_size), encoding="utf-8"
    )
    procs.write_lock(r.root / "out", "demo")
    import sys

    p = subprocess.Popen(
        [sys.executable, "-m", "framegate", "watch", "demo"],
        cwd=r.root,
        stdout=subprocess.PIPE,
        text=True,
    )
    o = orch(r)
    o.progress("demo", "IMPLEMENT start")
    first = p.stdout.readline() if p.stdout else ""
    assert "IMPLEMENT start" in first
    o.progress("demo", "DONE")
    procs.remove_lock(r.root / "out")
    rest, _ = p.communicate(timeout=30)
    assert p.returncode == 0
    assert "DONE" in rest and "watch ended:" in rest.splitlines()[-1]


# ---------- gate summary / status ----------
def test_gate_summary_tests_gate_with_questions(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(questions_in=["TESTS", "TEST_REVIEW"])
    to_tests_gate(r)
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("TESTS GATE: demo ($")
    # the decision and its default come first
    assert lines[1].startswith('Decision: answer questions 1-2 ("ok" takes every default)')
    # what the tests check, in plain words (test_summary.md's column, without the IDs)
    assert "What the tests check:\n  - sum" in out
    assert "Test reviewer: no objections." in out
    assert "Mechanical checks: all pass; 2 test(s), every requirement has tests." in out
    assert "\n  1. [test writer] [TESTS]" in out and "\n  2. [test reviewer] [TEST_REVIEW]" in out
    assert "Spec changes" not in out  # none: not shown
    assert lines[-1].startswith("On request: the full gate file (out/demo/tests_gate.md)")
    assert len(lines) < 25


def test_gate_summary_spec_diff_and_final_gate_parked(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.scenario(park_steps=[2], assume=True)
    to_tests_gate(r)
    (r.root / "out/demo/answers.md").write_text("1. Q: x\n   A: ints only\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE  # SPEC_UPDATE -> tests gate again
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    out = capsys.readouterr().out
    assert "Spec changes from your answers (specs/demo/requirements.md; +1 -1;" in out
    assert "(answer ANS-1)" in out
    assert out.splitlines()[1].startswith('Decision: "ok" approves the tests (default)')
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "FINAL GATE: demo"
    assert lines[1].startswith('Decision: answer question 1 ("ok" takes every default below)')
    # no brief from the summary agent: generated plan lines and struggles
    assert "Plan followed?\n  - Step 1 (add): done\n  - Step 2 (neg): parked (see the items)" in out
    # the parked step's tests are expected red, not a failure
    assert "Outcomes: final gates PASSED; skipped: " in out
    assert "0 failed attempt(s), 1 parked; $" in out
    assert "regression" not in out  # only coverage / mutation scores, and none ran
    assert "structure (not blocking): __init__.py " in out
    # one line when a section has one point
    assert "\nStruggles: step 2 - neg: parked (no reasonable assumption possible" in out
    assert "Code map: - (WARN: no code map at docs/code-map.md)" in out
    assert 'Questions (answer by number; "ok" takes every default):\n  1. [parked] step 2' in out
    assert "bool counts as int" not in out  # assumptions: on request
    assert lines[-1].startswith("On request: the full summary (out/demo/final_summary.md)")
    assert r.af("finish", "demo", "--no-retro") == 0
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 1
    assert "demo is not at a gate: DONE" in capsys.readouterr().out


def test_final_gate_brief_from_the_summary_agent(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """The summary agent's final_brief.md fills the plain-words sections and rewords the
    gate items as questions; the code map it writes is committed."""
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.scenario(park_steps=[2], brief=True, code_map=True)
    to_tests_gate(r)
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    out = capsys.readouterr().out
    assert "Plan followed?\n  - Step 1 (add two numbers): done as planned" in out
    assert "Struggles: No struggles." in out
    # the 3 most important points, the rest on request
    assert "Proven by the tests:\n  - adding two ints gives their sum" in out
    assert "  - the order does not matter\n  ... 1 more line(s) (on request)" in out
    assert "Not proven (check it by using it): nothing to check by hand" in out
    assert "Code map: add() listed under demo" in out and "WARN" not in out
    assert "  1. Should neg(0) give 0 or -0?\n     A: 0 (default) / B: -0" in out
    assert len(out.splitlines()) <= 20
    assert [e["problems"] for e in r.events() if e["kind"] == "brief_check"] == [[]]
    assert "demo: code map" in git(r.root, "log", "--format=%s", "-3")
    prog = r.progress()
    assert prog["code_map"] == {"exists": True, "updated": True, "missing": []}


def test_final_gate_questions_filtered_by_the_brief(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """The brief asks gate items by impact under its own numbers, lists its decisions and
    leaves out a repeated question; the answers are mapped back to the gate items, and the
    left-out item gets the answer of the one it repeats."""
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.scenario(park_steps=[2], questions_in=["REVIEW", "PR"], brief="filtered")
    to_final_gate(r)
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[1].startswith('Decision: answer questions 1-2 ("ok" takes')
    assert (
        "  1. Should add() accept floats?\n     A: no, ints only (default) / B: yes, floats too\n"
        "  2. Should neg(0) give 0 or -0?\n     A: 0 (default) / B: -0\n"
        "Decisions I made:\n  - bools count as ints (Python says so)\n"
        "Left out of the questions: 1 gate item(s)"
    ) in out
    answers = r.root / "out/demo/answers.md"
    answers.write_text(
        "# Answers (final gate)\n\n1. Q: Should add() accept floats?\n   A: yes, floats too\n"
        "2. Q: Should neg(0) give 0 or -0?\n   A: Default: 0\n",
        encoding="utf-8",
    )
    assert r.af("answer", "demo") == 0
    mapped = answers.read_text(encoding="utf-8")
    assert mapped.startswith("# Answers (final gate, numbered by gate item)")
    assert "1. Q: Should neg(0) give 0 or -0?\n   A: Default: 0\n" in mapped
    assert "2. Q: Should add() accept floats?\n   A: yes, floats too\n" in mapped
    assert "3. Q: [summary agent] [PR] Should add() accept floats?\n" in mapped
    assert "   A: Same as item 2: yes, floats too" in mapped
    ev = [e for e in r.events() if e["kind"] == "answer"][-1]
    assert ev["as_given"].startswith("# Answers (final gate)\n")


def test_final_brief_check_asks_for_one_rewrite(demo_repo: DemoRepo) -> None:
    """A brief whose question has no gate-item number and one option goes back to the PR
    agent once; the rewrite passes the check."""
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.scenario(park_steps=[2], brief="bad")
    to_final_gate(r)
    checks = [e for e in r.events() if e["kind"] == "brief_check"]
    assert [c["rewrite"] for c in checks] == [True, False]
    first = "\n".join(checks[0]["problems"])
    assert "name the gate item it answers as [N]" in first and "has one option" in first
    assert "gate items [1] are in none of" in first
    assert checks[1]["problems"] == []
    prompts = [c for c in r.calls() if c["phase"] == "PR"]
    assert len(prompts) == 2 and "rewrite out/demo/final_brief.md only" in prompts[1]["prompt"]
    assert any("final brief: 3 problem(s), rewriting once" in x for x in log_lines(r))


def test_status_brief_and_overview(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    r = demo_repo
    capsys.readouterr()
    assert r.af("status") == 0
    out = capsys.readouterr().out
    assert "Branch: feat/demo" in out and "- demo: SPEC (not approved yet)" in out
    to_tests_gate(r)
    capsys.readouterr()
    assert r.af("status", "demo", "--brief") == 0  # 0 even at a gate (skill injection)
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "demo: waiting at the tests gate -> framegate gate-summary demo"
    assert out[1].startswith("Branch: feat/demo; cost so far $")
    assert out[2] == "Last progress:" and "TESTS GATE" in out[-1]
    assert r.af("status", "nope", "--brief") == 0
    assert "No feature 'nope' here" in capsys.readouterr().out


# ---------- toast ----------
def test_toast_noop_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    cfg = Config(root=tmp_path)
    assert notify.toast(cfg, "t", "x") is None  # FRAMEGATE_TOAST=0 (conftest)
    monkeypatch.delenv("FRAMEGATE_TOAST")
    monkeypatch.setattr(notify.sys, "platform", "linux")
    assert notify.toast(cfg, "t", "x") is None
    monkeypatch.setattr(notify.sys, "platform", "win32")
    cfg.toast = False
    assert notify.toast(cfg, "t", "x") is None
    cfg.toast = True

    calls: list[dict[str, Any]] = []

    def failing(cmd: list[str], **kw: Any) -> Any:
        calls.append(kw)
        raise OSError("no powershell")

    monkeypatch.setattr(notify, "_spawn", failing)
    assert notify.toast(cfg, "t", "x") is None  # fails silently (tried twice: breakaway, plain)
    assert len(calls) == 2
    notify.settle(None)


def test_toast_command_and_calls_at_gate_and_done(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[dict[str, Any]] = []

    class FakeProc:
        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_popen(cmd: list[str], **kw: Any) -> FakeProc:
        started.append({"cmd": cmd, **kw})
        return FakeProc()

    monkeypatch.delenv("FRAMEGATE_TOAST")
    monkeypatch.setattr(notify.sys, "platform", "win32")
    monkeypatch.setattr(notify, "_spawn", fake_popen)
    r = demo_repo
    r.add_toml(FAST)
    to_final_gate(r)
    assert r.af("approve", "demo") == 0 and r.af("run", "demo") == 0
    titles = [s["env"]["FRAMEGATE_TOAST_TITLE"] for s in started]
    assert titles == [
        "framegate: demo at the tests gate",
        "framegate: demo at the final gate",
        "framegate: demo DONE",
    ]
    first = started[0]
    assert first["cmd"][:2] == ["powershell.exe", "-NoProfile"]
    assert "ToastNotificationManager" in first["cmd"][-1]
    assert first["env"]["FRAMEGATE_TOAST_TEXT"] == "State: all clean."
    assert first["stdout"] == subprocess.DEVNULL


# ---------- cleanup ----------
def test_cleanup_keeps_specs_and_metrics(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.add_toml(FAST)
    to_tests_gate(r)
    capsys.readouterr()
    assert r.af("cleanup", "demo") == 1
    assert "demo is not DONE (phase TESTS)" in capsys.readouterr().err
    procs.write_lock(r.root / "out", "demo")
    assert r.af("cleanup", "demo", "--force") == 1
    assert "a run of demo is active" in capsys.readouterr().err
    procs.remove_lock(r.root / "out")
    assert r.af("approve", "demo") == 0 and r.af("run", "demo") == EXIT_GATE
    assert r.af("finish", "demo", "--no-retro") == 0
    runs = r.root / "out/runs"
    other = runs / "20260101-000000-000000-demo-x-TESTS.json"  # another feature's log
    other.write_text("{}", encoding="utf-8")
    assert [p for p in runs.iterdir() if "-demo-T" in p.name]
    capsys.readouterr()
    assert r.af("cleanup", "demo") == 0
    out = capsys.readouterr().out
    assert "Removed: out/demo/" in out and "run log file(s) in out/runs/" in out
    assert "Tags kept for the CI frame diff: spec-approved/demo, tests-approved/demo" in out
    assert not (r.root / "out/demo").exists()
    assert sorted(p.name for p in runs.iterdir()) == [other.name]
    assert (r.root / "out/events.jsonl").is_file() and (r.root / "specs/demo/plan.md").is_file()
    assert ",demo," in (r.root / "metrics/workflow_metrics.csv").read_text(encoding="utf-8")
    assert r.af("status", "demo", "--brief") == 0
    assert "demo: DONE, run files cleaned up; tags left: spec-approved/demo" in (
        capsys.readouterr().out
    )
    assert r.af("cleanup", "demo", "--tags") == 0
    out = capsys.readouterr().out
    assert "Tags deleted (local): spec-approved/demo, tests-approved/demo" in out
    assert "git push origin --delete" not in out  # private refs: nothing was ever pushed
    assert not git(r.root, "for-each-ref", "refs/*-approved/demo", "refs/tags").strip()
    assert r.af("status") == 0
    assert "- demo" not in capsys.readouterr().out  # finished features drop off the overview


def test_protocol_command(capsys: pytest.CaptureFixture[str]) -> None:
    """The plugin skills inject `framegate protocol` (a plugin file outside the project can't
    be read by an injected command)."""
    from framegate import cli

    assert cli.main(["protocol"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# framegate run protocol (chat)")
    for part in ("framegate watch <f>", "framegate gate-summary <f>", "framegate cleanup <f>"):
        assert part in out
