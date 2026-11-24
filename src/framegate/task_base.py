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
only when the marker, the changed files or the commands differ.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile

from framegate.adapters.base import LanguageAdapter, run_command
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
        commit = git(cfg, "rev-parse", f"{marker}^{{commit}}").strip()
        changed = sorted(
            f
            for f in adapter.changed_since(marker) or ()
            if (cfg.root / f).is_file()
            and (not adapter.source_suffixes or f.endswith(adapter.source_suffixes))
        )
        if not changed:
            return marker
        # only files the marker has: a new file is the task's from the first line
        there = git(cfg, "-c", "core.quotepath=off", "ls-tree", "-r", "--name-only", commit)
        files = [f for f in changed if f in set(there.splitlines())]
        lint, fmt = adapter.fix_commands(files)
        if not lint and not fmt:
            return marker
        ref = REF + marker
        key = json.dumps([commit, files, lint, fmt])
        if _message(adapter, ref) != key:
            _build(adapter, ref, commit, files, [*lint, *fmt], key)
        return ref
    except (GitError, OSError):
        return marker


def _message(adapter: LanguageAdapter, ref: str) -> str:
    try:
        return git(adapter.cfg, "log", "-1", "--format=%B", ref, "--").strip()
    except GitError:
        return ""


def _build(
    adapter: LanguageAdapter,
    ref: str,
    commit: str,
    files: list[str],
    cmds: list[list[str]],
    key: str,
) -> None:
    cfg = adapter.cfg
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="framegate-base-"))
    tree = tmp / "tree"
    quiet = f"core.hooksPath={tmp}"  # a folder without hooks: no project hook runs here
    try:
        git(cfg, "-c", quiet, "worktree", "add", "--detach", str(tree), commit)
        for cmd in cmds:  # a fix that fails leaves the file as the marker has it
            run_command(cmd, tree, env=adapter.tool_env(), timeout=900)
        git(cfg, "-C", str(tree), "add", "--", *files)
        written = git(cfg, "-C", str(tree), "write-tree").strip()
        who = ("-c", "user.name=framegate", "-c", "user.email=framegate@localhost")
        base = git(cfg, *who, "commit-tree", written, "-p", commit, "-m", key).strip()
        git(cfg, "update-ref", ref, base)
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
