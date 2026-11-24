"""Proposed doc updates: no agent of the loop writes a documentation file, it proposes.

Two classes (is_important):
- important: `.md` / `.rst` under a `docs` / `doc` folder (plan, design, architecture,
  reference). A proposal says what the doc says today and what it should say; it is a question
  at the final gate, default "later". One whose doc the task's code now contradicts
  (`code now:`) asks about the code: approve it (the doc now or later), or reject it.
- minor: README, CHANGELOG, HANDOVER outside those folders. One brief line; no question.

A stale docstring or comment in a source file is code text, not documentation (is_code_text):
no question, no docs step; one line in the final summary.

A proposal says what the task built. Before an important one is asked, its text is checked
against the code (CORRECTIONS, written from the check's findings); text the owner gives with
an approval is checked before the docs step applies it (TEXT_ASK).

A spec lists proposals under `## Proposed doc updates` of requirements.md; an agent of the run
adds a line to {out}/<f>/doc_proposals.md. After the final gate is approved, one docs step
(phase DOCS, the only one that may write a doc) applies the minor ones and the approved
important ones as its own commit. The list is kept in {specs}/<f>/doc_proposals.md.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

from framegate import defaults
from framegate.config import Config

FILE = "doc_proposals.md"
SECTION = "Proposed doc updates"
PHASE = "DOCS"
#: the phase of the runs that check a doc text against the code (role doc_check)
CHECK_PHASE = "DOC_CHECK"
#: who asks the final-gate question of an important proposal (the tag in open_questions.md)
WHO = "doc update"
ASK = " A: apply it after the task (the docs step). B: later (default)."
#: the question of a doc the task's code now contradicts (a `code now:` line)
CONTRA_ASK = (
    " A: approve the code; the docs step updates the doc after the task. B: approve the code; "
    "leave the doc as it is (later). C: reject the code: write your solution. Default: B."
)
#: the question of an approved proposal whose text from the owner contradicts the code
TEXT_ASK = (
    " A: apply your text as you wrote it. B: apply it corrected to what the code does. "
    "C: leave the doc as it is (later). Default: C."
)
#: {out}/<f>/: proposals corrected to what the code does (one bullet each; the same file
#: and place as the proposal it replaces)
CORRECTIONS = "doc_corrections.md"
MINOR_TITLE = "Doc updates that will be applied after approval"
CODE_TITLE = "Stale docstrings and comments in source files (code text, low risk)"
IMPORTANT_FORMAT = (
    "- <file under docs/>, <where in it>: today: <what it says now, quoted or summarised> -> "
    "proposed: <the new text> (<why>)"
)
CONTRA_FORMAT = (
    "- <file under docs/>, <where in it>: today: <what it says now, quoted> -> code now: <what "
    "the task's code does instead>"
)
MINOR_FORMAT = "- <README / CHANGELOG / HANDOVER file>: <what changes, one brief line>"
CODE_FORMAT = (
    "- code text: <source file>:<lines>: today: <the docstring or comment, quoted> -> code "
    "now: <what the code does>"
)
FORMATS = (
    f"a doc under docs/ (the owner decides at the final gate): `{IMPORTANT_FORMAT}`; one the "
    f"task's code now contradicts: `{CONTRA_FORMAT}`; "
    f"a README, CHANGELOG or handover note (applied after the approval): `{MINOR_FORMAT}`. "
    "A docstring or comment in a source file that the task's code contradicts is code text, "
    "not a doc: the run that changes the file rewords it; in a file no run of the task "
    f"changes: `{CODE_FORMAT}` (one line in the final summary, no question)"
)

DOC_FOLDERS = ("docs", "doc")
DOC_NAMES = ("readme", "changelog", "handover")
#: memory files of coding agents: instructions, not documentation
AGENT_FILES = ("claude.md", "agents.md")

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_NOTHING = re.compile(r"^\W*(none|n/?a|nothing)\b[\W]*$", re.I)
_PLACEHOLDER = re.compile(r"^<.*>$")
_FILE = re.compile(r"[\w./\\-]*\w\.(?:md|rst)\b", re.I)
#: an answer that approves an important proposal; anything else is "later"
APPROVED = re.compile(r"^\W*(?:approved?|yes|apply|now|do it)\b", re.I)
_SAME_AS = re.compile(r"^\W*same as item \d+\s*:\s*", re.I)
_REJECT = re.compile(r"^\W*(?:C\b|reject)", re.I)
_LATER = re.compile(r"^\W*(?:B\b|default\b)|\b(?:later|leave)\b", re.I)
_CONTRA = re.compile(r"->\s*code now\s*:", re.I)
_LINE = re.compile(
    r"^(?P<head>.*?):\s*today\s*:\s*(?P<today>.*?)\s*->\s*(?:code now|proposed)\s*:\s*(?P<new>.*)$",
    re.I | re.S,
)
_PLACE_END = re.compile(r":\s*(?:today|the doc says|your text)\s*:", re.I)
_CODE_TEXT = re.compile(r"^\W*(?:code text|src docstring|source comment)\b[^:]*:", re.I)
_ANY_FILE = re.compile(r"[\w./\\-]*\w\.[A-Za-z]\w{0,5}\b")
_LINE_NO = re.compile(
    r"\b(?:(?:around|about|near)\s+)?(?:lines?|l\.)\s*~?\d+(?:\s*(?:[-–,]|and)\s*~?\d+)*"
    r"|:\d+\b|\(\s*~?\d+\s*\)",
    re.I,
)
_FILLER = ("section", "heading", "the")


def is_important(rel: str) -> bool:
    return any(x in DOC_FOLDERS for x in rel.lower().split("/")[:-1])


def is_own(cfg: Config, rel: str) -> bool:
    """A file framegate writes itself: its folders, the code map, the knowledge file."""
    p = cfg.paths
    own = [x.rstrip("/") + "/" for x in (p.specs, p.out, p.metrics, p.tests, p.review_tests)]
    return rel in (cfg.code_map, cfg.knowledge) or rel.startswith(tuple(own))


def is_doc(cfg: Config, rel: str) -> bool:
    """A documentation file of the project: a `.md` / `.rst` file under a `docs` or `doc`
    folder, or a README / CHANGELOG / HANDOVER anywhere. Not: framegate's own files, a dot
    folder, an agent's memory file."""
    parts = rel.lower().split("/")
    stem, _, ext = parts[-1].rpartition(".")
    if ext not in ("md", "rst") or parts[-1] in AGENT_FILES:
        return False
    if is_own(cfg, rel) or rel.startswith(".") or "/." in rel:
        return False
    return stem.startswith(DOC_NAMES) or is_important(rel)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def is_code_text(text: str) -> bool:
    """A line about a docstring or comment in a source file: it says so (`code text:`), or
    the file it names before `today:` is no documentation file."""
    if _CODE_TEXT.match(text):
        return True
    end = _PLACE_END.search(text)
    head = text[: end.start()] if end else ""
    return bool(head) and not _FILE.search(head) and bool(_ANY_FILE.search(head))


