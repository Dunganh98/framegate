"""A test file the spec names for a spec ID must be touched by the tests."""

from __future__ import annotations

from conftest import DemoRepo, git
from framegate.adapters import get_adapter
from framegate.checks import named_tests
from framegate.config import load_config
from framegate.state import set_marker

ROWS = """
| DEMO-R-002 | sub() subtracts | given 3, 2 then 1; test in `tests/test_cli.py` |

## Test data and fixtures
- DEMO-E-001: the refusal goes next to the old ones, tests/unit/test_old.py (unchanged).
- DEMO-E-001: see `tests/test_old.py::TestA` for the fixture.

## References
- DEMO-R-001: as tests/test_reference.py does it.
"""


def test_the_spec_named_test_file_must_be_touched(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    adapter = get_adapter(cfg)
    req = r.root / "specs/demo/requirements.md"
    text = req.read_text(encoding="utf-8")
    head, sep, rest = text.partition("\n## Edge cases")
    req.write_text(head.rstrip("\n") + ROWS.split("\n\n")[0] + "\n" + sep + rest, encoding="utf-8")
    assert named_tests.named_files(cfg, "demo") == {"tests/test_cli.py": ["DEMO-R-002"]}
    req.write_text(req.read_text(encoding="utf-8") + ROWS.split("\n\n", 1)[1], encoding="utf-8")
    # `::` names a test (the other rule), `(unchanged)` and other sections name no place
    assert named_tests.named_files(cfg, "demo") == {"tests/test_cli.py": ["DEMO-R-002"]}

    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "spec")
    set_marker(cfg, "spec-approved/demo")
    (r.root / "tests/test_new.py").write_text("def test_sub() -> None:\n    assert 1\n", "utf-8")
    res = named_tests.check(adapter, "demo")
    assert res.status == "FAIL", res.output
    assert "NOT TOUCHED (the spec names this file for the test of DEMO-R-002" in res.output
    assert "not in another file): tests/test_cli.py\n" in res.output
    (r.root / "tests/test_cli.py").write_text("def test_sub() -> None:\n    assert 1\n", "utf-8")
    res = named_tests.check(adapter, "demo")
    assert res.status == "PASS" and "touched: tests/test_cli.py (DEMO-R-002)" in res.output
