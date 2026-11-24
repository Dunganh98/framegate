"""Defaults change nothing (defaults.py) and what a task leaves for later goes to one list,
`.framegate/deferred.md`, that later tasks see (deferred.py)."""

from __future__ import annotations

import pytest

from conftest import PLAN, REQUIREMENTS, DemoRepo
from framegate import deferred, doc_proposals
from framegate.config import load_config
from framegate.defaults import defers_change, gist, takes_default
from framegate.deferred import Item
from framegate.scaffold import package_text

SPLIT = (
    "[structure report] src/a.py has 9 lines. Split it? A: leave it for later (default) / B: split"
)


@pytest.mark.parametrize(
    ("question", "answer", "taken"),
    [
        (SPLIT, "A", True),
        (SPLIT, "A: leave the split for later", True),
        (SPLIT, "B", False),
        (SPLIT, "split it now", False),
        (SPLIT, "ok", True),
        (SPLIT, "Default: leave it", True),
        (SPLIT, "Same as item 1: leave it", True),
        (SPLIT, "No answer needed (left out of the brief: a note)", True),
        (SPLIT, "", True),
        ("x" + doc_proposals.ASK, "B", True),
        ("x" + doc_proposals.ASK, "A", False),
        ("x" + doc_proposals.CONTRA_ASK, "B: later", True),
        ("x" + doc_proposals.CONTRA_ASK, "C: refuse floats", False),
        ("Seed? Default: keep seed 7.", "keep seed 7", True),
        ("Seed? Default: keep seed 7.", "use seed 12 instead", False),
        ("Seed? Default: keep seed 7.", "keep it\nand log the seed", False),
    ],
)
def test_an_answer_takes_the_default_or_not(question: str, answer: str, taken: bool) -> None:
    assert takes_default(question, answer) is taken


def test_a_default_that_leaves_a_change_for_later() -> None:
    assert defers_change(SPLIT) and not defers_change("Seed? Default: keep seed 7.")
    assert gist(SPLIT) == "src/a.py has 9 lines. Split it?"


def test_one_entry_per_item_with_a_stable_id(demo_repo: DemoRepo) -> None:
    cfg = load_config(demo_repo.root)
    assert deferred.add(cfg, "t1", []) == [] and not (demo_repo.root / deferred.PATH).exists()
    split = Item("refactor", "split it (623 lines, limit 400)", ["src/demo/state.py"])
    added = deferred.add(cfg, "t1", [split, Item("tests", "make x run", [], "offered")])
    assert [e.id for e in added] == ["D1", "D2"]
    text = (demo_repo.root / deferred.PATH).read_text(encoding="utf-8")
    assert "- D1 [refactor] src/demo/state.py: split it (623 lines, limit 400). From t1, " in text
    assert text.rstrip().endswith(": offered.") and "- D2 [tests] make x run. From t1, " in text
    # the same item again: its "From" part is updated, no second entry
    (again,) = deferred.add(cfg, "t2", [split])
    assert again.id == "D1" and again.origin.startswith("t2, ")
    assert [e.id for e in deferred.read(cfg)[0]] == ["D1", "D2"]
    assert deferred.listing(cfg, "state.py") == [again.line()]


