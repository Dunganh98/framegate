"""retro_input.md: item IDs per question / failed attempt / escalation / rejection / review
finding / final-gate failure, and the coverage check of retro_proposals.md."""

from __future__ import annotations

import pathlib

from agentflow.gates import GateReport, gate_report
from agentflow.retro import (
    add_knowledge,
    build,
    knowledge_items,
    match_answer,
    missing_ids,
    parse_answer_items,
    split_questions,
)
from conftest import git, init_git
from test_checks import adapter_for, write


def test_answers_matched_per_question() -> None:
    """Each question gets its own numbered answer (seen live: every question showed the same
    cut slice of answers.md). Items match on the question text, either way round clipped."""
    answers = (
        "# Answers (final gate)\n\n"
        "1. Q: step 2 - record: no reasonable assumption possible\n"
        "   A: Re-scan for seeds where a duel lap runs out.\n"
        "      Pin them; one extra match each.\n"
        "2. Q: [test writer] MAR-R-003 vs MAR-E-009: a duel where he picks Jockey and the lap "
        "runs out ...\n"
        "   A: A Missed entry, yes.\n"
        "3. No floats.\n"
    )
    items = parse_answer_items(answers)
    assert [(i["n"], i["a"]) for i in items] == [
        (1, "Re-scan for seeds where a duel lap runs out.\nPin them; one extra match each."),
        (2, "A Missed entry, yes."),
        (3, "No floats."),
    ]
    q = "MAR-R-003 vs MAR-E-009: a duel where he picks Jockey and the lap runs out, then what?"
    assert match_answer(q, items) == "A Missed entry, yes."
    assert match_answer("Something else entirely?", items) is None
    events = [
        {"kind": "spec_approved"},
        {"kind": "questions", "phase": "TESTS", "who": "test writer", "questions": f"- {q}\n- Y?"},
        {"kind": "answer", "gate": "final", "answer": answers, "items": items},
    ]
    text = build("f", events).text
    assert "Answer:\n~~~\nA Missed entry, yes.\n~~~" in text
    assert "Question: Y?\nAnswer:\n~~~\n(not answered: no numbered answer" in text
    assert "Re-scan" not in text


def test_knowledge_items_and_adding_them() -> None:
    proposals = (
        "# Retro\n\n## 1. Something\n- K9 [Tools]: not in the section\n\n"
        "## Project knowledge\n- K1 [Never changes]: the fingerprint hashes the whole report\n"
        "- K2 [Seeds]: find seeds with `scan`\n\nNot acted on: none\n"
    )
    assert knowledge_items(proposals) == [
        (1, "Never changes", "the fingerprint hashes the whole report"),
        (2, "Seeds", "find seeds with `scan`"),
    ]
    base = "# Project knowledge\n\n## Never changes\n<!-- c -->\n- old fact\n\n## Tools\n"
    text = add_knowledge(base, "never changes", "- new fact")
    assert text == (
        "# Project knowledge\n\n## Never changes\n<!-- c -->\n- old fact\n- new fact\n\n## Tools\n"
    )
    assert add_knowledge(base, "Seeds", "- x").endswith("## Tools\n\n## Seeds\n- x\n")


def test_split_questions() -> None:
    text = "# Questions\n- First?\n  more detail\n* Second?\n1. Third?\n2) Fourth?\n"
    assert split_questions(text) == ["First?\nmore detail", "Second?", "Third?", "Fourth?"]
    assert split_questions("Just one question without bullets?") == [
        "Just one question without bullets?"
    ]
    assert split_questions("  \n") == []


def test_missing_ids() -> None:
    proposals = "Evidence: Q10, F1.\n- E1: one-off\n"
    assert missing_ids(proposals, ["Q1", "Q10", "F1", "E1", "G1"]) == ["Q1", "G1"]
    assert missing_ids("", []) == []


