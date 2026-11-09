"""Base role prompts: rules that must stay in them."""

from __future__ import annotations

from agentflow.orchestrator import package_text


def test_test_writer_must_ask_instead_of_dropping_cases() -> None:
    text = package_text("roles", "tests.md")
    assert "Never silently leave an input, case or ID out of the tests" in text
    assert "questions.md" in text.split("Never silently leave")[1].split("\n- ")[0]
