"""The candidates file: one `## <name>` section per commit (format: docs/replay.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KINDS = ("feature", "bugfix", "refactor")
REQUIRED = ("repo", "commit", "kind")
LISTS = ("tests", "pythonpath")
FIELDS = (*REQUIRED, *LISTS, "python", "install", "setup", "max-usd", "max-hours")

TEMPLATE = """\
# Replay candidates

## <name>
- repo: <path or URL of the repository>
- commit: <sha>
- kind: <feature | bugfix | refactor>

### Task
<what is wanted, as the user would say it; never taken from the diff.
Leave this section out to use the commit message.>
"""

_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_FIELD = re.compile(r"-\s+([A-Za-z-]+)\s*:\s*(.*)")
_SHA = re.compile(r"[0-9a-fA-F]{7,40}")


class CandidatesError(Exception):
    """Every problem of the file, one per line: `line N: what is wrong`."""


@dataclass
class Candidate:
    name: str
    repo: str
    commit: str
    kind: str
    #: empty: the commit message
    task: str = ""
    #: empty: the test files the commit changes
    tests: list[str] = field(default_factory=list)
    #: import roots, relative to the repository; empty: `src` (if there) and `.`
    pythonpath: list[str] = field(default_factory=list)
    python: str = ""
    #: commands that install the project into the sandbox's environment; `{py}` = its python
    install: list[str] = field(default_factory=list)
    max_usd: float | None = None
    max_hours: float | None = None
    #: what the user tells the setup up front (sent with /framegate:setup), e.g. mutation off
    setup: str = ""


def _number(value: str) -> float | None:
    try:
        n = float(value)
    except ValueError:
        return None
    return n if n > 0 else None


def _candidate(
    name: str, line: int, fields: dict[str, tuple[int, str]], install: list[str], task: str
) -> tuple[Candidate | None, list[str]]:
    errors = [
        f"line {line}: candidate '{name}' has no `- {k}:`" for k in REQUIRED if k not in fields
    ]

    def value(key: str) -> str:
        return fields.get(key, (0, ""))[1]

    def bad(key: str, what: str) -> None:
        errors.append(f"line {fields[key][0]}: {key} '{value(key)}' {what}")

    if "commit" in fields and not _SHA.fullmatch(value("commit")):
        bad("commit", "is not a commit sha (7 to 40 hex digits)")
    if "kind" in fields and value("kind") not in KINDS:
        bad("kind", f"is not one of {', '.join(KINDS)}")
    caps: dict[str, float | None] = {}
    for key in ("max-usd", "max-hours"):
        caps[key] = _number(value(key)) if key in fields else None
        if key in fields and caps[key] is None:
            bad(key, "is not a number above 0")
    if errors:
        return None, errors
    lists = {k: [p.strip() for p in value(k).split(",") if p.strip()] for k in LISTS}
    return (
        Candidate(
            name,
            value("repo"),
            value("commit"),
            value("kind"),
            task.strip(),
            lists["tests"],
            lists["pythonpath"],
            value("python"),
            install,
            caps["max-usd"],
            caps["max-hours"],
            value("setup"),
        ),
        [],
    )


def parse(text: str) -> list[Candidate]:
    """The candidates of the file, in order. Raises CandidatesError with every problem."""
    errors: list[str] = []
    out: list[Candidate] = []
    names: set[str] = set()
    cur: tuple[str, int] | None = None
    fields: dict[str, tuple[int, str]] = {}
    install: list[str] = []
    task: list[str] = []
    in_task = fence = False

    def close() -> None:
        if cur is None:
            return
        cand, found = _candidate(cur[0], cur[1], fields, install, "\n".join(task))
        errors.extend(found)
        if cand:
            out.append(cand)

    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("```"):
            fence = not fence
        if raw.startswith("## ") and not fence:
            close()
            name = raw[3:].strip()
            cur, fields, install, task, in_task = None, {}, [], [], False
            if not _NAME.fullmatch(name):
                errors.append(
                    f"line {n}: '{name}' is not a candidate name (a-z, 0-9, '-'); "
                    "a heading inside a task needs ### or deeper"
                )
            elif name in names:
                errors.append(f"line {n}: candidate '{name}' is there twice")
            else:
                cur = (name, n)
                names.add(name)
        elif cur is None:
            continue  # the title, notes, and the rest of a section whose name was refused
        elif in_task:
            task.append(raw)
        elif line.lower() == "### task":
            in_task = True
        elif line:
            m = _FIELD.fullmatch(line)
            key = m.group(1).lower() if m else ""
            if not m:
                errors.append(f"line {n}: expected `- field: value` or `### Task`, got '{line}'")
            elif key not in FIELDS:
                errors.append(f"line {n}: unknown field '{key}' (known: {', '.join(FIELDS)})")
            elif not m.group(2).strip():
                errors.append(f"line {n}: {key} has no value")
            elif key == "install":
                install.append(m.group(2).strip())
            elif key in fields:
                errors.append(f"line {n}: {key} is there twice (first: line {fields[key][0]})")
            else:
                fields[key] = (n, m.group(2).strip())
    close()
    if not names and not errors:
        errors.append("line 1: no candidate found (a candidate starts with `## <name>`)")
    if errors:
        raise CandidatesError("\n".join(errors))
    return out