def key(text: str) -> str:
    """Two proposals with the same key are one: the same file and place (`<file>, <where>:`,
    without a line number), whatever their wording. A line without a place (a minor one): the
    same file and the same text."""
    m = _FILE.search(text)
    file = m.group(0).replace("\\", "/") if m else ""
    end = _PLACE_END.search(text)
    if m and end and end.start() >= m.end():
        where = _LINE_NO.sub(" ", text[m.end() : end.start()])
        return f"{file} @ {' '.join(w for w in _words(where).split() if w not in _FILLER)}"
    return f"{file} {_words(text[m.end() :] if m else text)}"


def verdict(question: str, answer: str) -> str:
    """The owner's answer to a `[doc update]` item: "approved" (A), "later" (B, the default)
    or "rejected" (C, a contradiction only: the answer is the owner's solution and changes
    the code). A letter means only what the question offered."""
    a = _SAME_AS.sub("", answer.strip())
    pick = defaults.chosen(question, answer)[0]
    if pick:  # the letter decides; the words after it are a note
        if TEXT_ASK in question:
            return "approved" if pick in ("A", "B") else "later"
        rejected = pick == "C" and CONTRA_ASK in question
        return "rejected" if rejected else "approved" if pick == "A" else "later"
    if TEXT_ASK in question:
        return "later"
    if CONTRA_ASK in question and _REJECT.match(a):
        return "rejected"
    if _LATER.search(a):
        return "later"
    return "approved" if re.match(r"^\W*A\b", a) or APPROVED.match(a) else "later"


def run_file(cfg: Config, feature: str = "<feature>") -> str:
    return f"{cfg.paths.out}/{feature}/{FILE}"


def write_denied(cfg: Config, phase: str, rel: str) -> str:
    """The write guard's answer to an agent (or the spec chat) that tries to edit a doc."""
    where = (
        f"under `## {SECTION}` of the spec's requirements.md"
        if phase == "SPEC"
        else f"to {run_file(cfg)} (keep the lines that are there)"
    )
    form = (
        f"{IMPORTANT_FORMAT}`, or where the task's code now contradicts it: `{CONTRA_FORMAT}"
        if is_important(rel)
        else MINOR_FORMAT
    )
    return (
        f"[{phase}] Blocked write to {rel}: docs are the owner's, nothing in the loop changes "
        f"them. Write the proposal {where}, one line: `{form}`. Then carry on with your work: "
        "do not park and do not ask for it."
    )


