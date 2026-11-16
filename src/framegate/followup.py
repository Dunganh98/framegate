"""Follow-ups: what a feature makes necessary outside itself. Two lists, one life cycle.

**Pipeline follow-up.** Deploy files (Dockerfile, compose, k8s manifests, ...) and CI files
(yml, scripts) are never changed by a feature, not even at spec time: they are locked for the
chat and for every agent. When a feature means one of them must change (a new package folder
the image must copy, a new dependency it must install, a new test selection for CI), that
change is a separate step after the feature.

**Reference follow-up.** A reference (another project, read-only; references.py) is never
changed by a feature either. What the REFERENCE should change because of this feature (a bug
fixed here that the reference shares, a deliberate improvement of a port) is work for that
project. Seen live (da-argos, a runner ported from da-otto): the reviewer found a crash the
reference has too; it was fixed in the port, and nothing recorded that the reference still
has it.

Neither may get lost, so each is noted in three places and raised by framegate itself:

- the spec: requirements.md, `## Pipeline follow-up` / `## Reference follow-up` (the spec
  dialogue fills them; "None.");
- the run: an agent that finds such a need does not attempt the change and writes one line,
  `- PIPELINE FOLLOW-UP [<file>]: <what must change and why>` or `- REFERENCE FOLLOW-UP
  [<reference file>]: <what and why>`, into its notes (the implementer's step note ->
  steps.md, the reviewer's review.md; for the reference also the triage's triage.md), like an
  assumption;
- the final gate: the generated part of the final brief, final_facts.md and final_summary.md
  list every item as a separate step to do after the feature. It is no gate item: nothing to
  answer, nothing that blocks, and the summary agent cannot leave it out.

**Outside the repository.** Some of what a task makes necessary is no file of this repository:
a variable in the CI's variable group, a secret in the vault, a migration of the real
database, a switch of an environment. Such an item is part of the pipeline follow-up, written
in one fixed form everywhere (the spec section, an agent's note, the kept list):
`- OUTSIDE [<where>]: <what must be done> (<why>)`. Nothing in framegate ever does one: it
is shown apart ("outside the repository: by hand"), never counted as open work of the
pipeline task, and put as an unticked checklist into the final summary and on top of the
pull request's description, so it is seen at merge time.

After DONE each list is kept in `{specs}/<f>/pipeline_followup.md` / `reference_followup.md`
(written with the metrics row; `framegate cleanup` keeps the spec folder) and in a
`pipeline_followup` / `reference_followup` event.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

from framegate.config import Config

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_NOTHING = re.compile(r"^\W*(none|n/?a|nothing)\b[\W]*$", re.I)
_PLACEHOLDER = re.compile(r"^<.*>$")
#: an item that is no file of this repository, as its text (the bullet already taken off)
_OUTSIDE = re.compile(
    r"^`?OUTSIDE(?:[ _-]*FOLLOW[ _-]?UP)?`?\s*\[([^\]]*)\]\s*:?\s*(.+?)\s*$", re.I
)
#: how such an item is written: in the spec section, in an agent's notes, in the kept list
OUTSIDE_FORMAT = "- OUTSIDE [<where>]: <what must be done> (<why>)"
OUTSIDE_TITLE = "Outside the repository, by hand (framegate does not do them)"
#: under that section in final_facts.md, for the summary agent
OUTSIDE_FACTS_NOTE = (
    "What a person must do by hand outside the repository because of this feature (a CI "
    "variable, a secret, a real database, an environment); nobody did it in this run."
)


def outside_parts(text: str) -> tuple[str, str] | None:
    """(where, what) of an item outside the repository; None for any other item."""
    m = _OUTSIDE.match(text.strip())
    return (m.group(1).strip().strip("`"), m.group(2)) if m else None


def _canonical(text: str) -> str:
    """An item's text; an outside item in its one form, `OUTSIDE [<where>]: <what>`."""
    parts = outside_parts(text)
    return f"OUTSIDE [{parts[0]}]: {parts[1]}" if parts else text


