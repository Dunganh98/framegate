"""The escaped-defect log (`framegate defect`), its numbers in `framegate stats`, and the
open defects a later task's retro input lists."""

from __future__ import annotations

import json

import pytest

from conftest import DemoRepo, git
from framegate import defects, stats
from framegate.adapters import get_adapter
from framegate.config import load_config


def accept(repo: DemoRepo, *tasks: str) -> None:
    """Metrics rows: the tasks passed their final gate."""
    path = repo.root / "metrics" / "workflow_metrics.csv"
    path.parent.mkdir(exist_ok=True)
    rows = "".join(f"2026-11-01,{t},0,0,0,0,1.0,0,0\n" for t in tasks)
    path.write_text("date,feature,human_rejections\n" + rows, encoding="utf-8")


def test_a_defect_is_recorded_against_an_accepted_task_and_committed(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    accept(r, "alpha")
    args = ["defect", "alpha", "--what", "the lock | is kept after a crash", "--severity", "high"]
    assert r.af(*args, "--found-by", "user", "--file", "src\\demo\\lock.py", "--id", "A-R-001") == 0
    assert (
        "Recorded in metrics/escaped_defects.md: X1 [open, high] alpha:" in capsys.readouterr().out
    )
    (d,) = defects.load(cfg)
    assert (d.id, d.task, d.status, d.found_by) == ("X1", "alpha", "open", "user")
    assert d.what == "the lock | is kept after a crash"
    assert (d.files, d.ids) == (["src/demo/lock.py"], ["A-R-001"])
    assert "escaped defect X1" in git(r.root, "log", "-1", "--format=%s")
    assert git(r.root, "status", "--porcelain", "--", "metrics/escaped_defects.md") == ""

    # a fix given with the record closes it at once; the id counts on
    assert r.af("defect", "alpha", "--what", "second", "--fix", "abc123") == 0
    assert [(x.id, x.status, x.fix) for x in defects.load(cfg)][1] == ("X2", "fixed", "abc123")
    assert r.af("defect", "--close", "X1", "--fix", "alpha-fix") == 0
    assert [(x.status, x.fix) for x in defects.load(cfg)][0] == ("fixed", "alpha-fix")
    capsys.readouterr()
    assert r.af("defect") == 0
    assert capsys.readouterr().out.startswith("0 open escaped defect(s)")
    assert r.af("defect", "--all") == 0
    assert "2 escaped defect(s)" in capsys.readouterr().out


def test_an_unknown_task_or_an_empty_text_is_refused(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    accept(r, "alpha")
    assert r.af("defect", "nope", "--what", "x") == 1
    assert "'nope' is no accepted task of this repository (accepted: alpha)" in (
        capsys.readouterr().out
    )
    assert r.af("defect", "alpha", "--what", " ") == 1
    assert r.af("defect", "--close", "X9") == 1
    assert defects.load(load_config(r.root)) == []
    assert r.af("defect", "nope", "--what", "x", "--force") == 0


def test_stats_counts_defects_per_accepted_task_and_in_total(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    accept(r, "alpha", "beta")
    events = [
        {"ts": "2026-11-01T10:00:00", "feature": f, "kind": "agent_run", "phase": "TESTS"}
        for f in ("alpha", "beta")
    ]
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    (cfg.out_dir / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    lines: list[str] = []
    assert stats.run(cfg, echo=lines.append) == 0
    assert "Escaped defects: 0 in 2 accepted task(s)." in lines

    defects.add(cfg, "alpha", "one")
    defects.add(cfg, "alpha", "two", fix="beta")
    # the defect's own event, logged long after the task, is not the task's time
    with open(cfg.out_dir / "events.jsonl", "a", encoding="utf-8") as fh:
        late = {"ts": "2026-12-24T10:00:00", "feature": "alpha", "kind": "escaped_defect"}
        fh.write(json.dumps(late) + "\n")
    lines.clear()
    assert stats.run(cfg, echo=lines.append) == 0
    head, alpha, beta = (x.split() for x in lines[:3])
    col = head.index("defects")
    assert alpha[0] == "alpha" and beta[0] == "beta"
    text = "\n".join(lines)
    assert "Escaped defects: 2 in 2 accepted task(s) (1 open); 1 task(s) with at least one." in text
    assert "  alpha: 2 (X1, X2)" in text and col > 0
    by_name = {s.feature: s for s in stats.collect(cfg)}
    assert len(by_name["alpha"].defects) == 2 and by_name["beta"].defects == []
    assert by_name["alpha"].ended == "2026-11-01T10:00:00"
    assert by_name["alpha"].as_dict()["escaped_defects"] == 2

    lines.clear()
    assert stats.run(cfg, "alpha", echo=lines.append) == 0
    at = lines.index("Escaped defects: 2")
    assert lines[at + 1].startswith("  X1 [open, medium] alpha: one")


def test_open_defects_on_the_same_files_or_ids_reach_the_retro_input(
    demo_repo: DemoRepo,
) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    defects.add(cfg, "alpha", "on a file", files=["src/demo/__init__.py"], force=True)
    defects.add(cfg, "alpha", "on a folder", files=["src/demo/"], force=True)
    defects.add(cfg, "alpha", "on an id", ids=["DEMO-E-001"], force=True)
    defects.add(cfg, "alpha", "elsewhere", files=["src/other.py"], ids=["X-R-001"], force=True)
    defects.add(cfg, "alpha", "fixed", files=["src/demo/__init__.py"], fix="c1", force=True)
    defects.add(cfg, "demo", "against this task", force=True)

    hits = defects.touching(cfg, ["src/demo/__init__.py"], ["DEMO-E-001"])
    assert [d.id for d in hits] == ["X1", "X2", "X3"]
    assert defects.retro_section(cfg, "demo", [], []).count("\n- ") == 1  # X6 only

    # the task's own files and IDs, read from git and the spec
    (r.root / "src/demo/__init__.py").write_text("def add(a, b):\n    return a + b\n", "utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "demo: step 1")
    text = defects.task_section(get_adapter(cfg), "demo")
    assert text.startswith("## Open escaped defects")
    listed = [x.split()[1] for x in text.splitlines() if x.startswith("- ")]
    assert listed == ["X1", "X2", "X3", "X6"]
