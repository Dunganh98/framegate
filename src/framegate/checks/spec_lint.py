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
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

from framegate.config import Config

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


def problems(cfg: Config, feature: str) -> list[str]:
    """Everything the spec check finds; [] = fine."""
    return [
        *spec_id_problems(cfg, feature),
        *assigned_as_problems(cfg, feature),
        *edge_case_fixture_problems(cfg, feature),
    ]
