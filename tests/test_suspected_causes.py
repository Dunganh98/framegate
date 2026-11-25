"""A bug report's suspected causes each name the row whose test reaches them, and a note
says which cause the task's tests never ran (checks/suspected_causes.py)."""

from __future__ import annotations

import pathlib

from conftest import git, init_git
from framegate.checks import spec_lint, suspected_causes
from framegate.scaffold import package_text
from test_checks import adapter_for, write

SRC = """\
def load(rows: list[int], mark: int) -> list[int]:
    start = max(mark, 0)
    if not rows:
        return []
    return [r for r in rows if r > mark and r >= start]
"""
TESTS = """\
import pytest

from pkg.a import load


@pytest.mark.req("SC-R-001")
def test_no_rows_is_empty() -> None:
    assert load([], 3) == []
"""
REPORT = """\
# bug
Mode: bugfix

## Reproduction
| ID | Given / when |
|---|---|
| SC-R-001 | no rows |

## Suspected causes
<!-- - H1: <what> (<file>: <method>) -> <ID> -->
- H1: the start is not floored, `start = max(mark, 0)` (src/pkg/a.py: load) -> SC-R-001
- H2: the row filter `r > mark` compares against the raw mark
  (src/pkg/a.py: load){h2}
- H3: the caller passes a float (src/pkg/b.py: run) -> ruled out: {evidence}
- H4:
"""
GATES = {"commands": {"lint": [], "format": [], "typecheck": []}, "mutation_tool": "none"}


def repo(root: pathlib.Path, h2: str = " -> SC-R-001", evidence: str = "it is an int") -> None:
    init_git(root)
    write(root, ".gitignore", "out/\n.phase\n.reqs\n__pycache__/\n.coverage\n")
    write(root, "src/pkg/a.py", SRC)
    write(root, "specs/f/requirements.md", REPORT.format(h2=h2, evidence=evidence))
    write(root, "pyproject.toml", "[tool.pytest.ini_options]\npythonpath = ['src']\n")
    write(root, "tests/test_feat.py", TESTS)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "spec")


def test_every_suspected_cause_names_its_row_or_why_it_is_ruled_out(
    tmp_path: pathlib.Path,
) -> None:
    repo(tmp_path, h2="", evidence="")
    cfg = adapter_for(tmp_path, {"gates": GATES}).cfg
    causes = suspected_causes.parse((tmp_path / "specs/f/requirements.md").read_text("utf-8"))
    assert [(c.label, c.file, c.function, c.expressions) for c in causes] == [
        ("H1", "src/pkg/a.py", "load", ["start = max(mark, 0)"]),
        ("H2", "src/pkg/a.py", "load", ["r > mark"]),
        ("H3", "src/pkg/b.py", "run", []),
    ]
    assert suspected_causes.spec_problems(cfg, "f") == [
        "requirements.md, Suspected causes, H2: no row of the report covers it, so no test "
        "reaches it. End it with `-> <the ID of the row whose test runs through that code>` or "
        "`-> ruled out: <evidence>`",
        "requirements.md, Suspected causes, H3: ruled out without evidence. Say what rules it out",
    ]
    assert suspected_causes.spec_problems(cfg, "f") == [
        p for p in spec_lint.problems(cfg, "f") if "Suspected causes" in p
    ]

    text = (tmp_path / "specs/f/requirements.md").read_text("utf-8")
    write(tmp_path, "specs/f/requirements.md", text.replace("-> SC-R-001", "-> SC-R-009"))
    assert suspected_causes.spec_problems(cfg, "f")[0].endswith(
        "H1: SC-R-009 is not an ID of this bug report"
    )
    # only a bug report has the section
    write(tmp_path, "specs/f/requirements.md", text.replace("Mode: bugfix\n", ""))
    assert suspected_causes.spec_problems(cfg, "f") == []


