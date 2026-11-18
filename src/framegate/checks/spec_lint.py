"""Mechanical checks of a spec before it is approved (`framegate spec-check`, `approve-spec`).

Both read columns of the spec templates' tables; a spec whose tables lack the column (written
from an older template) is not checked.

- "Spec ID" column (design.md: Error handling, Command-line surface): every filled row names
  a spec ID that requirements.md defines. Seen live (M5-moment-scan): a rule that was only a
  sentence in design.md ("with several threads, the first failing job is reported") had no ID,
  so no test covered it and the implementer assumed the opposite.
- "Assigned as" column (requirements.md: Existing data this spec reads): the code in the cell
  is in the file the "Set where" cell names. Seen live: the spec said a goal event's `Player`
  is the player id; the code assigns `shooter.Index`. The table named the right file, but
  nothing made anyone read the assignment.
- "Test data and fixtures" (requirements.md), for a spec that pins data: every edge-case ID
  has a row there (the data that shows it, or `unit-only: <why>`); see
  edge_case_fixture_problems.
- Forbidden markers in the stubs (stub_marker_problems): a line the spec phase added to a
  source file that holds one of the frame check's forbidden patterns (suppressions, skips).
  Seen live (runner-skeleton): a suppression comment written into a stub during the spec
  dialogue surfaced only at a step gate, where nobody could fix it.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

from framegate.adapters.base import LanguageAdapter
from framegate.config import Config
from framegate.state import GitError, git

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CODE = re.compile(r"`([^`]+)`")
_FILE = re.compile(r"[\w./\\-]*\w\.\w+")
#: an "Assigned as" cell that says why there is no assignment to copy
_NOT_APPLICABLE = re.compile(r"^\s*n/?a\b", re.I)


@dataclass
class Table:
    section: str
    header: list[str]
    rows: list[list[str]] = field(default_factory=list)

    def column(self, *starts: str) -> int | None:
        """The index of the first column whose title starts with one of `starts`."""
        for i, title in enumerate(self.header):
            if title.lower().startswith(starts):
                return i
        return None


def split_row(line: str) -> list[str]:
    """The cells of a markdown table row; a `|` inside backticks or after a backslash is text."""
    cells, cur, code = [], "", False
    text = line.strip()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and text[i + 1 : i + 2] == "|":
            cur += "|"
            i += 2
            continue
        if ch == "`":
            code = not code
        if ch == "|" and not code:
            cells.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    cells.append(cur.strip())
    if text.startswith("|"):
        cells = cells[1:]
    if text.endswith("|") and cells:
        cells = cells[:-1]
    return cells


def tables(text: str) -> list[Table]:
    """Every markdown table of a spec file, with the `## heading` it stands under."""
    out: list[Table] = []
    section = ""
    lines = _COMMENT.sub("", text).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#"):
            section = line.lstrip("#").strip()
        elif "|" in line and i + 1 < len(lines) and _SEPARATOR.match(lines[i + 1]):
            table = Table(section, split_row(line))
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                table.rows.append(split_row(lines[i]))
                i += 1
            out.append(table)
            continue
        i += 1
    return out


def _cell(row: list[str], i: int | None) -> str:
    return row[i] if i is not None and i < len(row) else ""


def _clip(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def spec_id_problems(cfg: Config, feature: str) -> list[str]:
    """design.md rows of a table with a "Spec ID" column that name no ID of requirements.md."""
    folder = cfg.spec_dir(feature)
    known = set(re.findall(cfg.id_pattern, _read(folder / "requirements.md")))
    problems = []
    for table in tables(_read(folder / "design.md")):
        col = table.column("spec id")
        if col is None:
            continue
        for row in table.rows:
            rest = [c for i, c in enumerate(row) if i != col and c]
            if not rest:
                continue  # an empty template row
            ids = re.findall(cfg.id_pattern, _cell(row, col))
            where = f'design.md, {table.section}, "{_clip(rest[0])}"'
            if not ids:
                problems.append(
                    f"{where}: no spec ID. A rule without an ID gets no test: add a "
                    "requirement or edge-case row for it in requirements.md and name it here"
                )
            for unknown in [x for x in ids if x not in known]:
                problems.append(f"{where}: {unknown} is not an ID of requirements.md")
    return problems


def _source_files(cfg: Config, cell: str) -> list[pathlib.Path]:
    """The files a "Set where (file: method)" cell names: a repo-relative path, or a file name
    looked up in the source folders."""
    found: list[pathlib.Path] = []
    for name in _FILE.findall(cell.replace("`", " ")):
        name = name.replace("\\", "/").lstrip("./")
        direct = cfg.root / name
        if direct.is_file():
            found.append(direct)
            continue
        for root in cfg.src_roots:
            base = cfg.root / root
            if base.is_dir():
                found += [p for p in base.rglob(name.rsplit("/", 1)[-1]) if p.is_file()]
    return list(dict.fromkeys(found))


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def assigned_as_problems(cfg: Config, feature: str) -> list[str]:
    """requirements.md rows whose "Assigned as" code is not in the file "Set where" names."""
    problems = []
    for table in tables(_read(cfg.spec_dir(feature) / "requirements.md")):
        col, where_col = table.column("assigned as"), table.column("set where")
        if col is None or where_col is None:
            continue
        for row in table.rows:
            if not any(c for i, c in enumerate(row) if i != col):
                continue  # an empty template row
            name = _clip(next((c for c in row if c), ""), 40)
            head = f'requirements.md, {table.section}, "{name}"'
            cell = _cell(row, col)
            if _NOT_APPLICABLE.match(cell):
                continue
            spans = _CODE.findall(cell) or ([cell] if cell else [])
            if not spans:
                problems.append(
                    f'{head}: "Assigned as" is empty. Copy the line of code that sets the '
                    "member (or write `n/a: <why>`)"
                )
                continue
            files = _source_files(cfg, _cell(row, where_col))
            if not files:
                problems.append(
                    f'{head}: no source file found for "Set where" '
                    f'("{_clip(_cell(row, where_col), 40)}"); name the file (path or file name)'
                )
                continue
            texts = [_squeeze(_read(p)) for p in files]
            names = ", ".join(p.relative_to(cfg.root).as_posix() for p in files[:3])
            for span in spans:
                if not any(_squeeze(span) in t for t in texts):
                    problems.append(
                        f"{head}: `{_clip(span)}` is not in {names}. Copy the assignment from "
                        "the code as it is, and check the spec's wording against it"
                    )
    return problems


_SHORT_ID = re.compile(r"(?:\b([A-Z][A-Z0-9]*)-)?\b([REI])-(\d{3})\b")
#: between two IDs: they are the ends of a range (`E-001..E-004`, `E-001 to E-004`)
_RANGE = re.compile(r"^\s*(?:\.{2,3}|…|–|—|to)\s*$")


def listed_ids(cell: str, code: str = "") -> set[str]:
    """The spec IDs an ID cell lists: full IDs, short ones (`E-002`: the prefix of the ID
    before it, else `code`) and ranges (`XXX-E-001..E-004`)."""
    out: set[str] = set()
    prefix, last, end = code, None, 0
    for m in _SHORT_ID.finditer(cell):
        prefix = m.group(1) or prefix
        kind, num = m.group(2), int(m.group(3))
        nums = [num]
        if last and last[0] == kind and last[1] < num and _RANGE.match(cell[end : m.start()]):
            nums = list(range(last[1] + 1, num + 1))
        if prefix:
            out.update(f"{prefix}-{kind}-{n:03d}" for n in nums)
        last, end = (kind, num), m.end()
    return out


def edge_case_fixture_problems(cfg: Config, feature: str) -> list[str]:
    """Edge cases of requirements.md without a row in "Test data and fixtures". Seen live
    (M5-reach-by-save-chance): the spec named an empty likely span, an unknown outer span and
    a saved shot; the only pinned match held none of them, the table had rows per requirement
    group, and the test writer and the test reviewer asked about the gaps five times. A row
    names the data that shows the case, or says `unit-only: <why>`.

    Checked only for a spec that pins data (a filled row with a Data cell): where every test
    builds its own inputs, no edge case can be missing from the pinned data."""
    text = _read(cfg.spec_dir(feature) / "requirements.md")
    code = re.search(r"^Feature code:\s*([A-Z][A-Z0-9]*)", text, re.M)
    edge: list[str] = []
    covered: set[str] = set()
    pinned = False
    for table in tables(text):
        section = table.section.lower()
        id_col = table.column("id")
        if id_col is None:
            continue
        if section.startswith("edge case"):
            for row in table.rows:
                if any(c for i, c in enumerate(row) if i != id_col):  # not the template row
                    edge += [x for x in re.findall(cfg.id_pattern, _cell(row, id_col))]
        elif section.startswith("test data"):
            data_col = table.column("data")
            if data_col is None:
                return []  # written from an older template
            for row in table.rows:
                if _cell(row, data_col):
                    pinned = True
                    covered |= listed_ids(_cell(row, id_col), code.group(1) if code else "")
    if not pinned:
        return []
    return [
        f"requirements.md, Test data and fixtures: edge case {eid} has no row. Name the data "
        'that shows it (seed / file / generator), or write "unit-only: <which test builds the '
        'case itself>: <why no pinned data is needed>" in its Data cell'
        for eid in dict.fromkeys(edge)
        if re.search(r"-E-\d+$", eid) and eid not in covered
    ]


#: the tables of requirements.md whose rows are requirements (all three templates)
_REQUIREMENT_SECTIONS = (
    "requirements",
    "edge case",
    "invariant",
    "reproduction",
    "must not change",
    "behavior to preserve",
)
#: quoted text (an error message, a log line) is behaviour: left out before the search
_QUOTED = re.compile(r"`[^`]*`|\"[^\"]*\"|(?<!\w)'[^']*'(?!\w)")
_DOC_TEXT = re.compile(
    r"\bdoc[ -]?strings?\b|\bdoc[ -]?comments?\b|\bxml[ -]?doc\b|\b(?:java|js)doc\b|\bdocblocks?\b"
    r"|\b(?:code|source|inline)[ -]comments?\b"
    r"|\bcomments?\s+(?:in|of|on|above)\s+(?:the\s+|its\s+|each\s+|every\s+|a\s+)?"
    r"(?:code|source|function|method|class|module|constant|field|definition|implementation)\b",
    re.I,
)


def doc_text_problems(cfg: Config, feature: str) -> list[str]:
    """Requirement rows whose subject is docstring or comment text: comments are no source, so
    no requirement, test or finding rests on one. Seen live: the test writer refused to test
    such a row, it went to the final gate, and traceability failed on it."""
    problems = []
    for table in tables(_read(cfg.spec_dir(feature) / "requirements.md")):
        id_col = table.column("id")
        if id_col is None or not table.section.lower().startswith(_REQUIREMENT_SECTIONS):
            continue
        for row in table.rows:
            text = " ".join(c for i, c in enumerate(row) if i != id_col)
            if '"""' in text or "'''" in text or _DOC_TEXT.search(_QUOTED.sub(" ", text)):
                problems.append(
                    f"requirements.md, {table.section}, {_cell(row, id_col) or _clip(text, 40)}: "
                    "a requirement on docstring or comment text: not a requirement (comments "
                    "are no source). Drop it, or put a doc file change under ## Proposed doc "
                    "updates"
                )
    return problems


