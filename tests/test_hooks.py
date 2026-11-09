from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time

import pytest

from agentflow.hooks import protect_paths, require_green


def hook_json(cwd: pathlib.Path, tool: str = "Write", **tool_input: str) -> str:
    return json.dumps({"cwd": str(cwd), "tool_name": tool, "tool_input": tool_input})


@pytest.fixture
def project(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "agentflow.toml").write_text('[project]\nlanguage = "python"\n', encoding="utf-8")
    return tmp_path


def set_phase(root: pathlib.Path, phase: str) -> None:
    (root / ".phase").write_text(phase, encoding="utf-8")


# ---------- pre-tool-use ----------
def test_no_config_is_silent_noop(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".phase").write_text("IMPLEMENT", encoding="utf-8")  # ignored without toml
    assert protect_paths.evaluate(hook_json(tmp_path, file_path=str(tmp_path / "x.py"))) == (0, "")


def test_no_phase_allows_everything(project: pathlib.Path) -> None:
    data = hook_json(project, file_path=str(project / "tests" / "test_x.py"))
    assert protect_paths.evaluate(data) == (0, "")


@pytest.mark.parametrize(
    ("phase", "rel", "allowed"),
    [
        ("TESTS", "tests/test_a.py", True),
        ("TESTS", "out/demo/test_summary.md", True),
        ("TESTS", "src/pkg/a.py", False),
        ("IMPLEMENT", "src/pkg/a.py", True),
        ("IMPLEMENT", "tests/test_a.py", False),
        ("IMPLEMENT", "agentflow.toml", False),
        ("IMPLEMENT", "pyproject.toml", False),
        ("REVIEW", "tests/review/test_r.py", True),
        ("REVIEW", "tests/test_a.py", False),
        ("REVIEW", "src/pkg/a.py", False),
        ("TEST_REVIEW", "out/demo/test_review.md", True),
        ("TEST_REVIEW", "tests/test_a.py", False),
        ("TRIAGE", "tests/review/test_r.py", True),
        ("TRIAGE", "tests/test_a.py", False),
        ("TRIAGE", "src/pkg/a.py", False),
        ("PR", "out/demo/final_summary.md", True),
        ("PR", "src/pkg/a.py", False),
        ("SPEC", "specs/demo/plan.md", True),
        ("SPEC", "src/pkg/a.py", True),
        ("SPEC", "tests/test_a.py", False),
        ("UNKNOWN", "out/x.md", False),
    ],
)
def test_phase_permissions(project: pathlib.Path, phase: str, rel: str, allowed: bool) -> None:
    set_phase(project, phase)
    code, msg = protect_paths.evaluate(hook_json(project, file_path=str(project / rel)))
    if allowed:
        assert (code, msg) == (0, "")
    else:
        assert code == 2
        assert f"[{phase}] Blocked write to {rel}" in msg
        assert ("park.md" if phase == "IMPLEMENT" else "questions.md") in msg


def test_relative_path_resolves_against_cwd(project: pathlib.Path) -> None:
    set_phase(project, "IMPLEMENT")
    sub = project / "src"
    sub.mkdir()
    assert protect_paths.evaluate(hook_json(sub, file_path="pkg/a.py"))[0] == 0
    assert protect_paths.evaluate(hook_json(sub, file_path="../tests/t.py"))[0] == 2


