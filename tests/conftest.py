from __future__ import annotations

import json
import pathlib
import re
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

DESIGN = (
    "# Demo — Design\n\n## Public interfaces\nadd(a: int, b: int) -> int (DEMO-R-001, DEMO-E-001)\n"
)

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

#: two-step variant (DemoRepo.two_steps): step 2 adds neg(), declared to depend on step 1
REQUIREMENTS2 = REQUIREMENTS.replace(
    "| DEMO-R-001 | add() returns the sum of two ints | given 2, 3 when add then 5 |\n",
    "| DEMO-R-001 | add() returns the sum of two ints | given 2, 3 when add then 5 |\n"
    "| DEMO-R-002 | neg() negates an int | given 4 when neg then -4 |\n",
)
DESIGN2 = DESIGN + "neg(a: int) -> int (DEMO-R-002)\n"
PLAN2 = PLAN + "\n## Step 2: neg\nCovers: DEMO-R-002\nDepends on: Step 1\nBuild neg().\n"
STUB2 = STUB + (
    '\n\ndef neg(a: int) -> int:\n    """Negate an integer. Serves DEMO-R-002."""\n'
    "    raise NotImplementedError\n"
)

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

    def two_steps(self) -> None:
        """Switch the demo spec to two plan steps (add, then neg) before approve-spec."""
        spec = self.root / "specs" / "demo"
        (spec / "requirements.md").write_text(REQUIREMENTS2, encoding="utf-8")
        (spec / "design.md").write_text(DESIGN2, encoding="utf-8")
        (spec / "plan.md").write_text(PLAN2, encoding="utf-8")
        (self.root / "src" / "demo" / "__init__.py").write_text(STUB2, encoding="utf-8")

    def set_toml(self, section: str, key: str, value: str) -> None:
        """Set `key = value` (TOML literal) in an existing [section] of agentflow.toml."""
        p = self.root / "agentflow.toml"
        text = p.read_text(encoding="utf-8")
        line = f"{key} = {value}"
        if re.search(rf"^{key} = .*$", text, re.M):
            text = re.sub(rf"^{key} = .*$", line, text, count=1, flags=re.M)
        else:
            header = f"[{section}]\n"
            assert header in text, section
            text = text.replace(header, header + line + "\n", 1)
        p.write_text(text, encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "config")

    def defer_tests_gate(self) -> None:
        """The default: no stop at the tests gate (two human gates, spec and final)."""
        self.set_toml("gates", "tests", '"defer"')

    def add_toml(self, text: str) -> None:
        p = self.root / "agentflow.toml"
        p.write_text(p.read_text(encoding="utf-8") + "\n" + text, encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "config")


@pytest.fixture(autouse=True)
def no_toast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never show Windows toasts from the test suite (tests/test_chat.py checks the call)."""
    monkeypatch.setenv("AGENTFLOW_TOAST", "0")


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
    # fast suite: never start WSL for mutmut (test_python_mutation_e2e.py covers that, slow).
    # The tests gate stops here ("human") and every plan step gets its own implementer run;
    # the defaults ("defer", step batches) have their own tests (DemoRepo.defer_tests_gate,
    # set_toml("agents", "step_batch_ids", ...)); no diagnosis runs unless a test sets
    # [agents] diagnose_after.
    toml = root / "agentflow.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        .replace("[gates]\n", '[gates]\nmutation_runner = "native"\ntests = "human"\n', 1)
        .replace("[agents]\n", "[agents]\nstep_batch_ids = 0\ndiagnose_after = 0\n", 1),
        encoding="utf-8",
    )
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