def _shown(text: str) -> str:
    """An item's text for a reader: an outside item as `<where>: <what>` (it stands under a
    title of its own)."""
    parts = outside_parts(text)
    if not parts:
        return text
    return f"{parts[0]}: {parts[1]}" if parts[0] else parts[1]


@dataclass(frozen=True)
class Kind:
    """One follow-up list: its spec section, its agent line and its files."""

    #: the event's name
    name: str
    #: the `## ` section of requirements.md
    section: str
    #: the generated list of the run ({out}/<f>/) and the durable copy ({specs}/<f>/)
    file: str
    #: the run files whose lines are read, with who wrote them
    run_files: tuple[tuple[str, str], ...]
    line_re: re.Pattern[str]
    #: what the list is, after its title ("{who}": whom it is for)
    heading: str
    #: first lines of the list's file
    intro: str
    #: under the list in final_facts.md, for the summary agent
    facts_note: str
    #: the progress line at DONE, after the count
    done: str

    def title(self, who: str = "") -> str:
        return self.heading.format(who=who or "the reference")


PIPELINE = Kind(
    "pipeline_followup",
    "Pipeline follow-up",
    "pipeline_followup.md",
    (("steps.md", "implementer"), ("review.md", "reviewer")),
    re.compile(
        r"^\s*[-*]\s*PIPELINE(?:[ _-]*FOLLOW[ _-]?UP)?\s*\[([^\]]*)\]\s*:?\s*(.+?)\s*$", re.I
    ),
    "Pipeline follow-up (a separate step after this feature; not part of it)",
    "Deploy / CI changes this feature makes necessary. The feature did not make them "
    "(those files are locked for it): do each one as a separate step.",
    "Deploy / CI changes this feature makes necessary; the feature did not make them.",
    "to do as a separate step (deploy / CI)",
)
REFERENCE = Kind(
    "reference_followup",
    "Reference follow-up",
    "reference_followup.md",
    (("steps.md", "implementer"), ("review.md", "reviewer"), ("triage.md", "triage")),
    re.compile(r"^\s*[-*]\s*REFERENCE[ _-]*FOLLOW[ _-]?UP\s*\[([^\]]*)\]\s*:?\s*(.+?)\s*$", re.I),
    "Reference follow-up (for {who}; not part of this feature)",
    "What the reference should change because of this feature (a bug fixed here that the "
    "reference shares, a deliberate improvement). The feature did not change the reference "
    "(it is read-only): each one is work for that project.",
    "What the reference should change because of this feature (a bug fixed here that it "
    "shares, a deliberate improvement); the feature did not change the reference.",
    "for the reference's own project",
)
KINDS = (PIPELINE, REFERENCE)
#: the pipeline list's names, as before the reference list existed
SECTION = PIPELINE.section
FILE = PIPELINE.file
RUN_FILES = PIPELINE.run_files
LINE_RE = PIPELINE.line_re
HEADING = PIPELINE.heading


@dataclass(frozen=True)
class Item:
    text: str
    #: "spec" | "implementer" | "reviewer" | "triage"
    source: str

    @property
    def outside(self) -> bool:
        """Not a file of this repository: done by hand, never by the pipeline task."""
        return outside_parts(self.text) is not None

    def _from(self) -> str:
        return "from the spec" if self.source == "spec" else self.source

    def line(self) -> str:
        """The item as the kept list holds it (an outside item keeps its `OUTSIDE [..]:`)."""
        return f"{self.text} ({self._from()})"

    def shown(self) -> str:
        """The item for a reader (an outside item without its marker)."""
        return f"{_shown(self.text)} ({self._from()})"


def _section(text: str, kind: Kind = PIPELINE) -> str:
    m = re.search(rf"^##\s+{re.escape(kind.section)}\b.*$", text, re.M | re.I)
    if not m:
        return ""
    rest = text[m.end() :]
    end = re.search(r"^##\s", rest, re.M)
    return rest[: end.start()] if end else rest


