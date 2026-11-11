"""The final brief's questions: which gate items are asked, decided or left out (and always
asked: parked work), the mapping of the human's answers back to the gate items, the check that
sends a brief back to the PR agent once."""

from __future__ import annotations

from agentflow import brief
from agentflow.retro import parse_answer_items

ITEMS = [
    "[parked] step 2 - neg: the sign of zero",
    "[test reviewer] Should add() accept floats?",
    "[summary agent] Should add() accept floats too?",
    "[test writer] Note: the suite takes 2 min",
]

GOOD = """\
# Final brief

## Questions
1. [2] Should add() accept floats?
   A: no, ints only (default) / B: yes
2. [1] neg(0): 0 or -0?
   A: 0 (default) / B: -0

## Decisions I made
- [4] the slow suite stays as it is
- "his goals" matched by match number (the id is never in match events)

## Left out
- [3] same as [2]
"""


def test_plan_orders_by_the_brief_and_hides_decided_and_repeated_items() -> None:
    p = brief.plan(brief.sections(GOOD), ITEMS, must_ask={1})
    assert p.questions == [
        (2, "Should add() accept floats?\nA: no, ints only (default) / B: yes"),
        (1, "neg(0): 0 or -0?\nA: 0 (default) / B: -0"),
    ]
    assert p.decided == [
        (4, "the slow suite stays as it is"),
        (None, '"his goals" matched by match number (the id is never in match events)'),
    ]
    assert p.left_out == [(3, "same as [2]", 2)] and p.remapped
    assert brief.check_brief(brief.sections(GOOD), len(ITEMS), {1}) == []


def test_parked_work_and_unplaced_items_are_always_asked() -> None:
    text = (
        "## Questions\n1. [2] floats?\n   A: no (default) / B: yes\n"
        "## Decisions I made\n- [1] keep 0\n## Left out\n- [3] answered at the tests gate\n"
        "- [9] out of range\n"
    )
    p = brief.plan(brief.sections(text), ITEMS, must_ask={1})
    # parked item 1 is asked although decided; item 4 is placed nowhere: asked as it is
    assert [t for t, _ in p.questions] == [2, 1, 4]
    assert p.questions[1][1] == ITEMS[0] and p.decided == []
    assert p.left_out == [(3, "answered at the tests gate", None)]
    problems = brief.check_brief(brief.sections(text), len(ITEMS), {1})
    assert any(x.startswith("decision [1]: parked work") for x in problems)
    assert any("gate items [4] are in none of" in x for x in problems)


def test_same_as_an_item_that_is_not_asked_is_asked() -> None:
    text = (
        "## Questions\n1. [1] neg?\n   A: 0 (default) / B: -0\n"
        "## Left out\n- [3] same as [4]\n- [4] a note\n"
    )
    p = brief.plan(brief.sections(text), ITEMS, must_ask=set())
    # item 4 is not asked, so item 3 cannot take its answer: asked as it is
    assert [t for t, _ in p.questions] == [1, 2, 3] and p.left_out == [(4, "a note", None)]
    # an item placed nowhere is asked, so "same as" it stays left out
    text = "## Questions\n1. [1] neg?\n   A: 0 (default) / B: -0\n## Left out\n- [3] same as [4]\n"
    p = brief.plan(brief.sections(text), ITEMS, must_ask=set())
    assert [t for t, _ in p.questions] == [1, 2, 4] and p.left_out == [(3, "same as [4]", 4)]


def test_an_older_brief_keeps_the_gate_numbers() -> None:
    text = "## Items\n1. neg(0)?\n   Default: 0.\n2. floats?\n"
    p = brief.plan(brief.sections(text), ITEMS[:2], must_ask=set())
    assert [t for t, _ in p.questions] == [1, 2] and not p.remapped


def test_answers_mapped_to_the_gate_items() -> None:
    p = brief.plan(brief.sections(GOOD), ITEMS, must_ask={1})
    given = parse_answer_items(
        "1. Q: Should add() accept floats?\n   A: yes\n2. Q: neg(0)\n   A: Default: 0\n"
    )
    mapped = brief.expand_answers(p, given, ITEMS)
    assert mapped.startswith(brief.MAPPED_HEADER + "\n\n1. Q: neg(0)\n   A: Default: 0\n")
    items = {it["n"]: it for it in parse_answer_items(mapped)}
    assert sorted(items) == [1, 2, 3, 4]
    assert items[2]["a"] == "yes"
    assert items[3]["a"] == "Same as item 2: yes" and items[3]["q"] == ITEMS[2]
    assert items[4]["a"] == "Default: the slow suite stays as it is (decided in the final brief)"


def test_unanswered_left_out_items_need_no_answer() -> None:
    text = "## Questions\n1. [1] neg?\n   A: 0 (default) / B: -0\n## Left out\n- [2] a note\n"
    p = brief.plan(brief.sections(text), ITEMS[:2], must_ask=set())
    items = {it["n"]: it for it in parse_answer_items(brief.expand_answers(p, [], ITEMS[:2]))}
    assert items == {
        2: {"n": 2, "q": ITEMS[1], "a": "No answer needed (left out of the brief: a note)"}
    }


def test_check_reports_what_to_rewrite() -> None:
    text = (
        "## Questions\n1. [2] Should add() accept floats?\n   Default: no.\n"
        "2. [3] Should add() accept floats?\n   A: no (default) / B: yes\n"
        "3. [4] Note only: the suite is slow\n   A: ok (default) / B: speed it up\n"
        "4. [2] floats again\n   A: x / B: y\n5. Untagged?\n   A: a / B: b\n"
        "## Left out\n- [1] resolved by the scan\n"
    )
    problems = brief.check_brief(brief.sections(text), len(ITEMS), {1})
    want = [
        "question [2]: has one option",
        "question [3]: the same question as [2]",
        "question [4]: reads as closed or a note",
        "question [2]: gate item 2 is asked twice",
        'question "Untagged?": name the gate item it answers as [N] (1-4',
        "left out [1]: parked work or the provisional steps must be asked",
    ]
    for w in want:
        assert any(x.startswith(w) for x in problems), (w, problems)
    assert len(problems) == len(want)