def test_a_cause_whose_code_no_test_of_the_task_runs_is_a_note(tmp_path: pathlib.Path) -> None:
    repo(tmp_path)
    a = adapter_for(tmp_path, {"gates": GATES})

    lines, note = suspected_causes.reach(a, "f")

    # the fixture has no rows: the early return comes before the filter (H1 ran, H3 is ruled out)
    assert note == "" and len(lines) == 1, (lines, note)
    assert lines[0].startswith("H2 (src/pkg/a.py:5): no test of the task runs this code: ")
    res = suspected_causes.check(a, "f")
    assert res.status == "PASS" and res.summary == "1 not reached"  # a note, never a failure

    reaching = TESTS + (
        '\n\n@pytest.mark.req("SC-R-001")\ndef test_rows_after_the_mark() -> None:\n'
        "    assert load([1, 5], 3) == [5]\n"
    )
    write(tmp_path, "tests/test_feat.py", reaching)
    assert suspected_causes.reach(a, "f") == ([], "")
    assert suspected_causes.check(a, "f").summary == ""


def test_the_cause_falls_back_to_the_body_of_the_named_function(tmp_path: pathlib.Path) -> None:
    repo(tmp_path)
    a = adapter_for(tmp_path, {"gates": GATES})
    assert a.function_body_lines("src/pkg/a.py", "load") == {2, 3, 4, 5}
    cause = suspected_causes.Cause("H9", "x", "SC-R-001", "a.py", "load", ["gone > now"])
    assert suspected_causes.cause_lines(a, cause) == ("src/pkg/a.py", {2, 3, 4, 5})
    nowhere = suspected_causes.Cause("H9", "x", "SC-R-001", "src/pkg/none.py", "load")
    assert suspected_causes.cause_lines(a, nowhere) == ("", set())


def test_the_bug_report_template_gives_the_line_form() -> None:
    text = package_text("templates", "specs/_template_bugfix/requirements.md")
    assert "(<file>: <method>) -> <ID>" in text and "-> ruled out: <evidence>" in text
    assert suspected_causes.parse(text) == []  # the bare `- H1:` bullets are no causes


OLD_TESTS = """\
from pkg.a import load


def _rows(mark: int) -> list[int]:
    return load([1, 2, 3], mark)


class TestLoad:
    def test_through_a_helper(self) -> None:
        assert _rows(1) == [2, 3]

    def test_direct(self) -> None:
        assert load([], 0) == []


def test_unrelated() -> None:
    assert True
"""


def test_a_bug_spec_names_every_existing_test_of_the_buggy_function(
    tmp_path: pathlib.Path,
) -> None:
    from framegate.checks import bug_tests

    repo(tmp_path)
    write(tmp_path, "tests/test_old.py", OLD_TESTS)
    design = "# fix\n\n## Where the bug is\nThe rows come in (src/pkg/a.py: load).\n\n## Fix\nx\n"
    write(tmp_path, "specs/f/design.md", design)
    adapter = adapter_for(tmp_path, {"gates": GATES})
    assert bug_tests.functions(adapter, design) == ["load"]
    (problem,) = bug_tests.spec_problems(adapter, "f")
    assert problem.startswith("requirements.md: 2 existing test(s) reach load")
    assert problem.endswith(
        ": tests/test_old.py::TestLoad::test_through_a_helper, "
        "tests/test_old.py::TestLoad::test_direct"
    )  # not the task's own tagged test, not the unrelated one
    assert problem in spec_lint.problems(adapter.cfg, "f", adapter)
    spec = tmp_path / "specs/f/requirements.md"
    text = spec.read_text("utf-8")
    keep = "\n## Must not change\n| ID | Behavior |\n|---|---|\n| SC-I-001 | test_direct stays |\n"
    data = "\n## Test data and fixtures\n- `tests/test_old.py::TestLoad::test_through_a_helper`\n"
    spec.write_text(text + keep, "utf-8")
    assert "1 existing test(s)" in bug_tests.spec_problems(adapter, "f")[0]
    spec.write_text(text + keep + data, "utf-8")
    assert bug_tests.spec_problems(adapter, "f") == []
    spec.write_text(text.replace("Mode: bugfix", "Mode: feature"), "utf-8")
    assert bug_tests.spec_problems(adapter, "f") == []
