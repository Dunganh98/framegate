"""Flag tests without any assertion and skipped / xfail / ignored tests (adapter-specific scan)."""

from __future__ import annotations

from agentflow.adapters.base import CheckResult, LanguageAdapter


def check(adapter: LanguageAdapter, feature: str = "") -> CheckResult:
    problems = adapter.test_smells()
    return CheckResult(
        "FAIL" if problems else "PASS", "\n".join(problems) or "No test smells found."
    )
