"""Mutation score on the source files changed since the tests were approved (adapter tool)."""

from __future__ import annotations

from agentflow.adapters.base import CheckResult, LanguageAdapter, TestScope
from agentflow.checks.frame_diff import approved_tag, changed_src_files
from agentflow.state import GitError

#: every adapter's score line starts with this (a FAIL without it is a tool failure)
SCORE_PREFIX = "Mutation score"


def check(adapter: LanguageAdapter, feature: str, scope: TestScope | None = None) -> CheckResult:
    tag = approved_tag(feature)
    try:
        changed = changed_src_files(adapter, tag)
    except GitError as e:
        return CheckResult("FAIL", str(e))
    res = adapter.mutation_check(feature, tag, changed, scope)
    # a FAIL without a score: the tool failed, the tests were not measured
    res.tool_failure = res.status == "FAIL" and not res.summary.startswith(SCORE_PREFIX)
    return res
