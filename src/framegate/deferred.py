"""The deferred-work list: `.framegate/deferred.md`, committed with each task's final commit,
so later tasks see what was left for later. One line per item, with a stable id:

    - D7 [refactor] src/app/state.py: split it into one file per concept (623 lines, limit
      400). From released-lock, 2026-11-18: answered "later".

At the end of a task (Orchestrator.keep_followups) it gets: the "later" answers to questions
that proposed a change, the important doc proposals not applied, parked work and provisional
steps left as they were, the final checks that had failed, the known-red tests' clean-up and
a pointer to each follow-up list. The same kind, files and text again updates the entry's
"From" part. A spec that does an entry says `Resolves: D7` in requirements.md: spec-check
refuses an unknown id, the task's final commit removes the entry. The owner may delete lines.
"""

from __future__ import annotations

import datetime
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from framegate import defaults, doc_proposals
from framegate.checks import plan_files
from framegate.config import DEFERRED_FILE as PATH
from framegate.config import Config

KINDS = ("refactor", "bugfix", "feature", "docs", "pipeline", "tests")
HEAD = (
    "# Deferred work\n\n"
    "Decisions left for later, one line each (framegate adds them at the end of a task). A "
    "task that does one says `Resolves: D<n>` in its requirements.md; delete a line to drop "
    "it.\n"
)
_LINE = re.compile(r"^-\s+D(\d+)\s+\[(\w+)\]\s+(.*)\s+From\s+(.+?)\s*$")
_NEXT = re.compile(r"<!--\s*next:\s*D(\d+)\s*-->")
_PATH = re.compile(r"[\w./\\-]*\w\.[A-Za-z]\w*")
_RESOLVES = re.compile(r"^\W*Resolves\s*:\s*(.+)$", re.M | re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)


@dataclass(frozen=True)
class Item:
    """Work to defer, before it has an id."""

    kind: str
    text: str
    files: list[str] = field(default_factory=list)
    how: str = 'answered "later"'


@dataclass
class Entry:
    n: int
    kind: str
    files: list[str]
    text: str
    origin: str

    @property
    def id(self) -> str:
        return f"D{self.n}"

    @property
    def key(self) -> tuple[str, tuple[str, ...], str]:
        return self.kind, tuple(sorted(self.files)), " ".join(defaults._words(self.text))

    @property
    def about(self) -> tuple[str, ...] | None:
        """What a second entry may not repeat, whatever its words: a refactor's files, a
        doc proposal's file and place (doc_proposals.key). None: only the same text."""
        if self.kind == "refactor" and self.files:
            return ("refactor", *sorted(self.files))
        if self.kind == "docs" and self.files:
            return ("docs", doc_proposals.key(f"{self.files[0]}, {self.text}"))
        return None

    def line(self) -> str:
        where = f"{', '.join(self.files)}: " if self.files else ""
        return f"- {self.id} [{self.kind}] {where}{self.text} From {self.origin}"


def _sentence(text: str, files: list[str]) -> str:
    text = " ".join(text.split())
    if files and text.startswith(files[0]):
        text = text[len(files[0]) :].lstrip(" ,:;")
    return text if text.endswith((".", "?", "!")) else text + "."


def _parse(line: str) -> Entry | None:
    m = _LINE.match(line)
    if not m:
        return None
    body = m.group(3)
    head, sep, rest = body.partition(": ")
    files = [f.strip() for f in head.split(",")]
    if sep and all(_PATH.fullmatch(f) for f in files):
        return Entry(int(m.group(1)), m.group(2), files, rest, m.group(4))
    return Entry(int(m.group(1)), m.group(2), [], body, m.group(4))


def read(cfg: Config) -> tuple[list[Entry], int]:
    """The open entries and the next id's number."""
    path = cfg.root / PATH
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    found = [e for e in map(_parse, text.splitlines()) if e]
    m = _NEXT.search(text)
    return found, max([int(m.group(1)) if m else 1, *(e.n + 1 for e in found)])


