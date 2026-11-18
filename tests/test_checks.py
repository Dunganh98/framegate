"""Frame diff, traceability, test smells, red-against-stubs, gate runner (python adapter)."""

from __future__ import annotations

import pathlib

import pytest

from conftest import DemoRepo, git, init_git
from framegate.adapters import get_adapter
from framegate.adapters.base import CheckResult
from framegate.adapters.python import PythonAdapter
from framegate.checks import frame_diff, named_tests, red_stubs, test_lint, traceability
from framegate.checks import test_smells as smells_check
from framegate.config import load_config, parse_config
from framegate.gates import run_gates
from framegate.state import set_marker


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
        "framegate.toml",
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


MOVED_DIFF = """\
diff --git a/old.py b/old.py
--- a/old.py
+++ b/old.py
@@ -1,4 +0,0 @@
-a = 1
-b = 2
-
-c = 3
diff --git a/new.py b/new.py
--- /dev/null
+++ b/new.py
@@ -0,0 +1,11 @@
+    a = 1
+b = 2
+
+c = 3
+x = 9
+a = 1
+x = 9
+a = 1
+b = 2
+b = 2 + 1
+c = 3
"""


def test_moved_lines_are_blocks() -> None:
    from framegate.moved import find_moved, parse_diff

    # a block of 3 counts, a single repeated line does not, a changed line breaks the block
    assert find_moved(*parse_diff(MOVED_DIFF)) == {"new.py": {1, 2, 3, 4}}


A_PY = "def f() -> int:\n    raise NotImplementedError\n"
G_PY = (
    "def g() -> int:\n    if True:\n        return 1\n"
    "    raise NotImplementedError  # pragma: no cover\n"
)


@pytest.fixture
def moved(framed: pathlib.Path) -> pathlib.Path:
    write(framed, "src/pkg/a.py", A_PY + "\n\n" + G_PY)
    write(framed, "tests/test_b.py", "import sys\n\nsys.path.insert(0, 'src')\nimport pkg.b\n")
    git(framed, "add", "-A")
    git(framed, "commit", "-q", "-m", "g")
    git(framed, "tag", "-f", "tests-approved/f")
    write(framed, "src/pkg/a.py", A_PY)
    write(framed, "src/pkg/b.py", "Y = 2  # noqa\n\n\n" + G_PY)
    git(framed, "add", "-A")
    return framed


def test_frame_moved_suppression_is_not_added(moved: pathlib.Path) -> None:
    res = frame_diff.check(adapter_for(moved), "f")
    assert res.output == "SUPPRESSION ADDED: Y = 2  # noqa"
    assert res.summary == "moved, not counted: 6 lines"


def test_diff_coverage_leaves_moved_lines_out(moved: pathlib.Path) -> None:
    diff = adapter_for(moved).coverage_checks("tests-approved/f")
    diff[0].run()
    res = diff[1].run()
    assert res.status == "PASS" and res.summary == (
        "Diff coverage 100% (1/1 changed lines covered), threshold 90%; "
        "moved, not counted: 6 lines."
    )


def test_report_without_lines_lcov(tmp_path: pathlib.Path) -> None:
    from framegate.adapters.base import report_without_lines

    src = tmp_path / "lcov.info"
    src.write_text("SF:src/b.py\nDA:1,0\nDA:2,0\nBRDA:2,0,0,0\nend_of_record\n", encoding="utf-8")
    report_without_lines(src, tmp_path / "out.info", tmp_path, {"src/b.py": {2}})
    assert (tmp_path / "out.info").read_text(encoding="utf-8") == (
        "SF:src/b.py\nDA:1,0\nend_of_record\n"
    )


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


