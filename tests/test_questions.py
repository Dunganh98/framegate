"""Questions on their way to a gate: a repeated question is dropped, an answer is found by the
question as it was asked (the chat rewords it). Both from the M5-moment-scan retro."""

from __future__ import annotations

from conftest import DemoRepo
from framegate import answered
from framegate.adapters import get_adapter
from framegate.checks import baseline, check_signature
from framegate.config import load_config
from framegate.orchestrator import EXIT_GATE, Orchestrator
from framegate.retro import match_answer, same_question
from framegate.state import log_event

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


GREEN = (
    "RUN-R-004: `test_runner_registry_lists_builtin_steps` in tests/test_runner_registry.py is "
    "carried over from the reference and already passes against the stubs (it only reads the "
    "registry constant), so the red-against-stubs check fails on it. Options: (a) keep the test "
    "as it is; (b) drop it from the port. Recommended: (a)."
)
GREEN_REWORDED = (
    "test_runner_registry_lists_builtin_steps (tests/test_runner_registry.py, RUN-R-004) comes "
    "from the reference unchanged. It is green before any implementation, since the registry "
    "constant already exists, and 'Red against stubs' reports it. Keep the test as it is "
    "(default), or drop it from the port?"
)
GREEN_OTHER = (
    "RUN-R-004: test_runner_registry_lists_builtin_steps (tests/test_runner_registry.py) expects "
    "the step names in registration order. Should the order be alphabetical instead? Default: no."
)


def test_a_reworded_question_about_the_same_test_is_the_same() -> None:
    """Seen live (da-argos): the test writer asked about the same carried-over test in two
    rounds, in other words (70% of the words shared, under the 80% rule): collected twice."""
    assert same_question(GREEN_REWORDED, GREEN) and same_question(GREEN, GREEN_REWORDED)
    # another question about the same test: its name alone does not make it the same
    assert not same_question(GREEN_OTHER, GREEN) and not same_question(GREEN_OTHER, GREEN_REWORDED)
    # the same words about another test, or another spec ID: another question
    other_test = GREEN_REWORDED.replace("lists_builtin_steps", "rejects_unknown_step")
    assert not same_question(other_test, GREEN)
    assert not same_question(GREEN_REWORDED.replace("RUN-R-004", "RUN-R-005"), GREEN)


def test_an_unwinnable_round_is_not_repeated(demo_repo: DemoRepo) -> None:
    """Seen live: "Red against stubs" failed on a test the writer may not change (carried
    over, green by design); the reviewer said OK twice, and the writer was still sent back
    twice and asked its question twice. Now: one round for the failure, before the reviewer
    runs; when it fails the same way again and the reviewer has no objections, it goes to
    the gate, and the reworded question is collected once."""
    r = demo_repo
    r.add_toml(FAST)
    r.defer_tests_gate()
    r.scenario(green_test=True, test_question=[GREEN, GREEN_REWORDED])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    phases = [c["phase"] for c in r.calls()]
    # the failed check goes back once without a review; the same failure again: one review
    assert phases[:4] == ["TESTS", "TESTS", "TEST_REVIEW", "IMPLEMENT"]
    back = [e for e in r.events() if e["kind"] == "test_checks"]
    assert [(e["round"], e["failed_checks"], e["sent_back"]) for e in back] == [
        (1, ["Red against stubs"], True)
    ]
    again = r.calls()[1]["prompt"]
    assert "These mechanical checks failed" in again and "### Red against stubs" in again
    assert "PASSES AGAINST STUB (weak or wrong test): " in again
    reviews = [e for e in r.events() if e["kind"] == "test_review"]
    assert [(e["round"], e["sent_back"]) for e in reviews] == [(1, False)]
    assert reviews[0]["unchanged_checks"] == ["Red against stubs"]
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert "before the test reviewer runs (check round 1 of 2): Red against stubs" in log
    assert "back to the test writer (round 1 of 2)" not in log
    assert "failed exactly as in the round before" in log
    assert "1 repeated question(s) dropped" in log
    asked = [e for e in r.events() if e["kind"] == "questions" and e["who"] == "test writer"]
    assert len(asked) == 1 and asked[0]["questions"].startswith("- RUN-R-004: `test_runner")
    # today's end state: the failed check is an item for the final gate
    deferred = [e for e in r.events() if e["kind"] == "tests_gate_deferred"]
    assert "failed checks: Red against stubs" in deferred[0]["reasons"]


