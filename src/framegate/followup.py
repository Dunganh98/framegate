"""Pipeline follow-up: what a feature makes necessary in deploy and CI files.

Deploy files (Dockerfile, compose, k8s manifests, ...) and CI files (yml, scripts) are never
changed by a feature, not even at spec time: they are locked for the chat and for every agent.
When a feature means one of them must change (a new package folder the image must copy, a new
dependency it must install, a new test selection for CI), that change is a separate step after
the feature. It must not get lost, so it is noted in three places and raised by framegate itself:

- the spec: requirements.md, `## Pipeline follow-up` (the spec dialogue fills it; "None.");
- the run: an agent that finds such a need does not attempt the change and writes one line,
  `- PIPELINE FOLLOW-UP [<file>]: <what must change and why>`, into its notes (the
  implementer's step note -> steps.md, the reviewer's review.md), like an assumption;
- the final gate: the generated part of the final brief, final_facts.md and final_summary.md
  list every item as a separate step to do after the feature. It is no gate item: nothing to
  answer, nothing that blocks, and the summary agent cannot leave it out.

After DONE the list is kept in `{specs}/<f>/pipeline_followup.md` (written with the metrics row;
`framegate cleanup` keeps the spec folder) and in a `pipeline_followup` event.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

from framegate.config import Config

SECTION = "Pipeline follow-up"
#: the generated list of the run ({out}/<f>/) and the durable copy ({specs}/<f>/)
FILE = "pipeline_followup.md"
#: the run files whose lines are read, with who wrote them
RUN_FILES = (("steps.md", "implementer"), ("review.md", "reviewer"))
LINE_RE = re.compile(
    r"^\s*[-*]\s*PIPELINE(?:[ _-]*FOLLOW[ _-]?UP)?\s*\[([^\]]*)\]\s*:?\s*(.+?)\s*$", re.I
)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_NOTHING = re.compile(r"^\W*(none|n/?a|nothing)\b[\W]*$", re.I)
_PLACEHOLDER = re.compile(r"^<.*>$")
HEADING = "Pipeline follow-up (a separate step after this feature; not part of it)"


@dataclass(frozen=True)
class Item:
    text: str
    #: "spec" | "implementer" | "reviewer"
    source: str

    def line(self) -> str:
        return f"{self.text} ({'from the spec' if self.source == 'spec' else self.source})"


def _section(text: str) -> str:
    m = re.search(rf"^##\s+{re.escape(SECTION)}\b.*$", text, re.M | re.I)
    if not m:
        return ""
    rest = text[m.end() :]
    end = re.search(r"^##\s", rest, re.M)
    return rest[: end.start()] if end else rest


def parse_spec(text: str) -> list[str]:
    """The items of a requirements.md's `## Pipeline follow-up`: its bullets, or (no bullet)
    its text lines; [] when the section is missing, empty or "None."."""
    body = [x.strip() for x in _section(_COMMENT.sub("", text)).splitlines() if x.strip()]
    bullets = [m.group(1) for x in body if (m := _BULLET.match(x))]
    found = bullets or body
    return [
        x
        for x in found
        if re.search(r"\w", x) and not _NOTHING.match(x) and not _PLACEHOLDER.match(x)
    ]


def parse_run(text: str) -> list[str]:
    """`- PIPELINE FOLLOW-UP [<file>]: <what, why>` lines of a run file -> "<file>: <what>"."""
    out = []
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if m:
            where = m.group(1).strip().strip("`")
            out.append(
                f"{where}: {m.group(2)}" if where and where.lower() != "none" else m.group(2)
            )
    return out


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def items(cfg: Config, feature: str) -> list[Item]:
    """Every pipeline follow-up of the feature: the spec's first, then the run's, each once."""
    found = [Item(x, "spec") for x in parse_spec(_read(cfg.spec_dir(feature) / "requirements.md"))]
    for name, who in RUN_FILES:
        found += [Item(x, who) for x in parse_run(_read(cfg.feature_out(feature) / name))]
    seen: set[str] = set()
    out = []
    for it in found:
        key = " ".join(it.text.lower().split())
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def brief_lines(found: list[Item]) -> list[str]:
    """The block of the final brief ([] when there is nothing to do)."""
    if not found:
        return []
    return [
        f"{HEADING}: {len(found)}; nothing to answer",
        *(f"  - {it.line()}" for it in found),
    ]


def markdown(feature: str, found: list[Item]) -> str:
    """The list as a file: {out}/<f>/pipeline_followup.md and its durable copy."""
    return (
        "\n".join(
            [
                f"# Pipeline follow-up: {feature}",
                "",
                "Deploy / CI changes this feature makes necessary. The feature did not make them "
                "(those files are locked for it): do each one as a separate step.",
                "",
                *(f"- [ ] {it.line()}" for it in found),
            ]
        )
        + "\n"
    )


def section_lines(found: list[Item]) -> list[str]:
    """The generated section of final_facts.md / final_summary.md ([] when empty)."""
    if not found:
        return []
    return [
        f"## {HEADING}: {len(found)}",
        "",
        *(f"- {it.line()}" for it in found),
    ]


def write(cfg: Config, feature: str, durable: bool = False) -> list[Item]:
    """Write the list to {out}/<f>/ (or, durable, to {specs}/<f>/); nothing listed: no file,
    and a stale one is removed."""
    found = items(cfg, feature)
    folder = cfg.spec_dir(feature) if durable else cfg.feature_out(feature)
    path = folder / FILE
    if found and folder.is_dir():
        path.write_text(markdown(feature, found), encoding="utf-8")
    elif not found:
        path.unlink(missing_ok=True)
    return found
