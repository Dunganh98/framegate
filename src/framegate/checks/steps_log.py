"""Step and fix gates: the implementer's notes (out/<f>/steps.md) against the spec.

- An `ASSUMPTION` whose words are a spec row's words (at least OVERLAP of them, of
  MIN_WORDS or more) is no assumption: the spec states it.
- A `RULED OUT: H<n>` / `ROOT CAUSE: H<n>` line of a bug fix that names the file or the
  function of another suspected cause and not its own, or a number the spec does not have.

Of two notes under the same heading the later one counts (a retry writes its note again).
"""

from __future__ import annotations

import re

from framegate.adapters.base import CheckResult, LanguageAdapter
from framegate.checks import suspected_causes
from framegate.checks.spec_lint import tables
from framegate.config import Config
from framegate.report import parse_assumptions, parse_causes

NAME = "step notes match the spec"
#: share of an assumption's words found in one spec row, as (numerator, denominator)
OVERLAP = (4, 5)
MIN_WORDS = 6
_LABEL = re.compile(r"^\W*(H\d+)\b")


def latest(text: str) -> str:
    """steps.md with only the last note of each heading."""
    notes: dict[str, list[str]] = {}
    head = ""
    for line in text.splitlines():
        if line.startswith("## "):
            head = " ".join(line.split())
            notes[head] = []
        notes.setdefault(head, []).append(line)
    return "\n".join(x for body in notes.values() for x in body)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def spec_rows(cfg: Config, text: str) -> dict[str, str]:
    """Spec ID -> the text of its row, for every table row of requirements.md that starts
    with an ID."""
    out: dict[str, str] = {}
    for table in tables(text):
        for row in table.rows:
            m = re.fullmatch(cfg.id_pattern, row[0].strip()) if row else None
            if m:
                out.setdefault(row[0].strip(), " ".join(row[1:]))
    return out


def assumption_problems(cfg: Config, spec: str, notes: str) -> list[str]:
    rows = {i: _words(t) for i, t in spec_rows(cfg, spec).items()}
    num, den = OVERLAP
    out = []
    for a in parse_assumptions(notes):
        said = _words(a.text)
        if len(said) < MIN_WORDS:
            continue
        hit = next((i for i, w in rows.items() if den * len(said & w) >= num * len(said)), None)
        if hit:
            out.append(
                f"ASSUMPTION [{a.spec_id}] '{a.text[:80]}': the spec states this in {hit}. "
                "It is no assumption: take the line out of your note."
            )
    return out


def _named(name: str, text: str) -> bool:
    return bool(name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text))


def _names(cause: suspected_causes.Cause) -> set[str]:
    return {x for x in (cause.file.rsplit("/", 1)[-1], cause.function.rsplit(".", 1)[-1]) if x}


def cause_problems(spec: str, notes: str) -> list[str]:
    causes = {c.label: c for c in suspected_causes.parse(spec)}
    out = []
    for kind, text in parse_causes(notes) if causes else []:
        m = _LABEL.match(text)
        if not m:
            continue
        label, rest = m.group(1), text[m.end() :]
        if label not in causes:
            out.append(f"{kind}: {label}: the spec's suspected causes have no {label}.")
            continue
        own = _names(causes[label])
        if not own or any(_named(n, rest) for n in own):
            continue
        other = next(
            (
                (c.label, n)
                for c in causes.values()
                if c.label != label
                for n in sorted(_names(c) - own)
                if _named(n, rest)
            ),
            None,
        )
        if other:
            where = ": ".join(x for x in (causes[label].file, causes[label].function) if x)
            out.append(
                f"{kind}: {label} names {other[1]} (the spec's {other[0]}); the spec's "
                f"{label} is {where}. Use the spec's cause numbers."
            )
    return out


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    cfg = adapter.cfg
    steps = cfg.feature_out(feature) / "steps.md"
    spec = cfg.spec_dir(feature) / "requirements.md"
    if not steps.is_file() or not spec.is_file():
        return CheckResult("PASS", "")
    notes = latest(steps.read_text(encoding="utf-8"))
    text = spec.read_text(encoding="utf-8")
    found = [*assumption_problems(cfg, text, notes), *cause_problems(text, notes)]
    if not found:
        return CheckResult("PASS", "")
    head = (
        f"{cfg.paths.out}/{feature}/steps.md does not match the spec. Write your step note "
        "again, in full, with these lines corrected:"
    )
    return CheckResult("FAIL", "\n".join([head, *(f"  {x}" for x in found)]), str(len(found)))
