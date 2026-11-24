"""A test file the spec names for a spec ID must be touched by the tests."""

from __future__ import annotations

from conftest import DemoRepo, git
from framegate.adapters import get_adapter
from framegate.checks import named_tests
from framegate.config import load_config
from framegate.state import set_marker

#: paths in prose: none of them says where a test goes
PROSE = """
| DEMO-R-002 | sub() | the test (`tests/test_old.py`) stays green: tests/test_contracts.py |

## Test data and fixtures
New test file `tests/test_sub.py` for DEMO-R-002.
- DEMO-E-001: the refusal goes next to the old ones, tests/unit/test_old.py (unchanged).
- DEMO-E-001: see `tests/test_old.py::TestA` for the fixture.
- DEMO-E-001: in tests/test_old.py::TestA
- DEMO-E-001: in `tests/unit/test_old.py` (unchanged)

## References
- DEMO-R-001: as tests/test_reference.py does it.
"""
PLACED = """
- DEMO-R-002: in `tests/test_cli.py`
- DEMO-R-001, DEMO-E-001: in tests/unit/test_add.py.

| ID | Test file |
|---|---|
| DEMO-E-001 | `tests/test_table.py` |
| DEMO-R-001 | tests/test_old.py::TestA |
"""


def test_the_spec_named_test_file_must_be_touched(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    adapter = get_adapter(cfg)
    req = r.root / "specs/demo/requirements.md"
    text = req.read_text(encoding="utf-8")
    head, sep, rest = text.partition("\n## Edge cases")
    row, sections = PROSE.split("\n\n", 1)
    req.write_text(head.rstrip("\n") + row + "\n" + sep + rest + sections, encoding="utf-8")
    assert named_tests.named_files(cfg, "demo") == {}
    assert named_tests.check(adapter, "demo").status == "PASS"
    req.write_text(req.read_text(encoding="utf-8") + PLACED, encoding="utf-8")
    assert named_tests.named_files(cfg, "demo") == {
        "tests/test_cli.py": ["DEMO-R-002"],
        "tests/unit/test_add.py": ["DEMO-E-001", "DEMO-R-001"],
        "tests/test_table.py": ["DEMO-E-001"],
    }
    req.write_text(text + "\n- DEMO-R-002: in `tests/test_cli.py`\n", encoding="utf-8")

    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "spec")
    set_marker(cfg, "spec-approved/demo")
    (r.root / "tests/test_new.py").write_text("def test_sub() -> None:\n    assert 1\n", "utf-8")
    res = named_tests.check(adapter, "demo")
    assert res.status == "FAIL", res.output
    assert "NOT TOUCHED (the spec names this file for the test of DEMO-R-002" in res.output
    assert res.output.endswith("not in another file): tests/test_cli.py")
    (r.root / "tests/test_cli.py").write_text("def test_sub() -> None:\n    assert 1\n", "utf-8")
    res = named_tests.check(adapter, "demo")
    assert res.status == "PASS" and "touched: tests/test_cli.py (DEMO-R-002)" in res.output
