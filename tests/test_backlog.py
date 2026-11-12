"""The retro backlog: accepted proposals survive the cleanup, with a status and a count of the
features whose retro raised them."""

from __future__ import annotations

import pytest

from conftest import DemoRepo, git
from framegate import backlog
from framegate.config import load_config
from framegate.orchestrator import EXIT_GATE

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"

RETRO_A = """\
# Retro proposals: alpha

## Brief

1. A number was copied from an older test -> the spec says how each number was measured
   (recommend: take)
2. A typo in a stub cost one attempt -> nothing (recommend: skip)
3. The output layout was left open -> one literal example line (recommend: fold into 1)
4. Runner code is never tested -> the test project references it (recommend: take)

## 1. Numbers copied from an older test

**Problem and evidence.** Q1 and Q3.

```diff
 ## Test data and fixtures
+| ID | Data | Measured with |
```

**Scope:** framework (template column).

## 2. A typo

**Scope:** project.

## 4. Runner code is never tested

Fix: a project reference.

**Scope:** project.

## Not acted on

- A11: an implementation detail.
"""

RETRO_B = """\
# Retro proposals: beta

## Brief

1. The stub typo again, two attempts this time -> a check that stubs compile
   (recommend: take; repeats B2)
2. Numbers were copied again -> as B1 (recommend: take; repeats B1)
3. A new thing -> a new fix (recommend: take)

## 1. The stub typo again

**Scope:** framework.

## 3. A new thing

**Scope:** project
"""


def put(r: DemoRepo, feature: str, text: str) -> None:
    d = r.root / "out" / feature
    d.mkdir(parents=True, exist_ok=True)
    (d / "retro_proposals.md").write_text(text, encoding="utf-8")


def rows(r: DemoRepo) -> dict[str, backlog.Item]:
    return {i.id: i for i in backlog.load(load_config(r.root))}


def test_proposals_of_a_retro() -> None:
    found = backlog.parse_proposals(RETRO_A)
    assert [(p.num, p.recommend, p.fold_into, p.scope) for p in found] == [
        (1, "take", None, "framework"),  # the "## Test data" heading in the diff is not a section
        (2, "skip", None, "project"),
        (3, "fold", 1, "?"),
        (4, "take", None, "project"),
    ]
    assert found[0].text == (
        "A number was copied from an older test -> the spec says how each number was measured"
    )
    again = backlog.parse_proposals(RETRO_B)
    assert [p.repeats for p in again] == [2, 1, None]
    assert again[0].text.endswith("a check that stubs compile")


def test_keep_then_track(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    put(r, "alpha", RETRO_A)
    assert r.af("keep", "alpha", "5") == 1  # no such proposal
    assert r.af("keep", "alpha", "take") == 0
    out = capsys.readouterr().out
    assert "Kept: B1 [open, framework] A number was copied" in out
    assert "Not kept (counted if they come back): B2" in out
    got = rows(r)
    assert [(i.id, i.status, i.scope, i.seen) for i in got.values()] == [
        ("B1", "open", "framework", ["alpha"]),
        ("B2", "skipped", "project", ["alpha"]),
        ("B3", "open", "project", ["alpha"]),  # proposal 4; the folded one has no row
    ]
    assert got["B3"].detail == "metrics/retro/alpha.md #4"
    # tracked and committed: the cleanup of out/ cannot lose it
    assert (r.root / "metrics/retro/alpha.md").read_text(encoding="utf-8") == RETRO_A
    assert git(r.root, "status", "--porcelain", "--", "metrics").strip() == ""
    assert "alpha: retro backlog (B1, B3)" in git(r.root, "log", "-1", "--format=%s")
    # the same pick again changes nothing; another pick changes this feature's rows
    assert r.af("keep", "alpha", "1,4") == 0 and len(rows(r)) == 3
    assert r.af("keep", "alpha", "1") == 0
    assert [i.status for i in rows(r).values()] == ["open", "skipped", "skipped"]
    assert r.af("keep", "alpha", "take") == 0

    assert r.af("backlog", "done", "B1", "--note", "PR #12") == 0
    assert (rows(r)["B1"].status, rows(r)["B1"].note) == ("applied", "PR #12")
    capsys.readouterr()
    assert r.af("backlog") == 0
    assert capsys.readouterr().out.splitlines() == [
        "1 open proposal(s) in metrics/retro_backlog.md:",
        "- B3 [open, project] Runner code is never tested -> the test project references it",
    ]
    assert r.af("backlog", "show", "B3") == 0
    shown = capsys.readouterr().out
    assert "Detail: metrics/retro/alpha.md #4" in shown and "Fix: a project reference." in shown
    assert r.af("backlog", "done", "B9") == 1

    # the next feature's retro: its input lists the backlog; what it sees again is counted
    section = backlog.retro_section(cfg)
    assert section.startswith("## Backlog (proposals of earlier retros")
    assert "- B2 [skipped, project] A typo in a stub cost one attempt -> nothing" in section
    assert backlog.note_repeats(cfg, "beta", RETRO_B) == ["B2", "B1"]
    assert backlog.note_repeats(cfg, "beta", RETRO_B) == []
    put(r, "beta", RETRO_B)
    assert r.af("keep", "beta", "all") == 0
    got = rows(r)
    # a skipped one that came back is open now; an applied one that came back too
    assert (got["B2"].status, got["B2"].seen) == ("open", ["alpha", "beta"])
    assert (got["B1"].status, got["B1"].seen) == ("open", ["alpha", "beta"])
    assert "again: metrics/retro/beta.md #2" in got["B1"].note and "PR #12" in got["B1"].note
    assert (got["B4"].text, got["B4"].scope) == ("A new thing -> a new fix", "project")
    capsys.readouterr()
    assert r.af("backlog", "--scope", "project") == 0
    listed = capsys.readouterr().out.splitlines()
    assert listed[1].startswith("- B2 [open, project, seen 2x] A typo")  # most often seen first
    assert r.af("backlog", "drop", "B4", "B3") == 0
    assert r.af("backlog", "--all") == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("- B4 [dropped, project]")


def test_the_retro_sees_the_backlog_and_repeats_are_counted(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    put(r, "alpha", RETRO_A)
    assert r.af("keep", "alpha", "none") == 0
    r.defer_tests_gate()
    r.scenario(retro_brief=["1. The typo again -> a check (recommend: take; repeats B2)"])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == 0
    retro_input = (r.root / "out/demo/retro_input.md").read_text(encoding="utf-8")
    assert "- B2 [skipped, project] A typo in a stub cost one attempt -> nothing" in retro_input
    assert rows(r)["B2"].seen == ["alpha", "demo"] and rows(r)["B2"].status == "skipped"
    assert git(r.root, "status", "--porcelain", "--", "metrics").strip() == ""
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert "seen again, counted in the backlog: B2" in log
    assert r.af("keep", "demo", "take") == 0
    assert rows(r)["B2"].status == "open"
    kept = [e for e in r.events() if e["kind"] == "retro_kept"][-1]
    assert kept["kept"] == ["B2"] and kept["feature"] == "demo"
