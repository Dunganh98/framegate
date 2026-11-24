"""Step gate: the step's diff must touch the lines its plan step names as `Reword:
<file>:<lines>` (numbered as at the spec approval); the new wording is not judged."""

from __future__ import annotations

import re
from collections.abc import Sequence

from framegate.adapters.base import CheckResult, LanguageAdapter
from framegate.checks.frame_diff import approved_tag
from framegate.checks.plan_files import step_bodies
from framegate.config import Config
from framegate.state import GitError, git, tag_exists

NAME = "plan rewords"
_LINE = re.compile(r"^\W*Reword\s*:\s*(.*)$", re.I | re.M)
_RANGE = r"(\d+)(?:\s*[-–]\s*(\d+))?"
_TARGET = re.compile(rf"`?([\w./\\-]*\w\.\w+)`?\s*:\s*{_RANGE}")
_MORE = re.compile(
    rf"\s*(?:…|\.{{3}})?\s*(?:,|;|and|&)\s*(?:lines?\s*)?{_RANGE}(?![\w.:/\\])", re.I
)
_OLD = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+", re.M)

Range = tuple[str, int, int]


def ranges(body: str) -> list[Range]:
    """(file, first, last) of every range a step's `Reword:` lines name, each once."""
    found: list[Range] = []
    for line in _LINE.findall(body):
        for m in _TARGET.finditer(line):
            rel = m.group(1).replace("\\", "/").removeprefix("./")
            spans = [m.group(2, 3)]
            at = m.end()
            while more := _MORE.match(line, at):
                spans.append(more.group(1, 2))
                at = more.end()
            for a, b in spans:
                lo, hi = sorted((int(a), int(b or a)))
                if (rel, lo, hi) not in found:
                    found.append((rel, lo, hi))
    return found


def step_ranges(plan: str, steps: Sequence[int]) -> dict[int, list[Range]]:
    """Plan step -> its reword ranges, for the steps of `steps` that name one."""
    bodies = step_bodies(plan)
    found = {n: ranges(bodies.get(n, "")) for n in steps}
    return {n: r for n, r in found.items() if r}


def touched(diff: str, first: int, last: int) -> bool:
    """Does a `git diff -U0` of one file touch the old lines first..last?"""
    for m in _OLD.finditer(diff):
        start, count = int(m.group(1)), int(m.group(2) or "1")
        if count == 0:  # lines added after `start`
            if first <= start < last:
                return True
        elif start <= last and start + count - 1 >= first:
            return True
    return False


def base_ref(cfg: Config, feature: str) -> str | None:
    tags = (f"spec-approved/{feature}", approved_tag(feature))
    return next((t for t in tags if tag_exists(cfg, t)), None)


def check(adapter: LanguageAdapter, feature: str, steps: Sequence[int]) -> CheckResult:
    cfg = adapter.cfg
    path = cfg.spec_dir(feature) / "plan.md"
    plan = path.read_text(encoding="utf-8") if path.is_file() else ""
    named = step_ranges(plan, steps)
    if not named:
        return CheckResult("PASS", "The step names no line to reword.")
    ref = base_ref(cfg, feature)
    if ref is None:
        return CheckResult("PASS", "Not checked: the spec approval marker is missing.")
    lines, missed, total = [], 0, 0
    diffs: dict[str, str | None] = {}
    for num, found in sorted(named.items()):
        for rel, first, last in found:
            where = f"{rel}:{first}-{last} (plan step {num})"
            if rel not in diffs:
                try:
                    git(cfg, "cat-file", "-e", f"{ref}:{rel}")
                    diffs[rel] = git(cfg, "diff", "-U0", ref, "--", rel)
                except GitError:
                    diffs[rel] = None
            diff = diffs[rel]
            if diff is None:
                lines.append(f"not checked (no such file when the spec was approved): {where}")
                continue
            total += 1
            if touched(diff, first, last):
                lines.append(f"touched: {where}")
                continue
            missed += 1
            why = "the lines are as they were" if diff.strip() else "the file is not changed"
            lines.append(f"REWORD NOT DONE: {where}: {why}")
    summary = f"{total - missed}/{total} range(s) touched"
    if not missed:
        return CheckResult("PASS", "\n".join(lines), summary)
    lines.append(
        "The plan step names these lines as text its change makes wrong (line numbers of the "
        "file when the spec was approved). Reword them in this run."
    )
    return CheckResult("FAIL", "\n".join(lines), summary)
