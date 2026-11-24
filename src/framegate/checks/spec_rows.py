"""Spec checks at approval (spec_lint.problems): an ID on an untested case, two existing
tests on one line of "Test data and fixtures", a refusal without a source."""

from __future__ import annotations

import re

from framegate.checks.spec_lint import (
    _NONE_ROW,
    _QUOTED,
    _REQUIREMENT_SECTIONS,
    _cell,
    _clip,
    _read,
    _section,
    tables,
)
from framegate.config import Config

_UNTESTED = re.compile(
    r"\buntested\b|\bnot\s+tested\b|\bno\s+tests?\b|\bundefined\s+(?:by\s+design|behaviou?r)\b"
    r"|\b(?:left|stays|remains|behaviou?r\s+is|result\s+is|outcome\s+is)\s+undefined\b",
    re.I,
)
_SOURCE = re.compile(
    r"^[`*\s]*(?:(?:task|asked)\b|code\b[`*\s]*:\s*`?[\w./\-]+\.\w+`?\s*:\s*\S"
    r"|docs\b[`*\s]*:\s*`?[\w./\-]*\w\.\w+)",
    re.I,
)
SOURCE_HOW = (
    "Write `task`, `code: <file>:<symbol>` (existing code that refuses this way), "
    "`docs: <file>` or `asked` (the user answered); an error type, a message or a rule on "
    "what is allowed that none of them gives is a question to the user first"
)


def untested_id_problems(cfg: Config, feature: str) -> list[str]:
    """Rows with a spec ID whose own text says the case is untested or undefined."""
    problems = []
    for table in tables(_read(cfg.spec_dir(feature) / "requirements.md")):
        id_col = table.column("id")
        section = table.section.lower()
        data = section.startswith("test data")
        if id_col is None or not (data or section.startswith(_REQUIREMENT_SECTIONS)):
            continue
        for row in table.rows:
            ids = re.findall(cfg.id_pattern, _cell(row, id_col))
            text = _QUOTED.sub(" ", " ".join(c for i, c in enumerate(row) if i != id_col))
            m = _UNTESTED.search(text)
            if ids and m:
                problems.append(
                    f"requirements.md, {table.section}, {', '.join(ids)}: the row says "
                    f'"{m.group(0)}", but every ID gets a test. Drop the ID (a case left open '
                    "has none), or define the behaviour"
                )
    return problems


def named_test_line_problems(cfg: Config, feature: str) -> list[str]:
    """Lines of "Test data and fixtures" that name two existing tests, or one without its file."""
    from framegate.checks.named_tests import SECTION

    text = _read(cfg.spec_dir(feature) / "requirements.md")
    folder = re.escape(cfg.paths.tests.replace("\\", "/").strip("/"))
    full = re.compile(rf"`{folder}/[^`\s:]+::\w+(?:::\w+)?`")
    short = re.compile(r"`::\w+(?:::\w+)?`")
    problems = []
    for line in _section(text, SECTION).replace("\\", "/").splitlines():
        where = f'requirements.md, {SECTION}, "{_clip(line.strip())}"'
        if short.search(line):
            problems.append(
                f"{where}: a test named without its file. Write `{cfg.paths.tests}/<file>::"
                "<Class>::<test>`, on a line of its own"
            )
        elif len(full.findall(line)) > 1:
            problems.append(
                f"{where}: more than one existing test on a line. One test per line, so "
                "`(unchanged)` after it belongs to that test only"
            )
    return problems


def refusal_source_problems(cfg: Config, feature: str) -> list[str]:
    """design.md, "Error handling": a filled row whose "Source" cell names no source."""
    problems = []
    for table in tables(_read(cfg.spec_dir(feature) / "design.md")):
        col = table.column("source")
        if col is None or not table.section.lower().startswith("error handling"):
            continue  # no such column: written from an older template
        for row in table.rows:
            rule = next((c for i, c in enumerate(row) if i != col and c), "")
            if not rule or _NONE_ROW.match(rule):
                continue
            cell = _cell(row, col)
            if not _SOURCE.match(cell):
                problems.append(
                    f'design.md, {table.section}, "{_clip(rule)}": no source for this refusal '
                    f'("{_clip(cell, 30)}"). {SOURCE_HOW}'
                )
    return problems


def problems(cfg: Config, feature: str) -> list[str]:
    return [
        *untested_id_problems(cfg, feature),
        *named_test_line_problems(cfg, feature),
        *refusal_source_problems(cfg, feature),
    ]