def test_frame_ignores_machine_files(framed: pathlib.Path) -> None:
    """Seen live: a tool rewrites its own tracked config (`.serena/project.yml`) during a
    run. Tool state of this machine is no change of the task: not a frame violation, also
    where a protected glob or a built-in deploy name would match it, tracked or not,
    committed or not; not in the changed-file lists either."""
    write(framed, ".serena/project.yml", "name: x\n")
    write(framed, "conf/app.yml", "a: 1\n")
    write(framed, "local.settings.json", "{}\n")
    git(framed, "add", "-A")
    git(framed, "commit", "-q", "-m", "tool state")
    git(framed, "tag", "-f", "tests-approved/f")
    data = {
        "protected": {"extra": ["*.yml", "*.json"]},
        "git": {"machine_files": ["local.settings.json"]},
    }
    a = adapter_for(framed, data)
    write(framed, ".serena/project.yml", "name: x\nlanguage: python\n")  # tracked, modified
    write(framed, ".vscode/settings.json", "{}\n")  # untracked
    write(framed, ".venv/Lib/pkg/Dockerfile", "FROM python\n")
    write(framed, ".idea/src.py", "x = 1  # noqa\n")
    write(framed, "local.settings.json", '{"a": 1}\n')
    assert frame_diff.check(a, "f") == CheckResult("PASS", "Frame intact.")
    assert frame_diff.setup_state(a) == ([], [])
    assert frame_diff.changed_files(a.cfg, "tests-approved/f") == (set(), set())
    assert not frame_diff.maybe_pipeline_file(a.cfg, ".venv/Lib/pkg/Dockerfile")
    git(framed, "add", "-A")
    git(framed, "commit", "-q", "-m", "someone committed the tool state")
    assert frame_diff.check(a, "f").status == "PASS"
    # the same change in a project file is still caught; so is a dot folder taken back
    write(framed, "conf/app.yml", "a: 2\n")
    out = frame_diff.check(a, "f").output
    assert out == "PROTECTED PATH CHANGED: conf/app.yml"
    back = adapter_for(framed, {**data, "git": {"project_folders": [".serena/"]}})
    assert "PROTECTED PATH CHANGED: .serena/project.yml" in frame_diff.check(back, "f").output


def test_dirty_lines_leave_machine_files_out(framed: pathlib.Path) -> None:
    """The one "is the working tree clean?" helper: git's status without the tool state."""
    from framegate.state import dirty_lines, dirty_machine_files

    cfg = adapter_for(framed).cfg
    write(framed, ".serena/project.yml", "name: x\n")
    write(framed, "docs/my notes.md", "a\n")
    git(framed, "add", "-A")
    git(framed, "commit", "-q", "-m", "more")
    assert dirty_lines(cfg) == [] and dirty_lines(cfg, "all") == []
    write(framed, ".serena/project.yml", "name: y\n")
    write(framed, ".vscode/settings.json", "{}\n")
    assert dirty_lines(cfg) == [] and dirty_lines(cfg, "all") == []
    assert dirty_machine_files(cfg) == [".serena/project.yml"]
    write(framed, "docs/my notes.md", "b\n")
    git(framed, "mv", "src/pkg/a.py", "src/pkg/moved.py")
    write(framed, "new.txt", "x\n")
    assert dirty_lines(cfg) == [" M docs/my notes.md", "R  src/pkg/moved.py"]
    assert dirty_lines(cfg, "all")[-1] == "?? new.txt" and len(dirty_lines(cfg, "all")) == 3


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
    assert "(1 in tests this feature did not add: ignored)" in res.output
    write(tmp_path, "tests/test_new.py", PICKED + "\n\ndef test_bare() -> None:\n    x = 1\n")
    res = smells_check.check(a, "f")
    assert res.status == "FAIL" and "NO ASSERTION: tests/test_new.py::test_bare" in res.output
    assert "test_old" not in res.output.replace("(1 in test files", "")
    assert smells_check.check(a).status == "FAIL"  # no feature: every test file


def test_old_skip_calls_in_a_touched_file_are_not_counted(tmp_path: pathlib.Path) -> None:
    init_git(tmp_path)
    old = "import pytest\n\n\ndef test_old() -> None:\n    pytest.skip('slow')\n    assert 1\n"
    write(tmp_path, "tests/test_s.py", old)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "old")
    git(tmp_path, "tag", "spec-approved/f")
    a = adapter_for(tmp_path)
    write(tmp_path, "tests/test_s.py", old + "\n\ndef test_new() -> None:\n    assert 1\n")
    res = smells_check.check(a, "f")
    assert res.status == "PASS" and "(1 in tests this feature did not add: ignored)" in res.output
    new = "\n\ndef test_new() -> None:\n    pytest.skip('x')\n    assert 1\n"
    write(tmp_path, "tests/test_s.py", old + new)
    res = smells_check.check(a, "f")
    assert res.status == "FAIL" and "pytest.skip() call in tests/test_s.py line 10" in res.output
    assert "line 5" not in res.output


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


STEP_TESTS = """\
import pytest

from pkg.a import f, g


@pytest.mark.req("FT-R-001")
def test_one() -> None:
    assert f() == 1


@pytest.mark.req("FT-R-002")
def test_two() -> None:
    assert g() == 2
"""
STEP_SRC = """\
def f() -> int:
    return {f}


def g() -> int:
    raise NotImplementedError


def old() -> int:
    return {old}
"""


