"""The task's own lines against the formatted base: file sizes, risk counts, and a
suppression comment the formatter must not move."""

from __future__ import annotations

import re

import pytest

from conftest import DemoRepo, git
from framegate import gates, red_tree, risk, task_base
from framegate.adapters import get_adapter
from framegate.adapters.base import CheckResult, changed_lines, keep_suppressed
from framegate.checks import structure
from framegate.config import load_config

RX = re.compile(r"#\s*(?:noqa|type:\s*ignore)\b")
LONG = "x = dict(" + ", ".join(f"key_number_{i}={i}" for i in range(9)) + ")  # type: ignore\n"


def test_a_rewrite_that_moves_a_suppression_comment_is_left_out() -> None:
    before = "a = [1,\n     2]\nc = 0\nx = f(aaa, bbb)  # noqa: E501\nb =  1\n"
    after = "a = [1, 2]\nc = 0\nx = f(\n    aaa,\n    bbb,\n)  # noqa: E501\nb = 1\n"
    text, kept = keep_suppressed(before, after, RX)
    # (the rewrite right below it, no unchanged line between, stays with it)
    assert text == "a = [1, 2]\nc = 0\nx = f(aaa, bbb)  # noqa: E501\nb =  1\n" and kept == [4]
    # same code, other quotes or spacing: the comment did not move
    same = keep_suppressed("x = f('a')  # noqa\n", 'x = f("a")  # noqa\n', RX)
    assert same == ('x = f("a")  # noqa\n', [])
    # a removed comment (the linter's fix of an unused one) is no move
    assert keep_suppressed("x = 1  # noqa\n", "x = 1\n", RX) == ("x = 1\n", [])


def test_a_format_check_does_not_name_a_file_left_unformatted(demo_repo: DemoRepo) -> None:
    adapter = get_adapter(load_config(demo_repo.root))
    out = "Would reformat: src/demo/__init__.py\n1 file would be reformatted"
    res = red_tree.without_files(adapter, CheckResult("FAIL", out), {"src/demo/__init__.py"}, "x")
    assert res.status == "PASS" and res.summary == "x: 1 file(s)"
    assert red_tree.without_files(adapter, CheckResult("FAIL", out), set(), "x").status == "FAIL"


def test_sizes_and_risk_counts_use_the_formatted_start(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = demo_repo
    for mod in (structure, risk):  # the feature starts after the debt
        monkeypatch.setattr(mod, "base_ref", lambda adapter, feature: "spec-approved/demo")
    rel = "src/demo/__init__.py"
    src = r.root / rel
    src.write_text(
        src.read_text(encoding="utf-8") + "\n\nOLD = [1,\n       2]\nMID = 0\n" + LONG, "utf-8"
    )
    git(r.root, "add", "src")
    git(r.root, "commit", "-qm", "debt from before the task")
    assert r.af("approve-spec", "demo", "--force") == 0
    adapter = get_adapter(load_config(r.root))
    src.write_text(src.read_text(encoding="utf-8") + "NEW = 3\n", "utf-8")
    note = adapter.format_fix("spec-approved/demo")
    text = src.read_text(encoding="utf-8")
    assert "OLD = [1, 2]" in text and LONG in text  # the debt is fixed, the statement stays
    kept = text.splitlines().index(LONG.rstrip("\n")) + 1
    assert adapter.format_kept == {rel: [kept + 1]}  # (its line before the format fix)
    assert f"suppression comment off its line): {rel}:{kept + 1}" in note

    size = structure.report(adapter, "demo").files[0]
    assert size.path == rel and size.before == size.lines - 1  # only NEW is growth
    stats = risk.change_stats(adapter, "demo")
    assert stats and stats["changed_lines"] == 1 and stats["removed_lines"] == 0

    base = task_base.task_base(adapter, "spec-approved/demo")
    assert base and changed_lines(adapter.cfg, base, rel) == {len(text.splitlines())}
    fmt = next(c for c in gates.lint_checks(adapter, "demo") if c.name == "format").run()
    assert fmt.status == "PASS" and gates.KEPT_NOTE in fmt.summary
