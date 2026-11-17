"""`doctor --tree`, [gates] unrunnable_tests and `commit-setup`: a red tree is found once."""

from __future__ import annotations

import pathlib

import pytest

from conftest import DemoRepo, git
from framegate import doctor, gates, scaffold, tree_check, unrunnable
from framegate.adapters import get_adapter
from framegate.adapters.base import CheckResult, TestCase, TestRun
from framegate.config import ConfigError, load_config, parse_config
from framegate.report import final_facts
from framegate.state import commit_command, set_marker

#: a "linter" for the demo repository: one line per line that holds BAD
LINT = """\
import pathlib
import sys

found = 0
for folder in ("src", "legacy"):
    for path in sorted(pathlib.Path(folder).rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "BAD" in line:
                print(f"{path.as_posix()}:{n}: X001 the word is not allowed")
                found += 1
sys.exit(1 if found else 0)
"""
COMMANDS = '[gates.commands]\nlint = ["python", "tools/lint.py"]\nformat = []\ntypecheck = []\n'
DB_TESTS = (
    "def test_connect() -> None:\n"
    '    raise ConnectionError("no database on this machine")\n\n\n'
    "def test_query() -> None:\n    assert 0\n"
)
LISTED = '[{ test = "tests/test_db.py", reason = "needs a PostgreSQL server" }]'


def _run(*cases: tuple[str, str]) -> TestRun:
    failed = any(o in ("failed", "error") for _, o in cases)
    return TestRun(
        "failed" if failed else "passed",
        1 if failed else 0,
        "out",
        [TestCase(n, o, "boom\nmore") for n, o in cases],
    )


def doctor_tree(r: DemoRepo, brief: bool = True) -> tuple[int, str]:
    lines: list[str] = []
    code = doctor.run(load_config(r.root), echo=lines.append, only_brief=brief, tree=True)
    return code, "\n".join(lines)


# ---------- [gates] unrunnable_tests: the config ----------
def test_a_listed_test_needs_its_file_and_a_reason(tmp_path: pathlib.Path) -> None:
    entry = {"test": "tests/test_db.py", "reason": "needs a database"}
    cfg = parse_config(tmp_path, {"gates": {"unrunnable_tests": [entry]}})
    assert cfg.unrunnable_tests == {"tests/test_db.py": "needs a database"}
    assert parse_config(tmp_path, {}).unrunnable_tests == {}
    for bad in (
        ["tests/test_db.py"],
        [{"test": "tests/test_db.py"}],
        [{"test": "tests/test_db.py", "reason": "x", "more": 1}],
        [{"test": "tests/test_db.py", "reason": 3}],
        [{"test": "tests/test_db.py", "reason": " "}],
    ):
        with pytest.raises(ConfigError, match="unrunnable_tests"):
            parse_config(tmp_path, {"gates": {"unrunnable_tests": bad}})
    for test in ("test_connect", "tests.test_db::test_connect", "tests/db"):
        entry = {"test": test, "reason": "needs a database"}
        with pytest.raises(ConfigError, match="must name the test file"):
            parse_config(tmp_path, {"gates": {"unrunnable_tests": [entry]}})


