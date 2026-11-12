"""Questions on their way to a gate: a repeated question is dropped, an answer is found by the
question as it was asked (the chat rewords it). Both from the M5-moment-scan retro."""

from __future__ import annotations

from conftest import DemoRepo
from framegate.adapters import get_adapter
from framegate.config import load_config
from framegate.orchestrator import EXIT_GATE, Orchestrator
from framegate.retro import match_answer, same_question

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"

Q1 = (
    "Q1: MSC-R-001 says job (8, 6, Bot) gives `SetPieceTakes` = 2, but the standard match has "
    "only ONE record of kind SetPieceTake / FreeKickShot / PenaltyKick; the second set-piece "
    "record is `SetPieceDefend`, which MSC-R-005 does not count. Options: (a) the number is 1 "
    "(keep the MSC-R-005 definition); (b) also count `SetPieceDefend`. Recommended default: "
    "(a). The tests currently assert 1."
)
Q1_AGAIN = (
    "Q1 (repeat): MSC-R-001 says (8, 6, Bot) gives `SetPieceTakes` = 2, but the match has only "
    "ONE record of kind SetPieceTake / FreeKickShot / PenaltyKick (the second set-piece record "
    "is `SetPieceDefend`, not counted by MSC-R-005). Options: (a) the number is 1, keep the "
    "R-005 definition; (b) also count `SetPieceDefend`. Recommended: (a). Tests assert 1."
)
Q2 = (
    "Q2: MSC-R-006 says `HisGoals` counts Goal events whose `Player` is the user. In the engine "
    "`MatchEvent.Player` is the agent index, not the player id. Options: (a) compare to the "
    "user's agent index; (b) compare to the player id. Recommended: (a)."
)
NOTE = (
    "Q3: MSC-R-005 `FreeKickReviews` / `PenaltyReviews`: no R-001 job has one. Resolved "
    "without an answer: the job (39, 10, Bot) has both and is added to the R-005 theory."
)


def test_same_question() -> None:
    assert same_question(Q1_AGAIN, Q1) and same_question(Q1, Q1_AGAIN)
    assert same_question("Q7: " + Q2[4:], Q2)  # only the agent's label differs
    assert not same_question(Q2, Q1)
    assert not same_question(NOTE, Q1) and not same_question(NOTE, Q2)
    # the same words about another spec ID: another question
    assert not same_question(Q1.replace("MSC-R-001", "MSC-R-002").replace("R-005", "R-007"), Q1)
    assert not same_question("", Q1) and not same_question("Yes or no?", "Yes or no, really?")


def test_a_repeated_question_is_collected_once(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    lines: list[str] = []
    orch = Orchestrator(cfg, get_adapter(cfg), echo=lines.append)
    qfile = r.root / "out/demo/questions.md"
    qfile.parent.mkdir(parents=True, exist_ok=True)

    qfile.write_text(f"- {Q1}\n", encoding="utf-8")
    assert orch.collect_questions("demo", "TESTS", "test writer") == 1
    # the next round: the same question again, a new one, and the new one twice
    qfile.write_text(f"- {Q1_AGAIN}\n- {Q2}\n- {Q2}\n", encoding="utf-8")
    assert orch.collect_questions("demo", "TESTS", "test writer") == 1
    assert any("2 repeated question(s) dropped" in x for x in lines)
    # another asker may ask the same thing (its view differs); its own tag is not doubled
    qfile.write_text(f"- [test reviewer] {Q1}\n", encoding="utf-8")
    assert orch.collect_questions("demo", "TEST_REVIEW", "test reviewer") == 1
    qfile.write_text(f"- {Q1_AGAIN}\n", encoding="utf-8")
    assert orch.collect_questions("demo", "TESTS", "test writer") == 0  # nothing new

    kept = orch.open_questions("demo")
    assert [q[:22] for q in kept] == [
        "[test writer] Q1: MSC-",
        "[test writer] Q2: MSC-",
        "[test reviewer] Q1: MS",
    ]
    events = [e for e in r.events() if e["kind"] == "questions"]
    assert [e["repeats"] for e in events] == [0, 2, 0]
    assert (
        events[1]["questions"].startswith("- Q2: MSC-R-006") and "Q1" not in events[1]["questions"]
    )


def test_an_answer_is_found_by_the_question_as_asked() -> None:
    """Seen live: the chat reworded the questions in plain words (no spec IDs, other words), so
    two of five answers were not found although they stood at the question's own number."""
    asked = (
        "[test reviewer] MSC-R-005 (`FreeKickReviews` / `PenaltyReviews`): the spec's fixtures "
        "may contain no free-kick shot or penalty by the user."
    )
    items = [
        {"n": 1, "q": "The set-piece count for seed 8?", "a": "Default: A.", "asked": Q1},
        {
            "n": 2,
            "q": "Whether an always-zero count would pass.",
            "a": "Default: A (already done).",
            "asked": asked,
        },
    ]
    assert (
        match_answer(asked.removeprefix("[test reviewer] "), items) == "Default: A (already done)."
    )
    assert match_answer(Q1, items) == "Default: A."
    old_style = [{k: v for k, v in it.items() if k != "asked"} for it in items]
    assert match_answer(asked, old_style) is None  # what the retro saw before


def test_answer_items_keep_the_question_as_asked(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(questions_in=["TESTS"])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    (r.root / "out/demo/answers.md").write_text(
        "1. Q: Are decimal numbers fine as input?\n   A: No, whole numbers only.\n",
        encoding="utf-8",
    )
    assert r.af("answer", "demo") == 0
    answer = [e for e in r.events() if e["kind"] == "answer"][-1]
    item = answer["items"][0]
    assert item["q"] == "Are decimal numbers fine as input?"
    assert item["asked"] == "[test writer] [TESTS] Should add() accept floats?"
    assert match_answer("[TESTS] Should add() accept floats?", answer["items"]) == (
        "No, whole numbers only."
    )
