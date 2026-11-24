"""Notes of a green step gate, never a failure: lines of the changed functions that no test
of the feature runs, and untouched comments that mention existing data the spec names."""

from __future__ import annotations

import ast
import re

from framegate.adapters.base import LanguageAdapter, changed_lines, cobertura_hits
from framegate.checks.frame_diff import changed_src_files
from framegate.checks.spec_lint import tables
from framegate.config import Config
from framegate.specs import feature_ids

#: most lines per note
MAX_LINES = 15
#: {out}/<feature>/<this>: the notes of every green step gate
FILE = "step_gate_notes.md"
UNEXECUTED = "Never run by the feature's tests, in the functions this step changed:"
STALE = (
    "Comments the step did not touch that mention existing data the spec names (reread "
    "them; reword what the change makes wrong):"
)
_CODE = re.compile(r"`([^`]+)`")
_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_HASH = re.compile(r"(?:^|\s)#(.*)$")
_SLASH = re.compile(r"(?:^|\s)(?://|/\*)(.*)$|^\s*\*(.*)$")
_HASH_SUFFIXES = (".py", ".rb", ".sh", ".ps1", ".r", ".pl")


def step_files(adapter: LanguageAdapter) -> list[str]:
    """The source files the run before this gate changed and did not commit yet."""
    suffixes = tuple(adapter.source_suffixes)
    files = changed_src_files(adapter, "HEAD")
    return [f for f in files if not suffixes or f.endswith(suffixes)]


def _ranges(missing: list[int], hits: dict[int, int]) -> list[str]:
    """Line ranges; two lines are one range when no line between them ran."""
    out: list[list[int]] = []
    for n in sorted(missing):
        if out and not any(hits.get(k, 0) > 0 for k in range(out[-1][1] + 1, n)):
            out[-1][1] = n
        else:
            out.append([n, n])
    return [str(a) if a == b else f"{a}-{b}" for a, b in out]


def unexecuted(adapter: LanguageAdapter, feature: str, files: list[str]) -> list[str]:
    """`<file> <function>: <ranges>` per changed function with lines no feature test runs."""
    cfg = adapter.cfg
    bodies: list[tuple[str, str, set[int]]] = []
    for rel in files:
        for name in adapter.changed_symbols(rel, changed_lines(cfg, "HEAD", rel)):
            body = adapter.function_body_lines(rel, name)
            if body:
                bodies.append((rel, name, body))
    if not bodies:
        return []
    ids = sorted(feature_ids(cfg, feature))
    reports, _ = adapter.scope_coverage_reports(ids, sorted({rel for rel, _, _ in bodies}))
    hits = cobertura_hits(reports, cfg.root) if reports else {}
    out = []
    for rel, name, body in bodies:
        got = hits.get(rel)
        missing = [n for n in body if got and got.get(n) == 0]
        if got and missing:
            out.append(f"{rel} {name}: {', '.join(_ranges(missing, got))}")
    return out


def members(cfg: Config, feature: str) -> list[str]:
    """The member names of the spec's "Existing data this spec reads" table (last part)."""
    path = cfg.spec_dir(feature) / "requirements.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    found: list[str] = []
    for table in tables(text):
        col = table.column("exact member")
        if col is None or not table.section.lower().startswith("existing data"):
            continue
        for row in table.rows:
            cell = row[col] if col < len(row) else ""
            for token in _CODE.findall(cell) or [cell]:
                m = _NAME.search(token)
                name = m.group(0).rsplit(".", 1)[-1] if m else ""
                if len(name) > 2 and name not in found:
                    found.append(name)
    return found


def comment_lines(rel: str, text: str) -> dict[int, str]:
    """Line -> its comment text (a Python docstring's lines too)."""
    hash_style = rel.endswith(_HASH_SUFFIXES)
    out: dict[int, str] = {}
    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        m = (_HASH if hash_style else _SLASH).search(line)
        if m:
            out[n] = line.strip()
    if rel.endswith(".py"):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return out
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and ast.get_docstring(node) is not None:
                for n in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                    out[n] = lines[n - 1].strip()
    return out


def stale_candidates(adapter: LanguageAdapter, feature: str, files: list[str]) -> list[str]:
    """`<file>:<line>: <comment>` per untouched comment line that names a spec member."""
    cfg = adapter.cfg
    names = members(cfg, feature)
    if not names:
        return []
    word = re.compile(r"(?<![\w.])(?:\w+\.)*(?:" + "|".join(map(re.escape, names)) + r")\b")
    out = []
    for rel in files:
        changed = changed_lines(cfg, "HEAD", rel)
        if changed is None:
            continue  # a new file: every line is the step's
        try:
            text = (cfg.root / rel).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for n, comment in sorted(comment_lines(rel, text).items()):
            if n not in changed and word.search(comment):
                out.append(f"{rel}:{n}: {comment[:120]}")
    return out


def _block(title: str, found: list[str]) -> list[str]:
    if not found:
        return []
    more = [f"  ... {len(found) - MAX_LINES} more"] if len(found) > MAX_LINES else []
    return [title, *(f"  {x}" for x in found[:MAX_LINES]), *more]


def lines(adapter: LanguageAdapter, feature: str) -> list[str]:
    """The notes of this step gate ([] = nothing to say)."""
    files = step_files(adapter)
    if not files:
        return []
    return [
        *_block(UNEXECUTED, unexecuted(adapter, feature, files)),
        *_block(STALE, stale_candidates(adapter, feature, files)),
    ]