def test_a_listed_test_is_a_file_or_a_test_in_it(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    name = "tests.db.test_pg.TestPool::test_connect[1]"
    for entry in ("tests/db/test_pg.py::TestPool::test_connect", "tests/db/test_pg.py"):
        assert unrunnable.matches(cfg, name, entry), entry
    for entry in ("tests/db/test_my.py", "tests/test_pg.py", "tests/db/test_pg.py::test_other"):
        assert not unrunnable.matches(cfg, name, entry), entry
    # a .NET report names namespace.class.method: the end of the file's path
    cs = "tests/Shop.Tests/DbTests.cs"
    assert unrunnable.matches(cfg, "Shop.Tests.DbTests.Connects(x: 1)", cs)
    assert not unrunnable.matches(cfg, "Shop.Tests.ApiTests.Connects", cs)


# ---------- the gates leave a listed test's failure out ----------
def test_a_failure_of_a_listed_test_is_left_out_and_counted(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    cfg.unrunnable_tests = {"tests/test_db.py": "needs a database"}
    run = _run(("tests.test_db::test_connect", "failed"), ("tests.test_x::test_fine", "passed"))
    kept, off = unrunnable.leave_out(cfg, run)
    assert off == ["tests.test_db::test_connect"] and kept.status == "passed"
    assert [c.outcome for c in kept.cases or []] == ["skipped", "passed"]
    # another failure still fails the run, and the listed one is not named as unexpected
    run = _run(("tests.test_db::test_connect", "error"), ("tests.test_x::test_new", "failed"))
    kept, off = unrunnable.leave_out(cfg, run)
    res = unrunnable.noted(gates.tests_expecting(kept, gates.ExpectedRed(), set()), off)
    assert res.status == "FAIL" and kept.status == "failed"
    assert "tests.test_x::test_new" in res.output and "\n  tests.test_db" not in res.output
    assert res.summary.startswith("listed in unrunnable_tests, left out: 1")
    # nothing listed, or no per-test results: the run is taken as it is
    plain = _run(("tests.test_db::test_connect", "failed"))
    assert unrunnable.leave_out(parse_config(tmp_path, {}), plain) == (plain, [])
    blind = TestRun("failed", 1, "out", None)
    assert unrunnable.leave_out(cfg, blind) == (blind, [])


def test_the_gates_of_every_mode_leave_the_listed_tests_out(demo_repo: DemoRepo) -> None:
    r = demo_repo
    (r.root / "tests" / "test_db.py").write_text(DB_TESTS, encoding="utf-8")
    cfg = load_config(r.root)
    adapter = get_adapter(cfg)
    set_marker(cfg, "tests-approved/demo")
    name = "regression: whole suite, no coverage"

    def final_regression() -> CheckResult:
        checks = gates.checks(get_adapter(load_config(r.root)), "demo", "final", True)
        return next(c for c in checks if c.name == name).run()

    assert final_regression().status == "FAIL"  # not listed: red is red
    assert gates.step_suite(adapter, "demo", ["DEMO-R-001"]).status == "FAIL"

    r.set_toml("gates", "unrunnable_tests", LISTED)
    adapter = get_adapter(load_config(r.root))
    res = final_regression()
    assert res.status == "PASS", res.output
    assert "listed in unrunnable_tests, left out: 2" in res.summary
    res = gates.step_suite(adapter, "demo", ["DEMO-R-001"])
    assert res.status == "PASS" and "listed in unrunnable_tests, left out: 2" in res.summary
    assert gates.record_regression_baseline(adapter, "demo") == (0, "")

    # a test that is not on the list still fails the gates
    (r.root / "tests" / "test_other.py").write_text(
        "def test_really_red() -> None:\n    assert 0\n", encoding="utf-8"
    )
    res = final_regression()
    assert res.status == "FAIL" and "test_really_red" in res.output
    assert "listed in unrunnable_tests, left out: 2" in res.summary
    assert gates.step_suite(adapter, "demo", ["DEMO-R-001"]).status == "FAIL"


def test_the_final_facts_name_the_listed_tests_every_time(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    assert unrunnable.facts_lines(cfg) == []
    cfg.unrunnable_tests = {"tests/test_db.py": "needs a PostgreSQL server"}
    lines = unrunnable.facts_lines(cfg)
    facts = final_facts("demo", [], {}, [], [], "", "", unrunnable=lines)
    assert "## Not checked: 1 listed test entry(ies)" in facts
    assert "- tests/test_db.py: needs a PostgreSQL server" in facts
    assert "not checked: <test> (<reason>)" in facts
    assert "Not checked" not in final_facts("demo", [], {}, [], [], "", "")


# ---------- framegate doctor --tree ----------
def test_setup_says_what_is_red_on_the_untouched_tree(demo_repo: DemoRepo) -> None:
    r = demo_repo
    (r.root / "tools").mkdir()
    (r.root / "tools" / "lint.py").write_text(LINT, encoding="utf-8")
    (r.root / "legacy").mkdir()
    (r.root / "legacy" / "old.py").write_text("A = 1  # BAD\nB = 2  # BAD\n", encoding="utf-8")
    (r.root / "legacy" / "older.py").write_text("C = 3  # BAD\n", encoding="utf-8")
    (r.root / "tests" / "test_db.py").write_text(DB_TESTS, encoding="utf-8")
    (r.root / "tests" / "test_fine.py").write_text(
        "def test_fine() -> None:\n    assert 1\n", encoding="utf-8"
    )
    r.add_toml(COMMANDS)

    code, out = doctor_tree(r)
    assert code == 1, out  # not ready while a red test is neither made to run nor listed
    assert out.startswith("Verdict: NOT READY (1 failure(s) to fix)")
    assert "- FAIL tree: whole test suite on the code as it is: 2 test(s) fail" in out
    tree = out[out.index(tree_check.HEADING) :].splitlines()
    assert tree[1] == "TESTS: 3 run, 2 fail."
    assert tree[2] == "PROBLEM: 2 test(s) fail on the untouched tree and are not listed:"
    assert tree[3].startswith("  tests.test_db::test_connect | ")
    assert "no database on this machine" in tree[3]
    assert tree[4].startswith("  tests.test_db::test_query")
    assert tree[5] == tree_check.RED_TESTS_ADVICE and '"red before any task: <what>"' in tree[5]
    assert tree[6] == "DEBT: lint: 3 line(s) in 2 file(s) (most in legacy/old.py, legacy/older.py)"
    assert tree[7] == tree_check.DEBT_ADVICE and "/framegate:refactor" in tree[7]
    assert tree[-1] == "Tree: not green (see the lines above)."

    # the user confirmed: the two tests need a database this machine does not have
    r.set_toml("gates", "unrunnable_tests", LISTED)
    code, out = doctor_tree(r, brief=False)
    assert code == 0
    assert "[OK] tree: whole test suite on the code as it is" in out
    assert "3 test(s); 2 listed failure(s) left out" in out
    tree = out[out.index(tree_check.HEADING) :].splitlines()
    assert tree[1] == (
        "TESTS: 3 run, 2 fail (2 of them listed in [gates] unrunnable_tests, left out)."
    )
    assert tree[2] == "LISTED: tests/test_db.py: needs a PostgreSQL server"
    assert not any(x.startswith("PROBLEM:") for x in tree)
    assert tree[-1] == "Tree: not green (see the lines above)."  # the lint debt is still there

    # the debt cleaned up: the tree is green, and the listed tests are still named
    (r.root / "tools" / "lint.py").write_text("", encoding="utf-8")
    _, out = doctor_tree(r)
    tree = out[out.index(tree_check.HEADING) :].splitlines()
    assert tree[2].startswith("LISTED: tests/test_db.py") and tree[-1] == "Tree: green."
    assert not any(x.startswith("DEBT:") for x in tree)


def test_the_tree_lines_for_a_suite_without_a_result_and_a_stale_entry(
    tmp_path: pathlib.Path,
) -> None:
    cfg = parse_config(tmp_path, {})
    cfg.unrunnable_tests = {
        "tests/test_gone.py": "needs a license server",
        "tests/test_x.py::test_todo": "red before any task: a placeholder",
    }
    adapter = get_adapter(cfg)
    run = _run(("tests.test_x::test_fine", "passed"), ("tests.test_x::test_todo", "failed"))
    seen = tree_check.suite(adapter, run)
    assert seen.status == "OK" and seen.lines == [
        "TESTS: 2 run, 1 fail (1 of them listed in [gates] unrunnable_tests, left out).",
        "LISTED: tests/test_gone.py: needs a license server (matches no test of the suite)",
        "LISTED: tests/test_x.py::test_todo: red before any task: a placeholder",
        # the project's own debt: the setup's summary suggests a task for it
        tree_check.LISTED_DEBT_ADVICE.format(n=1),
    ]
    cut = TestRun("error", 124, "a\nb", None, incomplete="timeout after 3600 s")
    seen = tree_check.suite(adapter, cut)
    assert seen.status == "FAIL" and seen.lines[0].startswith(
        "PROBLEM: the whole suite did not run to the end (timeout after 3600 s)"
    )
    seen = tree_check.suite(adapter, TestRun("failed", 1, "boom", None))
    assert seen.status == "FAIL" and "gave no per-test results" in seen.lines[0]
    seen = tree_check.suite(adapter, TestRun("no_tests", 5, "", []))
    assert (seen.status, seen.lines[0]) == ("WARN", "TESTS: none yet.")
    # more failing tests than fit: the rest is counted
    many = _run(*[(f"tests.test_x::test_{n}", "failed") for n in range(40)])
    lines = tree_check.suite(adapter, many).lines
    assert "  ... and 10 more" in lines and "  tests.test_x::test_0 | boom" in lines


def test_a_mutation_tool_that_cannot_run_here_is_one_line() -> None:
    tools = [
        ("pytest (tests)", False, "install pytest"),
        ("mutmut (mutation)", False, "python3 not found in WSL (install it in the distribution)"),
    ]
    assert tree_check.mutation_lines(tools) == [
        "MUTATION: mutmut cannot run on this machine: python3 not found in WSL (install it "
        "in the distribution). Make it run (the fix), or the user decides: [gates] "
        'mutation_tool = "none".'
    ]
    assert tree_check.mutation_lines([("mutmut (mutation)", True, "mutation off")]) == []


# ---------- the setup's files: one commit of their own ----------
def test_setup_commits_its_own_files_and_nothing_else(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    git(r.root, "checkout", "-q", "main")
    toml = r.root / "framegate.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n# edited by the setup\n", "utf-8")
    (r.root / "docs").mkdir()
    (r.root / "docs" / "code-map.md").write_text("# Code map\n", encoding="utf-8")
    know = r.root / ".framegate" / "project.md"
    know.write_text(know.read_text(encoding="utf-8") + "\n- a fact\n", encoding="utf-8")
    with open(r.root / "pyproject.toml", "a", encoding="utf-8") as fh:
        fh.write("\n# the req marker line\n")
    (r.root / "tests" / "tagging.py").write_text("# a tag helper\n", encoding="utf-8")
    (r.root / "notes.txt").write_text("mine", encoding="utf-8")
    (r.root / "README.md").write_text("mine too\n", encoding="utf-8")
    git(r.root, "add", "README.md")  # staged by the user: stays staged, not in the commit

    cfg = load_config(r.root)
    want = [".framegate/project.md", "docs/code-map.md", "framegate.toml", "pyproject.toml"]
    assert scaffold.setup_files(cfg) == want
    capsys.readouterr()
    assert r.af("commit-setup", "--dry-run", "tests/tagging.py") == 0
    out = capsys.readouterr().out
    assert out.startswith("Setup files to commit (5), on main: .framegate/project.md, ")
    assert "tests/tagging.py" in out and "notes.txt" not in out and "README.md" not in out
    assert out.rstrip().endswith("framegate commit-setup tests/tagging.py")
    assert git(r.root, "log", "-1", "--format=%s").strip() != scaffold.SETUP_COMMIT

    assert r.af("commit-setup", "tests/tagging.py") == 0
    assert 'Committed 5 setup file(s) on main ("framegate: setup")' in capsys.readouterr().out
    assert git(r.root, "log", "-1", "--format=%s").strip() == scaffold.SETUP_COMMIT
    committed = git(r.root, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == sorted([*want, "tests/tagging.py"])
    left = git(r.root, "status", "--porcelain").splitlines()
    assert "?? notes.txt" in left and "A  README.md" in left
    assert not any(line[3:] in committed for line in left)
    assert r.af("commit-setup") == 0
    assert "Setup files: nothing to commit." in capsys.readouterr().out
    # nothing of the setup is in the way of the first task
    git(r.root, "commit", "-q", "-m", "readme")
    assert r.af("spec", "other") == 0


def test_the_refusal_over_uncommitted_files_names_them_and_the_command(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    git(r.root, "checkout", "-q", "main")
    (r.root / "pyproject.toml").write_text("# changed\n", encoding="utf-8")
    with open(r.root / ".gitignore", "a", encoding="utf-8") as fh:
        fh.write("scratch/\n")
    capsys.readouterr()
    assert r.af("spec", "other") == 1
    err = capsys.readouterr().err
    assert " M .gitignore\n M pyproject.toml\n" in err
    assert (
        'To commit them as they are, in one commit: git commit -m "Changes before other" '
        "-- .gitignore pyproject.toml\n" in err
    )
    assert "Then run the command again: framegate spec other" in err
    # the printed command is the whole fix
    git(r.root, "commit", "-q", "-m", "Changes before other", "--", ".gitignore", "pyproject.toml")
    assert r.af("spec", "other") == 0
    assert commit_command([" M a b.txt", "D  c.py"], "x") == 'git commit -m "x" -- "a b.txt" c.py'