def test_build_all_kinds() -> None:
    events = [
        {"kind": "questions", "phase": "TESTS", "step": None, "questions": "- A?\n- B?\n"},
        {"kind": "answer", "waiting": "questions", "aborted": False, "answer": "A: yes. B: no."},
        {"kind": "questions", "phase": "IMPLEMENT", "step": "step 2 - x", "questions": "C?"},
        {"kind": "answer", "waiting": "questions", "aborted": True, "answer": ""},
        {"kind": "questions", "phase": "REVIEW", "questions": "D?"},  # never answered
        {"kind": "gate", "phase": "IMPLEMENT", "step": "s1", "attempt": 1, "passed": False},
        {"kind": "gate", "phase": "IMPLEMENT", "step": "s1", "attempt": 2, "passed": True},
        {"kind": "escalation", "phase": "IMPLEMENT", "step": "s1", "attempts": 3, "failed": ["x"]},
        {"kind": "human_gate", "gate": "tests", "approved": False, "reason": "too weak"},
        {"kind": "human_gate", "gate": "tests", "approved": True, "reason": None},
        {"kind": "review_findings", "tests": ["t::a", "t::b"]},
        {"kind": "human_gate", "gate": "review", "approved": False, "reason": "invalid"},
        {"kind": "final_gates", "passed": False, "failed": ["mutation score"], "excerpt": "low"},
        {"kind": "agent_run", "phase": "PR"},
        {
            "kind": "permission_denials",
            "phase": "IMPLEMENT",
            "step": "s1",
            "denials": [{"tool": "PowerShell", "input": "cd src; dotnet test"}],
        },
    ]
    ri = build("f", events, review_notes="notes")
    assert ri.ids == [
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "F1",
        "E1",
        "R1",
        "V1",
        "V2",
        "R2",
        "G1",
        "D1",
    ]  # event order
    assert ri.counts == {"Q": 4, "F": 1, "E": 1, "R": 2, "V": 2, "G": 1, "D": 1, "A": 0, "P": 0}
    t = ri.text
    assert "questions 4 (3 rounds); failed attempts 1; escalations 1; human rejections 2" in t
    assert "### Q1 — TESTS\nQuestion: A?" in t and "A: yes. B: no." in t
    assert "### Q3 — IMPLEMENT / step 2 - x" in t and "aborted: the spec had to change" in t
    assert "### Q4 — REVIEW\nQuestion: D?\nAnswer:\n~~~\n(not answered: the tests gate" in t
    assert "Failing checks: (not recorded)" in t  # old events without `failed`
    assert "### E1 — IMPLEMENT / s1 failed 3 attempts" in t and "(not resolved yet)" in t
    assert "### R1 — tests gate rejected\nReason: too weak" in t
    assert "### R2 — review gate rejected" in t
    assert "### V1 — t::a\nHuman decision: rejected (no fix): invalid" in t
    assert "### G1 — final gates failed: mutation score" in t and "low" in t
    assert "## Review notes" in t
    assert "final gate failures 1; permission denials 1" in t
    assert "### D1 — IMPLEMENT / s1: PowerShell denied\nDenied call:\n~~~\ncd src; dotnet test" in t
    assert missing_ids("- D1: wrong form", ["D1"]) == []


def test_summarize_denials() -> None:
    from agentflow.orchestrator import summarize_denials

    raw = [
        {
            "tool_name": "Bash",
            "tool_use_id": "x",
            "tool_input": {"command": "ls", "description": "d"},
        },
        {"tool_name": "Write", "tool_input": {"file_path": "tests/a.py", "content": "..."}},
        {"tool_name": "WebFetch", "tool_input": {"prompt": "p" * 400}},
        "junk",
    ]
    out = summarize_denials(raw)
    assert out[0] == {"tool": "Bash", "input": "ls"}
    assert out[1] == {"tool": "Write", "input": "tests/a.py"}
    assert out[2]["input"].startswith('{"prompt": "ppp') and out[2]["input"].endswith("...")
    assert len(out) == 3 and summarize_denials(None) == []


def test_gate_report_details(tmp_path: pathlib.Path) -> None:
    framed = tmp_path
    init_git(framed)
    write(framed, ".gitignore", "out/\n.phase\n")
    write(framed, "src/pkg/a.py", "X = 1\n")
    write(framed, "tests/test_a.py", "def test_a() -> None:\n    assert True\n")
    git(framed, "add", "-A")
    git(framed, "commit", "-q", "-m", "base")
    git(framed, "tag", "tests-approved/f")
    write(framed, "tests/test_a.py", "def test_a() -> None:\n    assert False\n")
    a = adapter_for(framed, {"gates": {"commands": {"lint": [], "format": [], "typecheck": []}}})
    rep = gate_report(a, "f", "step")
    assert not rep.ok and rep.failed == ["frame diff", "tests (current + earlier steps)"]
    excerpt = rep.excerpt()
    assert excerpt.startswith("[FAIL] frame diff") and "[FAIL] tests (current" in excerpt
    long = GateReport(False, "", ["x"], [], {"x": "\n".join("y" * 300 for _ in range(12))})
    assert long.excerpt().startswith("...") and len(long.excerpt()) == 2003


def test_parse_result_stream_counts_api_turns() -> None:
    """stream-json (claude 2.1.321): one line per content block, blocks of one API turn share
    the message id. --max-turns caps these turns; num_turns counts prompt + tool results."""
    from agentflow.orchestrator import hit_cap, parse_result

    lines = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"id": "m1", "content": [{"type": "tool_use"}]}},
        {"type": "assistant", "message": {"id": "m1", "content": [{"type": "tool_use"}]}},
        {"type": "assistant", "message": {"id": "m1", "content": [{"type": "tool_use"}]}},
        {"type": "user", "message": {"content": []}},
        {"type": "rate_limit_event"},
        {"type": "assistant", "message": {"id": "m2", "content": [{"type": "tool_use"}]}},
        {
            "type": "result",
            "subtype": "error_max_budget_usd",
            "terminal_reason": "budget_exhausted",
            "is_error": True,
            "num_turns": 5,
            "total_cost_usd": 0.0045,
        },
    ]
    import json

    res = parse_result("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    assert res["api_turns"] == 2 and res["num_turns"] == 5
    assert hit_cap(res) == "budget"
    assert hit_cap({"subtype": "error_max_turns"}) == "turns"
    assert hit_cap({"subtype": "success", "terminal_reason": "completed"}) is None
    # a single JSON object (--output-format json) still works; no stream -> api_turns None
    one = parse_result(json.dumps({"type": "result", "num_turns": 46, "subtype": "success"}))
    assert one["api_turns"] is None and one["num_turns"] == 46
    bad = parse_result("", "Error: boom")
    assert bad["is_error"] and bad["result"] == "Error: boom"