@dataclass(frozen=True)
class Proposal:
    text: str
    origin: str  # "spec" or "run"

    @property
    def file(self) -> str:
        m = _FILE.search(self.text)
        return m.group(0).replace("\\", "/") if m else ""

    @property
    def code_text(self) -> bool:
        """About a docstring or comment in a source file: no doc update."""
        return is_code_text(self.text)

    @property
    def important(self) -> bool:
        """Under a docs folder, or no file to tell by: the owner decides."""
        return not self.code_text and (not self.file or is_important(self.file))

    @property
    def minor(self) -> bool:
        return not self.code_text and not self.important

    @property
    def head(self) -> str:
        """`<file>, <where>` of an important proposal (the whole text when it has none)."""
        end = _PLACE_END.search(self.text)
        return self.text[: end.start()] if end else self.text

    def text_question(self, said: str, code: str) -> str:
        """The gate item of the owner's text `said` that contradicts the code (`code`)."""
        return f'{self.head}: your text: "{said}" -> code now: {code.rstrip(".")}.{TEXT_ASK}'

    @property
    def contradicts(self) -> bool:
        """The task's code contradicts what the doc says (a `code now:` line)."""
        return self.important and bool(_CONTRA.search(self.text))

    @property
    def body(self) -> str:
        """The gate item without its question; a contradiction in plain words."""
        m = _LINE.match(self.text) if self.contradicts else None
        if not m:
            return self.text
        today = m.group("today").strip().strip("'\"“”")
        code = m.group("new").rstrip(".")
        return f'{m.group("head")}: the doc says: "{today}". The code now does: {code}.'

    def shown(self) -> str:
        return f"{self.text} ({self.origin})"

    def question(self) -> str:
        return self.body + (CONTRA_ASK if self.contradicts else ASK)


def asked(question: str) -> str:
    """The proposal of a final-gate item (`[doc update] <body><ASK>`: its body), "" for
    another."""
    tag = f"[{WHO}] "
    if not question.startswith(tag):
        return ""
    body = question.removeprefix(tag)
    for ask in (ASK, CONTRA_ASK, TEXT_ASK):
        body = body.split(ask)[0]
    return body.strip()


def _bullets(text: str) -> list[str]:
    out: list[str] = []
    for line in _COMMENT.sub("", text).splitlines():
        m = _BULLET.match(line)
        if m:
            out.append(m.group(1))
        elif out and line.strip() and line[:1].isspace():
            out[-1] += " " + line.strip()
    return [x for x in out if not _NOTHING.match(x) and not _PLACEHOLDER.match(x)]


def _spec_section(text: str) -> str:
    m = re.search(rf"^##\s+{re.escape(SECTION)}\b.*$", text, re.M | re.I)
    if not m:
        return ""
    rest = text[m.end() :]
    end = re.search(r"^##\s", rest, re.M)
    return rest[: end.start()] if end else rest


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def proposals(cfg: Config, feature: str) -> list[Proposal]:
    """Every proposal of the task: the spec's first, then the run's; one per key (the first
    wording, a contradiction over a plain one), none for a file framegate writes itself."""
    spec = _bullets(_spec_section(_read(cfg.spec_dir(feature) / "requirements.md")))
    run = _bullets(_read(cfg.root / run_file(cfg, feature)))
    out: dict[str, Proposal] = {}
    for p in [Proposal(x, "spec") for x in spec] + [Proposal(x, "run") for x in run]:
        if p.file and is_own(cfg, p.file):
            continue
        k = key(p.text)
        if k not in out or (p.contradicts and not out[k].contradicts):
            out[k] = p
    for fixed in _bullets(_read(cfg.feature_out(feature) / CORRECTIONS)):
        old = out.get(key(fixed))
        if old is not None and old.important:
            out[key(fixed)] = Proposal(fixed, f"{old.origin}, corrected to the code")
    return list(out.values())


def among(p: Proposal, answered: list[str]) -> bool:
    """Is `p` one of the `answered` proposals (as asked; the same key)?"""
    return key(p.text) in {key(a) for a in answered}


def noted(p: Proposal, notes: dict[str, str]) -> str:
    """The owner's words on `p` at the gate (`notes`: proposal as asked -> the words after
    the option's letter), as a suffix; "" = none."""
    note = said(p, notes)
    return f" Owner's note: {note}" if note else ""


def said(p: Proposal, notes: dict[str, str]) -> str:
    """The owner's own words on `p` ("" = none)."""
    k = key(p.text)
    return next((n for asked, n in notes.items() if key(asked) == k), "")


