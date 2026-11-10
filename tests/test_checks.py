"""Frame diff, traceability, test smells, red-against-stubs, gate runner (python adapter)."""

from __future__ import annotations

import pathlib

import pytest

from agentflow.adapters import get_adapter
from agentflow.adapters.base import CheckResult
from agentflow.adapters.python import PythonAdapter
from agentflow.checks import frame_diff, red_stubs, traceability
from agentflow.checks import test_smells as smells_check
from agentflow.config import parse_config
from agentflow.gates import run_gates
from conftest import git, init_git


def write(root: pathlib.Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def adapter_for(root: pathlib.Path, data: dict | None = None) -> PythonAdapter:
    a = get_adapter(parse_config(root, data or {}))
    assert isinstance(a, PythonAdapter)
    return a


# ---------- frame diff ----------
@pytest.fixture
def framed(tmp_path: pathlib.Path) -> pathlib.Path:
    init_git(tmp_path)
    write(tmp_path, ".gitignore", "out/\n.phase\n")
    write(tmp_path, "src/pkg/a.py", "def f() -> int:\n    raise NotImplementedError\n")
    write(tmp_path, "tests/test_a.py", "def test_a() -> None:\n    assert True\n")
    write(tmp_path, "specs/f/requirements.md", "FT-R-001\n")
    write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "tag", "tests-approved/f")
    return tmp_path


def test_frame_intact(framed: pathlib.Path) -> None:
    write(framed, "src/pkg/a.py", "def f() -> int:\n    return 1\n")
    write(framed, "src/pkg/b.py", "X = 1\n")
    write(framed, "tests/review/test_r.py", "def test_r() -> None:\n    assert 1\n")
    write(framed, "out/f/notes.md", "ignored\n")
    res = frame_diff.check(adapter_for(framed), "f")
    assert res == CheckResult("PASS", "Frame intact.")


def test_frame_missing_tag(framed: pathlib.Path) -> None:
    res = frame_diff.check(adapter_for(framed), "other")
    assert res.status == "FAIL" and "tests-approved/other not found" in res.output


@pytest.mark.parametrize(
    "rel",
    [
        "tests/test_a.py",
        "tests/test_new.py",
        "specs/f/requirements.md",
        "pyproject.toml",
        "agentflow.toml",
        "CLAUDE.md",
        ".claude/settings.json",
        ".github/workflows/x.yml",
        "requirements-dev.txt",
        "uv.lock",
        "conftest.py",
    ],
)
def test_frame_protected_paths(framed: pathlib.Path, rel: str) -> None:
    write(framed, rel, "changed\n")
    res = frame_diff.check(adapter_for(framed), "f")
    assert res.status == "FAIL" and f"PROTECTED PATH CHANGED: {rel}" in res.output


def test_frame_committed_change_still_caught(framed: pathlib.Path) -> None:
    write(framed, "tests/test_a.py", "def test_a() -> None:\n    assert False\n")
    git(framed, "commit", "-q", "-am", "sneaky")
    assert (
        "PROTECTED PATH CHANGED: tests/test_a.py"
        in frame_diff.check(adapter_for(framed), "f").output
    )


@pytest.mark.parametrize(
    "line",
    [
        "x = f()  # type: ignore",
        "y = 1  # noqa: E501",
        "if x:  # pragma: no cover",
        "pytest.skip('later')",
        "@pytest.mark.xfail",
    ],
)
def test_frame_suppressions(framed: pathlib.Path, line: str) -> None:
    write(framed, "src/pkg/a.py", f"def f() -> int:\n    return 1\n{line}\n")
    res = frame_diff.check(adapter_for(framed), "f")
    assert res.status == "FAIL" and f"SUPPRESSION ADDED: {line}" in res.output


def test_frame_suppression_in_new_untracked_file(framed: pathlib.Path) -> None:
    write(framed, "src/pkg/new.py", "import os  # noqa\n")
    write(framed, "src/pkg/data.txt", "noqa\n")  # not a source suffix
    res = frame_diff.check(adapter_for(framed), "f")
    assert res.output == "SUPPRESSION ADDED: import os  # noqa"


def test_frame_config(framed: pathlib.Path) -> None:
    write(framed, "config/x.yml", "a: 1\n")
    write(framed, "src/pkg/a.py", "TODO_HACK = 1\n")
    a = adapter_for(
        framed,
        {"protected": {"extra": ["config/"]}, "frame": {"forbidden_patterns": ["HACK"]}},
    )
    out = frame_diff.check(a, "f").output
    assert (
        "PROTECTED PATH CHANGED: config/x.yml" in out and "SUPPRESSION ADDED: TODO_HACK = 1" in out
    )


