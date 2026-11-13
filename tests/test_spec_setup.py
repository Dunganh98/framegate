"""The spec phase covers the setup: setup files are committed with the approved spec, other
uncommitted protected files refuse the approval, `[env] refresh` runs before the commit,
forbidden markers in stubs are found by the spec check; a frame failure the implementer cannot
fix parks at once; with every step parked there is no review. From the first real da-argos
feature (runner-skeleton)."""

from __future__ import annotations

import pytest

from conftest import DemoRepo, git, marker
from framegate import doctor
from framegate.adapters import get_adapter
from framegate.checks import frame_diff, spec_lint
from framegate.config import ConfigError, load_config, parse_config
from framegate.gates import GateReport
from framegate.orchestrator import EXIT_GATE, Orchestrator

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"


def append(r: DemoRepo, rel: str, text: str) -> None:
    path = r.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(old + text, encoding="utf-8")


def phases(r: DemoRepo) -> list[str]:
    return [c["phase"] for c in r.calls()]


# ---------- which files are setup files ----------
@pytest.mark.parametrize(
    ("path", "setup"),
    [
        ("framegate.toml", True),
        ("pyproject.toml", True),
        ("pytest.ini", True),
        ("requirements-dev.txt", True),
        ("conftest.py", True),
        ("config/app.yaml", True),  # [protected] extra
        ("tests/conftest.py", False),
        ("specs/other/plan.md", False),
        ("CLAUDE.md", False),
        (".claude/settings.json", False),
        (".framegate/project.md", False),
        (".github/workflows/ci.yml", False),
        ("azure-pipelines.yml", False),
        ("roles/implement.md", False),  # [roles] extra_dir
        ("src/demo/__init__.py", False),
        ("README.md", False),
    ],
)
def test_setup_files(demo_repo: DemoRepo, path: str, setup: bool) -> None:
    r = demo_repo
    toml = r.root / "framegate.toml"
    text = toml.read_text(encoding="utf-8")
    text = text.replace("[protected]\n", '[protected]\nextra = ["config/"]\n', 1)
    toml.write_text(text + '\n[roles]\nextra_dir = "roles"\n', encoding="utf-8")
    adapter = get_adapter(load_config(r.root))
    assert frame_diff.is_setup_file(adapter, path) is setup


