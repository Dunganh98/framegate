"""A failing mutation run's survivors stay in mutation_survivors.md for the retro."""

from __future__ import annotations

import pathlib

import pytest

from conftest import DemoRepo, git
from framegate.adapters import get_adapter
from framegate.adapters.base import CheckResult, LanguageAdapter
from framegate.checks import mutation
from framegate.config import load_config

FAIL = CheckResult("FAIL", "x", summary="Mutation score 40.0% (2/5 killed), threshold 60%")
PASS = CheckResult("PASS", "x", summary="Mutation score 80.0% (4/5 killed), threshold 60%")


def test_a_failed_runs_survivors_are_kept_below_the_next_runs(tmp_path: pathlib.Path) -> None:
    path = tmp_path / mutation.SURVIVORS

    def run(survivors: str, res: CheckResult) -> str:
        before = path.read_text(encoding="utf-8") if path.is_file() else ""
        if survivors:  # (a run without mutants writes no list)
            path.write_text(survivors, encoding="utf-8")
        mutation.keep_failed(path, before, res)
        return path.read_text(encoding="utf-8")

    first = run("# Survivors\n- a.py:3 x > 0 -> x >= 0\n", FAIL)
    assert first.startswith("<!-- this run failed: Mutation score 40.0%") and "a.py:3" in first
    second = run("# Survivors\nNone.\n", PASS)
    assert second.startswith("# Survivors\nNone.\n\n## Earlier run 1 (failed: Mutation score 40.0%")
    assert "kept for the retro)\n\n# Survivors\n- a.py:3 x > 0 -> x >= 0\n" in second
    third = run("# Survivors\n- b.py:9\n", FAIL)
    assert third.startswith(mutation.FAILED) and third.count("## Earlier run") == 1
    fourth = run("", PASS)  # no mutants this time: the failed list is still not lost
    assert fourth.startswith("No survivor list from the latest run.\n\n## Earlier run 2 (failed")
    assert (
        fourth.index("b.py:9") < fourth.index("## Earlier run 1 (failed") < fourth.index("a.py:3")
    )
    assert run("", PASS) == fourth


def test_the_mutation_check_keeps_them(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = get_adapter(load_config(demo_repo.root))
    git(demo_repo.root, "tag", "tests-approved/demo")
    path = adapter.cfg.feature_out("demo") / mutation.SURVIVORS
    runs = iter([("- add: + -> -\n", FAIL), ("None.\n", PASS)])

    def fake(self: LanguageAdapter, *args: object) -> CheckResult:
        text, res = next(runs)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return res

    monkeypatch.setattr(type(adapter), "mutation_check", fake)
    assert mutation.check(adapter, "demo").status == "FAIL"
    assert mutation.check(adapter, "demo").status == "PASS"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("None.\n\n## Earlier run 1 (failed") and "- add: + -> -" in text