#: CI files that may list test files by path
_CI_GLOBS = (
    ".github/workflows/*.y*ml",
    ".circleci/*.y*ml",
    ".azure-pipelines/**/*.y*ml",
    ".gitlab/**/*.y*ml",
    ".gitlab-ci*.yml",
    "azure-pipelines*.yml",
    "bitbucket-pipelines.yml",
    ".travis.yml",
    "Jenkinsfile",
)
_TEST_NAME = re.compile(r"(?i)^test|tests?\.[^.]+$|\.test\.")


def _section(text: str, title: str) -> str:
    """The text under `## <title>...` up to the next `## ` heading, without comments."""
    m = re.search(
        rf"^##[ \t]+{re.escape(title)}[^\n]*\n?(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I
    )
    return _COMMENT.sub("", m.group(1)) if m else ""


def _test_paths(cfg: Config, text: str) -> set[str]:
    """Test file paths under the tests folder that `text` names (a node ID's file too)."""
    rx = rf"(?<![\w./-])(?:\./)?({re.escape(cfg.paths.tests.strip('/'))}/[\w./-]*\w\.\w+)"
    found = re.findall(rx, text.replace("\\", "/"))
    return {p for p in found if _TEST_NAME.search(p.rsplit("/", 1)[-1])}


def ci_test_file_warnings(cfg: Config, feature: str) -> list[str]:
    """A new test file while the CI lists test files by path and the spec names no pipeline
    follow-up. Seen live: a new test file that CI would never have run."""
    folder = cfg.spec_dir(feature)
    text = _read(folder / "requirements.md")
    if _section(text, "Pipeline follow-up").strip().rstrip(".").lower() not in ("", "none"):
        return []
    named = _test_paths(cfg, _read(folder / "plan.md") + _section(text, "Test data"))
    new = sorted(p for p in named if not (cfg.root / p).exists())
    if not new:
        return []
    ci = sorted(
        {p for g in _CI_GLOBS for p in cfg.root.glob(g) if p.is_file()},
        key=lambda p: p.as_posix(),
    )
    listing = [
        p.relative_to(cfg.root).as_posix()
        for p in ci
        if any((cfg.root / t).is_file() for t in _test_paths(cfg, _read(p)))
    ]
    if not listing:
        return []
    return [
        f"requirements.md, Pipeline follow-up: the spec adds {', '.join(new)}, and the CI runs "
        f"only the test files it lists ({', '.join(listing)}). Add the new one under "
        "## Pipeline follow-up"
    ]


