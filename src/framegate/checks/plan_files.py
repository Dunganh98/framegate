"""The files a plan step names, checked against what the implementer may write.

Seen live (a replay pilot, 2 of 3 runs): a plan step said "update docs/xyz.md". The
implementer may write the source folders only, the write guard blocked the edit and the step
parked, in one run twice. Nobody could have fixed that during the run: the plan is frozen.

So the plan is checked when it is made (`framegate spec-check`, `approve-spec`):

- A documentation file (is_doc_file: `.md` / `.rst` under a `docs` folder, a README or a
  CHANGELOG, and nothing the frame protects) that a step names becomes writable for the
  implementer while it runs that step. The list is read from plan.md as it was approved (the
  `spec-approved/<feature>` marker), so nothing becomes writable later in the run. The
  orchestrator hands the step's list to the agent's hooks through the environment (DOCS_ENV),
  which the agent cannot change; the write guard checks each file against the rule again.
- Every other file a step names that the implementer cannot write is a finding: it is moved
  out of the step before the approval.

The frame check needs no rule of its own: a file it protects is never a documentation file
here, so it fails on a change to anything else exactly as before.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping

from framegate.adapters.base import LanguageAdapter
from framegate.checks.frame_diff import (
    is_protected,
    is_setup_file,
    maybe_pipeline_file,
    protected_paths,
)
from framegate.config import Config, is_machine_file
from framegate.hooks import DOCS_ENV
from framegate.state import GitError, git

DOC_SUFFIXES = (".md", ".rst", ".txt")
DOC_FOLDERS = ("docs", "doc")
DOC_NAMES = ("readme", "changelog")
#: memory files of coding agents: instructions, not documentation, wherever they lie
AGENT_FILES = ("claude.md", "agents.md")
#: the optional last section of plan.md: files of this repository that no agent may write
BY_HAND_TITLE = "To do by hand"

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_URL = re.compile(r"\w+://\S+")
_HEADING = re.compile(r"^##\s+(.*)$", re.M)
_STEP = re.compile(r"Step\s+(\d+):", re.I)
_PATH = re.compile(r"[\w./\\-]*\w\.[A-Za-z]\w*")
#: right after a file name: the step reads it and does not change it
_READ_ONLY = re.compile(r"^[`'\"*_\s]*\(read[ -]only\b", re.I)
#: a README / CHANGELOG named without its file name
_BARE_DOC = re.compile(r"(?<![\w./\\-])(README|CHANGELOG)(?![\w/\\-]|\.\w)")


def step_bodies(text: str) -> dict[int, str]:
    """The text of each `## Step N: title` section of plan.md (title included), up to the
    next `## ` heading, without HTML comments."""
    text = _COMMENT.sub("", text)
    marks = list(_HEADING.finditer(text))
    out: dict[int, str] = {}
    for i, m in enumerate(marks):
        step = _STEP.match(m.group(1))
        if step:
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            out[int(step.group(1))] = text[m.start() : end]
    return out


def named_files(body: str) -> list[str]:
    """The path-like words of a step (posix, relative), in order, each once. Left out: a web
    address, a path that leaves the repository, and a name followed by "(read only)"."""
    body = _URL.sub(" ", body)
    found: list[str] = []
    for m in _PATH.finditer(body):
        if _READ_ONLY.match(body[m.end() :]):
            continue
        name = m.group(0).replace("\\", "/")
        while name.startswith("./"):
            name = name[2:]
        if not name or name.startswith("/") or ".." in name.split("/"):
            continue
        if name not in found:
            found.append(name)
    return found


def _under(rel: str, folder: str) -> bool:
    folder = folder.strip("/")
    return bool(folder) and (rel == folder or rel.startswith(folder + "/"))


def is_doc_name(rel: str) -> bool:
    """A documentation file by its name alone: a README / CHANGELOG (`.md`, `.rst`, `.txt`),
    or a `.md` / `.rst` file under a folder called `docs` or `doc`. No other `.txt` there:
    `docs/requirements.txt` is a dependency list."""
    parts = rel.lower().split("/")
    stem, dot, suffix = parts[-1].rpartition(".")
    if not dot or "." + suffix not in DOC_SUFFIXES or parts[-1] in AGENT_FILES:
        return False
    if stem in DOC_NAMES:
        return True
    return suffix != "txt" and any(p in DOC_FOLDERS for p in parts[:-1])


def is_doc_file(adapter: LanguageAdapter, rel: str) -> bool:
    """A documentation file a plan step may hand to the implementer. The name rule
    (is_doc_name), and none of: a dot folder (`.claude/`, `.github/`, `.framegate/`), the
    tests, the specs, the run and metrics folders, the code map and the knowledge file (other
    steps write those), a machine file, a setup file, a deploy / CI file, and every path the
    frame check protects (`[protected] extra` / `paths` included). When in doubt it is not
    one: the step then fails the spec check instead of widening the write guard."""
    cfg = adapter.cfg
    if not is_doc_name(rel) or any(p.startswith(".") for p in rel.split("/")):
        return False
    p = cfg.paths
    own = [p.tests, p.review_tests, p.specs, p.out, p.metrics, cfg.roles_extra_dir or ""]
    if any(_under(rel, x) for x in own) or rel in (cfg.code_map, cfg.knowledge):
        return False
    if is_machine_file(cfg, rel) or maybe_pipeline_file(cfg, rel) or is_setup_file(adapter, rel):
        return False
    return not is_protected(adapter, rel, protected_paths(adapter))


def implementer_writes(cfg: Config, rel: str) -> bool:
    """`rel` is in a path the implementer may write anyway (the source folders, the run
    folder)."""
    return any(rel == a.rstrip("/") or rel.startswith(a) for a in cfg.write_paths("IMPLEMENT"))


def _is_file_name(cfg: Config, rel: str) -> bool:
    """A word that surely names a file of this repository: it exists, it lies in a top folder
    that exists, it has a documentation or a deploy / CI name. `e.g`, `1.2` and
    `Parser.parse` are not."""
    if (cfg.root / rel).is_file() or is_doc_name(rel) or maybe_pipeline_file(cfg, rel):
        return True
    top, sep, _ = rel.partition("/")
    return bool(sep) and (cfg.root / top).is_dir()


def _advice(adapter: LanguageAdapter, rel: str) -> str:
    cfg = adapter.cfg
    if _under(rel, cfg.paths.tests) or _under(rel, cfg.paths.review_tests):
        return "the test writer writes the tests from the spec IDs, no plan step does"
    if maybe_pipeline_file(cfg, rel) and not is_setup_file(adapter, rel):
        return 'a deploy / CI change goes under "Pipeline follow-up" in requirements.md'
    if is_setup_file(adapter, rel):
        return "change a setup file now, in the spec phase (the approval commits it)"
    return (
        f'what the user must do there is one line under "## {BY_HAND_TITLE}" at the end of plan.md'
    )


def classify(adapter: LanguageAdapter, text: str) -> tuple[dict[int, list[str]], list[str]]:
    """plan.md `text` -> (per step: the documentation files it makes writable; the findings:
    files a step names that the implementer cannot write)."""
    cfg = adapter.cfg
    docs: dict[int, list[str]] = {}
    problems: list[str] = []
    allowed = ", ".join(cfg.write_paths("IMPLEMENT"))
    for num, body in sorted(step_bodies(text).items()):
        for rel in named_files(body):
            if implementer_writes(cfg, rel) and not maybe_pipeline_file(cfg, rel):
                continue
            if is_doc_file(adapter, rel):
                docs.setdefault(num, []).append(rel)
            elif _is_file_name(cfg, rel) and not _under(rel, cfg.paths.specs):
                problems.append(
                    f"plan.md, Step {num}: names {rel}, which the implementer cannot write "
                    f"(it writes {allowed} and a step's documentation files: .md / .rst "
                    "under a docs folder, README, CHANGELOG). Take it out of the step: "
                    f"{_advice(adapter, rel)}. A file the step only reads: write "
                    '"(read only)" after its name'
                )
        plain = _URL.sub(" ", body)
        for m in _BARE_DOC.finditer(plain):
            if _READ_ONLY.match(plain[m.end() :]):
                continue
            problems.append(
                f"plan.md, Step {num}: names {m.group(1)} without its file name. Write the "
                f"path (e.g. {m.group(1)}.md) so the step may write it, or take it out of "
                "the step"
            )
    return docs, list(dict.fromkeys(problems))


def _plan_text(cfg: Config, feature: str) -> str:
    path = cfg.spec_dir(feature) / "plan.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def problems(adapter: LanguageAdapter, feature: str) -> list[str]:
    """The spec check's findings about plan.md as it is now."""
    return classify(adapter, _plan_text(adapter.cfg, feature))[1]


