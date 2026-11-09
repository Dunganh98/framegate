from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import pytest

from agentflow import cli

HERE = pathlib.Path(__file__).resolve().parent
FAKE_CLAUDE = HERE / "fake_claude.py"

REQUIREMENTS = """\
# Demo — Requirements

Feature code: DEMO

## Requirements
| ID | Requirement | Acceptance criterion |
|---|---|---|
| DEMO-R-001 | add() returns the sum of two ints | given 2, 3 when add then 5 |

## Edge cases
| ID | Situation | Expected behavior |
|---|---|---|
| DEMO-E-001 | a non-int argument | raises TypeError matching "ints only" |
"""

DESIGN = "# Demo — Design\n\n## Public interfaces\nadd(a: int, b: int) -> int (DEMO-R-001, DEMO-E-001)\n"

PLAN = """\
# Demo — Implementation plan

## Step 1: add
Covers: DEMO-R-001, DEMO-E-001
Build add().
"""

STUB = '''\
"""Demo feature."""


def add(a: int, b: int) -> int:
    """Add two integers. Serves DEMO-R-001, DEMO-E-001."""
    raise NotImplementedError
'''

PYPROJECT = """\
[project]
name = "demo"
version = "0.0.0"

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
"""

GITIGNORE = "__pycache__/\n.mypy_cache/\n.ruff_cache/\n.pytest_cache/\n.coverage\n"


def git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def init_git(root: pathlib.Path) -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "commit.gpgsign", "false")


@dataclass
class DemoRepo:
    root: pathlib.Path

    def af(self, *args: str) -> int:
        return cli.main(list(args))

    def scenario(self, **kw: Any) -> None:
        (self.root.parent / "scenario.json").write_text(json.dumps(kw), encoding="utf-8")

    def calls(self) -> list[dict[str, Any]]:
        p = self.root.parent / "calls.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]

    def progress(self) -> dict[str, Any]:
        p = self.root / "out" / "demo" / "progress.json"
        result: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return result

    def events(self) -> list[dict[str, Any]]:
        p = self.root / "out" / "events.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]

    def add_toml(self, text: str) -> None:
        p = self.root / "agentflow.toml"
        p.write_text(p.read_text(encoding="utf-8") + "\n" + text, encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "config")


@pytest.fixture
def demo_repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> DemoRepo:
    """A tiny Python repo with agentflow initialised and the `demo` spec + stub written."""
    root = tmp_path / "repo"
    root.mkdir()
    init_git(root)
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setenv("AGENTFLOW_CLAUDE", str(FAKE_CLAUDE))
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", str(tmp_path / "scenario.json"))
    repo = DemoRepo(root)
    repo.scenario()
    assert repo.af("init") == 0
    (root / "src" / "demo").mkdir(parents=True)
    (root / "tests").mkdir()
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    assert repo.af("spec", "demo") == 0
    spec = root / "specs" / "demo"
    (spec / "requirements.md").write_text(REQUIREMENTS, encoding="utf-8")
    (spec / "design.md").write_text(DESIGN, encoding="utf-8")
    (spec / "plan.md").write_text(PLAN, encoding="utf-8")
    (root / "src" / "demo" / "__init__.py").write_text(STUB, encoding="utf-8")
    return repo


@pytest.fixture
def python_exe() -> str:
    return sys.executable
