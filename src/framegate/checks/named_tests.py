"""An existing test the spec names must change. requirements.md, "Test data and fixtures",
names it as `tests/<file>::<Class>` or `tests/<file>::<Class>::<test>`; `(unchanged)` right
after the name says it stays as it is. Seen live: the spec said a test class's fixtures must
change, the test writer put everything in a new file, and only the test reviewer noticed."""

from __future__ import annotations

import re

from framegate.adapters.base import CheckResult, LanguageAdapter
from framegate.config import Config
from framegate.state import GitError, git, tag_exists

NAME = "Spec-named tests changed"
SECTION = "Test data and fixtures"
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def named(cfg: Config, feature: str) -> list[tuple[str, tuple[str, ...], bool]]:
    """(file, (Class[, test]), marked unchanged) per test the spec's section names."""
    path = cfg.spec_dir(feature) / "requirements.md"
    text = _COMMENT.sub("", path.read_text(encoding="utf-8")) if path.is_file() else ""
    head = re.search(rf"^##\s+{re.escape(SECTION)}\b.*$", text, re.M | re.I)
    if not head:
        return []
    rest = text[head.end() :]
    end = re.search(r"^##\s", rest, re.M)
    folder = re.escape(cfg.paths.tests.replace("\\", "/").strip("/"))
    name = re.compile(rf"`({folder}/[^`\s:]+)::(\w+)(?:::(\w+))?`(\s*\(unchanged\))?", re.I)
    found: dict[tuple[str, tuple[str, ...]], bool] = {}
    for m in name.finditer(rest[: end.start()] if end else rest):
        key = (m.group(1), tuple(x for x in m.group(2, 3) if x))
        found[key] = found.get(key, False) or bool(m.group(4))
    return [(rel, names, same) for (rel, names), same in found.items()]


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    from framegate import gates

    cfg = adapter.cfg
    tag = f"spec-approved/{feature}"
    tests = named(cfg, feature)
    if not tests:
        return CheckResult("PASS", f"The spec's '{SECTION}' names no existing test.")
    if not tag_exists(cfg, tag):
        return CheckResult("PASS", "Not checked: the spec approval marker is missing.")
    task_lines = gates.task_lines(adapter, feature)
    lines = [f"{len(tests)} test(s) named in the spec's '{SECTION}'."]
    failed = False
    for rel, names, same in tests:
        label = "::".join([rel, *names])
        if same:
            lines.append(f"not checked (the spec says unchanged): {label}")
            continue
        try:
            before = adapter.test_lines(git(cfg, "show", f"{tag}:{rel}"), names)
        except GitError:
            before = set()
        if before == set():
            lines.append(f"not checked (new: not there when the spec was approved): {label}")
            continue
        path = cfg.root / rel
        now = (
            adapter.test_lines(path.read_text(encoding="utf-8"), names) if path.is_file() else set()
        )
        if now == set():
            failed = True
            lines.append(
                f"GONE (removed or renamed; the spec names it as a test to change): {label}"
            )
            continue
        changed = task_lines(rel)
        # no line range for this language: a changed line anywhere in the file counts
        if changed is None or (changed if now is None else changed & now):
            lines.append(f"changed: {label}")
        else:
            failed = True
            lines.append(
                f"NOT CHANGED (the spec names it as a test whose fixtures or assertions "
                f"change; change it there, not only in a new test): {label}"
            )
    return CheckResult("FAIL" if failed else "PASS", "\n".join(lines))
