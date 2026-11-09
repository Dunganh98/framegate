"""report.py: parsing steps.md assumptions, test_review.md, triage.md; the phase table; the retro
input items of the three-gate flow (A, P, triaged V, turn-cap E)."""

from __future__ import annotations

from agentflow.report import (
    Assumption,
    parse_assumptions,
    parse_test_review,
    parse_triage,
    phase_table,
    since_spec_approval,
    triage_verdict,
)
from agentflow.retro import build, missing_ids


def test_parse_assumptions() -> None:
    text = (
        "## Step 1 — add\nBuilt add().\n- ASSUMPTION [DEMO-E-001]: bool counts as int.\n"
        "* assumption [ ]: none given\n## Review fixes\nAssumptions: none\n"
        "## Step 2 — neg\n- ASSUMPTION [DEMO-R-002] -0 is 0\n- Assumption: not tagged\n"
    )
    assert parse_assumptions(text) == [
        Assumption("Step 1 — add", "DEMO-E-001", "bool counts as int."),
        Assumption("Step 1 — add", "none", "none given"),
        Assumption("Step 2 — neg", "DEMO-R-002", "-0 is 0"),
    ]


def test_parse_test_review() -> None:
    ok = parse_test_review("Verdict: OK\n\n## Objections\nNone.\n")
    assert ok.verdict == "OK" and ok.objections == [] and ok.clean
    bad = parse_test_review(
        "**Verdict:** OBJECTIONS\n\n## Objections\n- X-R-001 t1: weak\n  -> stricter\n"
        "- X-E-001 t2: wrong message\n\n## Notes\n- not an objection\n"
    )
    assert bad.verdict == "OBJECTIONS" and not bad.clean
    assert bad.objections == ["X-R-001 t1: weak -> stricter", "X-E-001 t2: wrong message"]
    # OK with objections listed counts as objections; missing file / verdict never passes
    assert not parse_test_review("Verdict: OK\n## Objections\n- a: b\n").clean
    assert parse_test_review(None).verdict == "MISSING" and not parse_test_review(None).clean
    assert parse_test_review("no verdict here").verdict == "OBJECTIONS"
    assert parse_test_review("Verdict: OBJECTIONS\n").objections  # without bullets: still one


def test_parse_triage_and_match() -> None:
    v = parse_triage(
        "- test_add_rejects_bool: VALID — DEMO-E-001 says ints only\n"
        "- `Demo.Tests.Review.R.Neg_Throws`: **INVALID** - the spec allows it\n"
        "- other: maybe\n"
    )
    assert v == {
        "test_add_rejects_bool": ("VALID", "DEMO-E-001 says ints only"),
        "Demo.Tests.Review.R.Neg_Throws": ("INVALID", "the spec allows it"),
    }
    assert (
        triage_verdict("tests/review/test_r.py::test_add_rejects_bool", v)
        == v["test_add_rejects_bool"]
    )
    assert triage_verdict("Demo.Tests.Review.R.Neg_Throws", v) == ("INVALID", "the spec allows it")
    assert triage_verdict("t::unknown", v) is None


def test_phase_table_since_spec_approval() -> None:
    ev = [
        {"kind": "agent_run", "phase": "TESTS", "seconds": 999, "cost": 9, "turns": 9},
        {"kind": "spec_approved"},
        {"kind": "agent_run", "phase": "TESTS", "seconds": 30, "cost": 0.5, "turns": 10},
        {"kind": "agent_run", "phase": "TEST_REVIEW", "seconds": 20, "cost": 0.25, "turns": 5},
        {"kind": "agent_run", "phase": "TESTS", "seconds": 40, "cost": 0.5, "turns": 12},
        {"kind": "gate", "seconds": 7},
        {"kind": "final_gates", "seconds": 125},
    ]
    t = phase_table(since_spec_approval(ev))
    assert "| TESTS | 2 | 1 min 10 s | 1.00 | 22 |" in t
    assert "| TEST_REVIEW | 1 | 20 s | 0.25 | 5 |" in t
    assert (
        "| step gates (1 runs) | - | 7 s |" in t
        and "| final gates (1 runs) | - | 2 min 05 s |" in t
    )
    assert "| **Total** | 3 | 3 min 42 s | 1.25 | 27 |" in t


def test_retro_items_of_the_three_gate_flow() -> None:
    events = [
        {"kind": "questions", "phase": "TESTS", "who": "test writer", "questions": "- A?\n"},
        {"kind": "questions", "phase": "TEST_REVIEW", "who": "test reviewer", "questions": "B?"},
        {"kind": "answer", "gate": "tests", "answer": "A yes, B no."},
        {"kind": "questions", "phase": "TESTS", "who": "test writer", "questions": "C?"},
        {"kind": "human_gate", "gate": "tests", "approved": True},
        {
            "kind": "escalation",
            "phase": "TEST_REVIEW",
            "attempts": 2,
            "failed": ["turn cap (30)"],
            "resolution": "continued without a stop",
        },
        {
            "kind": "parked",
            "item": "step",
            "label": "step 2 - x",
            "reason": "q",
            "attempts": 0,
            "question": "Which?",
        },  # fmt: skip
        {"kind": "review_findings", "tests": ["t::a", "t::b"]},
        {
            "kind": "triage",
            "findings": [
                {"test": "t::a", "verdict": "VALID", "reason": "R-001"},
                {"test": "t::b", "verdict": "DISMISSED", "reason": "not in spec"},
            ],
        },
        {"kind": "findings_fixed", "tests": ["t::a"]},
        {"kind": "answer", "gate": "final", "answer": "Use x."},
        {"kind": "redo_result", "label": "step 2 - x", "passed": False},
    ]
    ri = build("f", events, assumptions=[Assumption("Step 1 — a", "F-E-001", "bool is int")])
    assert ri.ids == ["Q1", "Q2", "Q3", "E1", "P1", "V1", "V2", "A1"]
    t = ri.text
    assert "### Q1 — TESTS (test writer)\nQuestion: A?" in t and "A yes, B no." in t
    assert "### Q2 — TEST_REVIEW (test reviewer)" in t
    assert "(not answered: the tests gate was approved with it open)" in t
    assert "### E1 — TEST_REVIEW stopped at the turn cap 2 times" in t
    assert "Resolution: continued without a stop" in t
    assert "### P1 — step 2 - x parked (0 failed attempts)" in t and "Which?" in t
    assert "answered at the final gate and redone: parked again" in t
    assert "### V1 — t::a\nTriage (test reviewer): VALID\nReason: R-001" in t
    assert "Resolution: fixed by the implementer" in t
    assert "### V2 — t::b\nTriage (test reviewer): DISMISSED\nReason: not in spec" in t
    assert "### A1 — Step 1 — a [F-E-001]\nAssumption: bool is int" in t
    assert "assumptions 1; parked items 1" in t
    assert missing_ids("A1, P1 and P10", ["A1", "P1", "P2"]) == ["P2"]