def warnings(cfg: Config, feature: str) -> list[str]:
    """What the spec check reports without failing."""
    return ci_test_file_warnings(cfg, feature)


_HUNK = re.compile(r"^@@ -\S+ \+(\d+)")


def added_source_lines(adapter: LanguageAdapter) -> list[tuple[str, int, str]]:
    """(file, line number, text) of every line in the source folders that is new since the
    feature branch left `[git] base` (committed or not; untracked source files whole)."""
    cfg = adapter.cfg
    roots = [r for r in cfg.src_roots if (cfg.root / r).exists()]
    if not roots:
        return []
    try:
        ref = git(cfg, "merge-base", "HEAD", cfg.git_base).strip()
    except GitError:  # no such base (e.g. [git] branch = false): the uncommitted work
        ref = "HEAD"
    out: list[tuple[str, int, str]] = []
    path, num = "", 0
    for line in git(cfg, "diff", "-U0", "--no-color", ref, "--", *roots).splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif m := _HUNK.match(line):
            num = int(m.group(1))
        elif line.startswith("+") and path:
            out.append((path, num, line[1:]))
            num += 1
    for rel in git(cfg, "ls-files", "--others", "--exclude-standard", "--", *roots).splitlines():
        file = cfg.root / rel
        named = not adapter.source_suffixes or rel.endswith(adapter.source_suffixes)
        if named and file.is_file():
            text = file.read_text(encoding="utf-8", errors="replace")
            out += [(rel, i, x) for i, x in enumerate(text.splitlines(), 1)]
    return out


