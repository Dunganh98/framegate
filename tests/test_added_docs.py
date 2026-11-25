"""The lines a run adds to source hold no new docstring and no spec ID (checks/added_docs.py)."""

from __future__ import annotations

import pathlib

from conftest import git, init_git
from framegate import doc_blocks, gates
from framegate.checks import added_docs
from test_checks import adapter_for, write

OLD = '''\
def f() -> int:
    """Old words."""
    return 1


def g() -> int:
    return 2
'''
NEW = '''\
def f() -> int:
    """New words."""
    return 1


def g() -> int:
    """Two."""
    return 2  # AB-R-001


def h() -> str:
    return "see AB-E-002"
'''
MOVED = '''\
def f() -> int:
    """Old words."""
    return 1
'''


def repo(root: pathlib.Path) -> None:
    init_git(root)
    write(root, ".gitignore", "out/\n")
    write(root, "src/pkg/a.py", OLD)
    write(root, "tests/test_a.py", "def test_a() -> None:\n    assert True\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")


def test_a_new_docstring_and_a_spec_id_fail_and_an_edited_docstring_does_not(
    tmp_path: pathlib.Path,
) -> None:
    repo(tmp_path)
    adapter = adapter_for(tmp_path)
    assert added_docs.check(adapter, "f").status == "PASS"
    write(tmp_path, "src/pkg/a.py", NEW)
    write(tmp_path, "tests/test_a.py", '"""AB-R-001."""\n')  # a test file is not checked
    res = added_docs.check(adapter, "f")
    assert res.status == "FAIL" and res.summary == "3"
    assert added_docs.problems(adapter) == [
        "src/pkg/a.py:12: spec ID AB-E-002",
        "src/pkg/a.py:7: new docstring (g)",
        "src/pkg/a.py:8: spec ID AB-R-001",
    ]


def test_a_docstring_moved_with_its_function_is_not_new(tmp_path: pathlib.Path) -> None:
    repo(tmp_path)
    write(tmp_path, "src/pkg/a.py", "def g() -> int:\n    return 2\n")
    write(tmp_path, "src/pkg/b.py", MOVED)
    assert added_docs.problems(adapter_for(tmp_path)) == []


def test_doc_comment_lines_of_another_language() -> None:
    text = "/// <summary>Adds.</summary>\n[Pure]\npublic int Add(int a) => a;\n// plain\n"
    assert doc_blocks.line_docs(text, "///") == [
        doc_blocks.Doc("public int Add(int a) => a;", 1, 1, "<summary>Adds.</summary>")
    ]


def test_the_step_gate_has_the_check_unless_it_is_switched_off(tmp_path: pathlib.Path) -> None:
    repo(tmp_path)
    names = [c.name for c in gates.checks(adapter_for(tmp_path), "f", "step", True)]
    assert added_docs.NAME in names
    off = adapter_for(tmp_path, {"gates": {"docstring_check": False}})
    assert added_docs.NAME not in [c.name for c in gates.checks(off, "f", "step", True)]
    assert added_docs.NAME not in [c.name for c in gates.checks(off, "f", "final", True)]
