"""`agentflow spec-check`: rules in design.md without a spec ID, and "Assigned as" code that is
not in the file the spec names. Both from the M5-moment-scan retro."""

from __future__ import annotations

from agentflow.checks import spec_lint
from agentflow.config import load_config
from agentflow.orchestrator import package_text
from conftest import DemoRepo

DESIGN = """\
# Demo — Design

## Error handling
| Situation | Exception / result, where raised, what callers do | Spec ID |
|---|---|---|
| a bool argument | `TypeError` from `add`, propagates | DEMO-E-001 |
| several threads, one job fails | the first failing job by job order is rethrown | |
| a float argument | `TypeError` | DEMO-E-099 |
| | | |

## Command-line surface
| Option | Default | Given without a value | Bad value: message + exit code | Spec ID |
|---|---|---|---|---|
| `--json <path>` | none | "missing path", exit 2 | n/a | covered by DEMO-R-001 |
| `--threads N` | all cores | exit 2 | "not a number", exit 2 | see the text |
"""

EXISTING = """\

## Existing data this spec reads
| Words in the spec | Exact member / event | Set where (file: method) | Assigned as (code, copied) |
|---|---|---|---|
| his number | `Event.player` | events.py: record | `player = shooter.index` |
| his id | `Event.player` | src/demo/events.py: record | `player = shooter.player_id` |
| the flag | `Event.done` | events.py | n/a: never assigned, a constant |
| the score | `Event.score` | events.py: record | |
| the clock | `Clock.now` | clock.py: tick | `now += 1` |
| or-ed | `Event.any` | events.py | `a \\| b` and `x || y` |
| | | | |
"""

EVENTS = """\
def record(shooter):
    player   =   shooter.index
    any_ = a | b
    return x || y
"""


def spec(r: DemoRepo, name: str) -> str:
    return (r.root / "specs" / "demo" / name).read_text(encoding="utf-8")


def test_table_rows() -> None:
    assert spec_lint.split_row("| a | `x | y` | c \\| d | |") == ["a", "`x | y`", "c | d", ""]
    text = (
        "## One\n<!-- | not | a table |\n|---|---| -->\n| A | B (hint) |\n|---|:--|\n| 1 | 2 |\n\n"
    )
    text += "prose | with a pipe\n\n## Two\n| C |\n|---|\n"
    one, two = spec_lint.tables(text)
    assert (one.section, one.header, one.rows) == ("One", ["A", "B (hint)"], [["1", "2"]])
    assert (two.section, two.rows) == ("Two", [])
    assert one.column("b") == 1 and one.column("zzz") is None


def test_error_rules_need_a_spec_id(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    assert spec_lint.problems(cfg, "demo") == []  # the demo spec has no such tables
    (r.root / "specs/demo/design.md").write_text(DESIGN, encoding="utf-8")
    found = spec_lint.spec_id_problems(cfg, "demo")
    assert len(found) == 3, found
    assert found[0].startswith(
        'design.md, Error handling, "several threads, one job fails": no spec ID.'
    )
    assert found[1] == (
        'design.md, Error handling, "a float argument": DEMO-E-099 is not an ID of requirements.md'
    )
    assert found[2].startswith('design.md, Command-line surface, "`--threads N`": no spec ID.')


def test_assigned_as_is_looked_up_in_the_code(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    (r.root / "src/demo/events.py").write_text(EVENTS, encoding="utf-8")
    req = r.root / "specs/demo/requirements.md"
    req.write_text(spec(r, "requirements.md") + EXISTING, encoding="utf-8")
    found = spec_lint.assigned_as_problems(cfg, "demo")
    assert len(found) == 3, found
    # spacing differs from the code: fine; the wrong member is not in the file
    assert found[0].startswith(
        'requirements.md, Existing data this spec reads, "his id": '
        "`player = shooter.player_id` is not in src/demo/events.py."
    )
    assert found[1].startswith(
        'requirements.md, Existing data this spec reads, "the score": "Assigned as" is empty.'
    )
    assert found[2].startswith(
        'requirements.md, Existing data this spec reads, "the clock": no source file found '
        'for "Set where" ("clock.py: tick")'
    )


def test_a_spec_from_the_templates_is_clean(demo_repo: DemoRepo) -> None:
    """The empty rows of a fresh template copy are not problems, and the templates have the
    columns the checks read."""
    r = demo_repo
    for name in ("requirements.md", "design.md"):
        text = package_text("templates", "specs", "_template", name)
        (r.root / "specs/demo" / name).write_text(text, encoding="utf-8")
    assert spec_lint.problems(load_config(r.root), "demo") == []
    design = spec_lint.tables(spec(r, "design.md"))
    assert [t.section for t in design if t.column("spec id") is not None] == [
        "Error handling",
        "Command-line surface",
    ]
    existing = [t for t in spec_lint.tables(spec(r, "requirements.md")) if t.column("assigned as")]
    assert len(existing) == 1 and existing[0].column("set where") == 2


def test_approve_spec_refuses_until_fixed_or_forced(demo_repo: DemoRepo, capsys) -> None:
    r = demo_repo
    design = r.root / "specs/demo/design.md"
    before = design.read_text(encoding="utf-8")
    design.write_text(DESIGN, encoding="utf-8")
    assert r.af("spec-check", "demo") == 1
    assert "Spec check: 3 problem(s)" in capsys.readouterr().out
    assert r.af("approve-spec", "demo") == 1
    err = capsys.readouterr()
    assert "Spec check: 3 problem(s); fix the spec" in err.out + err.err
    assert not [e for e in r.events() if e["kind"] == "spec_approved"]
    check = [e for e in r.events() if e["kind"] == "spec_check"][-1]
    assert check["forced"] is False and len(check["problems"]) == 3

    assert r.af("approve-spec", "demo", "--force") == 0
    assert [e for e in r.events() if e["kind"] == "spec_check"][-1]["forced"] is True
    assert [e for e in r.events() if e["kind"] == "spec_approved"]

    design.write_text(before, encoding="utf-8")
    assert r.af("spec-check", "demo") == 0
    assert capsys.readouterr().out.strip().endswith("Spec check: OK")
