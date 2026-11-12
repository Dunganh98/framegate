"""A refactor, before any code moves: which lines of the files in scope do the tagged tests run?

The characterization tests (existing ones the test writer tagged, and new ones) are the only
proof that a refactor changed nothing. A line of the code in scope that none of them runs is
behavior nothing pins; without this report that shows only at the final gate (diff coverage,
mutation score), where a refactor has no test repair. The report is a note in test_checks.md
for the test reviewer, never blocking: a line that is run is not yet a line whose result is
asserted (the mutation check measures that), and some lines cannot matter.

Scope files: the paths in backticks in the first column of the `## Scope` table of
requirements.md that are source files today. The run: the adapter's coverage run with the
feature's spec IDs (scope_coverage_reports: Cobertura XML).
"""

from __future__ import annotations

import pathlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence

from framegate.adapters.base import CheckResult, LanguageAdapter
from framegate.checks.spec_lint import tables
from framegate.config import Config
from framegate.specs import feature_ids

NAME = "Scope coverage"
_CODE = re.compile(r"`([^`]+)`")
#: most uncovered ranges listed per file (the rest is counted)
MAX_RANGES = 40


def scope_files(cfg: Config, feature: str) -> list[str]:
    """Repo-relative posix paths of the source files the spec's Scope table names."""
    path = cfg.root / cfg.paths.specs / feature / "requirements.md"
    if not path.is_file():
        return []
    found: list[str] = []
    for table in tables(path.read_text(encoding="utf-8")):
        if not table.section.lower().startswith("scope"):
            continue
        for row in table.rows:
            for token in _CODE.findall(row[0] if row else ""):
                rel = token.strip().replace("\\", "/").lstrip("./")
                if rel and cfg.in_src(rel) and (cfg.root / rel).is_file() and rel not in found:
                    found.append(rel)
    return found


def line_hits(reports: Sequence[pathlib.Path], root: pathlib.Path) -> dict[str, dict[int, int]]:
    """Cobertura reports -> {repo-relative posix file: {line: hits}} (the most hits of any
    report; a file name is resolved against the report's <source> folders and the root)."""
    out: dict[str, dict[int, int]] = {}
    base = root.resolve()
    for report in reports:
        try:
            top = ET.parse(report).getroot()
        except (ET.ParseError, OSError):
            continue
        sources = [pathlib.Path((s.text or "").strip()) for s in top.iter("source") if s.text]
        for cls in top.iter("class"):
            name = cls.get("filename") or ""
            rel = ""
            for cand in [pathlib.Path(name), *(s / name for s in sources), base / name]:
                try:
                    if cand.is_absolute() and cand.is_file():
                        rel = cand.resolve().relative_to(base).as_posix()
                        break
                except (ValueError, OSError):
                    continue
            if not rel:
                continue
            lines = out.setdefault(rel, {})
            for line in cls.iter("line"):
                try:
                    n, hits = int(line.get("number") or 0), int(line.get("hits") or 0)
                except ValueError:
                    continue
                lines[n] = max(lines.get(n, 0), hits)
    return out


def ranges(lines: Sequence[int]) -> list[str]:
    """[3, 4, 5, 9] -> ["3-5", "9"]."""
    out: list[str] = []
    start = prev = None
    for n in sorted(set(lines)):
        if prev is not None and n == prev + 1:
            prev = n
            continue
        if start is not None and prev is not None:
            out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    if start is not None and prev is not None:
        out.append(str(start) if start == prev else f"{start}-{prev}")
    return out


def report(files: Sequence[str], hits: dict[str, dict[int, int]]) -> tuple[list[str], int]:
    """(one line per scope file, lines no test ran)."""
    lines: list[str] = []
    missed = 0
    for f in files:
        got = hits.get(f)
        if not got:
            lines.append(f"{f}: not in the coverage report (no tagged test runs it, or not built)")
            continue
        not_run = [n for n, h in got.items() if h == 0]
        missed += len(not_run)
        pct = 100 * (len(got) - len(not_run)) / len(got)
        line = f"{f}: {pct:.0f}% ({len(got) - len(not_run)}/{len(got)} lines run)"
        if not_run:
            rs = ranges(not_run)
            more = f" (+{len(rs) - MAX_RANGES} more)" if len(rs) > MAX_RANGES else ""
            line += "; not run: " + ", ".join(rs[:MAX_RANGES]) + more
        lines.append(line)
    return lines, missed


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    cfg = adapter.cfg
    files = scope_files(cfg, feature)
    if not files:
        return CheckResult(
            "SKIPPED",
            "SKIPPED: the spec's Scope table names no existing source file in backticks.",
        )
    ids = sorted(feature_ids(cfg, feature))
    reports, note = adapter.scope_coverage_reports(ids, files)
    if not reports:
        return CheckResult("SKIPPED", f"SKIPPED: {note or 'no coverage report'}")
    lines, missed = report(files, line_hits(reports, cfg.root))
    head = (
        f"Lines of the {len(files)} file(s) in scope that the feature's tagged tests run, on "
        f"today's code. {missed} line(s) are run by no tagged test: what they do is pinned by "
        "nothing (a line that is run is not yet asserted; the mutation check measures that)."
    )
    return CheckResult("PASS", "\n".join([head, *lines]), summary=f"{missed} line(s) not run")
