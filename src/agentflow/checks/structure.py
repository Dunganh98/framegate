"""Structure report for the final gate (never blocking): sizes of the source files the feature
changed, their longest methods, and new public types the code map does not mention.

Seen live (FCSim M5-freekick-review): SetPieceShotModel.cs grew to 694 lines, mixing the kick
model with the review calculators; every check passed and nobody saw it until the PR. The human
owns what each class is for and how the classes work together, so a file over `[gates]
max_file_lines` becomes a question at the final gate, and a new public type missing from the code
map (`[docs] code_map`) is a warning there.

Method lengths: Python from the syntax tree; other languages by a brace heuristic (a line that
looks like a method signature, counted to its closing brace). Good enough for a report.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from agentflow.adapters.base import LanguageAdapter
from agentflow.checks.existing_members import base_ref
from agentflow.checks.frame_diff import changed_src_files
from agentflow.state import GitError, git

#: methods listed per report (the longest first)
LONGEST = 5
#: a method signature in a brace language (C#, Java, TypeScript, Go, Rust, ...)
SIGNATURE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|async|override|virtual|sealed|abstract"
    r"|partial|unsafe|extern|readonly|export|pub|fn|func|function)\s+)*"
    r"(?:[\w<>\[\],.?*&:]+\s+)*?(\w+)\s*(?:<[^>]*>)?\s*\([^;]*$"
)
NOT_METHODS = set(
    "if for foreach while switch catch using lock return new else fixed when nameof typeof "
    "sizeof default".split()
)
#: a new public type on an added diff line, per adapter
PUBLIC_TYPE = {
    "python": re.compile(r"^\+class\s+([A-Za-z]\w*)"),
    "dotnet": re.compile(
        r"^\+\s*public\s+(?:(?:static|sealed|abstract|partial|readonly|ref|unsafe|new|file)\s+)*"
        r"(?:class|struct|interface|enum|record(?:\s+struct|\s+class)?)\s+(\w+)"
    ),
}
GENERIC_TYPE = re.compile(
    r"^\+\s*(?:export\s+(?:default\s+)?|pub(?:\([^)]*\))?\s+|public\s+)"
    r"(?:(?:abstract|final|sealed|static|data|async)\s+)*"
    r"(?:class|struct|interface|enum|trait|type|record)\s+(\w+)"
)


@dataclass
class FileSize:
    path: str
    lines: int
    #: (method name, lines), longest first
    methods: list[tuple[str, int]] = field(default_factory=list)
    #: lines before the feature (None: a new file)
    before: int | None = None


@dataclass
class Structure:
    files: list[FileSize] = field(default_factory=list)
    limit: int = 0
    note: str = ""

    @property
    def over_limit(self) -> list[FileSize]:
        return [f for f in self.files if self.limit and f.lines > self.limit]

    def longest_methods(self, n: int = LONGEST) -> list[tuple[str, str, int]]:
        """(file, method, lines) over all files, longest first."""
        every = [(f.path, m, n_) for f in self.files for m, n_ in f.methods]
        return sorted(every, key=lambda x: -x[2])[:n]

    def lines(self) -> list[str]:
        """The report section of final_gates.md."""
        out = ["Structure (not blocking; files the feature changed):"]
        if self.note:
            return [*out, f"  {self.note}"]
        if not self.files:
            return [*out, "  no changed source files"]
        for f in sorted(self.files, key=lambda x: -x.lines):
            flag = f"  OVER {self.limit}" if self.limit and f.lines > self.limit else ""
            was = "new" if f.before is None else f"was {f.before}"
            out.append(f"  {f.path}: {f.lines} lines ({was}){flag}")
        longest = self.longest_methods()
        if longest:
            out.append("  longest methods: " + ", ".join(f"{m} ({n}, {p})" for p, m, n in longest))
        return out

    def as_dict(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "files": {f.path: f.lines for f in self.files},
            "before": {f.path: f.before for f in self.files if f.before is not None},
            "over": [f.path for f in self.over_limit],
            "longest": [list(x) for x in self.longest_methods()],
            "note": self.note,
        }


def python_methods(text: str) -> list[tuple[str, int]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
            out.append((node.name, node.end_lineno - node.lineno + 1))
    return out


def brace_methods(text: str) -> list[tuple[str, int]]:
    """Methods of a brace language: a signature line, then lines up to the matching brace."""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        m = SIGNATURE.match(lines[i])
        if not m or m.group(1) in NOT_METHODS:
            i += 1
            continue
        depth, opened, j = 0, False, i
        while j < len(lines) and j - i < 5000:
            code = re.sub(r'"(?:\\.|[^"\\])*"|//.*$', "", lines[j])
            depth += code.count("{") - code.count("}")
            opened = opened or "{" in code
            if opened and depth <= 0:
                break
            if not opened and (code.rstrip().endswith(";") or j - i > 3):
                break  # a declaration or an expression body without braces
            j += 1
        if opened:
            out.append((m.group(1), j - i + 1))
            i = j + 1
        else:
            i += 1
    return out


def methods(adapter: LanguageAdapter, text: str) -> list[tuple[str, int]]:
    found = python_methods(text) if adapter.name == "python" else brace_methods(text)
    return sorted(found, key=lambda x: -x[1])


def report(adapter: LanguageAdapter, feature: str) -> Structure:
    cfg = adapter.cfg
    ref = base_ref(adapter, feature)
    if ref is None:
        return Structure(limit=cfg.max_file_lines, note="no git base to tell the feature's files")
    try:
        files = changed_src_files(adapter, ref)
    except GitError as e:
        return Structure(limit=cfg.max_file_lines, note=f"git failed: {e}")
    suffixes = tuple(adapter.source_suffixes)
    sizes = []
    for path in files:
        if suffixes and not path.endswith(suffixes):
            continue
        text = (cfg.root / path).read_text(encoding="utf-8", errors="replace")
        try:
            before: int | None = len(git(cfg, "show", f"{ref}:{path}").splitlines())
        except GitError:
            before = None  # a new file
        sizes.append(
            FileSize(path, len(text.splitlines()), methods(adapter, text)[:LONGEST], before)
        )
    return Structure(sizes, cfg.max_file_lines)


def new_public_types(adapter: LanguageAdapter, feature: str) -> list[str]:
    """Public types the feature added to the sources (from the diff against its base)."""
    cfg = adapter.cfg
    ref = base_ref(adapter, feature)
    if ref is None:
        return []
    try:
        diff = git(cfg, "diff", "-U0", ref, "--", cfg.paths.src)
    except GitError:
        return []
    pattern = PUBLIC_TYPE.get(adapter.name, GENERIC_TYPE)
    found: list[str] = []
    for line in diff.splitlines():
        m = pattern.match(line)
        if m and m.group(1) not in found:
            found.append(m.group(1))
    return found


def missing_from_code_map(adapter: LanguageAdapter, feature: str) -> tuple[bool, list[str]]:
    """(the code map exists, the new public types it does not mention by name)."""
    path = adapter.cfg.root / adapter.cfg.code_map
    types = new_public_types(adapter, feature)
    if not path.is_file():
        return False, types
    text = path.read_text(encoding="utf-8", errors="replace")
    return True, [t for t in types if not re.search(rf"\b{re.escape(t)}\b", text)]
