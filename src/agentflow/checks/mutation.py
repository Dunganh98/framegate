"""Mutation score on the source files changed since the tests were approved (adapter tool)."""

from __future__ import annotations

from agentflow.adapters.base import CheckResult, LanguageAdapter, TestScope
from agentflow.checks.frame_diff import approved_tag, changed_src_files
from agentflow.state import GitError


def check(adapter: LanguageAdapter, feature: str, scope: TestScope | None = None) -> CheckResult:
    tag = approved_tag(feature)
    try:
        changed = changed_src_files(adapter, tag)
    except GitError as e:
        return CheckResult("FAIL", str(e))
    return adapter.mutation_check(feature, tag, changed, scope)
