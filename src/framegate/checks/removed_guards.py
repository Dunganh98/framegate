"""A final check: suppressions and catch-all handlers the task removed.

Seen live: a final repair made a lint finding go away by deleting the `except Exception`
around the call, and a `# type: ignore` with it; every gate passed. Per source file the task
changed, each guard the adapter knows (LanguageAdapter.guard_patterns: `# noqa`,
`# type: ignore`, `pragma: no cover`, `except Exception`, a bare `except:`) that the base
has and the file no longer has is listed. The base is the approved code formatted as the
gates format (task_base.py), a guard counts by its text per file, so a rewrapped or moved
one is not gone. A removal passes only when steps.md names it: one line with the file, the
guard and a spec ID.
"""

from __future__ import annotations

import re
from collections import Counter

from framegate import task_base
from framegate.adapters.base import CheckResult, LanguageAdapter
from framegate.checks import frame_diff
from framegate.specs import feature_ids
from framegate.state import GitError, git, tag_exists

NAME = "removed suppressions and catch-alls"


def guards(text: str, patterns: dict[str, str]) -> Counter[tuple[str, str]]:
    """(label, the matched text) -> how often `text` has it."""
    found: Counter[tuple[str, str]] = Counter()
    for label, pattern in patterns.items():
        for m in re.finditer(pattern, text, re.M):
            found[(label, " ".join(m.group(0).split()))] += 1
    return found


def removed(before: str, after: str, patterns: dict[str, str]) -> list[tuple[str, str]]:
    """The guards `before` has more of than `after`: (label, text), once per one gone."""
    gone = guards(before, patterns) - guards(after, patterns)
    return sorted(gone.elements())


def named(steps: str, rel: str, label: str, text: str, ids: set[str]) -> bool:
    """Does a line of steps.md name this removal: the file (path or name), the guard (its
    label or its text) and a spec ID of the feature?"""
    words = (label.lstrip("# ").lower(), text.lstrip("# ").lower())
    for line in steps.splitlines():
        low = line.lower()
        if (
            (rel.lower() in low or rel.rsplit("/", 1)[-1].lower() in low)
            and any(w in low for w in words)
            and any(re.search(rf"\b{re.escape(i)}\b", line) for i in ids)
        ):
            return True
    return False


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    cfg = adapter.cfg
    patterns = adapter.guard_patterns()
    if not patterns:
        return CheckResult("SKIPPED", f"SKIPPED: no guard patterns for the {adapter.name} adapter")
    tags = (frame_diff.approved_tag(feature), f"spec-approved/{feature}")
    marker = next((t for t in tags if tag_exists(cfg, t)), None)
    if marker is None:
        return CheckResult("PASS", "Not checked: no approval marker.")
    base = task_base.task_base(adapter, marker) or marker
    steps_file = cfg.feature_out(feature) / "steps.md"
    steps = steps_file.read_text(encoding="utf-8") if steps_file.is_file() else ""
    ids = feature_ids(cfg, feature)
    lines: list[str] = []
    failed = 0
    for rel in frame_diff.changed_src_files(adapter, marker):
        try:
            before = git(cfg, "show", f"{base}:{rel}")
        except GitError:
            continue  # a new file
        after = (cfg.root / rel).read_text(encoding="utf-8", errors="replace")
        for label, text in removed(before, after, patterns):
            if named(steps, rel, label, text, ids):
                lines.append(f"removed, named in steps.md: {rel}: {label} (`{text}`)")
            else:
                failed += 1
                lines.append(f"REMOVED, NOT NAMED: {rel}: {label} (`{text}`)")
    if failed:
        lines.append(
            "Put each one back, or name the removal in a line of your step note: the file, "
            "the guard and the spec ID that needs it gone "
            "(`Removed <guard> in <file>: <spec ID>, <why>`)."
        )
    head = f"{failed} removed without a spec ID" if failed else "Nothing removed without a spec ID."
    return CheckResult("FAIL" if failed else "PASS", "\n".join([head, *lines]))