def parse_spec(text: str, kind: Kind = PIPELINE) -> list[str]:
    """The items of a requirements.md's follow-up section: its bullets, or (no bullet) its
    text lines; [] when the section is missing, empty or "None."."""
    body = [x.strip() for x in _section(_COMMENT.sub("", text), kind).splitlines() if x.strip()]
    bullets = [m.group(1) for x in body if (m := _BULLET.match(x))]
    found = bullets or body
    return [
        _canonical(x) if kind is PIPELINE else x
        for x in found
        if re.search(r"\w", x) and not _NOTHING.match(x) and not _PLACEHOLDER.match(x)
    ]


def parse_run(text: str, kind: Kind = PIPELINE) -> list[str]:
    """`- PIPELINE FOLLOW-UP [<file>]: <what, why>` (or `- REFERENCE FOLLOW-UP [...]: ...`)
    lines of a run file -> "<file>: <what>"; for the pipeline list also `- OUTSIDE [<where>]:
    <what>` lines -> "OUTSIDE [<where>]: <what>"."""
    out = []
    for line in text.splitlines():
        bullet = _BULLET.match(line) if kind is PIPELINE else None
        if bullet and outside_parts(bullet.group(1)):
            out.append(_canonical(bullet.group(1)))
            continue
        m = kind.line_re.match(line)
        if m:
            where = m.group(1).strip().strip("`")
            out.append(
                f"{where}: {m.group(2)}" if where and where.lower() != "none" else m.group(2)
            )
    return out


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def items(cfg: Config, feature: str, kind: Kind = PIPELINE) -> list[Item]:
    """Every follow-up of the feature of one kind: the spec's first, then the run's, each
    once."""
    spec = _read(cfg.spec_dir(feature) / "requirements.md")
    found = [Item(x, "spec") for x in parse_spec(spec, kind)]
    for name, who in kind.run_files:
        found += [Item(x, who) for x in parse_run(_read(cfg.feature_out(feature) / name), kind)]
    seen: set[str] = set()
    out = []
    for it in found:
        key = " ".join(it.text.lower().split())
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def split(found: list[Item]) -> tuple[list[Item], list[Item]]:
    """(the items in the repository, the ones outside it)."""
    return [it for it in found if not it.outside], [it for it in found if it.outside]


def brief_lines(found: list[Item], kind: Kind = PIPELINE, who: str = "") -> list[str]:
    """The block of the final brief ([] when there is nothing to do): the items in the
    repository, then (under their own line) the ones outside it."""
    repo, outside = split(found)
    out: list[str] = []
    if repo:
        out += [
            f"{kind.title(who)}: {len(repo)}; nothing to answer",
            *(f"  - {it.shown()}" for it in repo),
        ]
    if outside:
        out += [
            f"{kind.section}, outside the repository: by hand: {len(outside)}; nothing to "
            "answer (a checklist in the pull request)",
            *(f"  - {it.shown()}" for it in outside),
        ]
    return out


def markdown(feature: str, found: list[Item], kind: Kind = PIPELINE) -> str:
    """The list as a file: {out}/<f>/<the kind's file> and its durable copy."""
    return (
        "\n".join(
            [
                f"# {kind.section}: {feature}",
                "",
                kind.intro,
                *(
                    [
                        "A line that starts with OUTSIDE is no file of this repository (a CI "
                        "variable, a secret, a database, an environment): done by hand, never "
                        "by the pipeline task. Tick it yourself when it is done."
                    ]
                    if any(it.outside for it in found)
                    else []
                ),
                "",
                *(f"- [ ] {it.line()}" for it in found),
            ]
        )
        + "\n"
    )


def section_lines(found: list[Item], kind: Kind = PIPELINE, who: str = "") -> list[str]:
    """The generated section of final_facts.md / final_summary.md for the items in the
    repository ([] when there is none)."""
    repo, _ = split(found)
    if not repo:
        return []
    return [
        f"## {kind.title(who)}: {len(repo)}",
        "",
        *(f"- {it.shown()}" for it in repo),
    ]