def test_without_check_rounds_the_reviewer_sees_the_failed_check(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.defer_tests_gate()
    r.set_toml("agents", "test_check_rounds", "0")
    r.scenario(green_test=True)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    phases = [c["phase"] for c in r.calls()]
    assert phases[:5] == ["TESTS", "TEST_REVIEW", "TESTS", "TEST_REVIEW", "IMPLEMENT"]
    assert not [e for e in r.events() if e["kind"] == "test_checks"]


def test_a_changed_failure_or_an_objection_still_gets_its_round(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    orch = Orchestrator(cfg, get_adapter(cfg), echo=lambda s: None)
    same = {"check_failures": {"A": "x1"}, "check_failures_before": {"A": "x1"}}
    assert not orch.same_check_failures({**same, "round": 0}, ["A"])  # not seen by the writer
    assert orch.same_check_failures({**same, "round": 1}, ["A"])
    moved = {"round": 1, "check_failures": {"A": "x2"}, "check_failures_before": {"A": "x1"}}
    assert not orch.same_check_failures(moved, ["A"])  # other tests fail now
    new = {**same, "round": 1, "check_failures": {"A": "x1", "B": "y"}}
    assert not orch.same_check_failures(new, ["A", "B"])  # a check that passed before
    assert not orch.same_check_failures({"round": 1}, ["A"])
    assert orch.same_check_failures({**same, "check_rounds": 1}, ["A"])  # sent back by the checks
    # the digest: the problem lines, not the count line, the order or the timings
    one = "3 tests for feature f.\nPASSES AGAINST STUB: t::a\nERROR: t::b in 0.12s"
    two = "4 tests for feature f.\nERROR: t::b in 1.5 s\nPASSES AGAINST STUB: t::a"
    assert check_signature(one) == check_signature(two)
    assert check_signature(one) != check_signature(one.replace("t::a", "t::c"))
    # objections with the same failed check: the writer goes back (it has something to fix)
    r.add_toml(FAST)
    r.scenario(green_test=True, test_review_objections=2)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    reviews = [e for e in r.events() if e["kind"] == "test_review"]
    assert [e["sent_back"] for e in reviews] == [True, True, False]


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


SPLIT = "Split src/demo/state.py by concept."


def test_an_answered_question_is_not_asked_again(demo_repo: DemoRepo) -> None:
    """Seen live: the owner answered "split state.py: later" at the first final gate; the
    reviewer proposed the split again and it was asked at the second."""
    r = demo_repo
    cfg = load_config(r.root)
    orch = Orchestrator(cfg, get_adapter(cfg), echo=lambda s: None)
    orch.ask_split_later("demo", {SPLIT: ("src/demo/state.py", 500)})
    orch.ask_split_later(
        "demo", {"Move the parser out of src/demo/state.py.": ("src/demo/state.py", 500)}
    )
    (asked,) = orch.open_questions("demo")
    log_event(cfg, "demo", "answer", items=[{"n": 1, "asked": asked, "a": "Same as item 2: later"}])
    log_event(cfg, "demo", "answer", items=[{"n": 1, "asked": f"[test writer] {Q2}", "a": "A"}])
    (r.root / "out/demo/open_questions.md").unlink()

    orch.ask_split_later("demo", {SPLIT: ("src/demo/state.py", 510)})
    (r.root / "out/demo/questions.md").write_text(f"- {Q2}\n- {Q1}\n", encoding="utf-8")
    assert orch.collect_questions("demo", "REVIEW", "adversarial reviewer") == 1
    assert [q[:26] for q in orch.open_questions("demo")] == ["[adversarial reviewer] Q1:"]
    events = r.events()
    assert [e["who"] for e in events if e["kind"] == "asked_again"] == [
        "the reviewer",
        "adversarial reviewer",
    ]
    lines = answered.lines(events)
    assert lines[0].startswith("- src/demo/state.py had 510 lines") and lines[0].endswith(
        ": proposed again by the reviewer; your answer 'later' stands"
    )
    assert lines[1].endswith("proposed again by adversarial reviewer; your answer 'A' stands")
    assert answered.section(events)[0] == "## Answered before: 2"
    log_event(cfg, "demo", "answer", items=[])
    assert answered.section(r.events()) == []  # shown at the gate they were asked again for


GREEN_ASKED = (
    "test_reference_constant passes against the stubs: the existing code already accepts the "
    "input. List it under Green against stubs (default) or drop it?"
)


def test_a_green_test_the_writer_asked_about_is_not_sent_back(demo_repo: DemoRepo) -> None:
    """Seen live: an accepted input and an existing error that wins pass against the stubs;
    only the owner can decide, and the writer was sent back twice and asked twice."""
    r = demo_repo
    r.add_toml(FAST)
    r.defer_tests_gate()
    r.scenario(green_test=True, test_question=[GREEN_ASKED])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert [c["phase"] for c in r.calls()][:3] == ["TESTS", "TEST_REVIEW", "IMPLEMENT"]
    assert not [e for e in r.events() if e["kind"] == "test_checks"]
    reviews = [e for e in r.events() if e["kind"] == "test_review"]
    assert [(e["sent_back"], e["failed_checks"]) for e in reviews] == [(False, [])]
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert "Red against stubs: every test that passes before the code is a question" in log
    deferred = [e for e in r.events() if e["kind"] == "tests_gate_deferred"]
    assert "failed checks: Red against stubs" in deferred[0]["reasons"]


def test_green_only() -> None:
    head = "3 tests for feature f.\npasses by design (listed): t::x — scan\nNOTE: the spec lists y"
    assert baseline.green_only(f"{head}\nPASSES AGAINST STUB (weak or wrong test): t.py::C::a[1]")
    assert baseline.green_only("PASSES TODAY (does not show the bug): tests.t.b") == ["b"]
    assert baseline.green_only(f"{head}\nPASSES AGAINST STUB (weak): t::a\nERROR (x): t::b") == []
    assert baseline.green_only("No reproduction test ran.") == []
    assert (
        baseline.green_only("PASSES TODAY: t::a\nThe must-not-change tests produced no report")
        == []
    )
