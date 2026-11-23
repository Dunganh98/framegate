"""Mutation score on the source files changed since the tests were approved (adapter tool).

The adapter writes the run's survivors to mutation_survivors.md, each run anew. A failing
run's survivors are what the retro learns from, so a run that fails keeps its list: the
next run's file gets it below, as `## Earlier run <n>` (the newest first; EARLIER at most).
"""

from __future__ import annotations

import pathlib

from framegate.adapters.base import CheckResult, LanguageAdapter, TestScope
from framegate.checks.frame_diff import approved_tag, changed_src_files
from framegate.state import GitError

#: every adapter's score line starts with this (a FAIL without it is a tool failure)
SCORE_PREFIX = "Mutation score"
SURVIVORS = "mutation_survivors.md"
#: first line of the file after a failing run: its result (`keep_failed` reads it)
FAILED = "<!-- this run failed: "
_EARLIER = "\n## Earlier run "
EARLIER = 5


def keep_failed(path: pathlib.Path, before: str, res: CheckResult) -> None:
    """After a run: put the survivors of the failed run before it (`before`: the file as
    that run left it) under the new list, and mark this run's list when it failed."""
    try:
        now = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return
    if now == before and not before.startswith(FAILED):
        return  # this run wrote nothing, and nothing waits to be kept
    if now == before:
        now = "No survivor list from the latest run.\n"
    head, _, old = before.partition(_EARLIER)
    earlier = [x for x in (_EARLIER + old).split(_EARLIER) if x.strip()] if old else []
    if head.startswith(FAILED):
        first, _, rest = head.partition("\n")
        result = first.removeprefix(FAILED).removesuffix(" -->")
        n = len(earlier) + 1
        earlier.insert(0, f"{n} (failed: {result}; kept for the retro)\n\n{rest.strip()}\n")
    mark = f"{FAILED}{res.summary or 'no score'} -->\n" if res.status == "FAIL" else ""
    text = mark + now.rstrip("\n") + "\n" + "".join(_EARLIER + x for x in earlier[:EARLIER])
    path.write_text(text, encoding="utf-8")


def check(adapter: LanguageAdapter, feature: str, scope: TestScope | None = None) -> CheckResult:
    tag = approved_tag(feature)
    try:
        changed = changed_src_files(adapter, tag)
    except GitError as e:
        return CheckResult("FAIL", str(e))
    path = adapter.cfg.feature_out(feature) / SURVIVORS
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    res = adapter.mutation_check(feature, tag, changed, scope)
    # a FAIL without a score: the tool failed, the tests were not measured
    res.tool_failure = res.status == "FAIL" and not res.summary.startswith(SCORE_PREFIX)
    if not res.tool_failure and res.status in ("PASS", "FAIL"):
        keep_failed(path, before, res)
    return res
