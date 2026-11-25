from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROOTS = ["src", "tests", "tools", "ci"]
_spec = importlib.util.spec_from_file_location("check_rules", ROOT / "ci" / "check_rules.py")
assert _spec and _spec.loader
check_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_rules)


@pytest.fixture
def tree(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    return tmp_path


def _write(tree: pathlib.Path, text: str) -> pathlib.Path:
    path = tree / "src" / "m.py"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_clean_file_counts_nothing(tree: pathlib.Path) -> None:
    assert check_rules.measure(_write(tree, "# one\n# two\nx = 1  # trailing\n# three\n")) == {}


def test_a_comment_block_over_two_lines_is_counted_once(tree: pathlib.Path) -> None:
    assert check_rules.measure(_write(tree, "# 1\n# 2\n# 3\n# 4\nx = 1\n")) == {"comment-block": 1}


@pytest.mark.parametrize(
    "comment", ["# this used to return a list", "# was renamed in the refactor", "# see AB-123"]
)
def test_history_in_a_comment_is_counted(tree: pathlib.Path, comment: str) -> None:
    assert check_rules.measure(_write(tree, f"x = 1  {comment}\n")) == {"comment-history": 1}


@pytest.mark.parametrize(
    "comment", ["# looked up now, not at import", "# a fixed width", "# UTF-8"]
)
def test_present_tense_prose_is_not_history(tree: pathlib.Path, comment: str) -> None:
    assert check_rules.measure(_write(tree, f"x = 1  {comment}\n")) == {}


def test_suppressions_and_docstrings_are_counted(tree: pathlib.Path) -> None:
    hush = "noqa"
    text = f"import os  # {hush}: F401\ny = '# {hush} in a string'\n"
    assert check_rules.measure(_write(tree, text)) == {"suppression": 1}
    quotes = '"' * 3
    text = f"{quotes}M.{quotes}\nclass C:\n    {quotes}C.{quotes}\n"
    assert check_rules.measure(_write(tree, text)) == {"docstring": 2}


def test_a_file_over_400_lines_counts_its_size(tree: pathlib.Path) -> None:
    assert check_rules.measure(_write(tree, "x = 1\n" * 400)) == {}
    assert check_rules.measure(_write(tree, "x = 1\n" * 401)) == {"file-size": 401}


def test_a_new_violation_fails_and_the_list_may_only_shrink(
    tree: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tree, "x = 1\n" * 401)
    assert check_rules.main("allow.txt", ["src"]) == 1
    assert capsys.readouterr().out.strip() == "file-size src/m.py: 401 > 0"

    assert check_rules.main("allow.txt", ["src"], update=True) == 0
    assert (tree / "allow.txt").read_text(encoding="utf-8") == "file-size src/m.py 401\n"
    assert check_rules.main("allow.txt", ["src"]) == 0

    _write(tree, "x = 1\n")
    assert check_rules.main("allow.txt", ["src"]) == 1
    assert "allow-list says 401, is 0: lower it" in capsys.readouterr().out


def test_the_committed_list_matches_the_tree(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(ROOT)
    assert check_rules.main("ci/allowlist.txt", ROOTS) == 0, capsys.readouterr().out


def test_the_workflow_runs_the_check_and_new_tools_add_nothing() -> None:
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    assert f"uv run python ci/check_rules.py ci/allowlist.txt {' '.join(ROOTS)}" in workflow
    listed = (ROOT / "ci/allowlist.txt").read_text(encoding="utf-8")
    assert " tools/" not in listed