def test_step_gate_runs_the_whole_suite_only_when_the_config_says(framed: pathlib.Path) -> None:
    """A step gate judges the task's tests; the whole suite runs once, at the final gate.
    [gates] step_tests = "suite" runs it at every step gate too; the tests of this feature's
    later steps are red by design and left out."""
    from framegate.gates import STEP_SUITE, is_test_at, later_step_tests
    from framegate.state import clear_reqs, set_reqs

    write(framed, "specs/f/requirements.md", "FT-R-001\nFT-R-002\n")
    write(
        framed,
        "pyproject.toml",
        "[project]\nname = 'x'\n[tool.pytest.ini_options]\npythonpath = ['src']\n",
    )
    write(framed, ".gitignore", "out/\n.phase\n.reqs\n__pycache__/\n")
    write(framed, "src/pkg/a.py", STEP_SRC.format(f="None", old=7))
    write(framed, "tests/test_feat.py", STEP_TESTS)
    other = "from pkg.a import old\n\n\ndef test_two() -> None:\n    assert old() == 7\n"
    write(framed, "tests/test_other.py", other)
    git(framed, "add", "-A")
    git(framed, "commit", "-q", "-m", "tests")
    git(framed, "tag", "-f", "tests-approved/f")
    off = {"commands": {"lint": [], "format": [], "typecheck": []}}
    a = adapter_for(framed, {"gates": {**off, "step_tests": "suite"}})
    assert later_step_tests(a, "f", ["FT-R-001"]) == ["tests/test_feat.py::test_two"]
    # the same method name in another file or class is another test
    assert is_test_at("tests.test_feat::test_two", ["tests/test_feat.py::test_two"])
    assert not is_test_at("tests.test_other::test_two", ["tests/test_feat.py::test_two"])
    assert is_test_at("Ns.FeatTests.Two(x: 1)", ["tests/Match/Feat.cs::FeatTests.Two"])
    assert not is_test_at("Ns.OtherTests.Two", ["tests/Match/Feat.cs::FeatTests.Two"])

    set_reqs(a.cfg, ["FT-R-001"])
    try:
        # step 1 done, step 2 (FT-R-002) still a stub: its test is left out
        write(framed, "src/pkg/a.py", STEP_SRC.format(f=1, old=7))
        ok, report = run_gates(a, "f", "step")
        assert ok and f"[PASS] {STEP_SUITE}" in report and "left out" in report
        # the step breaks what another test pins: the step gate fails and names that test
        write(framed, "src/pkg/a.py", STEP_SRC.format(f=1, old=8))
        ok, report = run_gates(a, "f", "step")
        assert not ok and f"[FAIL] {STEP_SUITE}" in report
        broken = report.split("Tests outside this step fail")[1]
        assert "test_other::test_two" in broken and "test_feat::test_two" not in broken
        # the default: only the step's IDs; what it broke elsewhere is the final gate's to find
        b = adapter_for(framed, {"gates": off})
        ok, report = run_gates(b, "f", "step")
        assert ok and STEP_SUITE not in report, report
        # the step's own tests are red: the suite is not run on top
        write(framed, "src/pkg/a.py", STEP_SRC.format(f=3, old=7))
        ok, report = run_gates(a, "f", "step")
        assert not ok and f"[SKIPPED] {STEP_SUITE}" in report
    finally:
        clear_reqs(a.cfg)


