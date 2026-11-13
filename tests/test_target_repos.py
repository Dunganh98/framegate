"""A shared, not-all-green target repository (first used on a Python service repo whose full
suite needs external services): pytest arguments for every run, a baseline of the tests that
already fail, commits limited to the workflow's paths, markers as private refs, and the
project's own formatter for the format fix."""

from __future__ import annotations

import json
import pathlib

import pytest

from conftest import DemoRepo, git, marker
from framegate import gates
from framegate.adapters import get_adapter
from framegate.adapters.base import TestCase, TestRun
from framegate.config import ConfigError, load_config, parse_config
from framegate.state import commit, delete_marker, set_marker, tag_exists


# ---------- [python] pytest_args ----------
def test_pytest_args_reach_every_run_and_the_agents_command(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml('[python]\npytest_args = ["-m", "not integration and not slow"]\n')
    adapter = get_adapter(load_config(r.root))
    assert adapter.test_command(["DEMO-R-001"]) == (
        'python -m pytest -q -m "not integration and not slow" --req DEMO-R-001'
    )
    assert get_adapter(parse_config(r.root, {})).test_command([]) == "python -m pytest -q"
    with pytest.raises(ConfigError, match="list of strings"):
        parse_config(r.root, {"python": {"pytest_args": ["-m", 3]}})
    with pytest.raises(ConfigError, match="unknown key"):
        parse_config(r.root, {"python": {"pytest_arg": []}})


def test_pytest_args_deselect_tests(demo_repo: DemoRepo) -> None:
    """A test the machine cannot run (marked) is left out of the test run and the regression."""
    r = demo_repo
    (r.root / "tests" / "test_marks.py").write_text(
        "import pytest\n\n\n"
        "@pytest.mark.integration\ndef test_needs_a_service() -> None:\n    assert 0\n\n\n"
        "def test_plain() -> None:\n    assert 1\n",
        encoding="utf-8",
    )
    strict = get_adapter(load_config(r.root))
    assert strict.run_tests(paths=["tests/test_marks.py"]).status == "failed"
    r.add_toml('[python]\npytest_args = ["-m", "not integration"]\n')
    adapter = get_adapter(load_config(r.root))
    assert adapter.run_tests(paths=["tests/test_marks.py"]).status == "passed"
    run = adapter.regression_run()
    assert run.status == "passed" and not run.incomplete


# ---------- [gates] regression_baseline ----------
def _run(*cases: tuple[str, str]) -> TestRun:
    failed = any(o in ("failed", "error") for _, o in cases)
    return TestRun(
        "failed" if failed else "passed",
        1 if failed else 0,
        "out",
        [TestCase(n, o, "") for n, o in cases],
    )


def test_known_failures_are_left_out_of_the_verdict() -> None:
    exp = gates.ExpectedRed()
    old = _run(("t.old", "failed"), ("t.fine", "passed"))
    res = gates.tests_expecting(old, exp, set(), {"t.old"})
    assert res.status == "PASS"
    assert "already failing at spec approval (left out): 1" in res.summary
    new = _run(("t.old", "failed"), ("t.new", "failed"))
    res = gates.tests_expecting(new, exp, set(), {"t.old"})
    assert res.status == "FAIL" and "t.new" in res.output
    assert "UNEXPECTED failures" in res.output and "\n  t.old" not in res.output
    # without a baseline every failure counts
    assert gates.tests_expecting(old, exp, set()).status == "FAIL"


def test_baseline_is_recorded_at_spec_approval_and_used_by_the_final_gates(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    (r.root / "tests" / "test_legacy.py").write_text(
        "def test_red_before_the_feature() -> None:\n    assert 0\n\n\n"
        "def test_green() -> None:\n    assert 1\n",
        encoding="utf-8",
    )
    r.set_toml("gates", "regression_baseline", "true")
    assert r.af("approve-spec", "demo") == 0
    assert "Regression baseline: 1 test(s) already fail" in capsys.readouterr().out
    path = r.root / "out" / "demo" / gates.BASELINE_FILE
    failing = json.loads(path.read_text(encoding="utf-8"))["failing"]
    assert len(failing) == 1 and failing[0].endswith("test_red_before_the_feature")
    assert any(e["kind"] == "regression_baseline" and e["failing"] == 1 for e in r.events())

    cfg = load_config(r.root)
    adapter = get_adapter(cfg)
    known = gates.regression_baseline(cfg, "demo")
    assert known == set(failing)
    assert gates.regression_expecting(adapter, gates.ExpectedRed(), known).status == "PASS"
    # the final checks use it: same check name, judged against the baseline
    set_marker(cfg, "tests-approved/demo")
    names = [c.name for c in gates.checks(adapter, "demo", "final", skip_mutation=True)]
    assert "regression: whole suite, no coverage" in names

    # a test that breaks later is still a failure
    (r.root / "tests" / "test_later.py").write_text(
        "def test_broken_by_the_feature() -> None:\n    assert 0\n", encoding="utf-8"
    )
    res = gates.regression_expecting(adapter, gates.ExpectedRed(), known)
    assert res.status == "FAIL" and "test_broken_by_the_feature" in res.output

    # a second approval keeps the first record (the feature's own work may be red by then)
    path.write_text(json.dumps({"failing": ["kept"]}), encoding="utf-8")
    assert r.af("approve-spec", "demo") == 0
    assert json.loads(path.read_text(encoding="utf-8"))["failing"] == ["kept"]


def test_baseline_off_by_default(demo_repo: DemoRepo) -> None:
    r = demo_repo
    assert r.af("approve-spec", "demo") == 0
    assert not (r.root / "out" / "demo" / gates.BASELINE_FILE).exists()
    cfg = load_config(r.root)
    (r.root / "out" / "demo" / gates.BASELINE_FILE).write_text(
        json.dumps({"failing": ["x"]}), encoding="utf-8"
    )
    assert gates.regression_baseline(cfg, "demo") == set()  # off: a stray file is not used


# ---------- commits hold only the workflow's paths ----------
def test_commit_leaves_the_users_other_files_alone(demo_repo: DemoRepo) -> None:
    r = demo_repo
    (r.root / "notes.md").write_text("my notes\n", encoding="utf-8")  # untracked
    (r.root / "scratch").mkdir()
    (r.root / "scratch" / "try.py").write_text("X = 1\n", encoding="utf-8")
    (r.root / "docs").mkdir()
    (r.root / "docs" / "guide.md").write_text("v1\n", encoding="utf-8")
    git(r.root, "add", "docs")
    git(r.root, "commit", "-q", "-m", "docs")
    (r.root / "docs" / "guide.md").write_text("v2, a local edit\n", encoding="utf-8")
    (r.root / "README.md").write_text("staged by the user\n", encoding="utf-8")
    git(r.root, "add", "README.md")
    assert r.af("approve-spec", "demo") == 0
    committed = git(r.root, "show", "--name-only", "--format=", "HEAD").split()
    assert "specs/demo/requirements.md" in committed and "src/demo/__init__.py" in committed
    assert not {"notes.md", "scratch/try.py", "docs/guide.md", "README.md"} & set(committed)
    status = git(r.root, "status", "--porcelain")
    assert "?? notes.md" in status and "?? scratch/" in status
    assert " M docs/guide.md" in status and "A  README.md" in status  # still staged, not lost


def test_commit_with_nothing_of_its_own_is_no_commit(demo_repo: DemoRepo) -> None:
    r = demo_repo
    assert r.af("approve-spec", "demo") == 0
    head = git(r.root, "rev-parse", "HEAD")
    (r.root / "notes.md").write_text("x\n", encoding="utf-8")
    assert commit(load_config(r.root), "nothing") is False
    assert git(r.root, "rev-parse", "HEAD") == head


# ---------- markers: private refs, not tags ----------
def test_markers_are_private_refs(demo_repo: DemoRepo, tmp_path: pathlib.Path) -> None:
    r = demo_repo
    assert r.af("approve-spec", "demo") == 0
    assert marker(r.root, "spec-approved/demo")
    assert not git(r.root, "tag", "--list").strip()
    # the short name resolves like a tag did (prompts, diff-cover, git diff)
    assert git(r.root, "rev-parse", "spec-approved/demo") == git(r.root, "rev-parse", "HEAD")
    # nothing a push sends: neither --all nor --tags carries the marker
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(r.root, "push", "-q", str(remote), "--all")
    git(r.root, "push", "-q", str(remote), "--tags")
    refs = git(remote, "for-each-ref", "--format=%(refname)").split()
    assert refs and all(x.startswith("refs/heads/") for x in refs)


def test_old_tags_are_read_and_replaced(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    git(r.root, "tag", "tests-approved/demo")  # left by an older framegate
    assert tag_exists(cfg, "tests-approved/demo") and not marker(r.root, "tests-approved/demo")
    set_marker(cfg, "tests-approved/demo")
    assert marker(r.root, "tests-approved/demo")
    assert not git(r.root, "tag", "--list").strip()  # no ambiguous name left
    delete_marker(cfg, "tests-approved/demo")
    assert not tag_exists(cfg, "tests-approved/demo")
    delete_marker(cfg, "tests-approved/demo")  # missing is fine


def test_markers_as_tags_for_ci(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.set_toml("git", "markers", '"tags"')
    assert r.af("approve-spec", "demo") == 0
    assert git(r.root, "tag", "--list").strip() == "spec-approved/demo"
    assert not marker(r.root, "spec-approved/demo")
    with pytest.raises(ConfigError, match="markers must be one of"):
        parse_config(r.root, {"git": {"markers": "notes"}})


# ---------- the project's own formatter for the format fix ----------
def test_configured_format_fix(demo_repo: DemoRepo, python_exe: str) -> None:
    r = demo_repo
    cfg = parse_config(r.root, {"gates": {"commands": {"format": ["black", "--check", "{src}"]}}})
    assert not get_adapter(cfg).format_fix_enabled()  # own check, no own fix: off, as before
    cfg = parse_config(
        r.root,
        {
            "gates": {
                "commands": {
                    "format": ["black", "--check", "{src}"],
                    "format_fix": ["python", "-m", "black", "-q", "{files}", "--fast"],
                }
            }
        },
    )
    adapter = get_adapter(cfg)
    assert adapter.format_fix_enabled()
    assert "format_fix" not in cfg.gate_commands  # never run as a check
    assert adapter.configured_format_fix(["src/a.py", "src/b.py"]) == [
        [adapter.python, "-m", "black", "-q", "src/a.py", "src/b.py", "--fast"]
    ]
    off = parse_config(
        r.root,
        {"gates": {"format_fix": False, "commands": {"format_fix": ["fmt", "{files}"]}}},
    )
    assert not get_adapter(off).format_fix_enabled()
    with pytest.raises(ConfigError, match=r"\{files\}"):
        parse_config(r.root, {"gates": {"commands": {"format_fix": ["black", "src"]}}})
    with pytest.raises(ConfigError, match="unknown key"):
        parse_config(r.root, {"gates": {"commands": {"formatfix": ["x"]}}})


def test_configured_format_fix_runs_on_the_changed_files(demo_repo: DemoRepo) -> None:
    """The configured command (here: a tiny stand-in formatter) rewrites the changed file."""
    r = demo_repo
    fmt = r.root.parent / "fmt.py"
    fmt.write_text(
        "import pathlib, sys\n"
        "for f in sys.argv[1:]:\n"
        "    p = pathlib.Path(f)\n"
        "    p.write_text(p.read_text(encoding='utf-8').replace('X=1', 'X = 1'),"
        " encoding='utf-8')\n",
        encoding="utf-8",
    )
    r.add_toml(
        "[gates.commands]\n"
        'format = ["python", "-c", "pass"]\n'
        f'format_fix = ["python", {json.dumps(str(fmt))}, "{{files}}"]\n'
    )
    cfg = load_config(r.root)
    adapter = get_adapter(cfg)
    target = r.root / "src" / "demo" / "extra.py"
    target.write_text("X=1\n", encoding="utf-8")
    adapter.format_files(["src/demo/extra.py"])
    assert target.read_text(encoding="utf-8") == "X = 1\n"
