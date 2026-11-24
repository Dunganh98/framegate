"""Files over the size limit: named once at the spec check ("later" goes to the deferred
list), and a file the task took over the limit is a line of the final facts."""

from __future__ import annotations

import pytest

from conftest import DemoRepo
from framegate import cli, deferred
from framegate.adapters import get_adapter
from framegate.checks import structure
from framegate.config import load_config

REL = "src/demo/__init__.py"


def test_the_spec_check_names_an_over_limit_plan_file_once(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.set_toml("gates", "max_file_lines", "5")
    plan = r.root / "specs/demo/plan.md"
    plan.write_text(plan.read_text(encoding="utf-8") + f"Change {REL} and docs/x.md.\n", "utf-8")
    cfg = load_config(r.root)
    lines = len((r.root / REL).read_text(encoding="utf-8").splitlines())
    assert structure.plan_over_limit(get_adapter(cfg), "demo") == [(REL, lines)]

    capsys.readouterr()
    cli.main(["spec-check", "demo"])
    out = capsys.readouterr().out
    assert "already over the size limit (5 lines): ask the user once per file" in out
    assert f"- {REL}: {lines} lines" in out
    cli.main(["spec-check", "demo"])
    assert "over the size limit" not in capsys.readouterr().out  # said once

    assert cli.main(["deferred", "--later", REL, "--feature", "demo"]) == 0
    assert capsys.readouterr().out.startswith(
        f"- D1 [refactor] {REL}: split it into one file per concept ({lines} lines, limit 5). "
        "From demo, "
    )
    assert [e.files for e in deferred.read(cfg)[0]] == [[REL]]
    (cfg.feature_out("demo") / deferred.NOTED).unlink()
    assert deferred.over_limit_note(cfg, "demo", [(REL, lines)]) == []  # on the list already
    assert cli.main(["deferred", "--later", "src/none.py"]) == 1


def test_a_file_taken_over_the_limit_is_a_final_fact() -> None:
    shape = {
        "limit": 400,
        "files": {"src/a.py": 412, "src/b.py": 900, "src/c.py": 450},
        "before": {"src/a.py": 380, "src/b.py": 870},
        "grown": ["src/a.py", "src/b.py", "src/c.py"],
    }
    events = [
        {"kind": "gate", "step": "step 1", "passed": True, "grown": ["src/b.py"]},
        {"kind": "gate", "step": "step 2", "passed": False, "grown": ["src/a.py"]},
        {"kind": "gate", "step": "step 2", "passed": True, "grown": ["src/a.py", "src/b.py"]},
        {"kind": "final_gates", "structure": shape},
    ]
    assert structure.crossed_lines(events) == [
        "## Files taken over the size limit: 2",
        "",
        "- src/a.py: this task took it over the size limit (380 -> 412 lines, limit 400), "
        "in step 2",
        "- src/c.py: this task took it over the size limit (new, 450 lines, limit 400)",
    ]
    assert structure.crossed_lines(events[:3]) == []


def test_a_longer_file_over_the_limit_is_deferred_once_and_never_asked(demo_repo: DemoRepo) -> None:
    """The owner's decision: a note in the final summary and a deferred entry, no question."""
    from framegate.orchestrator import Orchestrator
    from framegate.state import log_event

    r = demo_repo
    cfg = load_config(r.root)
    orch = Orchestrator(cfg, echo=lambda s: None)
    shape = {"limit": 5, "files": {REL: 9}, "before": {REL: 7}, "over": [REL], "grown": [REL]}
    log_event(cfg, "demo", "final_gates", passed=True, structure=shape)
    questions = r.root / "out/demo/open_questions.md"
    prog: dict[str, object] = {}
    orch.note_file_sizes("demo", prog)
    orch.note_file_sizes("demo", prog)  # a second summary round: noted once
    assert not questions.exists()
    assert prog["size_noted"] == [REL]
    (item,) = prog["deferred"]  # type: ignore[misc]
    assert item["files"] == [REL] and item["kind"] == "refactor"
    assert item["text"] == "split it into one file per concept (9 lines, limit 5)"
    assert structure.over_limit_lines(r.events())[:3] == [
        "## Files over the size limit made longer: 1",
        "",
        f"- {REL}: over the size limit before this task, now longer (7 -> 9 lines, limit 5)",
    ]
    # answered at spec time, or on the list already: no second entry
    (cfg.feature_out("demo") / deferred.NOTED).write_text(f'["{REL}"]', encoding="utf-8")
    again: dict[str, object] = {}
    orch.note_file_sizes("demo", again)
    assert again == {}
    (cfg.feature_out("demo") / deferred.NOTED).unlink()
    deferred.split_later(cfg, "demo", REL)
    orch.note_file_sizes("demo", again)
    assert again == {} and len(deferred.read(cfg)[0]) == 1


def test_spec_time_sizes_are_counted_as_the_gates_format(demo_repo: DemoRepo) -> None:
    """Seen live: 598 lines at spec time, "it had 622 lines before" at the final gate."""
    from framegate import task_base

    r = demo_repo
    adapter = get_adapter(load_config(r.root))
    src = r.root / REL
    src.write_text(src.read_text(encoding="utf-8") + "\n\nOLD = [1,\n       2]\n", "utf-8")
    plain = len(src.read_text(encoding="utf-8").splitlines())
    assert task_base.formatted_lines(adapter, [REL, "src/none.py"]) == {REL: plain - 1}
    assert len(src.read_text(encoding="utf-8").splitlines()) == plain  # the file is untouched
