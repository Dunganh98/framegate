"""Which tests a step gate runs and what is kept when a run cannot pass as a whole: a test
that spans plan steps runs at its latest step, a batched run is judged step by step, and a
task with nothing built has no silent "leave it parked"."""

from __future__ import annotations

import pathlib

import pytest

from conftest import DemoRepo, git
from framegate import defaults, state
from framegate.adapters.base import TestCase, TestRun
from framegate.gates import gate_report, later_spanning, spanning_tests, without_later
from framegate.orchestrator import EXIT_GATE, NOTHING_BUILT_ASK
from test_checks import adapter_for, write
from test_e2e import FAST, gate, phases, read

PLAN = """\
## Step 1: add
Covers: FT-R-001, FT-E-001

## Step 2: neg
Covers: FT-R-002
Depends on: Step 1
"""
SRC = """\
def add(a: int, b: int) -> int:
    return a + b


def neg(a: int) -> int:
    raise NotImplementedError
"""
TESTS = """\
import pytest

from pkg.a import add, neg


@pytest.mark.req("FT-R-001")
def test_add() -> None:
    assert add(1, 2) == 3


@pytest.mark.req("FT-R-001", "FT-R-002")
def test_add_then_neg() -> None:
    assert neg(add(1, 2)) == -3


@pytest.mark.req("FT-R-002")
def test_neg() -> None:
    assert neg(1) == -1
"""
SPAN = "tests/test_f.py::test_add_then_neg"
GATES = {"commands": {"lint": [], "format": [], "typecheck": []}, "mutation_tool": "none"}


def repo(root: pathlib.Path) -> None:
    write(root, "specs/f/requirements.md", "FT-R-001 FT-E-001 FT-R-002")
    write(root, "specs/f/plan.md", PLAN)
    write(root, "src/pkg/a.py", SRC)
    write(root, "tests/test_f.py", TESTS)
    write(root, "pyproject.toml", "[tool.pytest.ini_options]\npythonpath = ['src']\n")


def test_a_test_runs_at_the_latest_plan_step_of_its_ids(tmp_path: pathlib.Path) -> None:
    repo(tmp_path)
    a = adapter_for(tmp_path, {"gates": GATES, "protected": {"paths": []}})
    step1 = ["FT-R-001", "FT-E-001"]

    assert spanning_tests(a, "f") == {SPAN: [1, 2]}
    assert later_spanning(a, "f", step1) == [SPAN]
    assert later_spanning(a, "f", [*step1, "FT-R-002"]) == []  # its latest step: it runs

    # the gate of step 1: without the list the test of step 2's code fails it
    state.set_reqs(a.cfg, step1)
    assert "tests (current + earlier steps)" in gate_report(a, "", "step").failed
    state.set_later_tests(a.cfg, later_spanning(a, "f", step1))
    rep = gate_report(a, "", "step")
    assert "tests (current + earlier steps)" not in rep.failed, rep.report
    assert "left out, they also need a later step: 1" in rep.report
    state.clear_reqs(a.cfg)
    assert state.read_later_tests(a.cfg) == []


def test_only_the_failures_of_the_later_tests_are_left_out() -> None:
    cases = [
        TestCase("tests.test_f::test_add_then_neg", "failed"),
        TestCase("tests.test_f::test_add", "failed"),
    ]
    run, off = without_later(TestRun("failed", 1, "", cases), [SPAN])
    assert off == ["tests.test_f::test_add_then_neg"] and run.status == "failed"
    run, off = without_later(TestRun("failed", 1, "", cases[:1]), [SPAN])
    assert run.status == "passed" and [c.outcome for c in run.cases or []] == ["skipped"]
    same = TestRun("failed", 1, "", None)
    assert without_later(same, [SPAN]) == (same, [])


def test_a_spanning_test_does_not_fail_the_gate_of_the_earlier_step(demo_repo: DemoRepo) -> None:
    """Seen live: tests tagged with an ID of step 1 and one of step 4 ran at the gate of step
    1, which no code of step 1 could pass."""
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.defer_tests_gate()
    r.scenario(spanning=True)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"

    gates = [(e["step"], e["attempt"], e["passed"]) for e in r.events() if e["kind"] == "gate"]
    assert gates == [("step 1 - add", 1, True), ("step 2 - neg", 1, True)]
    assert r.progress()["completed_steps"] == [1, 2] and not r.progress()["parked"]
    where = "tests/test_demo.py::test_add_then_neg"
    checks = read(r, "out/demo/test_checks.md")
    assert "### Tests that span plan steps (note, not blocking): 1" in checks
    line = f"- {where}: spec IDs of steps 1, 2; runs at the gate of step 2"
    assert line in checks and line in read(r, "out/demo/tests_gate.md")
    step1, step2 = (c["prompt"] for c in r.calls() if c["phase"] == "IMPLEMENT")
    assert "Tests left out of your step gate (they also carry spec IDs of a later plan" in step1
    assert f"whose gate runs them): {where}." in step1
    assert "Tests left out of your step gate" not in step2


