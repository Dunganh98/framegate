"""A plan step may not name an existing code file outside the source and test folders."""

from __future__ import annotations

from conftest import PLAN2, DemoRepo
from framegate.adapters import get_adapter
from framegate.checks import plan_files
from framegate.config import load_config


def test_a_step_that_changes_code_outside_the_source_folders_is_refused(
    demo_repo: DemoRepo,
) -> None:
    r = demo_repo
    (r.root / "tools").mkdir()
    (r.root / "tools" / "ingest.py").write_text("X = 1\n", encoding="utf-8")
    adapter = get_adapter(load_config(r.root))
    plan = PLAN2 + "Read naive timestamps in `tools/ingest.py`; tests in tests/test_calc.py.\n"
    (found,) = plan_files.outside_problems(adapter, plan)
    assert found.startswith("plan.md step 2 changes tools/ingest.py") and "Add tools" in found
    assert plan_files.outside_problems(adapter, PLAN2 + "Also docs/guide.md.\n") == []
