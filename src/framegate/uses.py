"""`framegate uses <symbol>`: every place in the source (not the tests) that reads a
function or a variable, as `file:line: line`.

For the spec writer: a requirement that changes what an input may be (None allowed, a new
unit, another type) holds only when every use site was looked at. A search by the name's
word, with the adapter's pattern for the lines that define it (LanguageAdapter.
definition_pattern), which are listed apart. Comment lines are left out.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

from framegate.adapters.base import LanguageAdapter
from framegate.checks.structure import SKIP_DIRS
from framegate.config import Config


@dataclass
class Uses:
    symbol: str
    defined: list[str] = field(default_factory=list)
    read: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"{len(self.read)} use(s) of `{self.symbol}` in the source (tests left out):"]
        out += self.read
        if self.defined:
            out += [f"Defined or assigned ({len(self.defined)}):", *self.defined]
        return out


def source_files(cfg: Config, adapter: LanguageAdapter, only: str = "") -> list[pathlib.Path]:
    """The source files under [paths] src (`only`: under that file or folder), without the
    test folders."""
    suffixes = tuple(adapter.source_suffixes)
    tests = [(cfg.root / t).resolve() for t in (cfg.paths.tests, cfg.paths.review_tests)]
    starts = [cfg.root / only] if only else [cfg.root / r for r in cfg.src_roots]
    found: set[pathlib.Path] = set()
    for start in starts:
        files = [start] if start.is_file() else start.rglob("*") if start.is_dir() else []
        for p in files:
            if not p.is_file() or (suffixes and not p.name.endswith(suffixes)):
                continue
            rel = p.relative_to(cfg.root)
            if SKIP_DIRS & set(rel.parts[:-1]) or any(t in p.resolve().parents for t in tests):
                continue
            found.add(p)
    return sorted(found)


def find(cfg: Config, adapter: LanguageAdapter, symbol: str, only: str = "") -> Uses:
    name = re.split(r"[.:]+", symbol.strip().rstrip("()"))[-1]
    out = Uses(name)
    if not re.fullmatch(r"\w+", name):
        return out
    word = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])")
    pattern = adapter.definition_pattern(name)
    defines = re.compile(pattern) if pattern else None
    comment = tuple(adapter.comment_prefixes)
    for p in source_files(cfg, adapter, only):
        rel = p.relative_to(cfg.root).as_posix()
        text = p.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if not word.search(line) or (comment and line.lstrip().startswith(comment)):
                continue
            hit = f"{rel}:{n}: {line.strip()}"
            (out.defined if defines and defines.search(line) else out.read).append(hit)
    return out


def run(cfg: Config, adapter: LanguageAdapter, symbol: str, only: str = "") -> int:
    for line in find(cfg, adapter, symbol, only).lines():
        print(line)
    return 0
