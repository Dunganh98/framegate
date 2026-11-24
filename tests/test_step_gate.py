"""The step gate's own checks: plan `Reword:` lines, and the notes of a gate whose tests pass."""

from __future__ import annotations

import pathlib

import pytest

from conftest import git, init_git
from framegate import state
from framegate.checks import plan_rewords, step_notes
from framegate.gates import gate_report
from test_checks import adapter_for, write

PLAN = """\
## Step 1: add
Covers: FT-R-001
Reword: `src/pkg/a.py`:1-3 and 12
Reword: src/pkg/b.py:2, docs/none.md:4-5

## Step 2: neg
Covers: FT-R-002
Reword: src/pkg/a.py:9-10
"""
REQUIREMENTS = """\
FT-R-001 FT-R-002

## Existing data this spec reads
| Words in the spec | Exact member / event | Set where | Assigned as |
|---|---|---|---|
| the limit | `Limits.max_size` | a.py: add | `n/a` |
"""
A = '''\
"""Adds.

Old text."""

MAX = 3


def add(a: int, b: int) -> int:
    # max_size caps the sum
    if a > 100:
        return 0
    return a + b  # plain


def neg(a: int) -> int:
    raise NotImplementedError
'''
B = "X = 1\n# old words\n"
TESTS = """\
import pytest

from pkg.a import add


@pytest.mark.req("FT-R-001")
def test_add() -> None:
    assert add(1, 2) == 3
"""
GATES = {"commands": {"lint": [], "format": [], "typecheck": []}, "mutation_tool": "none"}
NAME = plan_rewords.NAME


def repo(root: pathlib.Path) -> None:
    init_git(root)
    write(root, ".gitignore", "out/\n.phase\n.reqs\n__pycache__/\n.framegate/\n")
    write(root, "specs/f/requirements.md", REQUIREMENTS)
    write(root, "specs/f/plan.md", PLAN)
    write(root, "src/pkg/a.py", A)
    write(root, "src/pkg/b.py", B)
    write(root, "tests/test_f.py", TESTS)
    write(root, "pyproject.toml", "[tool.pytest.ini_options]\npythonpath = ['src']\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "spec")


def test_the_ranges_of_a_reword_line() -> None:
    assert plan_rewords.step_ranges(PLAN, [1, 2, 3]) == {
        1: [("src/pkg/a.py", 1, 3), ("src/pkg/a.py", 12, 12), ("src/pkg/b.py", 2, 2),
            ("docs/none.md", 4, 5)],
        2: [("src/pkg/a.py", 9, 10)],
    }  # fmt: skip
    assert plan_rewords.ranges("Reword the text in a.py:3\n") == []
    assert plan_rewords.touched("@@ -2 +2 @@\n", 1, 3)
    assert plan_rewords.touched("@@ -3,0 +4,2 @@\n", 3, 5)
    assert not plan_rewords.touched("@@ -3,0 +4,2 @@\n@@ -9,2 +11 @@\n", 4, 8)


def test_a_reword_the_step_did_not_do_fails_its_gate(tmp_path: pathlib.Path) -> None:
    repo(tmp_path)
    a = adapter_for(tmp_path, {"gates": GATES, "protected": {"paths": []}})
    state.set_marker(a.cfg, "spec-approved/f")
    state.set_reqs(a.cfg, ["FT-R-001"])
    assert NAME not in gate_report(a, "f", "step").results  # no step named: not checked

    res = plan_rewords.check(a, "f", [1])
    assert res.status == "FAIL" and res.summary == "0/3 range(s) touched"
    assert "REWORD NOT DONE: src/pkg/b.py:2-2 (plan step 1): the file is not changed" in res.output
    assert "not checked (no such file when the spec was approved): docs/none.md:4-5" in res.output

    # two lines added above shift the file: the old line numbers still count
    text = A.replace("Old text.", "New text.").replace("MAX = 3\n", "MAX = 3\nMIN = 0\nMID = 1\n")
    write(tmp_path, "src/pkg/a.py", text.replace("# plain", "# the sum"))
    write(tmp_path, "src/pkg/b.py", "X = 1\n# new words\n")
    assert plan_rewords.check(a, "f", [1]).status == "PASS"
    batch = plan_rewords.check(a, "f", [1, 2])
    assert batch.status == "FAIL" and batch.summary == "3/4 range(s) touched"
    assert "REWORD NOT DONE: src/pkg/a.py:9-10 (plan step 2): the lines are" in batch.output
    rep = gate_report(a, "f", "step", steps=[1, 2])
    assert NAME in rep.failed and "src/pkg/a.py:9-10" in rep.outputs[NAME]
    assert NAME not in gate_report(a, "f", "step", steps=[1]).failed


def test_a_green_step_gate_notes_unexecuted_lines_and_stale_comments(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo(tmp_path)
    a = adapter_for(tmp_path, {"gates": GATES, "protected": {"paths": []}})
    state.set_reqs(a.cfg, ["FT-R-001"])
    assert step_notes.lines(a, "f") == []  # nothing changed
    write(tmp_path, "src/pkg/a.py", A.replace("return a + b  # plain", "return b + a  # plain"))

    assert step_notes.members(a.cfg, "f") == ["max_size"]
    assert step_notes.stale_candidates(a, "f", ["src/pkg/a.py"]) == [
        "src/pkg/a.py:9: # max_size caps the sum"
    ]
    notes = gate_report(a, "f", "step", steps=[1]).notes
    assert notes[-2:] == [step_notes.STALE, "  src/pkg/a.py:9: # max_size caps the sum"]
    if len(notes) > 2:  # with pytest-cov in the environment
        assert notes[:2] == [step_notes.UNEXECUTED, "  src/pkg/a.py add: 11"]

    hits = {"src/pkg/a.py": {10: 1, 11: 0, 12: 1, 16: 0}}
    monkeypatch.setattr(a, "scope_coverage_reports", lambda ids, files: (["x"], ""))
    monkeypatch.setattr(step_notes, "cobertura_hits", lambda reports, root: hits)
    assert step_notes.unexecuted(a, "f", ["src/pkg/a.py"]) == ["src/pkg/a.py add: 11"]
    monkeypatch.setattr(a, "scope_coverage_reports", lambda ids, files: ([], "no tool"))
    assert step_notes.unexecuted(a, "f", ["src/pkg/a.py"]) == []
