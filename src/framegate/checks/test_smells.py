"""Flag tests without any assertion and skipped / xfail / ignored tests (adapter-specific scan).

Only the tests the feature added count (since spec-approved/<feature>): old tests are not
the feature's business, also when the feature touched their file (a refactor tags an
existing test with its spec ID; a new test may sit beside old ones). Lines starting with
"NOTE" (heuristics such as a loop over picked
cases without a visible non-empty check) are shown but never fail the check; the test reviewer
reads them in test_checks.md.
"""

from __future__ import annotations

import re

from framegate.adapters.base import CheckResult, LanguageAdapter, changed_lines
from framegate.state import GitError, git, tag_exists


def feature_files(adapter: LanguageAdapter, feature: str) -> set[str] | None:
    """Files changed since spec-approved/<feature> (incl. untracked); None = unknown (all)."""
    if not feature:
        return None
    from framegate.checks.frame_diff import changed_files

    tag = f"spec-approved/{feature}"
    try:
        if not tag_exists(adapter.cfg, tag):
            return None
        return changed_files(adapter.cfg, tag)[0]
    except GitError:
        return None


def added_text(adapter: LanguageAdapter, feature: str, files: set[str]) -> dict[str, str | None]:
    """Per changed file that existed at spec-approved/<feature>: the lines added since
    (None: the diff cannot be read, so every test of the file counts)."""
    cfg = adapter.cfg
    tag = f"spec-approved/{feature}"
    try:
        old = set(git(cfg, "ls-tree", "-r", "--name-only", tag).splitlines())
    except GitError:
        return {}
    out: dict[str, str | None] = {}
    for f in sorted(files & old):
        try:
            diff = git(cfg, "diff", "-U0", tag, "--", f)
        except GitError:
            out[f] = None
            continue
        out[f] = "\n".join(
            line[1:]
            for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++ ")
        )
    return out


def is_old_test(problem: str, added: dict[str, str | None]) -> bool:
    """A smell line `...: <file>::<test>` of a file that existed before, whose test name is
    in no added line: the test was there before the feature."""
    if "::" not in problem:
        return False
    head, _, test = problem.rpartition("::")
    name = test.strip().rsplit(".", 1)[-1]
    for f, text in added.items():
        if head.endswith(f):
            return (
                bool(name) and text is not None and not re.search(rf"\b{re.escape(name)}\b", text)
            )
    return False


#: a smell on one line: `... <file> line <N>` (python), `...: <file>:<N>` (generic)
LINE_SMELL = re.compile(r"(?: line |:)(\d+)$")


def is_old_line(problem: str, lines: dict[str, set[int] | None]) -> bool:
    """A smell on a line of a file that existed before, not among the lines changed since."""
    m = LINE_SMELL.search(problem)
    if not m:
        return False
    head = problem[: m.start()]
    for f, changed in lines.items():
        if head.endswith(f):
            return changed is not None and int(m.group(1)) not in changed
    return False


def check(adapter: LanguageAdapter, feature: str = "") -> CheckResult:
    problems = adapter.test_smells()
    files = feature_files(adapter, feature)
    ignored = 0
    if files is not None:
        added = added_text(adapter, feature, files)
        tag = f"spec-approved/{feature}"
        on_lines = [p for p in problems if LINE_SMELL.search(p)]
        changed = {
            f: changed_lines(adapter.cfg, tag, f) for f in added if any(f in p for p in on_lines)
        }
        kept = [
            p
            for p in problems
            if any(f in p for f in files)
            and not is_old_test(p, added)
            and not is_old_line(p, changed)
        ]
        ignored = len(problems) - len(kept)
        problems = kept
    notes = [p for p in problems if p.startswith("NOTE")]
    failing = [p for p in problems if not p.startswith("NOTE")]
    lines = failing + notes
    if ignored:
        lines.append(f"({ignored} in tests this feature did not add: ignored)")
    return CheckResult("FAIL" if failing else "PASS", "\n".join(lines) or "No test smells found.")
