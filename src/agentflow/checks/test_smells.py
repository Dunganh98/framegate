"""Flag tests without any assertion and skipped / xfail / ignored tests (adapter-specific scan).

Only the test files the feature changed count (since spec-approved/<feature>): old tests are
not the feature's business. Lines starting with "NOTE" (heuristics such as a loop over picked
cases without a visible non-empty check) are shown but never fail the check; the test reviewer
reads them in test_checks.md.
"""

from __future__ import annotations

from agentflow.adapters.base import CheckResult, LanguageAdapter
from agentflow.state import GitError, tag_exists


def feature_files(adapter: LanguageAdapter, feature: str) -> set[str] | None:
    """Files changed since spec-approved/<feature> (incl. untracked); None = unknown (all)."""
    if not feature:
        return None
    from agentflow.checks.frame_diff import changed_files

    tag = f"spec-approved/{feature}"
    try:
        if not tag_exists(adapter.cfg, tag):
            return None
        return changed_files(adapter.cfg, tag)[0]
    except GitError:
        return None


def check(adapter: LanguageAdapter, feature: str = "") -> CheckResult:
    problems = adapter.test_smells()
    files = feature_files(adapter, feature)
    ignored = 0
    if files is not None:
        kept = [p for p in problems if any(f in p for f in files)]
        ignored = len(problems) - len(kept)
        problems = kept
    notes = [p for p in problems if p.startswith("NOTE")]
    failing = [p for p in problems if not p.startswith("NOTE")]
    lines = failing + notes
    if ignored:
        lines.append(f"({ignored} in test files this feature did not change: ignored)")
    return CheckResult("FAIL" if failing else "PASS", "\n".join(lines) or "No test smells found.")
