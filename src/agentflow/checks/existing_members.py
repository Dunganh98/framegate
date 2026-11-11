"""Existing members the feature's tests assert (a note for the test reviewer, never blocking).

Seen live (M5-freekick-review): frozen tests asserted `rec.Goal == (Outcome == Goal)`, but
`KeyMomentRecord.Goal` existed before the feature and meant something else (a foreseen goal,
always false for set pieces). Nothing ran that claim before the tests were frozen: the stub's
exception hid it in the red run, and the step parked a round later.

The scan lists every member that the feature's changed test files read inside an assertion
(`Assert.*(...)` in C#, `assert` in Python) and that is declared only in code this feature did
not change (declaration line outside the diff against the feature's base), unless a fixture
precondition test (`Fixture_*` / `test_fixture_*`) asserts it too: such a test runs green on the
stubs and so already checks the claim. The test reviewer reads the list in test_checks.md.
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import dataclass, field

from agentflow.adapters import csharp
from agentflow.adapters.base import CheckResult, LanguageAdapter, changed_lines
from agentflow.state import GitError, git, tag_exists

#: framework members an assertion reads all the time; never reported even if src has one
COMMON = {
    "Count",
    "Length",
    "Value",
    "HasValue",
    "Key",
    "Keys",
    "Values",
    "Message",
    "Item1",
    "Item2",
    "Item3",
    "Item4",
    "ToString",
    "Equals",
    "GetType",
    "GetHashCode",
    # LINQ / collection calls inside an assertion (`recs.Select(...)`), same name as some
    # unrelated method in src
    "Select",
    "Where",
    "ToList",
    "ToArray",
    "Any",
    "All",
    "First",
    "FirstOrDefault",
    "Last",
    "Single",
    "Sum",
    "Min",
    "Max",
    "Average",
    "OrderBy",
    "Contains",
    "Distinct",
    "Skip",
    "Take",
}

_ASSERT_CALL = re.compile(r"\bAssert\.\w+\s*(?:<[^<>()]*>)?\s*\(")
_GROUP = r"(?:\s*(?:\[[^\[\]]*\]|\([^()]*\)))*"
#: a member chain that starts at a local (lowercase) name: `rec.Goal`, `k.Result.Outcome`,
#: `recs[0].Goal`; `StrikeOutcome.Goal` (a type or enum value) starts upper case and is skipped
_CHAIN = re.compile(rf"(?<![\w.])[a-z_]\w*{_GROUP}(?:\s*\??\.\s*\w+{_GROUP})+")
_MEMBER = re.compile(r"\??\.\s*([A-Z]\w*)")
_MODS = (
    r"(?:static|readonly|virtual|override|abstract|sealed|new|required|const|volatile|async"
    r"|partial|extern|unsafe)"
)
_DECL = re.compile(
    rf"\b(?:public|internal|protected|private)(?:\s+{_MODS})*\s+"
    r"(?!(?:class|struct|record|interface|enum|delegate|event)\b)"
    r"[\w<>\[\],.?()\s]*?\b([A-Z]\w*)\s*(?=\{|=>|;|=|\()"
)
_RECORD = re.compile(r"\brecord\s+(?:struct\s+|class\s+)?([A-Z]\w*)\s*(?:<[^<>]*>)?\s*\(")


@dataclass
class Decl:
    owner: str
    path: str
    line: int
    new: bool


@dataclass
class Found:
    #: member name -> where an assertion reads it ("file:line")
    asserted: dict[str, list[str]] = field(default_factory=dict)
    #: members a fixture precondition test asserts
    fixture: set[str] = field(default_factory=set)


def base_ref(adapter: LanguageAdapter, feature: str) -> str | None:
    """Where the feature's code starts: the merge base with `[git] base`. The spec dialogue's
    stubs are part of the spec-approved commit, so a diff from that tag would miss them; if the
    merge base is that commit or later (no feature branch), the commit before it."""
    cfg = adapter.cfg
    tag = f"spec-approved/{feature}"
    try:
        base = git(cfg, "merge-base", "HEAD", cfg.git_base).strip()
    except GitError:
        base = ""
    try:
        if tag_exists(cfg, tag):
            if not base:
                return f"{tag}~1"
            git(cfg, "merge-base", "--is-ancestor", tag, base)  # raises if not
            return f"{tag}~1"
    except GitError:
        pass
    return base or None


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    if adapter.name not in ("dotnet", "python"):
        return CheckResult("SKIPPED", f"SKIPPED: no member scan for the {adapter.name} adapter")
    cfg = adapter.cfg
    ref = base_ref(adapter, feature)
    if ref is None:
        return CheckResult("SKIPPED", "SKIPPED: no git base to tell new code from existing code")
    from agentflow.checks.frame_diff import changed_files

    try:
        changed, _ = changed_files(cfg, ref)
    except GitError as e:
        return CheckResult("SKIPPED", f"SKIPPED: {e}")
    suffix = ".cs" if adapter.name == "dotnet" else ".py"
    tests_dir = cfg.paths.tests.rstrip("/") + "/"
    test_files = sorted(
        f
        for f in changed
        if f.startswith(tests_dir) and f.endswith(suffix) and (cfg.root / f).is_file()
    )
    found = Found()
    scan_test = _cs_asserts if suffix == ".cs" else _py_asserts
    for rel in test_files:
        scan_test(cfg.root / rel, rel, found)
    names = {
        n
        for n in found.asserted
        if len(n) > 1 and n not in COMMON and n not in found.fixture  # X / Y: coordinates
    }
    decls = _declarations(adapter, ref, names, suffix)
    lines = []
    for name in sorted(names):
        where = decls.get(name) or []
        if not where or any(d.new for d in where):
            continue  # not in src (framework, test code) or a member this feature adds
        decl = list(dict.fromkeys(f"{d.owner}.{name} ({d.path}:{d.line})" for d in where))
        owners = " or ".join(decl[:3]) + (f" (+{len(decl) - 3} more)" if len(decl) > 3 else "")
        uses = found.asserted[name]
        shown = ", ".join(uses[:6]) + (f" (+{len(uses) - 6} more)" if len(uses) > 6 else "")
        lines.append(f"- {owners}: asserted at {shown}")
    if not lines:
        return CheckResult(
            "PASS", "No member of existing code is asserted without a fixture precondition test."
        )
    head = (
        f"{len(lines)} member(s) of code that existed before this feature, asserted by its tests "
        "and by no fixture precondition test (Fixture_* / test_fixture_*). The red run cannot "
        "check these claims (the stub fails first). For each: does the test read the member the "
        "spec means, with the meaning it has where it is set?"
    )
    return CheckResult("PASS", "\n".join([head, *lines]))


# ---------- C# ----------
def _cs_members(args: str) -> list[str]:
    return [m for chain in _CHAIN.finditer(args) for m in _MEMBER.findall(chain.group(0))]


def _cs_assert_args(mask: str) -> list[tuple[int, str]]:
    out = []
    for m in _ASSERT_CALL.finditer(mask):
        open_at = m.end() - 1
        close = csharp.match_forward(mask, open_at)
        if close > open_at:
            out.append((m.start(), mask[open_at + 1 : close]))
    return out


def _cs_asserts(path: pathlib.Path, rel: str, found: Found) -> None:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    _, mask = csharp.strip(text)
    for pos, args in _cs_assert_args(mask):
        line = text.count("\n", 0, pos) + 1
        for name in _cs_members(args):
            where = f"{rel}:{line}"
            if where not in found.asserted.setdefault(name, []):
                found.asserted[name].append(where)
    for m in csharp.scan(text).tests:
        if m.name.lower().startswith("fixture_"):
            for _, args in _cs_assert_args(m.body):
                found.fixture.update(_cs_members(args))


def _cs_declarations(
    path: pathlib.Path, rel: str, changed: set[int] | None
) -> list[tuple[str, Decl]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    _, mask = csharp.strip(text)
    classes = csharp.scan(text).classes
    out = []

    def owner_at(pos: int) -> str:
        inside = [c for c in classes if c.start < pos < c.end]
        return max(inside, key=lambda c: c.start).name if inside else "?"

    def add(name: str, owner: str, pos: int) -> None:
        line = text.count("\n", 0, pos) + 1
        out.append((name, Decl(owner, rel, line, changed is None or line in changed)))

    for m in _DECL.finditer(mask):
        add(m.group(1), owner_at(m.start(1)), m.start(1))
    for m in _RECORD.finditer(mask):
        close = csharp.match_forward(mask, m.end() - 1)
        params = mask[m.end() : close] if close > m.end() else ""
        offset = m.end()
        for part in csharp.split_top_level(params, ","):
            ident = re.search(r"([A-Z]\w*)\s*(?:=[^,]*)?$", part.strip())
            if ident:
                add(ident.group(1), m.group(1), offset + part.find(ident.group(1)))
            offset += len(part) + 1
    return out


# ---------- Python ----------
def _py_members(node: ast.AST) -> list[str]:
    return [n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)]


def _py_asserts(path: pathlib.Path, rel: str, found: Found) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            for name in _py_members(node.test):
                where = f"{rel}:{node.lineno}"
                if where not in found.asserted.setdefault(name, []):
                    found.asserted[name].append(where)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_fixture_"
        ):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assert):
                    found.fixture.update(_py_members(sub.test))


def _py_declarations(
    path: pathlib.Path, rel: str, changed: set[int] | None
) -> list[tuple[str, Decl]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    out = []

    def add(name: str, owner: str, line: int) -> None:
        out.append((name, Decl(owner, rel, line, changed is None or line in changed)))

    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                add(item.name, cls.name, item.lineno)
                for sub in ast.walk(item):  # self.x = ... in methods
                    targets = (
                        sub.targets
                        if isinstance(sub, ast.Assign)
                        else [sub.target]
                        if isinstance(sub, ast.AnnAssign)
                        else []
                    )
                    for t in targets:
                        if (
                            isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Name)
                            and t.value.id == "self"
                        ):
                            add(t.attr, cls.name, t.lineno)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                add(item.target.id, cls.name, item.lineno)
            elif isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name):
                        add(t.id, cls.name, item.lineno)
    return out


def _declarations(
    adapter: LanguageAdapter, ref: str, names: set[str], suffix: str
) -> dict[str, list[Decl]]:
    """Member name -> its declarations in the source folder (only `names` are kept)."""
    if not names:
        return {}
    cfg = adapter.cfg
    src = cfg.root / cfg.paths.src
    if not src.is_dir():
        return {}
    scan = _cs_declarations if suffix == ".cs" else _py_declarations
    pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(names))) + r")\b")
    found: dict[str, list[Decl]] = {}
    for path in sorted(src.rglob(f"*{suffix}")):
        if {"bin", "obj", "__pycache__"} & set(path.relative_to(cfg.root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if not pattern.search(text):
            continue
        rel = path.relative_to(cfg.root).as_posix()
        for name, decl in scan(path, rel, changed_lines(cfg, ref, rel)):
            if name in names:
                found.setdefault(name, []).append(decl)
    return found