def test_changed_src_files(framed: pathlib.Path) -> None:
    write(framed, "src/pkg/a.py", "A = 1\n")
    write(framed, "src/pkg/new.py", "B = 1\n")
    write(framed, "tests/test_b.py", "x\n")
    assert frame_diff.changed_src_files(adapter_for(framed), "tests-approved/f") == [
        "src/pkg/a.py",
        "src/pkg/new.py",
    ]


# ---------- traceability ----------
TAGGED = """\
import pytest


@pytest.mark.req("FT-R-001", "FT-E-001")
def test_one() -> None:
    assert True


@pytest.mark.req("FT-I-001")
class TestProps:
    def test_p(self) -> None:
        assert True


@pytest.mark.req("ZZ-R-404")
async def test_unknown() -> None:
    assert True
"""


def test_traceability(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "specs/f/requirements.md", "FT-R-001 FT-E-001 FT-I-001 FT-R-002")
    write(tmp_path, "tests/test_f.py", TAGGED)
    a = adapter_for(tmp_path)
    tags = a.collect_req_tags()
    assert tags["FT-R-001"] == ["tests/test_f.py::test_one"]
    assert tags["FT-I-001"] == ["tests/test_f.py::TestProps"]
    res = traceability.check(a, "f")
    assert res.status == "FAIL"
    assert "4 spec IDs, 3 covered by tests." in res.output
    assert "MISSING test for FT-R-002" in res.output
    assert "UNKNOWN ID ZZ-R-404 used in ['tests/test_f.py::test_unknown']" in res.output
    write(tmp_path, "specs/f/requirements.md", "FT-R-001 FT-E-001 FT-I-001")
    write(tmp_path, "specs/zz/requirements.md", "ZZ-R-404")
    assert traceability.check(a, "f").status == "PASS"
    assert traceability.check(a, "nothing").status == "FAIL"


# ---------- smells ----------
SMELLY = """\
import pytest


def test_no_assert() -> None:
    x = 1


def test_raises() -> None:
    with pytest.raises(ValueError):
        int("x")


def test_np_style() -> None:
    np.testing.assert_allclose(1, 1)


@pytest.mark.skip(reason="later")
def test_skipped() -> None:
    assert True


@pytest.mark.xfail
def test_xfail() -> None:
    assert True


def test_inline_skip() -> None:
    pytest.skip("no")
    assert True


def helper() -> None:
    pass
"""


def test_smells(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "tests/test_s.py", SMELLY)
    res = smells_check.check(adapter_for(tmp_path))
    assert res.status == "FAIL"
    assert "NO ASSERTION: tests/test_s.py::test_no_assert" in res.output
    assert "SKIP/XFAIL: tests/test_s.py::test_skipped" in res.output
    assert "SKIP/XFAIL: tests/test_s.py::test_xfail" in res.output
    assert "pytest.skip() call in tests/test_s.py line 28" in res.output
    assert "line 17" not in res.output  # the @pytest.mark.skip decorator is reported once
    for fine in ("test_raises", "test_np_style", "helper"):
        assert f"::{fine}" not in res.output


PICKED = """\
RECORDS = [{"lap": True, "missed": True}]


def test_loop_over_picked() -> None:
    for r in [x for x in RECORDS if x["lap"]]:
        assert r["missed"]


def test_all_over_picked() -> None:
    assert all(x["missed"] for x in RECORDS if x["lap"])


def test_named_picked() -> None:
    laps = list(filter(lambda x: x["lap"], RECORDS))
    for r in laps:
        assert r["missed"]


def test_guarded() -> None:
    laps = [x for x in RECORDS if x["lap"]]
    assert laps
    for r in laps:
        assert r["missed"]


def test_guarded_len() -> None:
    laps = [x for x in RECORDS if x["lap"]]
    assert len(laps) >= 1 and all(x["missed"] for x in laps)


def test_plain_loop() -> None:
    for r in RECORDS:
        assert r["missed"]
"""


def test_smell_loop_over_picked_cases(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "tests/test_p.py", PICKED)
    out = smells_check.check(adapter_for(tmp_path)).output
    flagged = [n for n in ("loop_over_picked", "all_over_picked", "named_picked") if n in out]
    assert flagged == ["loop_over_picked", "all_over_picked", "named_picked"]
    for fine in ("test_guarded", "test_guarded_len", "test_plain_loop"):
        assert f"::{fine}" not in out


