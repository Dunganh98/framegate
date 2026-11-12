"""CLI: init, spec-brief, gate commands (approve / reject / answer) on crafted progress states."""

from __future__ import annotations

import json
import pathlib

import pytest

from conftest import DemoRepo, git, marker
from framegate import cli
from framegate.orchestrator import EXIT_GATE


def set_waiting(r: DemoRepo, phase: str, waiting: dict | None, **extra: object) -> None:
    prog = {
        "phase": phase,
        "completed_steps": [],
        "sub": None,
        "attempt": 0,
        "feedback": "",
        "waiting": waiting,
        **extra,
    }
    d = r.root / "out" / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.json").write_text(json.dumps(prog), encoding="utf-8")


# ---------- init ----------
def test_init_scaffolds_and_never_overwrites(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("build/\nout/", encoding="utf-8")
    (tmp_path / "specs" / "_template").mkdir(parents=True)
    (tmp_path / "specs" / "_template" / "plan.md").write_text("MINE", encoding="utf-8")
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert 'language = "python"' in (tmp_path / "framegate.toml").read_text(encoding="utf-8")
    assert (tmp_path / "specs/_template/requirements.md").is_file()
    assert (tmp_path / "specs/_template/plan.md").read_text(encoding="utf-8") == "MINE"
    assert "skipped  specs/_template/plan.md (exists, not overwritten)" in out
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == (
        "build/\nout/\n.phase\n.reqs\nmutants/\n"
    )
    assert "--- Add to CLAUDE.md" in out and "# Workflow" in out
    assert 'framegate gates "$FEATURE" --scope final' in out and "setup-python" in out
    assert not (tmp_path / "CLAUDE.md").exists() and not (tmp_path / ".github").exists()

    (tmp_path / "framegate.toml").write_text("# mine\n", encoding="utf-8")
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "framegate.toml").read_text(encoding="utf-8") == "# mine\n"
    assert "skipped  framegate.toml" in out and ".gitignore: already complete" in out