def test_a_spec_resolves_an_entry_and_the_plan_names_its_file(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    deferred.add(cfg, "t1", [Item("refactor", "split it", ["src/demo/__init__.py"])])
    deferred.add(cfg, "t1", [Item("docs", "one more line", ["docs/x.md"])])
    plan = r.root / "specs/demo/plan.md"
    plan.write_text(PLAN.replace("Build add().", "Build add() in src/demo/__init__.py."), "utf-8")
    capsys.readouterr()
    r.af("spec-check", "demo")
    out = capsys.readouterr().out
    assert "Deferred work on these files: include it in this task" in out
    assert "- D1 [refactor] src/demo/__init__.py: split it." in out and "D2" not in out
    # an unknown id fails the spec check; a known one is removed at the end of the task
    req = r.root / "specs/demo/requirements.md"
    req.write_text(REQUIREMENTS + "\nResolves: D1, D7\n", encoding="utf-8")
    assert deferred.spec_problems(cfg, "demo") == [
        f"requirements.md: Resolves: D7 is no open entry of {deferred.PATH} (framegate "
        "deferred lists them)"
    ]
    for name in ("_template", "_template_bugfix", "_template_refactor"):
        text = package_text("templates", f"specs/{name}/requirements.md")
        assert "\nResolves:" in text and deferred.resolves(text) == [], name
    (gone,) = deferred.resolve(cfg, "demo")
    assert gone.id == "D1" and [e.id for e in deferred.read(cfg)[0]] == ["D2"]
    assert deferred.add(cfg, "t3", [Item("bugfix", "y")])[0].id == "D3"  # ids are not reused
    capsys.readouterr()
    assert r.af("deferred") == 0
    assert capsys.readouterr().out.startswith(f"2 open deferred item(s) in {deferred.PATH}:\n- D2")


def test_what_a_task_leaves_for_later(demo_repo: DemoRepo) -> None:
    cfg = load_config(demo_repo.root)
    prog = {
        "deferred": [{"kind": "tests", "text": "a", "files": [], "how": 'answered "later"'}],
        "left_parked": [{"label": "step 2 - neg", "reason": "spec unclear", "step": 2}],
        "left_provisional": [{"label": "step 1 - add", "contested": ["tests/t.py::test_a"]}],
    }
    events = [{"kind": "final_gates", "passed": False, "failed": ["Coverage"]}]
    found = deferred.task_items(cfg, "demo", "feature", prog, events)
    assert [(i.kind, i.how) for i in found] == [
        ("tests", 'answered "later"'),
        ("feature", "left parked"),
        ("tests", "left provisional, its contested tests red"),
        ("tests", "approved with failing checks"),
    ]
    assert found[2].files == ["tests/t.py"]


def test_a_second_entry_about_the_same_thing_is_refused(demo_repo: DemoRepo) -> None:
    """Seen live: the spec-time "later" and the final gate's "later" for one file made two
    entries; one doc place proposed by the spec and by the run made two."""
    cfg = load_config(demo_repo.root)
    rel, doc = "src/demo/state.py", "docs/DESIGN.md"
    first = deferred.add(
        cfg,
        "t1",
        [
            Item("refactor", "split it into one file per concept (598 lines, limit 400)", [rel]),
            Item("docs", f"{doc}, §9 flag lines (`[--x]`): today: a -> proposed: b", [doc]),
        ],
    )
    again = deferred.add(
        cfg,
        "t1",
        [
            Item("refactor", "has 629 lines (limit 400; it had 622). Split it?", [rel]),
            Item(
                "docs",
                f"{doc}, §9 flag lines (`[--x]`, lines ~3 and ~9): today: a -> proposed: c",
                [doc],
            ),
            Item("docs", f"{doc}, §10: today: a -> proposed: c", [doc]),
            Item("tests", "make x run", [rel]),
        ],
    )
    assert [e.id for e in again] == ["D1", "D2", "D3", "D4"] and again[:2] == first
    assert "598 lines" in again[0].text and len(deferred.read(cfg)[0]) == 4


def test_the_letter_decides_and_the_words_after_it_are_a_note() -> None:
    from framegate import defaults

    q = "[summary agent] A bare string. A: leave it (default) / B: keep a lone string."
    assert takes_default(q, "A: leave it.\nBut tell the pump team.")
    assert not takes_default(q, "B\nas discussed")
    assert defaults.chosen(q, "option B: do it") == ("B", "do it")
    assert defaults.chosen(q, "2. the second") == ("B", "the second")
    free = "No new validation in this task: a wrong value stays an error row."
    assert defaults.chosen(q, free) == ("", free)
    assert defaults.unclear(q, free)
    # an option's own words name it (cut short, or with more after them)
    assert defaults.chosen(q, "keep a lone string") == ("B", "")
    assert defaults.chosen(q, "leave it\nbut log it") == ("A", "but log it")
    assert defaults.chosen("X? A: 0 (default) / B: -0", "0") == ("", "0")  # both: no option
    for plain in ("", "ok", "leave it", "Default: leave it (decided in the final brief)", "A"):
        assert not defaults.unclear(q, plain), plain
    assert not defaults.unclear("Seed? Default: keep seed 7.", "use seed 12")  # no options
    assert not defaults.unclear(q, "Approved: apply the proposed fix as written.")
    forced = f"X. A: keep it (no change) / B: change it. {defaults.NO_DEFAULT}"
    assert defaults.unclear(forced, "ok") and defaults.unclear(forced, "")
    assert takes_default(forced, "A: keep") and not takes_default(forced, "B")
    assert not takes_default(forced, "ok") and not takes_default(forced, "Default: keep it")
