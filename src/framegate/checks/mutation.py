"""Mutation score on the source files changed since the tests were approved (adapter tool).

The adapter writes the run's survivors to mutation_survivors.md, each run anew. A failing
run's survivors are what the retro learns from, so a run that fails keeps its list: the
next run's file gets it below, as `## Earlier run <n>` (the newest first; EARLIER at most).

A scored run is kept with a hash of what it ran on (mutation_cache.json): while no source
or test file changed, the check gives that result again without a run.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from framegate.adapters.base import CheckResult, LanguageAdapter, TestScope
from framegate.checks.frame_diff import approved_tag, changed_src_files
from framegate.state import GitError, git

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


#: out/<feature>/<this>: the last scored run and what it ran on (`state_key`)
CACHE = "mutation_cache.json"
REUSED = "nothing changed since that run: result reused, not run again"


def state_key(
    adapter: LanguageAdapter, tag: str, changed: list[str], scope: TestScope | None
) -> str:
    """A hash of what a mutation run depends on: every source and test file's content, the
    files changed since `tag`, the test scope and the threshold."""
    cfg = adapter.cfg
    folders = sorted({*cfg.src_roots, cfg.paths.tests, cfg.paths.review_tests})
    listed = git(cfg, "-c", "core.quotepath=off", "ls-files", "-co", "--exclude-standard", "--")
    digest = hashlib.sha256()
    ids = [list(scope.req_ids), list(scope.paths)] if scope else None
    head = [git(cfg, "rev-parse", tag).strip(), changed, ids, cfg.mutation_score, cfg.mutation_tool]
    digest.update(json.dumps(head).encode("utf-8"))
    for rel in sorted(listed.splitlines()):
        file = cfg.root / rel
        inside = any(rel == f or rel.startswith(f.rstrip("/") + "/") for f in folders)
        if inside and file.is_file():
            digest.update(rel.encode("utf-8") + b"\0" + file.read_bytes() + b"\0")
    return digest.hexdigest()


def check(adapter: LanguageAdapter, feature: str, scope: TestScope | None = None) -> CheckResult:
    tag = approved_tag(feature)
    try:
        changed = changed_src_files(adapter, tag)
        key = state_key(adapter, tag, changed, scope)
    except (GitError, OSError) as e:
        return CheckResult("FAIL", str(e))
    cache = adapter.cfg.feature_out(feature) / CACHE
    try:
        last = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    if isinstance(last, dict) and last.get("key") == key:
        # seen live: the same 4.5 minutes three times in one task
        summary = f"{last['summary']} ({REUSED})"
        return CheckResult(str(last["status"]), f"{last['output']}\n({REUSED})", summary)
    path = adapter.cfg.feature_out(feature) / SURVIVORS
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    res = adapter.mutation_check(feature, tag, changed, scope)
    # a FAIL without a score: the tool failed, the tests were not measured
    res.tool_failure = res.status == "FAIL" and not res.summary.startswith(SCORE_PREFIX)
    if not res.tool_failure and res.status in ("PASS", "FAIL"):
        keep_failed(path, before, res)
    if res.summary.startswith(SCORE_PREFIX):  # a scored run only: anything else runs again
        cache.parent.mkdir(parents=True, exist_ok=True)
        entry = {"key": key, "status": res.status, "output": res.output, "summary": res.summary}
        cache.write_text(json.dumps(entry), encoding="utf-8")
    return res