def _write(cfg: Config, entries: list[Entry], nxt: int) -> None:
    path = cfg.root / PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [HEAD, f"<!-- next: D{nxt} -->", "", *(e.line() for e in entries)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def add(cfg: Config, feature: str, items: list[Item]) -> list[Entry]:
    """Record `items`; an item already listed (same kind, files and text) gets the new
    "From" part; one about the same thing in other words (Entry.about) is refused: the
    listed entry stands. Returns the entries added, updated or standing."""
    if not items:
        return []
    entries, nxt = read(cfg)
    by_key = {e.key: e for e in entries}
    by_about = {e.about: e for e in reversed(entries) if e.about}
    today = datetime.date.today().isoformat()
    touched: list[Entry] = []
    for it in items:
        text = _sentence(it.text, list(it.files))
        e = Entry(0, it.kind, list(it.files), text, f"{feature}, {today}: {it.how}.")
        old = by_key.get(e.key)
        if old:
            old.origin = e.origin
        elif e.about in by_about:
            old = by_about[e.about]
        else:
            e.n, nxt = nxt, nxt + 1
            entries.append(e)
            by_key[e.key] = e
            if e.about:
                by_about[e.about] = e
        if (old or e) not in touched:
            touched.append(old or e)
    _write(cfg, entries, nxt)
    return touched


def resolves(text: str) -> list[str]:
    """The ids of the `Resolves: D7, D9` lines of a requirements.md."""
    found = _RESOLVES.findall(_COMMENT.sub("", text))
    return [f"D{n}" for line in found for n in re.findall(r"\bD(\d+)\b", line, re.I)]


def _requirements(cfg: Config, feature: str) -> str:
    path = cfg.spec_dir(feature) / "requirements.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def resolve(cfg: Config, feature: str) -> list[Entry]:
    """The task is done: remove the entries its spec resolves. Returns them."""
    ids = set(resolves(_requirements(cfg, feature)))
    entries, nxt = read(cfg)
    gone = [e for e in entries if e.id in ids]
    if gone:
        _write(cfg, [e for e in entries if e.id not in ids], nxt)
    return gone


def spec_problems(cfg: Config, feature: str) -> list[str]:
    """`Resolves:` ids that are no open entry."""
    known = {e.id for e in read(cfg)[0]}
    return [
        f"requirements.md: Resolves: {i} is no open entry of {PATH} (framegate deferred lists them)"
        for i in resolves(_requirements(cfg, feature))
        if i not in known
    ]


def _same(a: str, b: str) -> bool:
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def on_files(cfg: Config, files: list[str]) -> list[Entry]:
    """The open entries about any of `files` (a bare file name matches its path)."""
    return [e for e in read(cfg)[0] if any(_same(f, x) for f in e.files for x in files)]


def plan_note(cfg: Config, feature: str) -> list[str]:
    """For the spec brief: the open entries on the files the plan names ([] = none)."""
    path = cfg.spec_dir(feature) / "plan.md"
    plan = path.read_text(encoding="utf-8") if path.is_file() else ""
    found = on_files(cfg, plan_files.named_files(_COMMENT.sub("", plan)))
    if not found:
        return []
    return [
        "Deferred work on these files: include it in this task (`Resolves: D<n>` in "
        "requirements.md), or leave it:",
        *(e.line() for e in found),
    ]


#: out/<feature>/<this>: the over-limit files the spec check already named (said once)
NOTED = "over_limit_noted.json"
SPLIT = "split it into one file per concept"


def noted(cfg: Config, feature: str) -> list[str]:
    """The files the spec check named as over the limit: the user was asked at spec time."""
    path = cfg.feature_out(feature) / NOTED
    try:
        return [str(x) for x in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []


def split_answered(cfg: Config, feature: str) -> set[str]:
    """The files whose split question is answered: asked at spec time in this task, or on
    the list as a refactor. No gate asks about them again."""
    return {
        *noted(cfg, feature),
        *(f for e in read(cfg)[0] if e.kind == "refactor" for f in e.files),
    }


def over_limit_note(
    cfg: Config,
    feature: str,
    over: list[tuple[str, int]],
    sizes: Callable[[list[str]], dict[str, int]] | None = None,
) -> list[str]:
    """For the spec brief, once per file: the files the plan names that are already over
    the size limit (`over`: structure.plan_over_limit) and have no refactor entry yet.
    `sizes`: their lines as the gates count them (task_base.formatted_lines)."""
    path = cfg.feature_out(feature) / NOTED
    seen = noted(cfg, feature)
    listed = {f for e in read(cfg)[0] if e.kind == "refactor" for f in e.files}
    new = [(f, n) for f, n in over if f not in seen and f not in listed]
    if not new:
        return []
    counted = sizes([f for f, _ in new]) if sizes else {}
    new = [(f, counted.get(f, n)) for f, n in new]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([*seen, *(f for f, _ in new)]), encoding="utf-8")
    return [
        f"Files the plan names that are already over the size limit ({cfg.max_file_lines} "
        "lines): ask the user once per file: split it in this task (a plan step of its own), "
        f"or later (`framegate deferred --later <file> --feature {feature}`):",
        *(f"- {f}: {n} lines" for f, n in new),
    ]


def split_later(cfg: Config, feature: str, rel: str, lines: int | None = None) -> Entry | None:
    """The user's "later" to a file over the size limit: an entry of the list (None: no
    such file). `lines`: its size as the gates count it."""
    rel = rel.replace("\\", "/")
    file = cfg.root / rel
    if not file.is_file():
        return None
    n = lines or len(file.read_text(encoding="utf-8", errors="replace").splitlines())
    text = f"{SPLIT} ({n} lines, limit {cfg.max_file_lines})"
    return add(cfg, feature or "the spec dialogue", [Item("refactor", text, [rel])])[0]


def listing(cfg: Config, file: str = "") -> list[str]:
    entries = on_files(cfg, [file]) if file else read(cfg)[0]
    return [e.line() for e in entries]


# ---------- what a finished task leaves for later ----------
def answered_later(question: str, shown: str, kind: str) -> dict[str, Any] | None:
    """A question answered with its default, which leaves a proposed change for later (in
    the gate item or the brief's wording `shown`) -> an item as the progress file keeps it."""
    if not defaults.defers_change(f"{shown}\n{question}"):
        return None
    text = defaults.gist(question)
    return asdict(Item(kind, text, _files(text)))


def _files(text: str) -> list[str]:
    return [f for f in plan_files.named_files(text) if "/" in f]


def _step_files(cfg: Config, feature: str, steps: list[Any]) -> list[str]:
    path = cfg.spec_dir(feature) / "plan.md"
    bodies = plan_files.step_bodies(path.read_text(encoding="utf-8") if path.is_file() else "")
    out: list[str] = []
    for s in steps:
        for f in plan_files.named_files(bodies.get(int(s), "")) if str(s).isdigit() else []:
            if f not in out and (cfg.in_src(f) or (cfg.root / f).is_file()):
                out.append(f)
    return out


def task_items(
    cfg: Config, feature: str, mode: str, prog: dict[str, Any], events: list[dict[str, Any]]
) -> list[Item]:
    """What the task leaves for later, from its progress and its events."""
    items = [Item(**d) for d in prog.get("deferred") or []]
    for p in [*(prog.get("parked") or []), *(prog.get("left_parked") or [])]:
        steps = p.get("steps") or [p.get("step")]
        text = f"{p.get('label')}: parked: {p.get('reason')}"
        items.append(Item(mode, text, _step_files(cfg, feature, steps), "left parked"))
    for r in [*(prog.get("provisional") or []), *(prog.get("left_provisional") or [])]:
        tests = list(r.get("contested") or [])
        text = f"{r.get('label')}: the proposed test fixes, not applied: {', '.join(tests)}"
        files = sorted({t.split("::")[0] for t in tests if "/" in t.split("::")[0]})
        items.append(Item("tests", text, files, "left provisional, its contested tests red"))
    last = [e for e in events if e.get("kind") == "final_gates"]
    if last and not last[-1].get("passed") and last[-1].get("failed"):
        failed = ", ".join(last[-1]["failed"])
        text = f"final checks that failed when the task was approved: {failed}"
        items.append(Item("tests", text, [], "approved with failing checks"))
    step = prog.get("docs_step") or {}
    applied = step.get("applied") or []
    for d in doc_proposals.proposals(cfg, feature):
        if not d.important or d.text in applied:
            continue
        if not doc_proposals.among(d, prog.get("doc_rejected") or []):
            ok = doc_proposals.among(d, prog.get("doc_approved") or [])
            how = "approved, the docs step did not apply it" if ok else 'answered "later"'
            text = d.body + doc_proposals.noted(d, prog.get("doc_notes") or {})
            items.append(Item("docs", text, [d.file] if d.file else [], how))
    if cfg.unrunnable_tests:
        names = sorted(cfg.unrunnable_tests)
        files = sorted({t.split("::")[0] for t in names if "/" in t.split("::")[0]})
        text = "make the known-red tests run or pass ([gates] unrunnable_tests): "
        text += ", ".join(names)
        items.append(Item("tests", text, files, "offered as a follow-up"))
    return items
