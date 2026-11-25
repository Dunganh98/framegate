"""Step and fix gates: the lines a run adds to non-test source hold no new docstring and no
spec ID ([gates] docstring_check). A docstring that was there before may stay, be reworded,
moved or deleted: only one on a member that had none, with a text no changed file had."""

from __future__ import annotations

import re

from framegate.adapters.base import CheckResult, LanguageAdapter, changed_lines
from framegate.checks.frame_diff import changed_src_files
from framegate.config import Config
from framegate.state import GitError, git

NAME = "no new docstrings or spec IDs in source"
ADVICE = (
    "Lines this run added to source files. Write no docstring and no spec ID in source "
    "code: delete these. A docstring that was there before the run may stay, be reworded "
    "or be deleted."
)


def _squeeze(text: str) -> str:
    return " ".join(text.split())


def _is_test(cfg: Config, rel: str) -> bool:
    folders = (cfg.paths.tests, cfg.paths.review_tests)
    return any(rel.startswith(t.rstrip("/") + "/") for t in folders if t)


def _at(cfg: Config, base: str, rel: str) -> str:
    try:
        return git(cfg, "show", f"{base}:{rel}")
    except GitError:
        return ""


def problems(adapter: LanguageAdapter, base: str = "HEAD") -> list[str]:
    """`file:line: what` per added line with a spec ID and per new docstring."""
    cfg = adapter.cfg
    suffixes = tuple(adapter.source_suffixes)
    files = [
        f
        for f in changed_src_files(adapter, base)
        if (not suffixes or f.endswith(suffixes)) and not _is_test(cfg, f)
    ]
    try:
        gone = git(cfg, "diff", "--name-only", "--diff-filter=D", base, "--", *cfg.src_roots)
    except GitError:
        gone = ""
    before = {f: _at(cfg, base, f) for f in [*files, *gone.split()]}
    old = {f: adapter.doc_blocks(text) or [] for f, text in before.items() if text}
    known = {_squeeze(d.text) for docs in old.values() for d in docs}
    spec_id = re.compile(cfg.id_pattern)
    out: list[str] = []
    for rel in files:
        text = (cfg.root / rel).read_text(encoding="utf-8", errors="replace")
        added = changed_lines(cfg, base, rel)
        for n, line in enumerate(text.splitlines(), 1):
            m = spec_id.search(line) if added is None or n in added else None
            if m:
                out.append(f"{rel}:{n}: spec ID {m.group(0)}")
        had = {d.owner for d in old.get(rel, [])}
        for d in adapter.doc_blocks(text) or []:
            touched = added is None or any(n in added for n in range(d.first, d.last + 1))
            if touched and d.owner not in had and _squeeze(d.text) not in known:
                out.append(f"{rel}:{d.first}: new docstring ({d.owner or 'the file'})")
    return sorted(out)


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    found = problems(adapter)
    if not found:
        return CheckResult("PASS", "")
    return CheckResult("FAIL", "\n".join([ADVICE, *(f"  {x}" for x in found)]), str(len(found)))