def corrections(text: str, checked: list[Proposal]) -> list[str]:
    """The lines of the doc check's result (`text`) that correct one of `checked`: the same
    file and place, another wording."""
    known = {key(p.text): p.text for p in checked}
    return [x for x in _bullets(text) if key(x) in known and x != known[key(x)]]


_CONFLICT = re.compile(r"^(\d+)\W+code now\s*:\s*(.*\S)", re.I | re.S)


def text_conflicts(text: str) -> dict[int, str]:
    """The text check's result: item number -> what the code does instead."""
    found = (_CONFLICT.match(x) for x in _bullets(text))
    return {int(m.group(1)): m.group(2) for m in found if m}


def to_apply(cfg: Config, feature: str, approved: list[str]) -> list[Proposal]:
    """What the docs step applies: the minor ones and the approved important ones."""
    found = proposals(cfg, feature)
    return [p for p in found if p.minor or (p.important and among(p, approved))]


def brief_lines(cfg: Config, feature: str) -> list[str]:
    """The block of the final brief: the minor ones ([] = none). The important ones are
    questions of the gate."""
    minor = [p for p in proposals(cfg, feature) if p.minor]
    if not minor:
        return []
    return [
        f"{MINOR_TITLE}: {len(minor)}; nothing to answer",
        *(f"  - {p.shown()}" for p in minor),
    ]


def section(cfg: Config, feature: str) -> list[str]:
    """The generated section of final_summary.md and final_facts.md ([] = none)."""
    every = proposals(cfg, feature)
    found = [p for p in every if not p.code_text]
    stale = [
        f"## {CODE_TITLE}: {len(every) - len(found)}",
        "",
        *(f"- {p.shown()}" for p in every if p.code_text),
        "",
        "No step of the task changed these files, so they were not reworded; nothing to answer.",
    ]
    if not found:
        return stale if every else []
    minor = [p for p in found if p.minor]
    major = [p for p in found if p.important]
    out = [f"## {SECTION}: {len(found)}", ""]
    if minor:
        out += [f"{MINOR_TITLE}: {len(minor)}", *(f"- {p.shown()}" for p in minor), ""]
    if major:
        out += [
            f"Yours to decide at the final gate (default: later): {len(major)}",
            *(f"- {p.shown()}" for p in major),
            "",
        ]
    return [
        *out,
        "No doc was changed in the run. After the final gate is approved, one docs step "
        "applies the first group and what you approved of the second, as its own commit.",
        *(["", *stale] if len(every) > len(found) else []),
    ]


def facts_lines(cfg: Config, feature: str) -> list[str]:
    """The section of final_facts.md ([] = none)."""
    found = section(cfg, feature)
    if not found:
        return []
    if all(p.code_text for p in proposals(cfg, feature)):
        return [*found, "No gate item: one short line in the summary, as low risk."]
    return [
        *found,
        f"The first group is no gate item: no question, no decision, no 'Left out' line. The "
        f"second group is among the numbered gate items (`[{WHO}]`): ask each in the brief with "
        "what the doc says today, the proposed text and the default 'later'; one whose item "
        "says what the code now does (the code contradicts the doc) with its options A / B / C "
        "as the item words them, default B. A stale docstring or comment (if listed) is no "
        "gate item either: one short line in the summary, as low risk. framegate shows "
        "the first group on the brief and appends this section to final_summary.md itself.",
    ]


def keep(
    cfg: Config,
    feature: str,
    applied: list[str],
    approved: list[str],
    why: str = "",
    rejected: list[str] | None = None,
    notes: dict[str, str] | None = None,
) -> str:
    """The task ends: the list goes to {specs}/<f>/doc_proposals.md, each line marked applied
    or not (`framegate cleanup` keeps the spec folder). Returns its path, "" when there is
    nothing to keep. `why`: why the docs step applied nothing; `rejected`: contradictions
    whose code the owner rejected; `notes`: the owner's words at the gate (`noted`)."""
    found = proposals(cfg, feature)
    if not found:
        return ""

    def line(p: Proposal) -> str:
        if p.code_text:
            return f"- [ ] {p.shown()}: code text, left as it is (no step changed the file)"
        said = noted(p, notes or {})
        if p.text in applied:
            return f"- [x] {p.shown()}: applied" + (f".{said}" if said else "")
        later = p.important and not among(p, approved)
        reason = "later" if later else why or "the docs step did not run"
        if among(p, rejected or []):
            reason = "you rejected the code"
        return f"- [ ] {p.shown()}: not applied ({reason})" + (f".{said}" if said else "")

    head = [f"# {SECTION}: {feature}", "", "Applied ones are in the commit `<task>: docs`.", ""]
    path = cfg.spec_dir(feature) / FILE
    path.write_text("\n".join([*head, *map(line, found)]) + "\n", encoding="utf-8")
    return f"{cfg.paths.specs}/{feature}/{FILE}"