def outside_lines(shown: list[str]) -> list[str]:
    """The items outside the repository as a section with an unticked checklist: in
    final_summary.md and on top of a pull request's description ([] when there is none)."""
    if not shown:
        return []
    return [f"## {OUTSIDE_TITLE}: {len(shown)}", "", *(f"- [ ] {x}" for x in shown)]


def sections(found: list[Item], kind: Kind = PIPELINE, who: str = "") -> list[list[str]]:
    """Every generated section of one kind: the repository's items, then the outside ones."""
    _, outside = split(found)
    both = [section_lines(found, kind, who), outside_lines([it.shown() for it in outside])]
    return [x for x in both if x]


_OUTSIDE_SECTION = re.compile(rf"^## {re.escape(OUTSIDE_TITLE)}.*?(?=^## |\Z)", re.M | re.S)


def pr_checklist(cfg: Config, feature: str) -> list[str]:
    """What is still to do by hand outside the repository, for a pull request: the unticked
    outside lines of the kept list, or (no list yet: the task is not DONE) the spec's and the
    run's."""
    if list_path(cfg, feature).is_file():
        return [it.shown() for it in read_list(cfg, feature) if it.outside and not it.ticked]
    return [it.shown() for it in items(cfg, feature) if it.outside]


def with_checklist(text: str, shown: list[str]) -> str:
    """A pull request's description with the outside checklist on top (some hosts cut a long
    text at its end); the same section further down in `text` is taken out."""
    if not shown:
        return text
    rest = _OUTSIDE_SECTION.sub("", text).strip()
    return "\n".join([*outside_lines(shown), *(["", rest] if rest else []), ""])


def write(cfg: Config, feature: str, durable: bool = False, kind: Kind = PIPELINE) -> list[Item]:
    """Write the list to {out}/<f>/ (or, durable, to {specs}/<f>/); nothing listed: no file,
    and a stale one is removed."""
    found = items(cfg, feature, kind)
    folder = cfg.spec_dir(feature) if durable else cfg.feature_out(feature)
    path = folder / kind.file
    if found and folder.is_dir():
        path.write_text(markdown(feature, found, kind), encoding="utf-8")
    elif not found:
        path.unlink(missing_ok=True)
    return found


# ---------- the kept pipeline list: the input of the pipeline task (pipeline.py) ----------
_LIST_LINE = re.compile(r"^(\s*[-*]\s*\[)([ xX])(\]\s*)(.+?)\s*$")
_LEFT_OPEN = re.compile(r"\s*\(\s*left open\s*:\s*(.*?)\s*\)\s*$", re.I)
_ITEM_FILE = re.compile(r"^`?([^\s:`]+)`?\s*:(?:\s|$)")
#: how a line of the list is written, for messages
LINE_FORMAT = "- [ ] <file>: <what must change> (<why>)"
LEFT_OPEN_FORMAT = "(left open: <reason>)"


@dataclass(frozen=True)
class ListItem:
    """One `- [ ]` line of {specs}/<f>/pipeline_followup.md."""

    #: the line without its box and without a left-open note
    text: str
    #: the deploy / CI file it names (repo-relative posix; "" = none; `ci/` = a folder)
    file: str
    #: `[x]`: done and approved in a pipeline task
    ticked: bool
    #: `(left open: <reason>)` at the end of the line ("" = none)
    reason: str
    #: `OUTSIDE [<where>]: ...`: no file of this repository; by hand, never the task's work
    outside: bool = False

    def shown(self) -> str:
        """The item for a reader (an outside item without its marker)."""
        return _shown(self.text)

    def covers(self, rel: str) -> bool:
        return bool(self.file) and (
            rel == self.file or (self.file.endswith("/") and rel.startswith(self.file))
        )


