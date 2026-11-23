"""The red check judges the tests the feature added or changed: a test file it did not
touch that was broken before the task is a note."""

from __future__ import annotations

import json
import pathlib

import pytest

from conftest import git, init_git
from framegate.adapters import get_adapter
from framegate.adapters.base import LanguageAdapter
from framegate.checks import baseline, red_stubs
from framegate.config import parse_config
from framegate.state import set_marker

RED = """\
import pytest


@pytest.mark.req("FT-R-001")
def test_red() -> None:
    raise NotImplementedError
"""
WEAK = RED + '\n\n@pytest.mark.req("FT-R-001")\ndef test_weak() -> None:\n    assert True\n'
LISTED = {"gates": {"unrunnable_tests": [{"test": "tests/test_old.py", "reason": "needs spark"}]}}


def repo(root: pathlib.Path, data: dict | None = None) -> LanguageAdapter:
    init_git(root)
    files = {
        ".gitignore": "out/\n",
        "specs/f/requirements.md": "FT-R-001",
        "tests/test_old.py": "import a_framework_that_is_not_installed\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "before the task")
    adapter = get_adapter(parse_config(root, data or {}))
    set_marker(adapter.cfg, "spec-approved/f")
    (root / "tests/test_f.py").write_text(RED, encoding="utf-8")
    return adapter


def test_a_file_that_could_not_load_at_the_spec_approval_is_a_note(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = repo(tmp_path)
    res = red_stubs.check(adapter, "f")
    assert res.status == "PASS", res.output
    assert "NOTE: could not load before the task: tests.test_old" in res.output
    record = adapter.cfg.feature_out("f") / red_stubs.BEFORE_FILE
    assert json.loads(record.read_text(encoding="utf-8"))["broken"] == ["tests.test_old"]
    # asked once per commit and set of files
    monkeypatch.setattr(type(adapter), "broken_at", lambda *a: pytest.fail("asked again"))
    assert red_stubs.check(adapter, "f").status == "PASS"


def test_a_file_the_task_broke_stays_an_error(tmp_path: pathlib.Path) -> None:
    init_git(tmp_path)
    files = {
        ".gitignore": "out/\n",
        "specs/f/requirements.md": "FT-R-001",
        "tests/helper.py": "LIMIT = 3\n",
        "tests/test_uses_helper.py": "from helper import LIMIT\n\nassert LIMIT\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "before the task")
    adapter = get_adapter(parse_config(tmp_path, {}))
    set_marker(adapter.cfg, "spec-approved/f")
    (tmp_path / "tests/test_f.py").write_text(RED, encoding="utf-8")
    (tmp_path / "tests/helper.py").write_text("OTHER = 3\n", encoding="utf-8")
    res = red_stubs.check(adapter, "f")
    assert res.status == "FAIL", res.output
    assert "ERROR (import/fixture/collection problem): tests.test_uses_helper" in res.output
    assert red_stubs.UNTOUCHED_HINT in res.output and "NOTE" not in res.output


def test_a_listed_test_file_that_cannot_load_is_a_note(tmp_path: pathlib.Path) -> None:
    res = red_stubs.check(repo(tmp_path, LISTED), "f")
    assert res.status == "PASS", res.output
    assert res.output.splitlines()[0] == "1 tests for feature f."
    assert (
        "NOTE: cannot load, listed in unrunnable_tests (needs spark): tests.test_old" in res.output
    )
    assert baseline.green_only(res.output) == []


def test_a_file_broken_at_the_baseline_is_a_note(tmp_path: pathlib.Path) -> None:
    adapter = repo(tmp_path, {"gates": {"regression_baseline": True}})
    record = adapter.cfg.feature_out("f") / "regression_baseline.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"failing": ["tests.test_old"]}), encoding="utf-8")
    res = red_stubs.check(adapter, "f")
    assert res.status == "PASS", res.output
    assert "NOTE: could not load before the task either: tests.test_old" in res.output
    # a file the feature wrote counts, whatever is listed
    (tmp_path / "tests/test_new.py").write_text("import nothing_at_all\n", encoding="utf-8")
    res = red_stubs.check(adapter, "f")
    assert res.status == "FAIL" and "ERROR" in res.output and "tests.test_new" in res.output
    assert red_stubs.UNTOUCHED_HINT not in res.output


def test_a_new_test_that_passes_gets_the_remedy(tmp_path: pathlib.Path) -> None:
    adapter = repo(tmp_path, LISTED)
    (tmp_path / "tests/test_f.py").write_text(WEAK, encoding="utf-8")
    res = red_stubs.check(adapter, "f")
    assert res.status == "FAIL"
    assert "PASSES AGAINST STUB (weak or wrong test): tests.test_f::test_weak" in res.output
    assert (
        "make it fail first (assert the accepted case next to the refused one), or ask to "
        "list it as green against stubs" in res.output
    )
    assert baseline.green_only(res.output) == ["test_weak"]
