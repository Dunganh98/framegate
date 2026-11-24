"""Check timing and heartbeat, exit records, formatting commits, structure-fix coverage, probes."""

from __future__ import annotations

import os
import signal
import time

import pytest

from conftest import DemoRepo, git
from framegate import format_split, gates, procs
from framegate.adapters import get_adapter
from framegate.adapters.base import Check, CheckResult, TestCase, TestRun, uncovered_lines
from framegate.config import load_config
from framegate.orchestrator import Orchestrator
from framegate.retro import run_health
from framegate.state import log_event, read_events

SRC = "src/demo/__init__.py"


def orch(r: DemoRepo, lines: list[str] | None = None) -> Orchestrator:
    return Orchestrator(load_config(r.root), echo=(lines.append if lines is not None else print))


def test_heartbeat_says_while_running_and_never_after() -> None:
    said: list[int] = []
    with procs.heartbeat(said.append, every=0.05):
        time.sleep(0.3)
    n = len(said)
    time.sleep(0.2)
    assert n >= 2 and len(said) == n
    with pytest.raises(ValueError), procs.heartbeat(said.append, every=0.05):
        raise ValueError
    time.sleep(0.15)
    assert len(said) == n


def test_stop_signals_tell_before_raising() -> None:
    told: list[str] = []
    with pytest.raises(procs.Interrupted), procs.stop_signals(told.append):
        signal.raise_signal(signal.SIGTERM)
        time.sleep(2)
    assert told == ["SIGTERM"]