def test_fix_gate_runs_the_features_tests_not_the_suite(framed: pathlib.Path) -> None:
    """The gate of a run after the review (scope "fix"): the feature's tests and the review
    tests. Another feature's broken test does not fail it (the final regression finds that,
    right after); a failing review test, tagged or not, does."""
    from framegate.gates import FIX_TESTS, REVIEW_TESTS, STEP_SUITE, gate_report
    from framegate.state import clear_reqs, set_reqs

    write(framed, "specs/f/requirements.md", "FT-R-001\nFT-R-002\n")
    write(
        framed,
        "pyproject.toml",
        "[project]\nname = 'x'\n[tool.pytest.ini_options]\npythonpath = ['src']\n",
    )
    write(framed, ".gitignore", "out/\n.phase\n.reqs\n__pycache__/\n")
    write(
        framed,
        "src/pkg/a.py",
        STEP_SRC.format(f=1, old=8).replace("raise NotImplementedError", "return 2"),
    )
    write(framed, "tests/test_feat.py", STEP_TESTS)
    other = "from pkg.a import old\n\n\ndef test_two() -> None:\n    assert old() == 7\n"
    write(framed, "tests/test_other.py", other)  # red: old() is 8
    git(framed, "add", "-A")
    git(framed, "commit", "-q", "-m", "tests")
    git(framed, "tag", "-f", "tests-approved/f")
    off = {"commands": {"lint": [], "format": [], "typecheck": []}}
    a = adapter_for(framed, {"gates": off})
    set_reqs(a.cfg, ["FT-R-001", "FT-R-002"])
    try:
        rep = gate_report(a, "f", "fix")
        assert rep.ok and f"[PASS] {FIX_TESTS}" in rep.report, rep.report
        assert STEP_SUITE not in rep.report and REVIEW_TESTS not in rep.report  # no folder
        suite = adapter_for(framed, {"gates": {**off, "step_tests": "suite"}})
        assert not gate_report(suite, "f", "step").ok  # a plan step runs it when asked to
        # an untagged review test is run too
        review = "from pkg.a import f\n\n\ndef test_r() -> None:\n    assert f() in {n}\n"
        write(framed, "tests/review/test_r.py", review.format(n="(1, 3)"))
        rep = gate_report(a, "f", "fix")
        assert rep.ok and f"[PASS] {REVIEW_TESTS}" in rep.report
        write(framed, "tests/review/test_r.py", review.format(n="(5,)"))
        rep = gate_report(a, "f", "fix")
        assert rep.failed == [REVIEW_TESTS] and "test_r" in rep.outputs[REVIEW_TESTS]
        # the feature's own test red: that check fails
        write(framed, "tests/review/test_r.py", review.format(n="(1, 3)"))
        write(framed, "src/pkg/a.py", STEP_SRC.format(f=3, old=7))
        assert gate_report(a, "f", "fix").failed == [FIX_TESTS]
    finally:
        clear_reqs(a.cfg)
    # without IDs the scope runs everything, as a step gate does
    rep = gate_report(a, "f", "fix")
    assert "tests (current + earlier steps)" in rep.report and REVIEW_TESTS not in rep.report


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
    from framegate.adapters.base import diff_coverage_summary

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
    from framegate import gates
    from framegate.adapters.base import Check

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


# ---------- a refactor: existing tests first ----------
OLD_TESTS = """\
import pytest


def test_old_bare() -> None:
    x = 1


def test_old_pins() -> None:
    assert 1 + 1 == 2
"""


def test_refactor_counts_tagged_existing_tests_and_ignores_their_old_smells(
    tmp_path: pathlib.Path,
) -> None:
    """A refactor tags the existing tests that already pin a row instead of writing them again
    (live, FCSim: 835 lines of new tests beside 789 lines of existing ones). The check says
    which tests existed; a smell in an old test of a touched file is not the refactor's."""
    from framegate.checks import baseline

    init_git(tmp_path)
    write(tmp_path, "tests/test_old.py", OLD_TESTS)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "old")
    git(tmp_path, "tag", "spec-approved/f")
    a = adapter_for(tmp_path)
    ids = ["FT-R-001", "FT-R-002"]
    none = baseline.existing_tests(a, "f", ids)
    assert none[0].startswith("0 existing test(s) tagged") and "NOTE: no existing test" in none[1]

    tagged = OLD_TESTS.replace(
        "def test_old_pins", '@pytest.mark.req("FT-R-001")\ndef test_old_pins'
    )
    write(tmp_path, "tests/test_old.py", tagged)
    write(
        tmp_path,
        "tests/test_new.py",
        'import pytest\n\n\n@pytest.mark.req("FT-R-002")\n'
        "def test_new() -> None:\n    assert 2 == 2\n",
    )
    lines = baseline.existing_tests(a, "f", ids)
    assert lines == [
        "1 existing test(s) tagged with a spec ID of this refactor (they already pin the "
        "behavior), 1 new test(s).",
        "Existing test files with tagged tests: tests/test_old.py",
        "Pinned by existing tests only: FT-R-001",
    ]
    # the old bare test sits in a file the refactor touched: still not its business
    res = smells_check.check(a, "f")
    assert res.status == "PASS" and "(1 in tests this feature did not add: ignored)" in res.output
    # a new test added beside the old ones counts
    write(tmp_path, "tests/test_old.py", tagged + "\n\ndef test_added_bare() -> None:\n    y = 2\n")
    res = smells_check.check(a, "f")
    assert res.status == "FAIL" and "test_added_bare" in res.output
    assert "test_old_bare" not in res.output
    # an existing test that was changed, not only tagged: a note for the test reviewer
    write(tmp_path, "tests/test_old.py", tagged.replace("assert 1 + 1 == 2", "assert True"))
    note = baseline.existing_tests(a, "f", ids)[-1]
    assert note.startswith("NOTE: tests/test_old.py: 1 line(s) of this existing test file")


TEST_LINT = """import pathlib
import sys

bad = [
    f"{p.as_posix()}:{n}: X001 the word is not allowed"
    for p in sorted(pathlib.Path("tests").rglob("*.py"))
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
    if "BAD" in line
]
print("\\n".join(bad))
sys.exit(1 if bad else 0)
"""


