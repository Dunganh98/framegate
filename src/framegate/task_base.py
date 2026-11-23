"""The base the task's own lines are counted against.

The gates format a changed file whole: formatting debt from before the task is fixed with
it. Seen live (da-otto): the formatter re-wrapped 36 old lines of a touched file, diff
coverage counted them as changed and failed on 9 old lines without a test; the fix itself
was covered.

So the lines a task wrote are not "changed since the approval" but "changed since the
approved files, formatted the same way": the marker's commit with the files the task
changed run through the gates' own fix commands (the linter's safe fixes, the formatter).
A line only the formatter rewrote is then the same on both sides, also where the formatter
moved lines. The base is a commit on no branch, kept under `refs/framegate/task-base/`,
built in a temporary worktree (so the tools find the project's config) and built again
only when the marker, the changed files or the commands differ. The file sizes of the
structure report and the risk's line counts use the same base, built from the feature's
start commit (`formatted`).
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import shutil
import tempfile
from collections.abc import Iterable, Iterator

from framegate.adapters.base import LanguageAdapter, keep_suppressed_file, run_command
from framegate.config import Config
from framegate.state import GitError, git, tag_exists

REF = "refs/framegate/task-base/"


def task_base(adapter: LanguageAdapter, marker: str | None) -> str | None:
    """The ref to compare with instead of `marker` (see the module text). `marker` itself
    when there is nothing to format, the adapter formats changed lines only, or git fails."""
    cfg = adapter.cfg
    if not marker or adapter.line_scoped_format:
        return marker
    try:
        if not tag_exists(cfg, marker):
            return marker
        return _formatted(adapter, marker, marker, adapter.changed_since(marker) or ())
    except (GitError, OSError):
        return marker


def formatted(adapter: LanguageAdapter, rev: str, name: str) -> str:
    """The same for any commit `rev` (the feature's start: file sizes, the risk's line
    counts), kept under the ref name `name`."""
    cfg = adapter.cfg
    if adapter.line_scoped_format:
        return rev
    try:
        changed = git(cfg, "-c", "core.quotepath=off", "diff", "--name-only", rev).splitlines()
        return _formatted(adapter, rev, name, changed)
    except (GitError, OSError):
        return rev


def _formatted(adapter: LanguageAdapter, rev: str, name: str, changed: Iterable[str]) -> str:
    cfg = adapter.cfg
    commit = git(cfg, "rev-parse", f"{rev}^{{commit}}").strip()
    mine = sorted(
        f
        for f in changed
        if (cfg.root / f).is_file()
        and (not adapter.source_suffixes or f.endswith(adapter.source_suffixes))
    )
    if not mine:
        return rev
    # only files the commit has: a new file is the task's from the first line
    there = git(cfg, "-c", "core.quotepath=off", "ls-tree", "-r", "--name-only", commit)
    files = [f for f in mine if f in set(there.splitlines())]
    lint, fmt = adapter.fix_commands(files)
    if not lint and not fmt:
        return rev
    ref = REF + name
    key = json.dumps([commit, files, lint, fmt, adapter.line_suppression])
    if _message(adapter, ref) != key:
        _build(adapter, ref, commit, files, [*lint, *fmt], key)
    return ref


def _message(adapter: LanguageAdapter, ref: str) -> str:
    try:
        return git(adapter.cfg, "log", "-1", "--format=%B", ref, "--").strip()
    except GitError:
        return ""


@contextlib.contextmanager
def scratch(cfg: Config, commit: str) -> Iterator[pathlib.Path]:
    """A temporary worktree of `commit` (detached, no project hook runs there), removed
    afterwards."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="framegate-base-"))
    tree = tmp / "tree"
    quiet = f"core.hooksPath={tmp}"  # a folder without hooks
    try:
        git(cfg, "-c", quiet, "worktree", "add", "--detach", str(tree), commit)
        yield tree
    finally:
        try:
            git(cfg, "worktree", "remove", "--force", str(tree))
        except GitError:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            git(cfg, "worktree", "prune")
        except GitError:
            pass


def _build(
    adapter: LanguageAdapter,
    ref: str,
    commit: str,
    files: list[str],
    cmds: list[list[str]],
    key: str,
) -> None:
    cfg = adapter.cfg
    with scratch(cfg, commit) as tree:
        old = {f: (tree / f).read_bytes() for f in files} if adapter.line_suppression else {}
        for cmd in cmds:  # a fix that fails leaves the file as the marker has it
            run_command(cmd, tree, env=adapter.tool_env(), timeout=900)
        for f, was in old.items():  # as format_files: a suppression comment stays put
            keep_suppressed_file(tree / f, was, adapter.line_suppression)
        git(cfg, "-C", str(tree), "add", "--", *files)
        written = git(cfg, "-C", str(tree), "write-tree").strip()
        who = ("-c", "user.name=framegate", "-c", "user.email=framegate@localhost")
        base = git(cfg, *who, "commit-tree", written, "-p", commit, "-m", key).strip()
        git(cfg, "update-ref", ref, base)
