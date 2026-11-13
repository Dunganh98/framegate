"""Assertions of the feature's tests that never ran fail the final gate (checks/unreached.py).
From the M5-reach-by-save-chance retro: `if the shot was saved: assert ...` on a match
without a saved shot passed every check."""

from __future__ import annotations

import pathlib

from conftest import git, init_git
from framegate.adapters import csharp
from framegate.adapters.base import TestAssertions, cobertura_hits, cobertura_without
from framegate.checks import unreached
from framegate.gates import gate_report
from test_checks import adapter_for, write

SRC = """\
def shots() -> list[dict[str, object]]:
    return [{"outcome": "goal", "chance": 0.4}]
"""

TESTS = """\
import pytest

from pkg.a import shots


@pytest.mark.req("FT-R-001")
def test_save_chance() -> None:
    for shot in shots():
        if shot["outcome"] == "saved":
            assert shot["chance"] > 0
    assert shots()


@pytest.mark.req("FT-R-001")
def test_guards() -> None:
    try:
        shots()
    except ValueError:
        pytest.fail("shots() raised")
    if not shots():
        assert False, "never empty"
    if len(shots()) > 5:
        # unreached: ANS-1, the pinned match has one shot; unit-only
        assert shots()[5]
    assert len(shots()) == 1


def test_not_run() -> None:  # no spec ID: not one of the feature's tests, not run here
    assert shots() == []
"""

GATES = {"commands": {"lint": [], "format": [], "typecheck": []}, "mutation_tool": "none"}


def repo(root: pathlib.Path) -> pathlib.Path:
    init_git(root)
    write(root, ".gitignore", "out/\n.phase\n.reqs\n__pycache__/\n.coverage\n")
    write(root, "src/pkg/a.py", "def shots() -> list[dict[str, object]]:\n    return []\n")
    write(root, "tests/test_old.py", "def test_old() -> None:\n    if 0:\n        assert 1\n")
    write(root, "specs/f/requirements.md", "FT-R-001\n")
    write(root, "pyproject.toml", "[tool.pytest.ini_options]\npythonpath = ['src']\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "spec")
    git(root, "tag", "spec-approved/f")
    write(root, "tests/test_feat.py", TESTS)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "tests")
    git(root, "tag", "tests-approved/f")
    write(root, "src/pkg/a.py", SRC)
    return root


def test_an_assertion_that_never_ran_fails_the_final_gate(tmp_path: pathlib.Path) -> None:
    root = repo(tmp_path)
    a = adapter_for(root, {"gates": GATES, "protected": {"paths": []}})
    assert unreached.feature_test_files(a, "f") == ["tests/test_feat.py"]
    rep = gate_report(a, "f", "final")
    text = rep.report
    assert f"[FAIL] {unreached.NAME}" in text, text
    assert "  1 assertion(s) never ran" in text
    assert 'tests/test_feat.py:10: test_save_chance: assert shot["chance"] > 0' in text
    # guards (pytest.fail, assert False), a marked line and a test that did not run are left out
    found = text.split("ASSERTIONS THAT NEVER RAN")[1]
    assert "test_guards" not in found and "test_not_run" not in found and "test_old" not in found
    assert unreached.NAME in rep.failed and "test smells" not in rep.failed, text
    # the tests' own lines are not part of diff coverage (only the changed source lines)
    assert "  Diff coverage 100% (1/1 changed lines covered)" in text, text

    # data that holds the case: the assertion runs
    write(root, "src/pkg/a.py", SRC.replace('"goal"', '"saved"'))
    rep = gate_report(a, "f", "final")
    assert f"[PASS] {unreached.NAME}" in rep.report, rep.report
    assert unreached.NAME not in rep.failed

    off = adapter_for(
        root, {"gates": {**GATES, "unreached_assertions": False}, "protected": {"paths": []}}
    )
    write(root, "src/pkg/a.py", SRC)
    rep = gate_report(off, "f", "final")
    assert unreached.NAME not in rep.report


def test_python_assertion_lines(tmp_path: pathlib.Path) -> None:
    root = repo(tmp_path)
    a = adapter_for(root)
    tests = {t.name: t for t in a.test_assertions("tests/test_feat.py")}
    assert tests["test_save_chance"] == TestAssertions("test_save_chance", 8, 11, [10, 11])
    assert tests["test_guards"].lines == [24, 25]


CSHARP = """\
namespace N;

public class ReviewTests
{
    [Fact]
    public void SaveChance_OnTarget()
    {
        var r = Build();
        if (r.Outcome == Outcome.Saved)
        {
            Assert.True(r.SaveChance > 0); // "Assert.Fail(" in a comment
        }
        Assert.Equal(2, r.Count);
        if (r.Count > 9) Assert.Fail("too many");
        Assert.True(false, "never");
        r.Count.Should().Be(2);
    }

    [Fact]
    public void Short() => Assert.Equal(1, One());

    private static void Helper() { Assert.True(true); }
}
"""

COBERTURA = """\
<?xml version="1.0" encoding="utf-8"?>
<coverage>
  <sources><source>{root}</source></sources>
  <packages><package name="T"><classes>
    <class name="N.ReviewTests" filename="tests/T/ReviewTests.cs"><lines>
      <line number="8" hits="1"/><line number="9" hits="1"/><line number="11" hits="0"/>
      <line number="13" hits="1"/>
    </lines></class>
    <class name="N.ReviewTests/&lt;&gt;c" filename="tests/T/ReviewTests.cs"><lines>
      <line number="13" hits="0"/><line number="16" hits="3"/>
    </lines></class>
    <class name="N.Model" filename="src/App/Model.cs"><lines>
      <line number="1" hits="4"/>
    </lines></class>
    <class name="Gone" filename="src/App/Gone.cs"><lines><line number="1" hits="1"/></lines></class>
  </classes></package></packages>
</coverage>
"""


def test_csharp_assertion_lines_and_cobertura(tmp_path: pathlib.Path) -> None:
    parsed = csharp.scan(CSHARP)
    by_name = {m.name: m for m in parsed.tests}
    first = by_name["SaveChance_OnTarget"]
    assert (first.body_line, first.end_line) == (7, 17)
    # not the guards that always fail (Assert.Fail, Assert.True(false))
    assert csharp.assertion_lines(first) == [11, 13, 16]
    assert csharp.assertion_lines(by_name["Short"]) == [20]

    write(tmp_path, "tests/T/ReviewTests.cs", CSHARP)
    write(tmp_path, "src/App/Model.cs", "class Model { }\n")
    report = tmp_path / "out/coverage.cobertura.xml"
    write(tmp_path, "out/coverage.cobertura.xml", COBERTURA.format(root=tmp_path.as_posix()))
    hits = cobertura_hits([report], tmp_path)
    assert hits == {
        "tests/T/ReviewTests.cs": {8: 1, 9: 1, 11: 0, 13: 1, 16: 3},  # the most hits per line
        "src/App/Model.cs": {1: 4},
    }
    src_only = tmp_path / "out/src.xml"
    cobertura_without(report, src_only, tmp_path, "tests/")
    assert sorted(cobertura_hits([src_only], tmp_path)) == ["src/App/Model.cs"]
