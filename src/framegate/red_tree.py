"""Which files a check's output names, and a failing lint / format / typecheck / imports
check judged by the lines the task itself wrote.

Seen live (three replay runs on three repositories): a format check that wanted to reformat a
file from before the feature, 353 lint findings in a repository that never configured the
linter. Every step gate blamed the feature, the steps parked, and the run reached the final
gate with nothing built. So a finding counts only in a file the task changed, and there only
on a line the task wrote (not on old lines, not on lines only the formatter rewrote).
"""

from __future__ import annotations

import pathlib
import re
from collections import Counter
from collections.abc import Callable

from framegate.adapters.base import CheckResult, LanguageAdapter

#: under a lint / format / typecheck / imports check: the findings that are not the task's
LEFT_OUT = "not on the task's own lines (left out)"


def _path_regex(adapter: LanguageAdapter) -> re.Pattern[str]:
    sfx = "|".join(re.escape(s.lstrip(".")) for s in adapter.source_suffixes) or r"\w+"
    # the line number a tool puts behind the file: `x.py:12:`, `x.py(12,5)`, `x.py", line 12`
    return re.compile(
        rf'(?P<path>(?:[A-Za-z]:)?[\w.\-/\\]+\.(?:{sfx}))\b(?:(?::|\(|", line )(?P<line>\d+))?'
    )


def line_refs(adapter: LanguageAdapter, line: str) -> dict[str, int | None]:
    """Repository file (posix, relative to the root) -> the line number one output line
    names behind it (None = none)."""
    root = adapter.cfg.root.resolve()
    found: dict[str, int | None] = {}
    for m in _path_regex(adapter).finditer(line):
        path = pathlib.Path(m.group("path").replace("\\", "/"))
        try:
            rel = (path if path.is_absolute() else root / path).resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        if (root / rel).is_file():
            found[rel.as_posix()] = int(m.group("line")) if m.group("line") else None
    return found


def line_files(adapter: LanguageAdapter, line: str) -> set[str]:
    """Repository files one output line names."""
    return set(line_refs(adapter, line))


def file_counts(adapter: LanguageAdapter, text: str) -> dict[str, int]:
    """File -> the number of output lines that name it. A tool prints the same number of
    such lines per finding from run to run, so a higher number means more findings."""
    counts: Counter[str] = Counter()
    for line in text.splitlines():
        counts.update(line_files(adapter, line))
    return dict(counts)


def in_changed(
    adapter: LanguageAdapter,
    changed: set[str],
    res: CheckResult,
    task_lines: Callable[[str], set[int] | None] | None = None,
) -> CheckResult:
    """A failing check with only the task's findings: PASS when it has none. A finding is
    the task's when it names a `changed` file and, where it names a line and `task_lines`
    (file -> the lines the task wrote, None = all) is given, one of those lines. The output
    is read in blocks (separated by an empty line: ruff prints one per finding); a block
    without a finding of the task is dropped, and so is such a line (mypy: one line per
    finding). An output that names no file stays as it is: it cannot be told whose it is."""
    if res.status != "FAIL" or res.tool_failure:
        return res

    def ours(refs: dict[str, int | None]) -> bool:
        for file, n in refs.items():
            if file not in changed:
                continue
            wrote = task_lines(file) if task_lines and n is not None else None
            if wrote is None or n in wrote:
                return True
        return False

    kept: list[str] = []
    mine = other = 0
    for block in re.split(r"\n[ \t]*\n", res.output):
        lines = [(line, line_refs(adapter, line)) for line in block.splitlines()]
        keep = [line for line, refs in lines if not refs or ours(refs)]
        hits = sum(1 for _, refs in lines if ours(refs))
        mine += hits
        other += len(lines) - len(keep)
        if hits or not any(refs for _, refs in lines):
            kept.append("\n".join(keep))
    if not other:
        return res
    left_out = f"{LEFT_OUT}: {other} line(s)"
    if not mine:
        return CheckResult("PASS", res.output, left_out)
    return CheckResult("FAIL", "\n\n".join(kept) + f"\n\n({left_out})", left_out)
