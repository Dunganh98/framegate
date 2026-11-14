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

    def line(self) -> str:
        return f"{self.text} ({'from the spec' if self.source == 'spec' else self.source})"


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
        x
        for x in found
        if re.search(r"\w", x) and not _NOTHING.match(x) and not _PLACEHOLDER.match(x)
    ]


def parse_run(text: str, kind: Kind = PIPELINE) -> list[str]:
    """`- PIPELINE FOLLOW-UP [<file>]: <what, why>` (or `- REFERENCE FOLLOW-UP [...]: ...`)
    lines of a run file -> "<file>: <what>"."""
    out = []
    for line in text.splitlines():
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


def brief_lines(found: list[Item], kind: Kind = PIPELINE, who: str = "") -> list[str]:
    """The block of the final brief ([] when there is nothing to do)."""
    if not found:
        return []
    return [
        f"{kind.title(who)}: {len(found)}; nothing to answer",
        *(f"  - {it.line()}" for it in found),
    ]


def markdown(feature: str, found: list[Item], kind: Kind = PIPELINE) -> str:
    """The list as a file: {out}/<f>/<the kind's file> and its durable copy."""
    return (
        "\n".join(
            [
                f"# {kind.section}: {feature}",
                "",
                kind.intro,
                "",
                *(f"- [ ] {it.line()}" for it in found),
            ]
        )
        + "\n"
    )


def section_lines(found: list[Item], kind: Kind = PIPELINE, who: str = "") -> list[str]:
    """The generated section of final_facts.md / final_summary.md ([] when empty)."""
    if not found:
        return []
    return [
        f"## {kind.title(who)}: {len(found)}",
        "",
        *(f"- {it.line()}" for it in found),
    ]


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


# ---------- both lists ----------
def reference_name(cfg: Config, feature: str) -> str:
    """Whom the reference follow-up is for: the feature's reference(s), by name."""
    from framegate import references  # it reads the spec's tables; this module stays a leaf

    return ", ".join(references.named(cfg, feature))


def all_brief_lines(cfg: Config, feature: str) -> list[str]:
    """The follow-up blocks of the final brief, the pipeline's first."""
    who = reference_name(cfg, feature)
    return [x for k in KINDS for x in brief_lines(items(cfg, feature, k), k, who)]


def all_sections(cfg: Config, feature: str) -> list[list[str]]:
    """The generated follow-up sections of final_summary.md: one list of lines per kind that
    has items."""
    who = reference_name(cfg, feature)
    found = [(k, items(cfg, feature, k)) for k in KINDS]
    return [section_lines(x, k, who) for k, x in found if x]


def facts_lines(cfg: Config, feature: str) -> list[str]:
    """final_facts.md: every follow-up section with what the summary agent does with it
    (nothing). The lists are written to {out}/<f>/ on the way."""
    out: list[str] = []
    who = reference_name(cfg, feature)
    for kind in KINDS:
        found = write(cfg, feature, kind=kind)
        if found:
            out += [
                *([""] if out else []),
                *section_lines(found, kind, who),
                "",
                f"{kind.facts_note} Not a gate item: do not turn it into a question, a decision "
                "or a 'Left out' line. framegate shows the list on the final brief and appends "
                "it to final_summary.md itself.",
            ]
    return out
