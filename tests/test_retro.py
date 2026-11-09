"""retro_input.md: item IDs per question / failed attempt / escalation / rejection / review
finding / final-gate failure, and the coverage check of retro_proposals.md."""

from __future__ import annotations

import pathlib

from agentflow.gates import GateReport, gate_report
from agentflow.retro import build, missing_ids, split_questions
from conftest import git, init_git
from test_checks import adapter_for, write


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
    assert ri.counts == {"Q": 4, "F": 1, "E": 1, "R": 2, "V": 2, "G": 1, "D": 1}
    t = ri.text
    assert "questions 4 (3 rounds); failed attempts 1; escalations 1; human rejections 2" in t
    assert "### Q1 — TESTS\nQuestion: A?" in t and "A: yes. B: no." in t
    assert "### Q3 — IMPLEMENT / step 2 - x" in t and "aborted: the spec had to change" in t
    assert "### Q4 — REVIEW\nQuestion: D?\nAnswer:\n~~~\n(not answered yet)" in t
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