def test_lint_on_the_test_writers_own_lines(demo_repo: DemoRepo) -> None:
    r = demo_repo
    (r.root / "tools").mkdir()
    (r.root / "tools" / "lint.py").write_text(TEST_LINT, encoding="utf-8")
    (r.root / "tests" / "test_old.py").write_text("A = 1  # BAD\n", encoding="utf-8")
    r.add_toml(
        '[gates.commands]\nlint = ["python", "tools/lint.py"]\nformat = []\ntypecheck = []\n'
    )
    cfg = load_config(r.root)
    set_marker(cfg, "spec-approved/demo")
    adapter = get_adapter(cfg)
    assert test_lint.check(adapter, "demo").status == "PASS"  # old debt is not the writer's
    (r.root / "tests" / "test_new.py").write_text("B = 2  # BAD\n", encoding="utf-8")
    res = test_lint.check(adapter, "demo")
    assert res.status == "FAIL" and "tests/test_new.py:1" in res.output, res.output
    assert "test_old" not in res.output


NAMED_CLASSES = """class TestA:
    def test_x(self) -> None:
        assert 1 == 1

    def test_y(self) -> None:
        assert 2 == 2


class TestB:
    def test_z(self) -> None:
        assert 3 == 3
"""
NAMED = """
## Test data and fixtures
<!-- name it as `tests/<file>::<Class>`; this `tests/test_old.py::TestB` is a comment -->
- `tests/test_old.py::TestA`: its fixtures get the new field
- `tests/test_old.py::TestA::test_y` expects 4
- `tests/test_old.py::TestB` (unchanged)
- `tests/test_new.py::TestN` is new

## Notes
`tests/test_old.py::TestB` outside the section is not read.
"""


def test_a_test_the_spec_names_must_change(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    adapter = get_adapter(cfg)
    assert named_tests.check(adapter, "demo").status == "PASS"  # the spec names none
    write(r.root, "tests/test_old.py", NAMED_CLASSES)
    req = r.root / "specs/demo/requirements.md"
    req.write_text(req.read_text(encoding="utf-8") + NAMED, encoding="utf-8")
    assert named_tests.check(adapter, "demo").status == "PASS"  # no marker: not checked
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "spec")
    set_marker(cfg, "spec-approved/demo")

    # everything went into a new file
    write(
        r.root,
        "tests/test_new.py",
        "class TestN:\n    def test_n(self) -> None:\n        assert 1\n",
    )
    res = named_tests.check(adapter, "demo")
    assert res.status == "FAIL", res.output
    assert "NOT CHANGED" in res.output and "tests/test_old.py::TestA\n" in res.output
    assert "tests/test_old.py::TestA::test_y" in res.output.split("NOT CHANGED", 2)[2]
    assert "not checked (the spec says unchanged): tests/test_old.py::TestB" in res.output
    assert "not checked (new: not there when the spec was approved): tests/test_new.py::TestN" in (
        res.output
    )
    # a change in the class, but not in the test the spec names
    write(r.root, "tests/test_old.py", NAMED_CLASSES.replace("1 == 1", "1 == 1.0"))
    res = named_tests.check(adapter, "demo")
    assert "changed: tests/test_old.py::TestA\n" in res.output
    assert res.status == "FAIL" and "NOT CHANGED" in res.output and "TestA::test_y" in res.output
    write(r.root, "tests/test_old.py", NAMED_CLASSES.replace("2 == 2", "4 == 4"))
    assert named_tests.check(adapter, "demo").status == "PASS"
    write(r.root, "tests/test_old.py", NAMED_CLASSES.replace("TestA", "TestRenamed"))
    res = named_tests.check(adapter, "demo")
    assert res.status == "FAIL" and "GONE (removed or renamed" in res.output


def test_named_tests_without_line_ranges_fall_back_to_the_file(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    adapter = get_adapter(cfg)
    monkeypatch.setattr(type(adapter), "test_lines", lambda self, source, names: None)
    write(r.root, "tests/test_old.py", NAMED_CLASSES)
    req = r.root / "specs/demo/requirements.md"
    req.write_text(req.read_text(encoding="utf-8") + NAMED, encoding="utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "spec")
    set_marker(cfg, "spec-approved/demo")
    assert named_tests.check(adapter, "demo").status == "FAIL"
    write(r.root, "tests/test_old.py", NAMED_CLASSES.replace("3 == 3", "3 == 3.0"))
    assert named_tests.check(adapter, "demo").status == "PASS"
