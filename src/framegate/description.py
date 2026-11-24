"""The pull request's description: written by framegate itself (no agent), for a reviewer who
has not seen the run.

`final_summary.md` is the run's internal report: long, and gone with the cleanup. The
description is a text of its own, kept in `{specs}/<f>/pr_description.md` (the cleanup keeps
the spec folder; with `[git] state_branch` it is one of framegate's files and never reaches
the product branch). Its sections, in plain words:

    ## What                                        the task in 2-4 lines
    ## To do by hand outside the repository: N     the unticked checklist (followup.py)
    ## Changes                                     the steps, one line each, how each went
    ## How it is proven                            what the tests check + one line of numbers
    ## Not proven / check by hand
    ## Decisions
    ## Deploy / CI changes                         the done items of the pipeline list
    Built with framegate <version>; N tests

Sources, best first: the final brief and the spec brief of the run ({out}/<f>/), the final
summary's Summary, then the spec (requirements.md, plan.md) and the kept pipeline list. No
cost figure and no path into the out folder ever gets into it.

It always fits Azure DevOps' description limit (hosts.AZURE_DESCRIPTION_MAX): when it is too
long, the least important sections lose their extra lines first, no section is cut in the
middle, and the last line says "(shortened)" once.

Written at DONE (with the metrics row), again by `framegate pipeline-approve` (the deploy / CI
section changes) and by `framegate publish`. When the run files are gone (after the cleanup)
an existing description keeps its text and only the two lists are brought up to date; with no
description either (a task finished by an older framegate) it is built from the spec alone.

A deploy / CI change with a pull request of its own (publish.py) has its own text,
`{specs}/<f>/pipeline_pr_description.md`: the brief the user approved, else the list.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

from framegate import __version__, followup, hosts
from framegate.adapters.base import NOT_MEASURED
from framegate.config import Config, ConfigError
from framegate.report import since_spec_approval
from framegate.specs import feature_ids, feature_mode, plan_steps
from framegate.state import read_events

FILE = "pr_description.md"
PIPELINE_FILE = "pipeline_pr_description.md"
LIMIT = hosts.AZURE_DESCRIPTION_MAX
#: the run files a full description is built from ({out}/<f>/)
SPEC_BRIEF = "spec_brief.md"
FINAL_BRIEF = "final_brief.md"
SUMMARY = "final_summary.md"
PIPELINE_BRIEF = "pipeline_brief.md"
FOOTER = "Built with framegate"
SHORTENED = "(shortened)"
OUTSIDE_TITLE = "To do by hand outside the repository"
DEPLOY_TITLE = "Deploy / CI changes"
CHECKS = "Checks:"
#: said once by `publish` when the description had to be built from the spec alone
SPEC_ONLY_NOTE = (
    "The description is short: the run files of {feature} are gone (cleaned up before it "
    "was written), so it is built from the spec."
)
LINE_MAX = 300
#: (most lines of a section, most characters of a line), one step shorter each
LEVELS = ((None, LINE_MAX), (8, 240), (5, 180), (3, 130), (2, 90))
#: the sections in the order they are written: key -> title
TITLES = {
    "what": "What",
    "outside": OUTSIDE_TITLE,
    "changes": "Changes",
    "proven": "How it is proven",
    "not_proven": "Not proven / check by hand",
    "decisions": "Decisions",
    "deploy": DEPLOY_TITLE,
}
#: shortened in this order, the least important first; the last three lose no line
SHORTEN_ORDER = ("decisions", "not_proven", "deploy", "proven", "what", "changes", "outside")
KEEP_LINES = ("what", "changes", "outside")
#: dropped whole, in this order, when shorter lines are not enough
DROP_ORDER = ("decisions", "not_proven", "proven")

_HEADING = re.compile(r"^#{1,4}\s+(.+?)\s*$")
_BOLD = re.compile(r"^\s*\*\*(.+?)\*\*\s*(?:[—–:-]+\s*)?(.*)$")
_NUMBER = re.compile(r"^\d+[.)]\s*")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TAG = re.compile(r"^\[\d+\]\s*")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SOURCE = re.compile(r"\s*\((from the spec|implementer|reviewer|triage)\)\s*$")
_NOTHING = re.compile(r"^\W*(none|n/?a|nothing)\b\W*$", re.I)
#: the end of a requirements.md title ("Demo — Requirements"): the kind of document
_DOC_KIND = re.compile(r"\s+[—–-]+\s+(Requirements|Bug report|Refactor brief)\s*$", re.I)


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)
    #: lines that are never dropped (the numbers line)
    tail: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Written:
    #: the file, repo-relative
    rel: str
    #: its text differs from what was there
    changed: bool
    #: one line for the user ("" = nothing to say)
    note: str = ""


def rel(cfg: Config, feature: str, pipeline: bool = False) -> str:
    return f"{cfg.paths.specs}/{feature}/{PIPELINE_FILE if pipeline else FILE}"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _clip(text: str, n: int = LINE_MAX) -> str:
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


# ---------- reading the sources ----------
def parts(text: str) -> dict[str, list[str]]:
    """A brief or a summary -> {section title in lower case, without its number: its
    non-empty lines}. A section starts at a `## Title` line or at a `**Title**` line (the
    spec brief's form; text after the title on the same line is its first line)."""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in _COMMENT.sub("", text).splitlines():
        head, bold = _HEADING.match(line), _BOLD.match(line)
        if head or bold:
            title = head.group(1) if head else bold.group(1) if bold else ""
            key = _NUMBER.sub("", title.strip().strip("*:").strip()).lower()
            current = out.setdefault(key, [])
            rest = bold.group(2).strip() if bold and not head else ""
            if rest:
                current.append(rest)
        elif current is not None and line.strip():
            current.append(line.rstrip())
    return out


def _find(sections: dict[str, list[str]], *names: str) -> list[str]:
    for name in names:
        for key, lines in sections.items():
            if key.startswith(name):
                return lines
    return []


class _Cleaner:
    """A source line as the description shows it: one line, no path into the out folder."""

    def __init__(self, cfg: Config) -> None:
        out = re.escape(cfg.paths.out.strip("/"))
        self.bracket = re.compile(rf"\s*\([^()]*(?<![\w./-]){out}/[^()]*\)")
        self.path = re.compile(rf"`?(?<![\w./-]){out}/[\w./-]+`?")

    def __call__(self, line: str) -> str:
        line = self.bracket.sub("", " ".join(line.split()))
        return self.path.sub("the run files", line).strip()


def _bullets(lines: list[str], clean: _Cleaner, numbered: bool = False) -> list[str]:
    """Top-level lines as a list (`- x`, or `N. x` kept as written); indented lines join the
    one above; "None." and empty lines are left out."""
    out: list[str] = []
    for raw in lines:
        text = clean(raw)
        if not text or _NOTHING.match(_BULLET.sub("", text)):
            continue
        if raw[:1].isspace() and out and not _BULLET.match(raw):
            out[-1] = f"{out[-1]} {text}"
            continue
        number = re.match(r"^\s*(\d+)[.)]\s+", raw)
        body = _TAG.sub("", _BULLET.sub("", text))
        out.append(f"{number.group(1)}. {body}" if numbered and number else f"- {body}")
    return out


#: most characters of the task's first paragraph when it comes from the spec itself
WHAT_MAX = 700


def _paragraphs(lines: list[str]) -> list[str]:
    """Wrapped lines of a spec section -> its paragraphs (a bullet starts a new one)."""
    out: list[str] = []
    for raw in lines:
        if _BULLET.match(raw) or not out:
            out.append(_BULLET.sub("", raw).strip())
        else:
            out[-1] = f"{out[-1]} {raw.strip()}"
    return out


def _sentences(text: str, limit: int = WHAT_MAX) -> str:
    """The text, ended at a sentence when it is longer than `limit`."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind(".) "))
    return cut[: end + 1] if end > limit // 4 else _clip(text, limit)


def _what(cfg: Config, feature: str, spec_brief: dict[str, list[str]], summary: str) -> list[str]:
    clean = _Cleaner(cfg)
    mode = feature_mode(cfg, feature)
    pairs = {
        "bugfix": (("bug", "Bug"), ("right result", "Right result")),
        "refactor": (("goal", "Goal"), ("what moves", "What moves")),
    }.get(mode)
    if pairs:
        found = []
        for name, label in pairs:
            text = " ".join(_BULLET.sub("", clean(x)) for x in _find(spec_brief, name))
            if text.strip():
                found.append(f"**{label}:** {text.strip()}")
        if found:
            return found
    lines = _find(spec_brief, "task") or _find(parts(summary), "summary")
    if lines:
        return [_BULLET.sub("", clean(x)) for x in lines][:5]
    # the spec alone: the mode's own sections of requirements.md
    req = _read(cfg.spec_dir(feature) / "requirements.md")
    spec = parts(req)
    names = {"bugfix": ("symptom",), "refactor": ("goal", "scope")}.get(mode, ("context", "goals"))
    found = []
    for name in names:
        kept = [x for x in _find(spec, name) if not x.lstrip().startswith(("|", "<"))]
        for para in _paragraphs([clean(x) for x in kept]):
            if para and not _NOTHING.match(para):
                found.append(_sentences(para))
    if not found:
        title = re.search(r"^#\s+(.+?)\s*$", _COMMENT.sub("", req), re.M)
        found = [_DOC_KIND.sub("", title.group(1)) if title else feature]
    return found[:4]


def _changes(
    cfg: Config, feature: str, final: dict[str, list[str]], spec_brief: dict[str, list[str]]
) -> list[str]:
    clean = _Cleaner(cfg)
    lines = _find(final, "steps", "plan followed", "plan") or _find(spec_brief, "plan")
    found = _bullets(lines, clean, numbered=True)
    return found or [f"{s.num}. {s.title}" for s in plan_steps(cfg, feature)]


def final_scores(last: dict[str, Any], fix: bool = True) -> list[str]:
    """The coverage and mutation results of a `final_gates` event, one text each (the
    regression run and skipped checks left out): the final brief and the description.
    A check whose tool cannot run on the machine is listed as NOT MEASURED with the reason,
    so no reader takes the silence for a pass. `fix`: also what to do about it (the brief;
    the description's reader is not at that machine)."""
    found = []
    for name, res in (last.get("results") or {}).items():
        text = " ".join(str(res).split())
        if not ("coverage" in name or "mutation" in name) or "regression" in name:
            continue
        if text.startswith(NOT_MEASURED):
            found.append(f"{name} {_clip(text if fix else text.split('. Fix: ')[0], 400)}")
        elif not text.startswith("SKIPPED"):
            found.append(f"{name} {_clip(text, 90)}")
    return found


def count_tests(cfg: Config, feature: str) -> int | None:
    """How many tests carry one of the feature's spec IDs (None = not countable here)."""
    from framegate.adapters import get_adapter

    try:
        tags = get_adapter(cfg).collect_req_tags()
        ids = feature_ids(cfg, feature)
    except Exception:  # a broken test file or config must not stop a publish
        return None
    return len({t for i in ids for t in tags.get(i, [])}) or None


def _numbers(cfg: Config, feature: str, tests: int | None) -> list[str]:
    try:
        events = since_spec_approval(read_events(cfg, feature))
    except (OSError, ValueError):
        events = []
    runs = [e for e in events if e.get("kind") == "final_gates"]
    last = runs[-1] if runs else {}
    if not last and not tests:
        return []
    found = []
    if last:
        failed = ", ".join(last.get("failed") or [])
        found.append("all final checks passed" if last.get("passed") else f"FAILED: {failed}")
    if tests:
        found.append(f"{tests} test(s)")
    return [f"{CHECKS} " + "; ".join([*found, *final_scores(last, fix=False)])]


def _deploy(cfg: Config, feature: str) -> tuple[list[str], bool]:
    """(the lines of the deploy / CI section, whether a change was made)."""
    done, other = [], []
    for it in followup.read_list(cfg, feature):
        text = _SOURCE.sub("", it.text)
        if it.outside:
            continue
        if it.not_needed:
            continue  # nothing was changed and nothing is left: no news for a reviewer
        elif it.ticked:
            done.append(f"- {text}")
        else:
            other.append(f"- not done yet, a separate step after this pull request: {text}")
    return [*done, *other], bool(done)


def _deploy_title(done: bool, own: bool) -> str:
    if not done:
        return DEPLOY_TITLE
    where = "a pull request of their own" if own else "the second commit of this pull request"
    return f"{DEPLOY_TITLE} ({where})"


def _outside(cfg: Config, feature: str) -> Section | None:
    todo = followup.pr_checklist(cfg, feature)
    if not todo:
        return None
    return Section(f"{OUTSIDE_TITLE}: {len(todo)}", [f"- [ ] {x}" for x in todo])


def _lists(cfg: Config, feature: str, own: bool) -> dict[str, Section | None]:
    """The two sections that change after DONE: read from the kept list each time."""
    lines, done = _deploy(cfg, feature)
    deploy = Section(_deploy_title(done, own), lines) if lines else None
    return {"outside": _outside(cfg, feature), "deploy": deploy}


def has_run_files(cfg: Config, feature: str) -> bool:
    out = cfg.feature_out(feature)
    return any((out / name).is_file() for name in (SPEC_BRIEF, FINAL_BRIEF, SUMMARY))


def _from_sources(cfg: Config, feature: str, own: bool) -> tuple[dict[str, Section], str]:
    out = cfg.feature_out(feature)
    clean = _Cleaner(cfg)
    spec_brief = parts(_read(out / SPEC_BRIEF))
    final = parts(_read(out / FINAL_BRIEF))
    tests = count_tests(cfg, feature)
    found: dict[str, Section | None] = {
        "what": Section(TITLES["what"], _what(cfg, feature, spec_brief, _read(out / SUMMARY))),
        "changes": Section(TITLES["changes"], _changes(cfg, feature, final, spec_brief)),
        "proven": Section(
            TITLES["proven"],
            _bullets(_find(final, "proven") or _find(spec_brief, "how it is proven"), clean),
            _numbers(cfg, feature, tests),
        ),
        "not_proven": Section(
            TITLES["not_proven"],
            _bullets(_find(final, "not proven") or _find(spec_brief, "not proven"), clean),
        ),
        "decisions": Section(
            TITLES["decisions"],
            _bullets(_find(final, "decisions") or _find(spec_brief, "decisions"), clean),
        ),
        **_lists(cfg, feature, own),
    }
    footer = f"{FOOTER} {__version__}" + (f"; {tests} tests" if tests else "")
    return {k: s for k, s in found.items() if s and (s.lines or s.tail)}, footer


def _from_text(text: str) -> tuple[dict[str, Section], str]:
    """A description written earlier, back into its sections and its last line."""
    found: dict[str, Section] = {}
    footer, current = "", None
    for line in text.splitlines():
        head = re.match(r"^## (.+?)\s*$", line)
        if head:
            title = head.group(1)
            key = next((k for k, t in TITLES.items() if title.startswith(t)), title.lower())
            current = found.setdefault(key, Section(title))
        elif line.startswith(FOOTER):
            footer = line.strip()  # with its "(shortened)": what was dropped stays dropped
        elif current is not None and line.strip():
            (current.tail if line.startswith(CHECKS) else current.lines).append(line)
    return found, footer or f"{FOOTER} {__version__}"


# ---------- writing ----------
def _render(sections: dict[str, Section], footer: str, levels: dict[str, int]) -> str:
    out: list[str] = []
    order = [*(k for k in TITLES if k in sections), *(k for k in sections if k not in TITLES)]
    for key in order:
        section = sections[key]
        most, width = LEVELS[levels.get(key, 0)]
        lines = section.lines if most is None or key in KEEP_LINES else section.lines[:most]
        if key == "what":  # paragraphs: room for whole sentences
            width = max(width, WHAT_MAX if most is None else width * 2)
        shown = [_clip(x, width) for x in [*lines, *section.tail]]
        if key == "what":  # plain lines, not a list: a paragraph each
            shown = [y for x in shown for y in (x, "")][:-1]
        out += [f"## {section.title}", "", *shown, ""]
    return "\n".join([*out, footer]) + "\n"


def fit(sections: dict[str, Section], footer: str, limit: int = LIMIT) -> str:
    """The description, at most `limit` characters: the whole text when it fits; else the
    sections get shorter one step at a time, the least important first (fewer lines, shorter
    lines; the steps and the checklist keep every line); then whole sections go. Never cut
    in the middle of a section. A shortened text says so once, in its last line."""
    sections = dict(sections)
    levels: dict[str, int] = {}
    full = _render(sections, footer, levels)
    if len(full) <= limit:
        return full
    short = footer if footer.endswith(SHORTENED) else f"{footer} {SHORTENED}"
    for level in range(1, len(LEVELS)):
        for key in (*SHORTEN_ORDER, *(k for k in sections if k not in TITLES)):
            if key in sections:
                levels[key] = level
                if len(_render(sections, short, levels)) <= limit:
                    return _render(sections, short, levels)
    for key in (*(k for k in sections if k not in TITLES), *DROP_ORDER, "deploy", "changes"):
        if sections.pop(key, None) and len(_render(sections, short, levels)) <= limit:
            break
    text = _render(sections, short, levels)
    return text if len(text) <= limit else _cut(text[: -len(short) - 1], short, limit)


def _cut(text: str, last: str, limit: int) -> str:
    """The last resort (a checklist alone longer than the limit): whole lines, then `last`."""
    head = text[: limit - len(last) - 2]
    end = head.rfind("\n")
    return (head[:end] if end > 0 else head).rstrip() + "\n\n" + last + "\n"


def compose(cfg: Config, feature: str, *, own: bool = False) -> tuple[str, str]:
    """(the description of the task's pull request, one line for the user or ""). `own`: the
    deploy / CI change goes out as a pull request of its own (the product is merged)."""
    old = _read(cfg.root / rel(cfg, feature))
    if has_run_files(cfg, feature) or not old.strip():
        sections, footer = _from_sources(cfg, feature, own)
        gone = not has_run_files(cfg, feature)
        return fit(sections, footer), SPEC_ONLY_NOTE.format(feature=feature) if gone else ""
    # the run files are gone: the text stays, the two lists are read again
    sections, footer = _from_text(old)
    for key, section in _lists(cfg, feature, own).items():
        if section:
            sections[key] = section
        else:
            sections.pop(key, None)
    return fit(sections, footer), ""


_OUTSIDE_BLOCK = re.compile(
    rf"^## {re.escape(OUTSIDE_TITLE)}[^\n]*\n\n(?:- \[[ xX]\][^\n]*\n)*\n?", re.M
)
_FOOTER_LINE = re.compile(rf"\n*^{re.escape(FOOTER)}[^\n]*\n?\Z", re.M)


def compose_pipeline(cfg: Config, feature: str) -> str:
    """The description of a deploy / CI change's own pull request: the brief the user
    approved (while it is there), else the text written before, else the list; the
    checklist of what is left to do by hand outside the repository on top."""
    brief = _read(cfg.feature_out(feature) / PIPELINE_BRIEF).strip()
    old = _read(cfg.root / rel(cfg, feature, pipeline=True))
    body = brief or _FOOTER_LINE.sub("", _OUTSIDE_BLOCK.sub("", old)).strip()
    if not body:
        lines, _ = _deploy(cfg, feature)
        body = "\n".join([f"## {DEPLOY_TITLE}", "", *(lines or ["- (the list has no item)"])])
    outside = _outside(cfg, feature)
    top = [f"## {outside.title}", "", *outside.lines, ""] if outside else []
    footer = f"{FOOTER} {__version__}"
    text = "\n".join([*top, body, "", footer]) + "\n"
    if len(text) <= LIMIT:
        return text
    return _cut("\n".join([*top, body]), f"{footer} {SHORTENED}", LIMIT)


def _write(cfg: Config, where: str, text: str, note: str = "") -> Written:
    path = cfg.root / where
    if _read(path) == text:
        return Written(where, False, note)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return Written(where, True, note)


def refresh(cfg: Config, feature: str, *, own: bool = False) -> Written:
    """Write {specs}/<f>/pr_description.md (only when its text changes)."""
    text, note = compose(cfg, feature, own=own)
    return _write(cfg, rel(cfg, feature), text, note)


def refresh_pipeline(cfg: Config, feature: str) -> Written:
    """Write {specs}/<f>/pipeline_pr_description.md (only when its text changes)."""
    return _write(cfg, rel(cfg, feature, pipeline=True), compose_pipeline(cfg, feature))


def safe_refresh(cfg: Config, feature: str) -> Written | None:
    """refresh for the end of a run: a description that cannot be written (a spec file that
    cannot be read, a config without an adapter) never stops the run."""
    try:
        return refresh(cfg, feature)
    except (OSError, ValueError, ConfigError):
        return None