def test_the_spec_session_is_told_its_setup_files(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    append(r, "framegate.toml", '\n[env]\nrefresh = ["uv", "sync"]\n')
    assert r.af("spec-brief", "demo") == 0
    out = capsys.readouterr().out
    assert "- Setup files (change them now" in out
    assert "frozen from then on): framegate.toml, pyproject.toml, setup.cfg," in out
    assert "The approval then runs [env] refresh (uv sync)." in out
    line = next(x for x in out.splitlines() if x.startswith("- Setup files"))
    assert "tests/" not in line and "CLAUDE.md" not in line and ".github/" not in line


def test_setup_files_dotnet(tmp_path) -> None:
    cfg = parse_config(tmp_path, {"project": {"language": "dotnet"}})
    adapter = get_adapter(cfg)
    assert frame_diff.is_setup_file(adapter, "App.sln")
    assert frame_diff.is_setup_file(adapter, "src/App/App.csproj")
    assert not frame_diff.is_setup_file(adapter, "tests/App.Tests/App.Tests.csproj")
    assert not frame_diff.is_setup_file(adapter, "CLAUDE.md")


# ---------- approve-spec ----------
def test_approve_spec_commits_the_setup_files(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """Edits to the config and the packaging files made in the spec phase, and a new one, are
    part of the "spec approved" commit: the frame check starts from a clean baseline."""
    r = demo_repo
    append(r, "framegate.toml", "\n# a new source folder\n")
    append(r, "pyproject.toml", "\n# registers the new package\n")
    append(r, "setup.cfg", "[metadata]\nname = demo\n")  # untracked
    append(r, "notes.txt", "mine\n")  # not protected: never committed
    adapter = get_adapter(load_config(r.root))
    wanted = ["framegate.toml", "pyproject.toml", "setup.cfg"]
    assert frame_diff.setup_state(adapter) == (wanted, [])
    assert r.af("spec-check", "demo") == 0
    out = capsys.readouterr().out
    assert "Setup files changed (committed with the spec): " + ", ".join(wanted) in out

    assert r.af("approve-spec", "demo") == 0
    assert "Setup files committed with the spec: " + ", ".join(wanted) + "." in (
        capsys.readouterr().out
    )
    committed = git(r.root, "show", "--name-only", "--format=", "HEAD").split()
    assert set(wanted) <= set(committed) and "notes.txt" not in committed
    assert "src/demo/__init__.py" in committed and "specs/demo/plan.md" in committed
    assert git(r.root, "status", "--porcelain").split() == ["??", "notes.txt"]
    (approved,) = [e for e in r.events() if e["kind"] == "spec_approved"]
    assert approved["setup"] == wanted
    assert frame_diff.setup_state(adapter) == ([], [])


def test_approve_spec_refuses_other_uncommitted_protected_files(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unrelated edit to CLAUDE.md (seen live) or an untracked CI file is not committed
    silently and not left to fail every frame check: the approval refuses and names them."""
    r = demo_repo
    append(r, "CLAUDE.md", "# my notes\n")
    append(r, ".github/workflows/ci.yml", "on: push\n")
    append(r, "pyproject.toml", "\n# setup\n")
    assert r.af("spec-check", "demo") == 1
    out = capsys.readouterr().out
    assert "2 protected file(s) have uncommitted changes" in out
    assert ".github/workflows/ci.yml, CLAUDE.md. Commit or revert them" in out
    assert "Setup files changed (committed with the spec): pyproject.toml" in out
    for args in (["approve-spec", "demo"], ["approve-spec", "demo", "--force"]):
        assert r.af(*args) == 1
        err = capsys.readouterr().err
        assert ".github/workflows/ci.yml, CLAUDE.md" in err and "Then approve again." in err
    assert not marker(r.root, "spec-approved/demo")
    assert (r.root / ".phase").read_text(encoding="utf-8") == "SPEC"  # the chat can still fix
    assert "pyproject.toml" in git(r.root, "status", "--porcelain")  # nothing was committed

    git(r.root, "add", "CLAUDE.md", ".github")
    git(r.root, "commit", "-q", "-m", "mine")
    assert r.af("approve-spec", "demo") == 0
    assert marker(r.root, "spec-approved/demo")


def test_env_refresh_runs_before_the_commit(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """[env] refresh: `python` is the project interpreter; a setup file the command rewrites
    (a lock file) is committed with the spec."""
    r = demo_repo
    code = "import pathlib; pathlib.Path('uv.lock').write_text('locked')"
    append(r, "framegate.toml", f'\n[env]\nrefresh = ["python", "-c", "{code}"]\n')
    assert r.af("approve-spec", "demo") == 0
    assert "Environment refresh: python -c" in capsys.readouterr().out
    committed = git(r.root, "show", "--name-only", "--format=", "HEAD").split()
    assert {"framegate.toml", "uv.lock"} <= set(committed)
    (ev,) = [e for e in r.events() if e["kind"] == "env_refresh"]
    assert ev["code"] == 0


def test_a_failed_env_refresh_refuses_the_approval(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    code = "import sys; print('no such package: runner'); sys.exit(3)"
    append(r, "framegate.toml", f'\n[env]\nrefresh = ["python", "-c", "{code}"]\n')
    assert r.af("approve-spec", "demo") == 1
    err = capsys.readouterr().err
    assert "[env] refresh failed (exit 3)" in err and "no such package: runner" in err
    assert "The spec is not approved." in err
    assert not marker(r.root, "spec-approved/demo")
    assert (r.root / ".phase").is_file()


def test_env_refresh_config(tmp_path) -> None:
    assert parse_config(tmp_path, {}).env_refresh == []
    cfg = parse_config(tmp_path, {"env": {"refresh": ["uv", "sync"]}})
    assert cfg.env_refresh == ["uv", "sync"]
    with pytest.raises(ConfigError, match=r"\[env\] refresh must be a list of strings"):
        parse_config(tmp_path, {"env": {"refresh": ["uv", 1]}})


# ---------- forbidden markers in the stubs ----------
def test_spec_check_finds_forbidden_markers_in_stubs(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """A suppression the spec phase adds to a stub is named with file and line; one that was
    in the code before the feature is not this spec's problem."""
    r = demo_repo
    # code from before the feature, on the base branch
    git(r.root, "checkout", "-q", "main")
    old = r.root / "src" / "old.py"
    old.write_text("import os  # noqa: F401\n\nX = 1\n", encoding="utf-8")
    git(r.root, "add", "src/old.py")
    git(r.root, "commit", "-q", "-m", "old code")
    git(r.root, "checkout", "-q", "feat/demo")
    git(r.root, "merge", "-q", "main")
    adapter = get_adapter(load_config(r.root))
    assert spec_lint.stub_marker_problems(adapter) == []

    append(r, "src/old.py", "Y: int = None  # type: ignore[assignment]\n")  # tracked, line 4
    stub = r.root / "src/demo/__init__.py"  # untracked
    stub.write_text(
        stub.read_text(encoding="utf-8").replace(
            "    raise NotImplementedError", "    raise NotImplementedError  # pragma: no cover"
        ),
        encoding="utf-8",
    )
    found = spec_lint.problems(adapter.cfg, "demo", adapter)
    assert len(found) == 2, found
    assert found[0].startswith("src/old.py:4: forbidden marker `type: ignore` in a stub")
    assert found[1].startswith("src/demo/__init__.py:6: forbidden marker `pragma: no cover`")
    assert spec_lint.problems(adapter.cfg, "demo") == []  # without the adapter: the tables only

    assert r.af("spec-check", "demo") == 1
    assert "src/demo/__init__.py:6: forbidden marker" in capsys.readouterr().out
    assert r.af("approve-spec", "demo") == 1
    assert "forbidden marker" in capsys.readouterr().err
    assert not marker(r.root, "spec-approved/demo")


# ---------- a failure the implementer cannot fix ----------
def frame_report(*lines: str) -> GateReport:
    name = frame_diff.CHECK_NAME
    return GateReport(False, "", [name], full_outputs={name: "\n".join(lines)})


def test_unfixable_frame_failures(demo_repo: DemoRepo) -> None:
    orch = Orchestrator(load_config(demo_repo.root))
    changed = frame_diff.PROTECTED_CHANGED
    assert orch.unfixable(frame_report(f"{changed}CLAUDE.md", f"{changed}pyproject.toml")) == (
        "the frame check fails only on protected files it may not edit, changed since the "
        "tests were approved: CLAUDE.md, pyproject.toml"
    )
    # its own suppression: the implementer removes it in the next attempt
    assert orch.unfixable(frame_report("SUPPRESSION ADDED: x = 1  # noqa")) == ""
    assert orch.unfixable(frame_report(f"{changed}CLAUDE.md", "SUPPRESSION ADDED: # noqa")) == ""
    # a protected file inside the source folders: it can revert that itself
    assert orch.unfixable(frame_report(f"{changed}src/demo/generated_pb2.py")) == ""
    # another failing check next to it: the normal loop
    mixed = frame_report(f"{changed}CLAUDE.md")
    mixed.failed.append("tests (current + earlier steps)")
    assert orch.unfixable(mixed) == ""


def test_frame_failure_on_protected_files_parks_at_once_and_skips_the_review(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """A protected file changed behind the run's back: the step parks after one attempt, with
    no diagnosis run, and names the file. Nothing was built, so no reviewer, no final checks
    and no summary run: the final gate comes at once and its brief still works."""
    r = demo_repo
    r.add_toml(FAST)
    r.set_toml("agents", "diagnose_after", "1")
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    append(r, "pyproject.toml", "\n# changed after the tests were approved\n")
    assert r.af("run", "demo") == EXIT_GATE
    assert phases(r) == ["TESTS", "TEST_REVIEW", "IMPLEMENT"]
    (p,) = r.progress()["parked"]
    assert p["attempts"] == 1 and p["reason"] == (
        "unfixable in IMPLEMENT: the frame check fails only on protected files it may not "
        "edit, changed since the tests were approved: pyproject.toml"
    )
    kinds = [e["kind"] for e in r.events()]
    assert kinds.count("review_skipped") == 1 and "final_gates" not in kinds
    prog = r.progress()
    assert prog["waiting"]["gate"] == "final" and prog["review_skipped"] is True
    assert prog["waiting"]["message"].startswith(
        "Nothing built (review and final checks skipped); 1 parked: step 1 - add"
    )
    out_dir = r.root / "out" / "demo"
    assert "Not run: every plan step is parked" in (out_dir / "final_gates.md").read_text("utf-8")
    assert "Nothing was built" in (out_dir / "final_summary.md").read_text("utf-8")
    assert "PARKED (unfixable in IMPLEMENT" in (out_dir / "final_facts.md").read_text("utf-8")
    assert not (out_dir / "final_brief.md").exists()

    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    brief = capsys.readouterr().out
    assert "Decision: answer question 1" in brief
    assert "Step 1 (add): parked (see the items)" in brief
    assert "Outcomes: nothing built, so no review and no final checks; 1 failed attempt(s)" in brief
    assert "pyproject.toml" in brief and "Proven by the tests" not in brief
    assert r.af("stats", "demo") == 0
    assert "Rounds with every step parked" in capsys.readouterr().out


def test_the_first_code_after_a_skipped_review_is_reviewed(demo_repo: DemoRepo) -> None:
    """Round 1: the only step parks (a question), nothing to review. After the answer the redo
    builds it: that code goes through the reviewer and the final checks like any first round."""
    r = demo_repo
    r.add_toml(FAST)
    r.defer_tests_gate()
    r.scenario(park_steps=[1])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert phases(r) == ["TESTS", "TEST_REVIEW", "IMPLEMENT"]
    (r.root / "out/demo/answers.md").write_text("1. Q: step 1\n   A: Ints only.\n", "utf-8")
    assert r.af("answer", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert phases(r)[3:] == ["SPEC_UPDATE", "TESTS", "IMPLEMENT", "REVIEW", "PR"]
    prog = r.progress()
    assert prog["completed_steps"] == [1] and "review_skipped" not in prog
    assert prog["waiting"]["message"].startswith("Final gates PASSED; nothing parked")
    assert [e["passed"] for e in r.events() if e["kind"] == "final_gates"] == [True]


# ---------- doctor ----------
def test_doctor_warns_about_uncommitted_protected_files(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    lines: list[str] = []
    assert doctor.run(load_config(r.root), echo=lines.append) == 0
    assert "[OK] git: uncommitted protected files" in lines
    append(r, "CLAUDE.md", "# notes\n")
    append(r, "pyproject.toml", "\n# x\n")
    lines.clear()
    assert doctor.run(load_config(r.root), echo=lines.append) == 0
    out = "\n".join(lines)
    assert "[WARN] git: uncommitted protected files" in out
    assert "commit or revert before a feature (approve-spec refuses): CLAUDE.md" in out
    assert "setup files, committed with the next approved spec: pyproject.toml" in out
    assert "- WARN git: uncommitted protected files" in out  # needs a decision: in the brief
