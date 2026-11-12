"""Base role prompts: rules that must stay in them."""

from __future__ import annotations

from framegate.orchestrator import package_text


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
    assert "final_facts.md" in package_text("roles", "pr.md")
    snippet = package_text("templates", "claude_md_snippet.md")
    assert "park.md" in snippet and "questions.md and stop" not in snippet


def test_spec_updater_keeps_ids_and_marks_changes() -> None:
    text = package_text("roles", "spec_update.md")
    assert "Never renumber, rename or delete an existing ID" in text
    assert "(answer ANS-2)" in text and "new ID" in text
    for name in ("requirements.md", "design.md", "plan.md"):
        assert name in text