def test_init_language(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 1  # nothing to detect
    assert (
        cli.main(["init", "--language", "dotnet"]) == 0
    )  # adapter not installed yet: still scaffolds
    assert 'language = "dotnet"' in (tmp_path / "framegate.toml").read_text(encoding="utf-8")


# ---------- spec-brief ----------
def test_spec_brief(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    assert demo_repo.af("spec-brief", "demo") == 0
    out = capsys.readouterr().out
    assert "feature 'demo', language python" in out
    assert "specs/demo/ (exists)" in out
    assert '@pytest.mark.req("XXX-R-001"' in out and "raise NotImplementedError" in out
    assert "## Step N: <title>" in out and "framegate approve-spec demo" in out
    assert demo_repo.af("spec-brief", "newfeat") == 0
    assert "missing — run: framegate spec newfeat" in capsys.readouterr().out


def test_commands_outside_project(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["spec-brief", "x"]) == 1
    assert "no framegate.toml found" in capsys.readouterr().err
    assert cli.main(["run", "x"]) == 1


def test_bad_config_is_a_clear_error(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    (demo_repo.root / "framegate.toml").write_text(
        "[agents]\nmax_turns = 'many'\n", encoding="utf-8"
    )
    assert demo_repo.af("status", "demo") == 1
    assert "max_turns must be int" in capsys.readouterr().err


# ---------- spec / approve-spec / run preconditions ----------
def test_spec_sets_phase_and_run_needs_approval(demo_repo: DemoRepo) -> None:
    assert (demo_repo.root / ".phase").read_text(encoding="utf-8") == "SPEC"
    assert demo_repo.af("run", "demo") == 1  # spec not approved
    assert demo_repo.af("approve-spec", "nope") == 1
    assert demo_repo.af("approve-spec", "demo") == 0
    assert not (demo_repo.root / ".phase").exists()
    assert "demo: spec approved" in git(demo_repo.root, "log", "--format=%s")


# ---------- gate commands ----------
def test_approve_tests_gate(demo_repo: DemoRepo) -> None:
    r = demo_repo
    assert r.af("approve-spec", "demo") == 0
    assert r.af("approve", "demo") == 1  # nothing waiting
    set_waiting(r, "TESTS", {"kind": "gate", "gate": "tests", "files": []})
    (r.root / "tests" / "test_demo.py").write_text(
        "def test_x() -> None:\n    assert 1\n", encoding="utf-8"
    )
    assert r.af("status", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    prog = r.progress()
    assert prog["phase"] == "IMPLEMENT" and prog["waiting"] is None
    assert marker(r.root, "tests-approved/demo")
    assert git(r.root, "log", "-1", "--format=%s").strip() == "demo: tests approved"
    assert r.events()[-1]["kind"] == "human_gate" and r.events()[-1]["approved"] is True


def test_reject_requires_reason_and_gate(demo_repo: DemoRepo) -> None:
    r = demo_repo
    set_waiting(r, "TESTS", {"kind": "gate", "gate": "tests", "files": []})
    with pytest.raises(SystemExit):
        r.af("reject", "demo")  # argparse: --reason is required
    assert r.af("reject", "demo", "--reason", "  ") == 1
    assert r.af("reject", "demo", "--reason", "too weak") == 0
    assert "too weak" in r.progress()["feedback"]
    assert r.af("reject", "demo", "--reason", "again") == 1  # no longer waiting


def test_final_gate_commands(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    r = demo_repo
    final = {"kind": "gate", "gate": "final", "files": []}
    set_waiting(r, "FINAL", final)
    capsys.readouterr()
    assert r.af("status", "demo") == EXIT_GATE
    assert "=== FINAL GATE (demo) ===" in capsys.readouterr().out
    assert r.af("reject", "demo", "--reason", "no") == 1  # no reject at the final gate
    assert r.af("approve", "demo") == 0
    assert r.progress()["phase"] == "RETRO" and r.progress()["waiting"] is None
    assert r.events()[-1]["gate"] == "final" and r.events()[-1]["approved"] is True
    assert r.af("approve", "demo") == 1  # no longer waiting
    set_waiting(r, "FINAL", final, parked=[])
    (r.root / "out/demo/answers.md").write_text("a\n", encoding="utf-8")
    assert r.af("answer", "demo") == 1  # nothing parked
    item = {"kind": "finding", "label": "review fixes", "tests": ["t::x"], "reason": "r"}
    set_waiting(r, "FINAL", final, parked=[item])
    assert r.af("answer", "demo") == 0
    prog = r.progress()
    # the answers go into the spec first, then the parked finding is redone in REVIEW / fix
    assert (prog["phase"], prog["redo"]) == ("SPEC_UPDATE", [item])
    then = prog["spec_update"]["then"]
    assert (then["phase"], then["sub"], prog["spec_update"]["gate"]) == ("REVIEW", "fix", "final")


def test_old_pause_kinds_are_dropped(demo_repo: DemoRepo) -> None:
    """A progress.json of an older framegate waiting for questions/escalation: no stop now."""
    r = demo_repo
    set_waiting(r, "IMPLEMENT", {"kind": "escalation", "step": "s", "files": []})
    assert r.af("status", "demo") == 0
    assert r.af("approve", "demo") == 1 and r.af("finish", "demo", "--no-retro") == 1


def test_main_module_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for cmd in (
        "init",
        "spec",
        "approve-spec",
        "run",
        "status",
        "approve",
        "reject",
        "answer",
        "finish",
        "gates",
        "spec-brief",
        "hook",
    ):
        assert cmd in out


def test_gates_command(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    assert demo_repo.af("gates", "demo", "--scope", "step") == 1  # no tests-approved tag yet
    assert "Tag tests-approved/demo not found" in capsys.readouterr().out
