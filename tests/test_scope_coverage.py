"""A refactor, before any code moves: the lines of the files in scope that no tagged test runs."""

from __future__ import annotations

import pathlib

from agentflow.adapters.base import CheckResult
from agentflow.checks import scope_coverage
from conftest import init_git
from test_checks import adapter_for, write

SPEC = """\
# Split — Refactor brief

Feature code: FT
Mode: refactor

## Scope
<!-- | `src/hidden.py` | a comment row is not read | -->
| File / type | What happens to it |
|---|---|
| `src/pkg/big.py` | split; `Helper` (in `src/pkg/big.py`) moves to `src/pkg/new.py` |
| `tests/test_big.py`, `docs/x.md` | not source files |
| `.\\src\\pkg\\other.py` | moved |

## Behavior to preserve
| ID | Behavior | Measured with |
|---|---|---|
| FT-R-001 | `src/pkg/unrelated.py` is named here, not in the scope | |
"""

COBERTURA = """\
<?xml version="1.0" ?>
<coverage>
  <sources><source>{root}</source></sources>
  <packages><package><classes>
    <class filename="src/pkg/big.py"><lines>
      <line number="1" hits="1"/><line number="2" hits="0"/><line number="3" hits="0"/>
      <line number="5" hits="2"/><line number="9" hits="0"/>
    </lines></class>
    <class filename="{root}/src/pkg/big.py"><lines>
      <line number="3" hits="4"/>
    </lines></class>
    <class filename="src/pkg/gone.py"><lines><line number="1" hits="1"/></lines></class>
  </classes></package></packages>
</coverage>
"""


def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    init_git(tmp_path)
    for rel in ("src/pkg/big.py", "src/pkg/other.py", "src/pkg/unrelated.py", "tests/test_big.py"):
        write(tmp_path, rel, "x = 1\n")
    write(tmp_path, "specs/split/requirements.md", SPEC)
    return tmp_path


def test_scope_files_are_the_source_files_of_the_scope_table(tmp_path: pathlib.Path) -> None:
    cfg = adapter_for(repo(tmp_path)).cfg
    # existing source files only, once each, in table order; new.py does not exist yet
    assert scope_coverage.scope_files(cfg, "split") == ["src/pkg/big.py", "src/pkg/other.py"]
    assert scope_coverage.scope_files(cfg, "nope") == []


def test_ranges() -> None:
    assert scope_coverage.ranges([9, 3, 4, 5, 5, 11, 12]) == ["3-5", "9", "11-12"]
    assert scope_coverage.ranges([]) == []


def test_report_lists_the_lines_no_test_ran(tmp_path: pathlib.Path) -> None:
    root = repo(tmp_path)
    xml = root / "out" / "cov.xml"
    write(root, "out/cov.xml", COBERTURA.format(root=root.as_posix()))
    hits = scope_coverage.line_hits([xml, root / "out" / "missing.xml"], root)
    # the two entries of big.py are one file; line 3 is run in one of them
    assert hits == {"src/pkg/big.py": {1: 1, 2: 0, 3: 4, 5: 2, 9: 0}}
    lines, missed = scope_coverage.report(["src/pkg/big.py", "src/pkg/other.py"], hits)
    assert missed == 2
    assert lines == [
        "src/pkg/big.py: 60% (3/5 lines run); not run: 2, 9",
        "src/pkg/other.py: not in the coverage report (no tagged test runs it, or not built)",
    ]


def test_check_runs_the_adapter_and_never_blocks(tmp_path: pathlib.Path) -> None:
    root = repo(tmp_path)
    adapter = adapter_for(root)
    xml = root / "out" / "cov.xml"
    write(root, "out/cov.xml", COBERTURA.format(root=root.as_posix()))
    asked: list[tuple[list[str], list[str]]] = []

    def reports(ids: list[str], files: list[str]) -> tuple[list[pathlib.Path], str]:
        asked.append((list(ids), list(files)))
        return [xml], ""

    adapter.scope_coverage_reports = reports  # type: ignore[method-assign,assignment]
    res = scope_coverage.check(adapter, "split")
    assert asked == [(["FT-R-001"], ["src/pkg/big.py", "src/pkg/other.py"])]
    assert res.status == "PASS" and res.summary == "2 line(s) not run"
    assert "2 line(s) are run by no tagged test" in res.output
    assert "src/pkg/big.py: 60% (3/5 lines run); not run: 2, 9" in res.output

    adapter.scope_coverage_reports = lambda ids, files: ([], "no tool")  # type: ignore[method-assign,assignment]
    assert scope_coverage.check(adapter, "split") == CheckResult("SKIPPED", "SKIPPED: no tool")
    write(root, "specs/split/requirements.md", "# x\n\nFeature code: FT\nMode: refactor\n")
    assert scope_coverage.check(adapter, "split").status == "SKIPPED"
