"""Moved code (git's `--color-moved` idea): the frame diff and diff coverage skip it."""

from __future__ import annotations

import re

from framegate.config import Config, drop_machine_files
from framegate.state import git

MIN_BLOCK = 3
TRIVIAL = re.compile(r"[\W_]*|pass|else:|try:|finally:|end|break|continue|return")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

Added = dict[str, list[tuple[int, str]]]


def norm(line: str) -> str:
    return " ".join(line.split())


def parse_diff(diff: str) -> tuple[Added, set[str]]:
    added: Added = {}
    removed: set[str] = set()
    path, n, in_hunk = "", 0, False
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            in_hunk = False
        elif not in_hunk and line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
        elif m := _HUNK.match(line):
            in_hunk, n = True, int(m.group(1))
        elif in_hunk and line.startswith("+"):
            added.setdefault(path, []).append((n, line[1:]))
            n += 1
        elif in_hunk and line.startswith("-"):
            removed.add(norm(line[1:]))
    return added, removed


def find_moved(added: Added, removed: set[str]) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for path, rows in added.items():
        runs: list[list[tuple[int, str]]] = []
        for n, text in rows:
            t = norm(text)
            if t not in removed:
                continue
            if runs and runs[-1][-1][0] == n - 1:
                runs[-1].append((n, t))
            else:
                runs.append([(n, t)])
        lines = {
            n
            for run in runs
            if sum(not TRIVIAL.fullmatch(t) for _, t in run) >= MIN_BLOCK
            for n, _ in run
        }
        if lines:
            out[path] = lines
    return out


def work_changes(cfg: Config, ref: str) -> tuple[Added, set[str]]:
    diff = git(
        cfg, "-c", "core.quotePath=false", "diff", "-U0", "--src-prefix=a/", "--dst-prefix=b/", ref
    )
    added, removed = parse_diff(diff)
    untracked = git(cfg, "ls-files", "--others", "--exclude-standard").splitlines()
    for f in drop_machine_files(cfg, untracked):
        path = cfg.root / f
        if cfg.in_src(f) and path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            added[f] = list(enumerate(text.splitlines(), 1))
    return added, removed


def moved_lines(cfg: Config, ref: str) -> dict[str, set[int]]:
    return find_moved(*work_changes(cfg, ref))


def moved_note(count: int) -> str:
    return f"moved, not counted: {count} lines" if count else ""
