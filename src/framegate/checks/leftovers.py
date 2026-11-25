"""A test run leaves no file behind ([gates] test_leftovers): the untracked files before and
after it are compared; a new one outside the out folder and the ignored paths fails the
check that ran the tests, is listed and removed."""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Callable

from framegate.adapters.base import Check, CheckResult, LanguageAdapter
from framegate.checks.structure import SKIP_DIRS
from framegate.config import Config, drop_machine_files
from framegate.state import GitError, git

HEAD = "The test run left {n} new file(s) in the tree (removed now):"
ADVICE = (
    "A test writes only under a temporary folder: change the test, or the code that writes "
    "the file where the test runs it."
)


def untracked(adapter: LanguageAdapter) -> set[str] | None:
    """The untracked, not ignored files outside framegate's own files and the folders a
    build or a test runner fills (bytecode, build output: SKIP_DIRS, the adapter's ignores)."""
    cfg = adapter.cfg
    try:
        listed = git(cfg, "-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard")
    except GitError:
        return None
    p = cfg.paths
    own = tuple(x.strip("/") + "/" for x in (p.out, p.metrics) if x.strip("/"))
    made = SKIP_DIRS | {e.strip("/") for e in adapter.gitignore_entries()}
    return {
        f
        for f in drop_machine_files(cfg, listed.splitlines())
        if not f.startswith(own)
        and f not in (p.phase_file, p.reqs_file)
        and not made & set(f.split("/")[:-1])
    }


def _remove(cfg: Config, files: list[str]) -> None:
    for rel in files:
        path = cfg.root / rel
        with contextlib.suppress(OSError):
            path.unlink()
        for parent in path.parents:
            if parent == cfg.root or cfg.root not in parent.parents:
                break
            try:
                parent.rmdir()  # only when the run left it empty
            except OSError:
                break


def judged(adapter: LanguageAdapter, before: set[str] | None, res: CheckResult) -> CheckResult:
    """`res` of a test run; FAIL with the files it left since `before` (they are removed)."""
    cfg = adapter.cfg
    after = untracked(adapter) if before is not None else None
    new = sorted((after or set()) - (before or set()))
    if not new:
        return res
    _remove(cfg, new)
    text = "\n".join([HEAD.format(n=len(new)), *(f"  {f}" for f in new), ADVICE])
    summary = "; ".join(x for x in (res.summary, f"files left behind: {len(new)}") if x)
    return CheckResult("FAIL", f"{res.output}\n\n{text}".strip(), summary)


def watched(adapter: LanguageAdapter, check: Check) -> Check:
    """`check` (it runs tests) with the comparison around it."""
    cfg = adapter.cfg
    if not cfg.test_leftovers:
        return check

    def run() -> CheckResult:
        before = untracked(adapter)
        return judged(adapter, before, check.run())

    return dataclasses.replace(check, run=run)


def watching(
    run: Callable[[LanguageAdapter, str], CheckResult],
) -> Callable[[LanguageAdapter, str], CheckResult]:
    """The same for a check of the TESTS phase (`run(adapter, feature)`)."""

    def wrapped(adapter: LanguageAdapter, feature: str) -> CheckResult:
        if not adapter.cfg.test_leftovers:
            return run(adapter, feature)
        before = untracked(adapter)
        return judged(adapter, before, run(adapter, feature))

    return wrapped