def list_path(cfg: Config, feature: str) -> pathlib.Path:
    return cfg.spec_dir(feature) / PIPELINE.file


def _list_item(line: str) -> ListItem | None:
    m = _LIST_LINE.match(line)
    if not m:
        return None
    text = m.group(4)
    left = _LEFT_OPEN.search(text)
    if left:
        text = text[: left.start()].rstrip()
    outside = outside_parts(text) is not None
    named = None if outside else _ITEM_FILE.match(text)
    file = named.group(1).replace("\\", "/") if named else ""
    while file.startswith("./"):
        file = file[2:]
    reason = left.group(1) if left else ""
    return ListItem(_canonical(text), file, m.group(2) != " ", reason, outside)


def read_list(cfg: Config, feature: str) -> list[ListItem]:
    """The items of the kept pipeline list ([] = no file, or no item in it)."""
    lines = _read(list_path(cfg, feature)).splitlines()
    return [it for it in map(_list_item, lines) if it is not None]


def open_count(cfg: Config, feature: str) -> int:
    """How many items of the kept pipeline list are open work for the pipeline task: not
    ticked, and a file of this repository."""
    return sum(1 for it in read_list(cfg, feature) if not it.ticked and not it.outside)


def outside_count(cfg: Config, feature: str) -> int:
    """How many items of the kept pipeline list are outside the repository and not ticked
    (done by hand; the user ticks them)."""
    return sum(1 for it in read_list(cfg, feature) if it.outside and not it.ticked)


def tick(cfg: Config, feature: str, changed: set[str]) -> int:
    """Tick the open items whose file is in `changed` (one left open with a reason stays
    open); returns how many."""
    path = list_path(cfg, feature)
    out, count = [], 0
    for line in _read(path).splitlines():
        it, m = _list_item(line), _LIST_LINE.match(line)
        done = it and m and not it.ticked and not it.reason and any(it.covers(f) for f in changed)
        if done and m:
            line = f"{m.group(1)}x{m.group(3)}{m.group(4)}"
            count += 1
        out.append(line)
    if count:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return count


# ---------- both lists ----------
def reference_name(cfg: Config, feature: str) -> str:
    """Whom the reference follow-up is for: the feature's reference(s), by name."""
    from framegate import references  # it reads the spec's tables; this module stays a leaf

    return ", ".join(references.named(cfg, feature))


def all_brief_lines(cfg: Config, feature: str) -> list[str]:
    """The follow-up blocks of the final brief, the pipeline's first."""
    who = reference_name(cfg, feature)
    out: list[str] = []
    for kind in KINDS:
        found = items(cfg, feature, kind)
        lines = brief_lines(found, kind, who)
        repo = len(split(found)[0])
        if repo and kind is PIPELINE:  # after the repository's items, before the outside ones
            lines.insert(repo + 1, f"  after this task: /framegate:pipeline {feature} does them")
        out += lines
    return out


def all_sections(cfg: Config, feature: str) -> list[list[str]]:
    """The generated follow-up sections of final_summary.md: one list of lines per kind that
    has items."""
    who = reference_name(cfg, feature)
    return [s for k in KINDS for s in sections(items(cfg, feature, k), k, who)]


def facts_lines(cfg: Config, feature: str) -> list[str]:
    """final_facts.md: every follow-up section with what the summary agent does with it
    (nothing). The lists are written to {out}/<f>/ on the way."""
    out: list[str] = []
    who = reference_name(cfg, feature)
    for kind in KINDS:
        found = write(cfg, feature, kind=kind)
        for section in sections(found, kind, who):
            by_hand = section[0].startswith(f"## {OUTSIDE_TITLE}")
            note = OUTSIDE_FACTS_NOTE if by_hand else kind.facts_note
            out += [
                *([""] if out else []),
                *section,
                "",
                f"{note} Not a gate item: do not turn it into a question, a decision "
                "or a 'Left out' line. framegate shows the list on the final brief and appends "
                "it to final_summary.md itself.",
            ]
    return out
