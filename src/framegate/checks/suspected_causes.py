"""A bug report's suspected causes: each one is tied to a test, and the tests reach it.

requirements.md of a bug fix, `## Suspected causes`: one bullet per cause,
`- H1: <what, the suspect expression in backticks> (<file>: <function>) -> <spec ID>` (the row
whose test runs through that code) or `... -> ruled out: <evidence>`.

- spec_problems (`framegate spec-check`): a cause with neither, or with an ID the report does
  not define. A cause no row covers gets no test, and the bug it names stays.
- reach (after the last step gate, a note): a cause that is not ruled out and whose code the
  feature's tests never run. Its code: the lines of the named file that hold a backticked
  expression, else the body of the named function.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

from framegate.adapters.base import CheckResult, LanguageAdapter
from framegate.config import Config
from framegate.specs import feature_ids, feature_mode

NAME = "suspected causes reached (feature tests)"
SECTION = "Suspected causes"
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_LABEL = re.compile(r"^\W*(H\d+)\W*:\s*(.*)$", re.S)
_TARGET = re.compile(r"\s*(?:->|→)\s*(?=[^>]*$)")
_RULED_OUT = re.compile(r"^\W*ruled out\b\W*(.*)$", re.I | re.S)
_CODE = re.compile(r"`([^`]+)`")
#: `(<file>: <function>)`, the function optional
_WHERE = re.compile(r"\(\s*`?([\w./\\-]*\w\.\w+)`?\s*(?::\s*`?([\w.]+)`?(?:\(\))?)?[^()]*\)")


@dataclass
class Cause:
    label: str
    text: str
    #: what stands after `->` ("" = nothing)
    target: str = ""
    file: str = ""
    function: str = ""
    expressions: list[str] = field(default_factory=list)

    @property
    def ruled_out(self) -> bool:
        return bool(_RULED_OUT.match(self.target))

    @property
    def evidence(self) -> str:
        m = _RULED_OUT.match(self.target)
        return m.group(1).strip() if m else ""


def parse(text: str) -> list[Cause]:
    """The filled bullets of `## Suspected causes` (a bare `- H1:` is the template's)."""
    clean = _COMMENT.sub("", text)
    m = re.search(rf"^##\s+{SECTION}\b.*$", clean, re.M | re.I)
    if not m:
        return []
    rest = clean[m.end() :]
    end = re.search(r"^##\s", rest, re.M)
    bullets: list[str] = []
    for line in (rest[: end.start()] if end else rest).splitlines():
        b = _BULLET.match(line)
        if b:
            bullets.append(b.group(1))
        elif bullets and line.strip() and line[:1].isspace():
            bullets[-1] += " " + line.strip()
    out = []
    for n, bullet in enumerate(bullets, 1):
        named = _LABEL.match(bullet)
        label, body = (named.group(1), named.group(2)) if named else (f"cause {n}", bullet)
        if not body.strip() or re.fullmatch(r"<.*>", body.strip()):
            continue
        parts = _TARGET.split(body, maxsplit=1)
        what, target = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        where = _WHERE.search(_CODE.sub(lambda c: c.group(1), what))
        spans = [s for s in _CODE.findall(what) if not _WHERE.fullmatch(f"({s})")]
        out.append(
            Cause(
                label,
                what.strip(),
                target,
                where.group(1).replace("\\", "/") if where else "",
                (where.group(2) or "") if where else "",
                [s for s in spans if not where or s.replace("\\", "/") != where.group(1)],
            )
        )
    return out


def _requirements(cfg: Config, feature: str) -> str:
    path = cfg.spec_dir(feature) / "requirements.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def spec_problems(cfg: Config, feature: str) -> list[str]:
    """Bug fix only: suspected causes that name neither a row of the report nor why they
    are ruled out."""
    if feature_mode(cfg, feature) != "bugfix":
        return []
    text = _requirements(cfg, feature)
    # the IDs the report defines: the ones outside this section
    rest = re.sub(rf"(?ims)^##\s+{SECTION}\b.*?(?=^##\s|\Z)", "", _COMMENT.sub("", text))
    known = set(re.findall(cfg.id_pattern, rest))
    how = (
        "End it with `-> <the ID of the row whose test runs through that code>` or `-> ruled "
        "out: <evidence>`"
    )
    problems = []
    for c in parse(text):
        head = f"requirements.md, {SECTION}, {c.label}"
        ids = re.findall(cfg.id_pattern, c.target)
        if c.ruled_out:
            if not c.evidence:
                problems.append(f"{head}: ruled out without evidence. Say what rules it out")
        elif not ids:
            problems.append(f"{head}: no row of the report covers it, so no test reaches it. {how}")
        else:
            problems += [
                f"{head}: {i} is not an ID of this bug report" for i in ids if i not in known
            ]
    return problems


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _files(cfg: Config, name: str) -> list[str]:
    """Repo-relative source files a cause's file name stands for (a path, or a bare name)."""
    name = name.lstrip("./")
    if (cfg.root / name).is_file():
        return [name]
    found: list[pathlib.Path] = []
    for root in cfg.src_roots:
        base = cfg.root / root
        if base.is_dir():
            found += [p for p in base.rglob(name.rsplit("/", 1)[-1]) if p.is_file()]
    rels = [p.relative_to(cfg.root).as_posix() for p in found]
    return [r for r in dict.fromkeys(rels) if r.endswith(name)]


def cause_lines(adapter: LanguageAdapter, cause: Cause) -> tuple[str, set[int]]:
    """(the source file, the lines that are the cause's code); ("", set()) = cannot tell."""
    for rel in _files(adapter.cfg, cause.file) if cause.file else []:
        try:
            source = (adapter.cfg.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        wanted = [_squeeze(e) for e in cause.expressions if len(_squeeze(e)) > 2]
        lines = {
            n
            for n, line in enumerate(source.splitlines(), 1)
            if any(w in _squeeze(line) for w in wanted)
        }
        if not lines and cause.function:
            lines = adapter.function_body_lines(rel, cause.function.rsplit(".", 1)[-1])
        if lines:
            return rel, lines
    return "", set()


def reach(adapter: LanguageAdapter, feature: str) -> tuple[list[str], str]:
    """(one line per suspected cause whose code no feature test runs, a note: why nothing
    can be said). A note for the reviewer and the owner, never blocking."""
    cfg = adapter.cfg
    if feature_mode(cfg, feature) != "bugfix":
        return [], ""
    causes = [c for c in parse(_requirements(cfg, feature)) if not c.ruled_out]
    places = [(c, *cause_lines(adapter, c)) for c in causes]
    files = sorted({rel for _, rel, _ in places if rel})
    if not files:
        return [], "no suspected cause names code that can be found" if causes else ""
    from framegate.checks.scope_coverage import line_hits

    reports, note = adapter.scope_coverage_reports(sorted(feature_ids(cfg, feature)), files)
    if not reports:
        return [], note or "no coverage report"
    hits = line_hits(reports, cfg.root)
    out = []
    for c, rel, lines in places:
        if not rel:
            continue
        ran = hits.get(rel) or {}
        measured = [n for n in sorted(lines) if n in ran]
        if measured and not any(ran[n] > 0 for n in measured):
            out.append(
                f"{c.label} ({rel}:{measured[0]}): no test of the task runs this code: "
                f"{c.text[:160]}"
            )
        elif not measured and rel not in hits:
            out.append(f"{c.label} ({rel}): no test of the task runs this file: {c.text[:160]}")
    return out, ""


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    """`reach` as a check result: PASS with its lines (a note, never a failure)."""
    lines, note = reach(adapter, feature)
    if note:
        return CheckResult("SKIPPED", f"SKIPPED: {note}")
    if not lines:
        return CheckResult("PASS", "The task's tests run the code of every suspected cause.")
    head = (
        "SUSPECTED CAUSES NO TEST OF THE TASK REACHES (the bug each one names can still be there):"
    )
    return CheckResult(
        "PASS", "\n".join([head, *(f"  {x}" for x in lines)]), f"{len(lines)} not reached"
    )