def test_outside_repository_blocked(
    project: pathlib.Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    set_phase(project, "IMPLEMENT")
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "x.py"
    code, msg = protect_paths.evaluate(hook_json(project, file_path=str(elsewhere)))
    assert code == 2 and "outside the repository" in msg


def test_notebook_path_and_tool_filter(project: pathlib.Path) -> None:
    set_phase(project, "IMPLEMENT")
    nb = str(project / "tests" / "nb.ipynb")
    assert protect_paths.evaluate(hook_json(project, "NotebookEdit", notebook_path=nb))[0] == 2
    assert protect_paths.evaluate(hook_json(project, "MultiEdit", file_path=nb))[0] == 2
    assert protect_paths.evaluate(hook_json(project, "Read", file_path=nb)) == (0, "")
    assert protect_paths.evaluate(hook_json(project, "Write")) == (0, "")  # no path


def test_configured_write_paths(project: pathlib.Path) -> None:
    (project / "agentflow.toml").write_text(
        '[paths]\nphase_file = "build/.phase"\n[phases.IMPLEMENT]\nwrite = ["lib/"]\n',
        encoding="utf-8",
    )
    (project / "build").mkdir()
    (project / "build" / ".phase").write_text("IMPLEMENT", encoding="utf-8")
    assert protect_paths.evaluate(hook_json(project, file_path=str(project / "lib/a.py")))[0] == 0
    assert protect_paths.evaluate(hook_json(project, file_path=str(project / "src/a.py")))[0] == 2


def test_garbage_input_is_noop(project: pathlib.Path) -> None:
    assert protect_paths.evaluate("not json") == (0, "")
    assert protect_paths.evaluate("") == (0, "")
    assert require_green.evaluate("[1, 2]") == (0, "")


def run_cli_hook(event: str, stdin: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentflow", "hook", event],
        input=stdin,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_cli_hook_silent_outside_project(tmp_path: pathlib.Path) -> None:
    t0 = time.perf_counter()
    for event in ("pre-tool-use", "stop"):
        r = run_cli_hook(event, hook_json(tmp_path, file_path=str(tmp_path / "a.py")), tmp_path)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert time.perf_counter() - t0 < 10  # two interpreter starts, no project work


def test_cli_hook_blocks_with_exit_2(project: pathlib.Path) -> None:
    set_phase(project, "TESTS")
    r = run_cli_hook(
        "pre-tool-use", hook_json(project, file_path=str(project / "src/a.py")), project
    )
    assert r.returncode == 2 and "Blocked write to src/a.py" in r.stderr and r.stdout == ""


# ---------- stop ----------
def stop_json(cwd: pathlib.Path, active: bool = False) -> str:
    return json.dumps({"cwd": str(cwd), "stop_hook_active": active, "hook_event_name": "Stop"})


@pytest.fixture
def green_red(project: pathlib.Path) -> pathlib.Path:
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        "import pytest\n\n\n"
        '@pytest.mark.req("X-R-001")\ndef test_green() -> None:\n    assert True\n\n\n'
        '@pytest.mark.req("X-R-002")\ndef test_red() -> None:\n    assert 1 == 2\n',
        encoding="utf-8",
    )
    return project


def test_stop_only_in_implement(green_red: pathlib.Path) -> None:
    assert require_green.evaluate(stop_json(green_red)) == (0, "")  # no phase
    set_phase(green_red, "TESTS")
    assert require_green.evaluate(stop_json(green_red)) == (0, "")


def test_stop_blocks_while_selected_tests_fail(green_red: pathlib.Path) -> None:
    set_phase(green_red, "IMPLEMENT")
    code, msg = require_green.evaluate(stop_json(green_red))  # no .reqs -> full suite
    assert code == 2 and "Tests are still failing" in msg and "test_red" in msg
    (green_red / ".reqs").write_text("X-R-001", encoding="utf-8")
    assert require_green.evaluate(stop_json(green_red)) == (0, "")
    (green_red / ".reqs").write_text("X-R-001,X-R-002", encoding="utf-8")
    assert require_green.evaluate(stop_json(green_red))[0] == 2


def test_stop_allowed_when_parked(green_red: pathlib.Path) -> None:
    """An implementer that parks (park.md: no reasonable assumption) may stop with red tests."""
    set_phase(green_red, "IMPLEMENT")
    code, msg = require_green.evaluate(stop_json(green_red))
    assert code == 2 and "park.md" in msg
    park = green_red / "out" / "f" / "park.md"
    park.parent.mkdir(parents=True)
    park.write_text("- Which?\n", encoding="utf-8")
    assert require_green.evaluate(stop_json(green_red)) == (0, "")


def test_stop_hook_active_allows_stop(green_red: pathlib.Path) -> None:
    set_phase(green_red, "IMPLEMENT")
    assert require_green.evaluate(stop_json(green_red, active=True)) == (0, "")
