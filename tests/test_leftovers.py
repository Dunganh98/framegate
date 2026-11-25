"""A test run that leaves a file in the tree fails its check (checks/leftovers.py)."""

from __future__ import annotations

import pathlib

from conftest import git, init_git
from framegate.adapters.base import Check, CheckResult, LanguageAdapter
from framegate.checks import leftovers
from test_checks import adapter_for, write


def test_a_new_untracked_file_fails_the_check_and_is_removed(tmp_path: pathlib.Path) -> None:
    init_git(tmp_path)
    write(tmp_path, ".gitignore", "cache/\n")
    write(tmp_path, "tests/test_new.py", "x = 1\n")  # the writer's own file, there before
    git(tmp_path, "add", ".gitignore")
    git(tmp_path, "commit", "-q", "-m", "start")
    adapter = adapter_for(tmp_path)

    def run() -> CheckResult:
        for rel in ("result-a.json", "made/deep/b.txt", "out/f/junit.xml", "cache/c.bin"):
            write(tmp_path, rel, "x")
        return CheckResult("PASS", "2 passed")

    res = leftovers.watched(adapter, Check("tests", run)).run()
    assert res.status == "FAIL" and res.summary == "files left behind: 2"
    assert "(removed now):\n  made/deep/b.txt\n  result-a.json\n" in res.output
    assert not (tmp_path / "result-a.json").exists() and not (tmp_path / "made").exists()
    for kept in ("out/f/junit.xml", "cache/c.bin", "tests/test_new.py"):
        assert (tmp_path / kept).is_file(), kept
    clean = leftovers.watched(adapter, Check("tests", lambda: CheckResult("PASS", "ok"))).run()
    assert clean.status == "PASS" and clean.output == "ok"

    def tests_phase(a: LanguageAdapter, feature: str) -> CheckResult:
        return run()

    assert leftovers.watching(tests_phase)(adapter, "f").status == "FAIL"
    off = adapter_for(tmp_path, {"gates": {"test_leftovers": False}})
    assert leftovers.watched(off, Check("tests", run)).run().status == "PASS"
    assert (tmp_path / "result-a.json").is_file()