def stub_marker_problems(adapter: LanguageAdapter) -> list[str]:
    """Source lines added in the spec phase (the stubs) that hold a forbidden pattern of the
    frame check: found now, while the human can still change the stub, not at a step gate."""
    from framegate.checks.frame_diff import forbidden_regex

    rx = forbidden_regex(adapter)
    if rx is None:
        return []
    try:
        lines = added_source_lines(adapter)
    except GitError:
        return []  # not a git repository yet: approve-spec says so itself
    return [
        f"{path}:{num}: forbidden marker `{m.group(0)}` in a stub ({_clip(text.strip())}). "
        "The frame check rejects it at every step gate: remove it and write the stub so that "
        "it needs none"
        for path, num, text in lines
        if (m := rx.search(text))
    ]


def problems(cfg: Config, feature: str, adapter: LanguageAdapter | None = None) -> list[str]:
    """Everything the spec check finds; [] = fine. With the adapter also the forbidden
    markers in the stubs."""
    from framegate import deferred, references  # they read the spec with this module
    from framegate.checks import plan_files, red_stubs

    plan = _read(cfg.spec_dir(feature) / "plan.md")
    return [
        *references.spec_problems(cfg, feature),
        *deferred.spec_problems(cfg, feature),
        *red_stubs.green_problems(_read(cfg.spec_dir(feature) / "requirements.md")),
        *spec_id_problems(cfg, feature),
        *assigned_as_problems(cfg, feature),
        *edge_case_fixture_problems(cfg, feature),
        *doc_text_problems(cfg, feature),
        *(stub_marker_problems(adapter) if adapter is not None else []),
        *(plan_files.outside_problems(adapter, plan) if adapter is not None else []),
    ]
