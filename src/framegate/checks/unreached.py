"""Assertions of the feature's tests that never ran (final gate, after the coverage run).

Seen live (M5-reach-by-save-chance): a test asserted `SaveChance > 0` only `if` the shot was
saved; the pinned match has no saved shot, so the line never ran and the test passed. The
test-smell scan sees loops over picked cases, not an `if`; the test writer raised it as a
question, and nothing mechanical stopped it.

The coverage run of the final gates records the lines of the test files the feature added or
changed (LanguageAdapter.coverage_checks, `cover_tests`). A test that ran, with an assertion
line that has no hit, fails this check. Left out:
- tests that did not run at all in this run (another check's business),
- tests that were there before the feature (as in test_smells),
- assertions that always fail (`pytest.fail`, `Assert.Fail`, `assert False`): guards,
- a line marked `unreached: <why>` in a comment on it or on the line above (the human's
  answer that the case needs no data).
"""

from __future__ import annotations

import re

from framegate.adapters.base import CheckResult, LanguageAdapter
from framegate.checks import test_smells

NAME = "unreached assertions (feature tests)"
#: `# unreached: <why>` / `// unreached: <why>` on the assertion's line or the line above
MARKED = re.compile(r"(?:#|//)\s*unreached:\s*\S", re.I)


def feature_test_files(adapter: LanguageAdapter, feature: str) -> list[str]:
    """The test files the feature added or changed (since spec-approved/<feature>), without
    the review tests: the reviewer writes them against the finished code."""
    cfg = adapter.cfg
    files = test_smells.feature_files(adapter, feature)
    if not files:
        return []
    tests = cfg.paths.tests.rstrip("/") + "/"
    review = cfg.paths.review_tests.rstrip("/") + "/"
    suffixes = tuple(adapter.source_suffixes)
    return sorted(
        f
        for f in files
        if f.startswith(tests)
        and not f.startswith(review)
        and (not suffixes or f.endswith(suffixes))
        and (cfg.root / f).is_file()
    )


def find(
    adapter: LanguageAdapter, feature: str, files: list[str], hits: dict[str, dict[int, int]]
) -> list[str]:
    """`<file>:<line>: <test>: <the line>` for every assertion that never ran."""
    added = test_smells.added_text(adapter, feature, set(files))
    found = []
    for rel in files:
        lines = hits.get(rel)
        if not lines:
            continue
        text = (adapter.cfg.root / rel).read_text(encoding="utf-8-sig", errors="replace")
        source = text.splitlines()
        for test in adapter.test_assertions(rel):
            if test_smells.is_old_test(f"{rel}::{test.name}", added):
                continue
            if not any(lines.get(n, 0) > 0 for n in range(test.first, test.last + 1)):
                continue  # the test did not run here
            for n in test.lines:
                if lines.get(n) != 0:
                    continue  # it ran, or the line is not one the coverage tool records
                here = source[n - 1] if 0 < n <= len(source) else ""
                above = source[n - 2] if 1 < n <= len(source) + 1 else ""
                if MARKED.search(here) or MARKED.search(above):
                    continue
                found.append(f"{rel}:{n}: {test.name}: {here.strip()[:120]}")
    return found


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    files = feature_test_files(adapter, feature)
    if not files:
        return CheckResult("PASS", "No test file added or changed by this feature.")
    hits, note = adapter.test_line_hits()
    if not hits:
        return CheckResult("SKIPPED", f"SKIPPED: {note}")
    found = find(adapter, feature, files, hits)
    if not found:
        return CheckResult("PASS", "Every assertion of the feature's tests ran.")
    return CheckResult(
        "FAIL",
        "ASSERTIONS THAT NEVER RAN (the test passed without them; the data holds no such "
        "case):\n"
        + "\n".join(f"  {x}" for x in found)
        + "\nUse data that holds the case (the spec's fixture table), or build the case in "
        "the test. A check that may stay unreached by the human's decision: mark its line "
        "`unreached: <why>` in a comment.",
        f"{len(found)} assertion(s) never ran",
    )