def test_smells_count_only_in_the_features_test_files(tmp_path: pathlib.Path) -> None:
    """Old tests are not the feature's business: smells in test files unchanged since
    spec-approved/<f> are ignored; NOTE lines (heuristics) never fail the check."""
    init_git(tmp_path)
    write(tmp_path, "tests/test_old.py", "def test_old() -> None:\n    x = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "old")
    git(tmp_path, "tag", "spec-approved/f")
    a = adapter_for(tmp_path)
    assert smells_check.check(a, "f").status == "PASS"
    write(tmp_path, "tests/test_new.py", PICKED)
    res = smells_check.check(a, "f")
    assert res.status == "PASS" and "NOTE: LOOP OVER PICKED CASES" in res.output
    assert "(1 in test files this feature did not change: ignored)" in res.output
    write(tmp_path, "tests/test_new.py", PICKED + "\n\ndef test_bare() -> None:\n    x = 1\n")
    res = smells_check.check(a, "f")
    assert res.status == "FAIL" and "NO ASSERTION: tests/test_new.py::test_bare" in res.output
    assert "test_old" not in res.output.replace("(1 in test files", "")
    assert smells_check.check(a).status == "FAIL"  # no feature: every test file


def test_no_smells(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "tests/test_ok.py", "def test_ok() -> None:\n    assert 1 == 1\n")
    assert smells_check.check(adapter_for(tmp_path)) == CheckResult("PASS", "No test smells found.")


# ---------- red against stubs ----------
RED = """\
import pytest


def f() -> int:
    raise NotImplementedError


@pytest.mark.req("FT-R-001")
def test_red() -> None:
    assert f() == 1


@pytest.mark.req("FT-R-001")
def test_weak() -> None:
    assert True


@pytest.mark.req("FT-E-001")
@pytest.mark.skip(reason="x")
def test_skip() -> None:
    assert f() == 2


def test_untagged_not_selected() -> None:
    assert True
"""


def test_red_stubs(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "specs/f/requirements.md", "FT-R-001 FT-E-001")
    write(tmp_path, "tests/test_f.py", RED)
    res = red_stubs.check(adapter_for(tmp_path), "f")
    assert res.status == "FAIL"
    assert "3 tests for feature f." in res.output
    assert "PASSES AGAINST STUB (weak or wrong test): tests.test_f::test_weak" in res.output
    assert "SKIPPED: tests.test_f::test_skip" in res.output
    assert "test_red" not in res.output


FIXTURE_CHECKS = """\
import pytest

DATA = [1, 2, 3]


def f() -> int:
    raise NotImplementedError


@pytest.mark.req("FT-R-001")
def test_fixture_data_has_a_three() -> None:
    assert 3 in DATA


@pytest.mark.req("FT-R-001")
def test_fixture_data_has_a_nine() -> None:
    assert 9 in DATA


@pytest.mark.req("FT-R-001")
def test_red() -> None:
    assert f() == 1
"""


def test_red_stubs_fixture_preconditions_must_pass(tmp_path: pathlib.Path) -> None:
    """A fixture precondition test proves pinned data has its situation with existing code: it
    must PASS against the stubs (a failing one = data picked wrong), unlike every other test."""
    write(tmp_path, "specs/f/requirements.md", "FT-R-001")
    write(tmp_path, "tests/test_f.py", FIXTURE_CHECKS)
    res = red_stubs.check(adapter_for(tmp_path), "f")
    assert res.status == "FAIL"
    assert "3 tests for feature f (2 fixture precondition checks, which must pass)." in res.output
    assert (
        "FIXTURE PRECONDITION FAILS" in res.output and "test_fixture_data_has_a_nine" in res.output
    )
    assert "test_fixture_data_has_a_three" not in res.output and "PASSES AGAINST" not in res.output
    write(tmp_path, "tests/test_f.py", FIXTURE_CHECKS.replace("9 in DATA", "1 in DATA"))
    assert red_stubs.check(adapter_for(tmp_path), "f").status == "PASS"
    assert red_stubs.is_fixture_check("Ns.LapTests.Fixture_LazyDuel(seed: 8)")
    assert not red_stubs.is_fixture_check("tests.test_f::test_fixtures_load")


def test_frame_allows_the_tagged_test_update(framed: pathlib.Path) -> None:
    """Answers at the final gate may need a test change: the test writer's tagged commit
    (tests-update/<f>) is allowed, any later test edit is a frame violation again."""
    a = adapter_for(framed)
    write(framed, "tests/test_a.py", "def test_a() -> None:\n    assert 1 == 1\n")
    assert frame_diff.check(a, "f").status == "FAIL"
    git(framed, "add", "-A")
    git(framed, "commit", "-q", "-m", "tests updated from answers ANS-1")
    git(framed, "tag", frame_diff.tests_update_tag("f"))
    assert frame_diff.check(a, "f").status == "PASS"
    write(framed, "tests/test_a.py", "def test_a() -> None:\n    assert 2 == 2\n")
    res = frame_diff.check(a, "f")
    assert res.status == "FAIL" and "PROTECTED PATH CHANGED: tests/test_a.py" in res.output


def test_red_stubs_all_red_and_no_report(tmp_path: pathlib.Path) -> None:
    write(tmp_path, "specs/f/requirements.md", "FT-R-001")
    write(tmp_path, "tests/test_f.py", RED.split('@pytest.mark.req("FT-R-001")\ndef test_weak')[0])
    assert red_stubs.check(adapter_for(tmp_path), "f").status == "PASS"
    write(tmp_path, "tests/test_f.py", "import not_a_module_xyz\n")
    res = red_stubs.check(adapter_for(tmp_path), "f")
    assert res.status == "FAIL"
    assert red_stubs.check(adapter_for(tmp_path), "nothing").output.startswith("No spec IDs")


# ---------- gate runner ----------
def test_gate_runner_step_scope(framed: pathlib.Path) -> None:
    write(framed, "src/pkg/a.py", "def f() -> int:\n    return 1\n")
    lines: list[str] = []
    a = adapter_for(framed, {"gates": {"commands": {"lint": [], "format": [], "typecheck": []}}})
    ok, report = run_gates(a, "f", "step", echo=lines.append)
    assert (
        ok and "[PASS] frame diff" in report and "[PASS] tests (current + earlier steps)" in report
    )
    assert report.endswith("ALL GATES PASSED") and lines
    write(framed, "tests/test_a.py", "def test_a() -> None:\n    assert False\n")
    ok, report = run_gates(a, "f", "step")
    assert not ok and "FAILED: frame diff, tests (current + earlier steps)" in report


def test_gate_runner_final_scope_and_skips(framed: pathlib.Path) -> None:
    a = adapter_for(
        framed,
        {
            "gates": {
                "mutation_tool": "none",
                "commands": {"lint": ["python", "-c", "print(1)"], "format": [], "typecheck": []},
            }
        },
    )
    ok, report = run_gates(a, "f", "final")
    assert "[PASS] lint" in report
    assert "[SKIPPED] mutation score" in report and "disabled" in report
    assert "[FAIL] traceability" in report  # FT-R-001 has no tagged test
    assert not ok
    _, report = run_gates(a, "f", "final", skip_mutation=True)
    assert "mutation" not in report


def test_gate_runner_missing_tool_and_crash(framed: pathlib.Path) -> None:
    a = adapter_for(
        framed,
        {"gates": {"commands": {"lint": ["no-such-tool-xyz"], "format": [], "typecheck": []}}},
    )
    ok, report = run_gates(a, "f", "step")
    assert not ok and "[FAIL] lint" in report and "Tool not found" in report


# ---------- diff coverage summary ----------
DIFF_COVER_OUT = """\
-------------
Diff Coverage
Diff: tests-approved/demo...HEAD, staged and unstaged changes
-------------
src/demo/calc.py (60.0%): Missing lines 12,14
-------------
Total:   5 lines
Missing: 2 lines
Coverage: 60%
-------------
"""


def test_diff_coverage_summary() -> None:
    from agentflow.adapters.base import diff_coverage_summary

    assert diff_coverage_summary(DIFF_COVER_OUT, 90) == (
        "Diff coverage 60% (3/5 changed lines covered), threshold 90%."
    )
    assert diff_coverage_summary("No lines with coverage information in this diff.", 85.5) == (
        "Diff coverage: no coverable changed lines, threshold 85.5%."
    )
    assert diff_coverage_summary("Traceback: boom", 90) == ""


def test_gate_report_shows_summary_under_fail_and_pass(
    framed: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentflow import gates
    from agentflow.adapters.base import Check

    a = adapter_for(framed)
    fake = [
        Check("diff coverage", lambda: CheckResult("FAIL", "detail", summary="Diff coverage 60%")),
        Check("mutation", lambda: CheckResult("PASS", "x", summary="Mutation score 100%")),
    ]
    monkeypatch.setattr(gates, "checks", lambda *args: fake)
    rep = gates.gate_report(a, "f", "final")
    lines = rep.report.splitlines()
    lines = lines[lines.index("[FAIL] diff coverage") :]  # after the scope header
    assert lines[:5] == [
        "[FAIL] diff coverage",
        "  Diff coverage 60%",
        "detail",
        "[PASS] mutation",
        "  Mutation score 100%",
    ]