def batched(r: DemoRepo, **scenario: object) -> None:
    r.two_steps()
    r.add_toml(FAST)
    r.defer_tests_gate()
    r.set_toml("agents", "step_batch_ids", "15")
    r.scenario(**scenario)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"


def test_a_batched_run_is_judged_step_by_step(demo_repo: DemoRepo) -> None:
    """One run built steps 1 and 2 and parked on a question about step 2: step 1 passes its
    own gate and is kept, only step 2 is parked, and the review sees what was built."""
    r = demo_repo
    batched(r, park_steps=[2])

    prog = r.progress()
    assert prog["completed_steps"] == [1]
    assert [(p["label"], p["step"]) for p in prog["parked"]] == [("step 2 - neg", 2)]
    assert "steps" not in prog["parked"][0]
    (split,) = [e for e in r.events() if e["kind"] == "batch_split"]
    assert (split["label"], split["kept"], split["left"]) == (
        "steps 1, 2",
        "step 1 - add",
        "step 2 - neg",
    )
    assert "demo: step 1 - add" in git(r.root, "log", "--format=%s").splitlines()
    assert phases(r) == ["TESTS", "TEST_REVIEW", "IMPLEMENT", "REVIEW", "SUMMARY"]
    facts = read(r, "out/demo/final_facts.md")
    assert "| step 1 - add | done | 0 |" in facts
    assert "| step 2 - neg | PARKED (no reasonable assumption possible (question)) | 0 |" in facts
    # something is built: the parked step keeps its default
    assert defaults.NO_DEFAULT not in facts

    (r.root / "out/demo/answers.md").write_text("1. neg(0) is 0.\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    assert r.progress()["completed_steps"] == [1, 2] and not r.progress()["parked"]
    redo = [e for e in r.events() if e["kind"] == "redo_result"]
    assert [(e["label"], e["passed"]) for e in redo] == [("step 2 - neg", True)]


def test_one_contested_test_does_not_stop_a_batched_run(demo_repo: DemoRepo) -> None:
    """The run contests one frozen test: the test is left out of the gate, the code of both
    steps is kept as provisional and reviewed, and the final gate shows the proposed fix."""
    r = demo_repo
    batched(r, contest_steps=[1])

    prog = r.progress()
    assert prog["completed_steps"] == [1, 2] and not prog["parked"]
    (rec,) = prog["provisional"]
    assert rec["label"] == "steps 1, 2"
    assert rec["contested"] == ["test_demo.test_add_refuses_bool_contested"]
    assert phases(r) == ["TESTS", "TEST_REVIEW", "IMPLEMENT", "REVIEW", "SUMMARY"]
    facts = read(r, "out/demo/final_facts.md")
    assert "## Provisional steps (code kept, test fixes proposed): 1" in facts
    assert "Proposed test fixes (the code of these steps is kept as provisional)" in facts


def test_a_task_with_nothing_built_has_no_silent_leave_it_parked(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """Seen live: every step parked on one wrong test, "ok" took the defaults, and the task
    ended with nothing built."""
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.defer_tests_gate()
    r.scenario(park_steps=[1])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    assert phases(r) == ["TESTS", "TEST_REVIEW", "IMPLEMENT"]  # nothing built: no review

    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    brief = capsys.readouterr().out
    assert brief.count(defaults.NO_DEFAULT) == 1  # step 2 only waits for step 1
    assert "A: leave it parked: the task ends with nothing built (no change) / B: redo it" in brief

    answers = r.root / "out/demo/answers.md"
    for silent in ("1. Default: leave it parked.\n2. ok\n", "1. ok\n2. ok\n", "2. ok\n"):
        answers.write_text(silent, encoding="utf-8")
        assert r.af("answer", "demo") == 1
        err = capsys.readouterr().err
        assert "name no option of their question" in err or "have no default" in err, err
        assert gate(r) == "final"

    # the owner's own words are an answer: the step is redone with them
    answers.write_text("1. Ints only.\n2. ok\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    (ev,) = [e for e in r.events() if e["kind"] == "answer"]
    assert (ev["items"][0]["a"], ev["items"][0]["option"]) == ("B: Ints only.", "B")
    assert [p["label"] for p in r.progress()["redo"]] == ["step 1 - add", "step 2 - neg"]


def test_leaving_it_parked_with_nothing_built_is_a_choice(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.two_steps()
    r.defer_tests_gate()
    r.scenario(park_steps=[1])
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and gate(r) == "final"
    assert defaults.takes_default(NOTHING_BUILT_ASK, "A") and not defaults.takes_default(
        NOTHING_BUILT_ASK, "B: do it"
    )

    (r.root / "out/demo/answers.md").write_text("1. A\n2. ok\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    assert r.progress()["phase"] == "RETRO"  # approved as it stands, by the owner's choice