def test_each_gate_check_is_timed_and_a_long_one_says_so(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow() -> CheckResult:
        time.sleep(0.3)
        return CheckResult("PASS", "")

    monkeypatch.setattr(gates, "checks", lambda *a, **k: [Check("slow", slow)])
    monkeypatch.setattr(procs, "HEARTBEAT", 0.05)
    said: list[str] = []
    adapter = get_adapter(load_config(demo_repo.root))
    rep = gates.gate_report(adapter, "demo", "fix", phase="REVIEW", progress=said.append)
    assert rep.ok and rep.durations["slow"] >= 0.3
    assert "[REVIEW] still running slow (0 s)" in said
    ev = [e for e in read_events(adapter.cfg, "demo") if e["kind"] == "check"]
    assert [(e["name"], e["phase"], e["status"]) for e in ev] == [("slow", "REVIEW", "PASS")]


def test_exit_reason_logged_and_quoted_when_the_run_is_found_gone(demo_repo: DemoRepo) -> None:
    o = orch(demo_repo, [])
    prog = {"phase": "IMPLEMENT", "sub": None}
    with pytest.raises(RuntimeError), o.exit_record("demo", prog):
        raise RuntimeError("boom")
    lock = {"pid": os.getpid(), "feature": "demo", "started": "2000-01-01T00:00:00"}
    o.recover_stale(lock, "run")
    o.recover_stale({**lock, "pid": 1}, "run")
    exits = [e for e in demo_repo.events() if e["kind"] == "orchestrator_exit"]
    assert [(e["reason"], e["phase"]) for e in exits] == [
        ("error: RuntimeError: boom", "IMPLEMENT")
    ]
    hows = [e["how"] for e in demo_repo.events() if e["kind"] == "interrupted"]
    assert hows[0].endswith("its exit record: error: RuntimeError: boom")
    assert hows[1].endswith("gone, no exit record (a hard kill)")


def test_retro_lists_the_slowest_checks_and_the_exits() -> None:
    events = [{"kind": "check", "name": f"c{i}", "phase": "TESTS", "seconds": i} for i in range(7)]
    events += [
        {"kind": "orchestrator_exit", "ts": "t1", "phase": "REVIEW", "reason": "stopped by SIGINT"},
        {"kind": "interrupted", "ts": "t2", "phase": "TESTS", "how": "orchestrator gone (pid 9)"},
    ]
    text = "\n".join(run_health(events))
    assert "- c6 (TESTS): 6 s" in text and "- c2 (TESTS)" in text and "c1 (TESTS)" not in text
    assert "t1 REVIEW: stopped by SIGINT" in text and "t2 TESTS: orchestrator gone" in text
    assert run_health([]) == []


def test_old_lines_the_formatter_rewrote_are_committed_apart(demo_repo: DemoRepo) -> None:
    r = demo_repo
    path = r.root / SRC
    path.write_text(path.read_text(encoding="utf-8") + "\n\nOLD = [1,\n       2]\n", "utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-qm", "debt")
    adapter = get_adapter(load_config(r.root))
    path.write_text(path.read_text(encoding="utf-8") + "\n\nNEW = 3\n", "utf-8")
    adapter.format_files([SRC])
    work = path.read_bytes()
    said: list[str] = []
    orch(r, said).commit_format("demo", "step 1", [SRC])
    assert path.read_bytes() == work
    assert git(r.root, "log", "-1", "--format=%s").strip() == f"demo: format {SRC}"
    diff = git(r.root, "diff", "-U0", "HEAD", "--", SRC)
    changed = [x for x in diff.splitlines() if x[:1] in "+-" and x[:3] not in ("+++", "---")]
    assert changed[-1] == "+NEW = 3" and not any("OLD" in x for x in changed)
    assert any("committed apart" in s for s in said)


def test_task_lines_touching_the_formatted_ones_stay_in_the_task_commit(
    demo_repo: DemoRepo,
) -> None:
    r = demo_repo
    path = r.root / SRC
    path.write_text(path.read_text(encoding="utf-8") + "\n\nOLD = [1,\n       2]\n", "utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-qm", "debt")
    head = git(r.root, "rev-parse", "HEAD")
    adapter = get_adapter(load_config(r.root))
    path.write_text(path.read_text(encoding="utf-8").replace("2]", "2, 5]"), "utf-8")
    adapter.format_files([SRC])
    work = path.read_bytes()
    assert format_split.commit_format(adapter, [SRC], lambda d: "x") == ([], [SRC])
    assert git(r.root, "rev-parse", "HEAD") == head and path.read_bytes() == work
    assert format_split.overlaps("a\nb\n", "a\nb\n", "a\nc\n") is False
    assert format_split.overlaps("a\nb  =1\nc\n", "a\nb = 1\nc\n", "a\nb = 2\nc\n")


def test_uncovered_lines_from_the_diff_cover_report() -> None:
    out = "src/a.py (50.0%): Missing lines 3-5,9\nsrc/b.py (100%)\nTotal:   8 lines\n"
    assert uncovered_lines(out) == {"src/a.py": {3, 4, 5, 9}}


def test_a_fix_gate_with_a_cover_base_runs_diff_coverage(demo_repo: DemoRepo) -> None:
    adapter = get_adapter(load_config(demo_repo.root))
    names = [c.name for c in gates.checks(adapter, "demo", "fix", True, None, "HEAD")]
    assert any(n.startswith("diff coverage") for n in names)
    names = [c.name for c in gates.checks(adapter, "demo", "fix", True)]
    assert not any(n.startswith("diff coverage") for n in names)


def test_uncovered_lines_only_from_structure_fixes_are_the_implementers(
    demo_repo: DemoRepo,
) -> None:
    r = demo_repo
    git(r.root, "add", "-A")
    git(r.root, "commit", "-qm", "task")
    path = r.root / SRC
    path.write_text(
        path.read_text(encoding="utf-8") + "\n\ndef tidy() -> int:\n    return 1\n", "utf-8"
    )
    git(r.root, "add", "-A")
    git(r.root, "commit", "-qm", "demo: structure fixes")
    o = orch(r, [])
    last = len(path.read_text(encoding="utf-8").splitlines())
    out = f"{SRC} (50.0%): Missing lines {last}"
    assert not o.structure_lines("demo", out)  # no structure_fixed record
    sha = git(r.root, "rev-parse", "HEAD").strip()
    log_event(o.cfg, "demo", "structure_fixed", findings=["x"], commits=[sha])
    assert o.structure_lines("demo", out)
    assert not o.structure_lines("demo", f"{SRC} (50.0%): Missing lines 1,{last}")


def test_passing_review_probes_are_moved_out(demo_repo: DemoRepo) -> None:
    r = demo_repo
    review = r.root / "tests" / "review"
    review.mkdir(parents=True)
    (review / "test_probe.py").write_text("def test_a() -> None:\n    pass\n", "utf-8")
    (review / "test_mixed.py").write_text("def test_b() -> None:\n    pass\n", "utf-8")
    cases = [
        TestCase("tests.review.test_probe::test_a", "passed"),
        TestCase("tests.review.test_mixed::test_b", "passed"),
        TestCase("tests.review.test_mixed::test_c", "failed"),
    ]
    said: list[str] = []
    orch(r, said).move_review_probes("demo", TestRun("failed", 1, "", cases))
    assert not (review / "test_probe.py").exists() and (review / "test_mixed.py").exists()
    assert (r.root / "out/demo/review_probes/test_probe.py").is_file()
    ev = [e for e in r.events() if e["kind"] == "review_probes"]
    assert ev[0]["moved"] == ["tests/review/test_probe.py"]
    assert ev[0]["mixed"] == ["tests/review/test_mixed.py"]
    assert any("review_probes/" in s for s in said)