def planned_docs(adapter: LanguageAdapter, feature: str) -> dict[int, list[str]]:
    """Per step, the documentation files plan.md (as it is now) makes writable: for
    `spec-check`, so the user approves them with the spec."""
    return classify(adapter, _plan_text(adapter.cfg, feature))[0]


def docs_line(docs: Mapping[int, list[str]]) -> str:
    """One line for `spec-check`: what the approval makes writable, per step."""
    listed = "; ".join(f"step {n}: {', '.join(files)}" for n, files in sorted(docs.items()))
    return "Documentation files the plan makes writable (frozen with the spec): " + (
        listed or "none"
    )


def approved_docs(adapter: LanguageAdapter, feature: str, steps: list[int]) -> list[str]:
    """The documentation files the approved plan names for these steps. Read from plan.md at
    the `spec-approved/<feature>` marker, not from the working tree: a later spec update or
    a stray edit of plan.md makes nothing new writable. No marker, no list."""
    cfg = adapter.cfg
    try:
        text = git(cfg, "show", f"spec-approved/{feature}:{cfg.paths.specs}/{feature}/plan.md")
    except GitError:
        return []
    docs = classify(adapter, text)[0]
    return list(dict.fromkeys(f for n in steps for f in docs.get(n, [])))


def env_docs(env: Mapping[str, str] | None = None) -> list[str]:
    """The list the orchestrator put into the agent's environment ([] = none, or unreadable)."""
    raw = (os.environ if env is None else env).get(DOCS_ENV, "")
    try:
        data = json.loads(raw) if raw.strip() else []
    except ValueError:
        return []
    return [x for x in data if isinstance(x, str)] if isinstance(data, list) else []


def step_may_write(adapter: LanguageAdapter, rel: str) -> bool:
    """The write guard's question in the IMPLEMENT phase: is `rel` one of this step's
    documentation files (the environment's list), and still one by the rule."""
    listed = {d.lower() for d in env_docs()}
    return rel.lower() in listed and is_doc_file(adapter, rel)


def task_note(docs: list[str]) -> str:
    """Task lines for the implementer: the documentation files of its step(s)."""
    if not docs:
        return ""
    return (
        "\n\nDocumentation files of this step (the approved plan names them; you may write "
        f"these, and no other file outside the source folders): {', '.join(docs)}."
    )
