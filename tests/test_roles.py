"""Base role prompts: rules that must stay in them."""

from __future__ import annotations

import pathlib

from framegate.config import parse_config
from framegate.orchestrator import Orchestrator, package_text


def test_test_writer_must_ask_instead_of_dropping_cases() -> None:
    text = package_text("roles", "tests.md")
    assert "Never silently leave an input, case or ID out of the tests" in text
    assert "questions.md" in text.split("Never silently leave")[1].split("\n- ")[0]


def test_no_stop_between_gates_in_roles() -> None:
    """Writer and reviewers ask without stopping; the implementer assumes from the spec and logs
    it, and parks only when no reasonable assumption is possible."""
    tests = package_text("roles", "tests.md")
    assert "and continue with" in tests and "Do not stop" in tests
    impl = package_text("roles", "implement.md")
    assert "- ASSUMPTION [<spec ID>]:" in impl and "park.md" in impl
    assert "no reasonable assumption is possible" in impl and "questions.md" not in impl
    review = package_text("roles", "test_review.md")
    for needle in ("Verdict: OK", "## Objections", "VALID", "INVALID", "triage.md"):
        assert needle in review
    assert "final_facts.md" in package_text("roles", "summary.md")
    snippet = package_text("templates", "claude_md_snippet.md")
    assert "park.md" in snippet and "questions.md and stop" not in snippet


def test_spec_updater_keeps_ids_and_marks_changes() -> None:
    text = package_text("roles", "spec_update.md")
    assert "Never renumber, rename or delete an existing ID" in text
    assert "(answer ANS-2)" in text and "new ID" in text
    for name in ("requirements.md", "design.md", "plan.md"):
        assert name in text


def test_the_spec_is_followed_and_the_guidelines_are_in_both_roles(tmp_path: pathlib.Path) -> None:
    tests = package_text("roles", "tests.md")
    assert "only for a contradiction inside the spec" in tests
    assert "first make it fail" in tests
    review = package_text("roles", "test_review.md")
    assert "## Spec conformance" in review and "| Spec ID | Test file | Fixture as" in review
    assert "a subclass, an object that" in review
    guide = package_text("templates", "coding_guidelines.md")
    rules = [line[2:] for line in guide.splitlines() if line.startswith("- ")]
    assert len(rules) == 5 and len(guide.splitlines()) <= 15
    orch = Orchestrator(parse_config(tmp_path, {}))
    for role in ("implement", "review"):
        assert not any(rule in package_text("roles", f"{role}.md") for rule in rules), role
        text = orch.role_text(role, "demo")
        assert text.count("# Coding guidelines") == 1 and all(r in text for r in rules), role
    assert "# Coding guidelines" not in orch.role_text("tests", "demo")
    summary = package_text("roles", "summary.md")
    assert "{out}/{feature}/questions.md" in summary and "over the size limit" in summary
    assert "(a replay)" not in package_text("templates", "chat_protocol.md")
