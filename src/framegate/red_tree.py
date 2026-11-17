"""Which files a check's output names, and a failing lint / format / typecheck / imports
check judged by the files the task changed.

Seen live (three replay runs on three repositories): a format check that wanted to reformat a
file from before the feature, 353 lint findings in a repository that never configured the
linter. Every step gate blamed the feature, the steps parked, and the run reached the final
gate with nothing built. So a finding counts only in a file the task changed.
"""

from __future__ import annotations

import pathlib
import re
from collections import Counter

from framegate.adapters.base import CheckResult, LanguageAdapter


def _path_regex(adapter: LanguageAdapter) -> re.Pattern[str]:
    sfx = "|".join(re.escape(s.lstrip(".")) for s in adapter.source_suffixes) or r"\w+"
    return re.compile(rf"(?:[A-Za-z]:)?[\w.\-/\\]+\.(?:{sfx})\b")


def line_files(adapter: LanguageAdapter, line: str) -> set[str]:
    """Repository files (posix, relative to the root) one output line names."""
    root = adapter.cfg.root.resolve()
    found = set()
    for m in _path_regex(adapter).finditer(line):
        path = pathlib.Path(m.group(0).replace("\\", "/"))
        try:
            rel = (path if path.is_absolute() else root / path).resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        if (root / rel).is_file():
            found.add(rel.as_posix())
    return found


def file_counts(adapter: LanguageAdapter, text: str) -> dict[str, int]:
    """File -> the number of output lines that name it. A tool prints the same number of
    such lines per finding from run to run, so a higher number means more findings."""
    counts: Counter[str] = Counter()
    for line in text.splitlines():
        counts.update(line_files(adapter, line))
    return dict(counts)


def in_changed(adapter: LanguageAdapter, changed: set[str], res: CheckResult) -> CheckResult:
    """A failing check with only the findings in `changed` files: PASS when every file it
    names is another one. The output is read in blocks (separated by an empty line: ruff
    prints one per finding); a block that names only other files is dropped, and so is a
    line that does (mypy: one line per finding). An output that names no file stays as it
    is: it cannot be told whose it is."""
    if res.status != "FAIL" or res.tool_failure:
        return res
    kept: list[str] = []
    mine = other = 0
    for block in re.split(r"\n[ \t]*\n", res.output):
        lines = [(line, line_files(adapter, line)) for line in block.splitlines()]
        ours = [line for line, files in lines if not files or files & changed]
        hits = sum(1 for _, files in lines if files & changed)
        mine += hits
        other += len(lines) - len(ours)
        if hits or not any(files for _, files in lines):
            kept.append("\n".join(ours))
    if not other:
        return res
    left_out = f"in files the task did not change (left out): {other} line(s)"
    if not mine:
        return CheckResult("PASS", res.output, left_out)
    return CheckResult("FAIL", "\n\n".join(kept) + f"\n\n({left_out})", left_out)
