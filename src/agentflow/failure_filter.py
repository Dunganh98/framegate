"""Shrink a failing check's output to what the implementer needs to act on: the lines that
look like failures, with a little context, plus the closing summary.

Long test and build logs often put the useful lines (the assertion, the first compiler error)
far above their end, so a plain tail of the last N lines can miss them. Short outputs are kept
whole.

Ported from harnessOrch (https://github.com/kehuydietlatoi/harnessOrch,
packages/engine/src/checks/filter.ts), shared by its author.
"""

from __future__ import annotations

import re

#: a line that looks like part of a failure (test frameworks, compilers, tracebacks)
FAILURE = re.compile(
    r"\b(fail(ed|ure|ures|ing|s)?|errors?|exception|traceback|panic|assert(ion)?\w*|expected"
    r"|actual|received|not ok|cannot|TypeError|ValueError|KeyError|AttributeError|NameError"
    r"|ImportError|SyntaxError|ReferenceError)\b|✗|×|✖",
    re.IGNORECASE,
)
#: pytest's assertion detail lines ("E       assert 3 == 5"); case-sensitive on purpose
PYTEST_E = re.compile(r"^E\s")
#: a file location: "src/x.py:12:", "Foo.cs(12,5)" (lint findings and compiler errors name
#: no failure word, but the location is what the implementer needs)
LOCATION = re.compile(r"[\w.\-/\\]+\.\w{1,6}(:\d+|\(\d+)")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
#: longest line kept (a minified bundle or a huge repr must not eat the budget)
MAX_LINE = 300
#: when nothing looks like a failure, this many tail lines are kept instead
FALLBACK_TAIL = 30


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE else line[:MAX_LINE] + "..."


def is_failure_line(line: str) -> bool:
    return bool(FAILURE.search(line) or PYTEST_E.match(line) or LOCATION.search(line))


def filter_failure_output(text: str, max_lines: int = 40, context: int = 2, tail: int = 8) -> str:
    """The failure lines of `text` with `context` lines around each, plus the last `tail`
    lines, at most `max_lines` lines (gaps marked "..."). Text of at most `max_lines` lines is
    returned whole (ANSI colors removed, runs of blank lines collapsed)."""
    raw = [line.rstrip() for line in ANSI.sub("", text).splitlines()]
    lines = [line for i, line in enumerate(raw) if line or (i and raw[i - 1])]
    while lines and not lines[-1]:
        lines.pop()
    if len(lines) <= max_lines:
        return "\n".join(_clip(line) for line in lines)

    keep: set[int] = set()
    for i, line in enumerate(lines):
        if is_failure_line(line):
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    if not keep:  # nothing looks like a failure: a longer tail
        keep.update(range(max(0, len(lines) - FALLBACK_TAIL), len(lines)))
    keep.update(range(max(0, len(lines) - tail), len(lines)))

    out: list[str] = []
    last = -1
    for i in sorted(keep):
        if last >= 0 and i > last + 1:
            out.append("...")
        out.append(_clip(lines[i]))
        last = i
    if keep and min(keep) > 0:
        out.insert(0, "...")
    if len(out) <= max_lines:
        return "\n".join(out)
    # over the budget: the first failures and the closing summary
    head = out[: max_lines - tail - 1]
    omitted = len(out) - len(head) - tail
    return "\n".join([*head, f"... ({omitted} more lines omitted)", *out[-tail:]])
