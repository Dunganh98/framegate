"""Formatting debt in a commit of its own.

The gates format a changed file whole (adapters.base.format_files): old formatting debt is
fixed with the task. Seen live: in a one-line bug fix the reviewer saw ~70 restyled lines,
asked to revert them, and the next gate formatted them again, twice. So before the task's
commit, each such file gets a commit of its own: the last commit's version run through the
same fix commands. The task's commit then shows the task's lines only. A file whose task
lines and formatted lines touch stays whole in the task's commit.
"""

from __future__ import annotations

import difflib
import subprocess
from collections.abc import Callable, Sequence

from framegate.adapters.base import LanguageAdapter
from framegate.state import git


def _head(adapter: LanguageAdapter, rel: str) -> bytes | None:
    """`rel` as HEAD has it, as a checkout writes it (eol filters); None = not in HEAD."""
    r = subprocess.run(
        ["git", "cat-file", "--filters", f"HEAD:{rel}"],
        cwd=adapter.cfg.root,
        capture_output=True,
    )
    return r.stdout if r.returncode == 0 else None


def _spans(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """The changed ranges of `b` (half-open line ranges; empty = a deletion point)."""
    ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    return [(j1, j2) for tag, _, _, j1, j2 in ops if tag != "equal"]


def _meet(x: tuple[int, int], y: tuple[int, int]) -> bool:
    (a, b), (c, d) = x, y
    if a == b or c == d:  # a point meets a range it is inside, or the same point
        p, (lo, hi) = (a, (c, d)) if a == b else (c, (a, b))
        return lo < p < hi or (lo == hi == p)
    return a < d and c < b


def overlaps(head: str, formatted: str, work: str) -> bool:
    """Do the formatter's changes (head -> formatted) and the task's (formatted -> work)
    touch the same lines of `formatted`?"""
    old, fmt, new = head.splitlines(), formatted.splitlines(), work.splitlines()
    mine = difflib.SequenceMatcher(None, fmt, new, autojunk=False).get_opcodes()
    task = [(i1, i2) for tag, i1, i2, _, _ in mine if tag != "equal"]
    return any(_meet(f, t) for f in _spans(old, fmt) for t in task)


def commit_format(
    adapter: LanguageAdapter, files: Sequence[str], message: Callable[[list[str]], str]
) -> tuple[list[str], list[str]]:
    """Commit `files` as HEAD has them, formatted, before the task's commit. Returns (files
    committed so, files kept whole in the task's commit: the lines overlap). The working tree
    is as it was afterwards."""
    root = adapter.cfg.root
    head = {f: b for f in files if (root / f).is_file() and (b := _head(adapter, f)) is not None}
    work = {f: (root / f).read_bytes() for f in head}
    debt = adapter.format_beyond
    split: list[str] = []
    kept: list[str] = []
    try:
        for f, data in head.items():
            (root / f).write_bytes(data)
        adapter.format_files(list(head))
        for f, data in head.items():
            old = data.decode("utf-8", "replace")
            fmt = (root / f).read_bytes().decode("utf-8", "replace")
            if fmt.splitlines() == old.splitlines():
                continue  # no debt left at HEAD
            mine = work[f].decode("utf-8", "replace")
            (kept if overlaps(old, fmt, mine) else split).append(f)
        for f in kept:
            (root / f).write_bytes(work[f])
        if split:
            git(adapter.cfg, "commit", "-q", "-m", message(split), "--", *split)
    finally:
        adapter.format_beyond = debt
        for f, data in work.items():
            (root / f).write_bytes(data)
    return split, kept
