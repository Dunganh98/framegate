"""Chat flow support: feature branch per spec, progress lines (live view), watch, gate-summary,
status --brief, toast notifications, cleanup."""

from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Any

import pytest

from agentflow import chat, notify, procs
from agentflow.config import Config, load_config
from agentflow.orchestrator import EXIT_GATE, Orchestrator
from conftest import DemoRepo, git

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
    r = demo_repo  # the fixture ran `agentflow spec demo` on a clean main
    assert branch(r) == "feat/demo"
    assert git(r.root, "rev-parse", "feat/demo") == git(r.root, "rev-parse", "main")
    assert git(r.root, "config", "--get", "branch.feat/demo.agentflowFeature").strip() == "demo"
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
    assert "exists but is not agentflow's branch for 'foreign'" in capsys.readouterr().err
    assert branch(r) == "main"


def test_spec_branch_options(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    r = demo_repo
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "spec draft")
    r.set_toml("git", "base", '"develop"')
    capsys.readouterr()
    assert r.af("spec", "x") == 1
    assert "Base branch 'develop' not found; set [git] base" in capsys.readouterr().err
    toml = r.root / "agentflow.toml"
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
    assert "an agentflow run is active (feature demo" in capsys.readouterr().err
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
        "[REVIEW] final gates: FAILED (",
        "FINAL GATE: waiting for you. Final gates FAILED; 1 parked: step 2 - neg.",
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
    assert out[-1] == "watch ended: waiting at the tests gate -> agentflow gate-summary demo"
    o.progress("demo", "one more")
    capsys.readouterr()
    assert chat.watch(o, "demo", start_wait=0) == 0  # re-armed: only what is new
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and "one more" in out[0]


def test_watch_follows_a_live_run(demo_repo: DemoRepo) -> None:
    """A real `agentflow watch` process sees lines written while the run holds the lock and
    ends once the lock is gone."""
    r = demo_repo
    to_tests_gate(r)
    (r.root / "out/demo/progress.cursor").write_text(
        str((r.root / "out/demo/progress.log").stat().st_size), encoding="utf-8"
    )
    procs.write_lock(r.root / "out", "demo")
    import sys

    p = subprocess.Popen(
        [sys.executable, "-m", "agentflow", "watch", "demo"],
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
    assert out.startswith("TESTS GATE: demo ($")
    assert "State: 2 open question(s)." in out and "State: State:" not in out
    assert "Checks: Red against stubs PASS, Traceability PASS, Test smells PASS" in out
    assert "Spec IDs -> tests: 2/2 IDs have tests, 2 tests" in out
    assert "DEMO-E-001 (1), DEMO-R-001 (1)" in out
    assert "Questions (answer by number):\n  1. [test writer] [TESTS]" in out
    assert "\n  2. [test reviewer] [TEST_REVIEW]" in out
    assert "Spec changes from your answers: none" in out
    assert "answers file: out/demo/answers.md" in out
    assert out.rstrip().endswith("answers to the numbered questions")
    assert len(out.splitlines()) < 25


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
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    out = capsys.readouterr().out
    assert out.startswith("FINAL GATE: demo ($")
    assert "Final gates: FAILED (" in out  # the parked step's tests are red
    assert "Plan steps: 1/2 green, 1 parked" in out
    assert "Assumptions: 1\n  - [DEMO-E-001] bool counts as int" in out
    assert "Parked (answer by number to redo them):\n  1. step 2 - neg: no reasonable" in out
    assert "Cost per phase: SPEC_UPDATE $" not in out  # since the (re)approval only
    assert out.rstrip().endswith("answers to the parked items")
    assert r.af("finish", "demo", "--no-retro") == 0
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 1
    assert "demo is not at a gate: DONE" in capsys.readouterr().out


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
    assert out[0] == "demo: waiting at the tests gate -> agentflow gate-summary demo"
    assert out[1].startswith("Branch: feat/demo; cost so far $")
    assert out[2] == "Last progress:" and "TESTS GATE" in out[-1]
    assert r.af("status", "nope", "--brief") == 0
    assert "No feature 'nope' here" in capsys.readouterr().out


# ---------- toast ----------
def test_toast_noop_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    cfg = Config(root=tmp_path)
    assert notify.toast(cfg, "t", "x") is None  # AGENTFLOW_TOAST=0 (conftest)
    monkeypatch.delenv("AGENTFLOW_TOAST")
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

    monkeypatch.delenv("AGENTFLOW_TOAST")
    monkeypatch.setattr(notify.sys, "platform", "win32")
    monkeypatch.setattr(notify, "_spawn", fake_popen)
    r = demo_repo
    r.add_toml(FAST)
    to_final_gate(r)
    assert r.af("approve", "demo") == 0 and r.af("run", "demo") == 0
    titles = [s["env"]["AGENTFLOW_TOAST_TITLE"] for s in started]
    assert titles == [
        "agentflow: demo at the tests gate",
        "agentflow: demo at the final gate",
        "agentflow: demo DONE",
    ]
    first = started[0]
    assert first["cmd"][:2] == ["powershell.exe", "-NoProfile"]
    assert "ToastNotificationManager" in first["cmd"][-1]
    assert first["env"]["AGENTFLOW_TOAST_TEXT"] == "State: all clean."
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
    assert "git push origin --delete spec-approved/demo tests-approved/demo" in out
    assert not git(r.root, "tag", "--list", "*/demo").strip()
    assert r.af("status") == 0
    assert "- demo" not in capsys.readouterr().out  # finished features drop off the overview


def test_protocol_command(capsys: pytest.CaptureFixture[str]) -> None:
    """The plugin skills inject `agentflow protocol` (a plugin file outside the project can't
    be read by an injected command)."""
    from agentflow import cli

    assert cli.main(["protocol"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# agentflow run protocol (chat)")
    for part in ("agentflow watch <f>", "agentflow gate-summary <f>", "agentflow cleanup <f>"):
        assert part in out
