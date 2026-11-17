"""The documentation files a plan step names: writable for the implementer in that step.

A plan step may mention any file. A Markdown documentation file it names (is_doc_file: a
README or CHANGELOG, or a `.md` file under a `docs` folder, and nothing the frame protects)
becomes writable for the implementer while it runs that step. Every other file outside the
source folders stays read-only.

The list is read from plan.md as it was approved (the `spec-approved/<feature>` marker), so
nothing becomes writable later in the run. The orchestrator hands it to the agent's hooks
through the environment (DOCS_ENV); the write guard checks each file against the rule again.
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
from framegate.config import is_machine_file
from framegate.hooks import DOCS_ENV
from framegate.state import GitError, git

DOC_FOLDERS = ("docs", "doc")
DOC_NAMES = ("readme", "changelog")
#: memory files of coding agents: instructions, not documentation, wherever they lie
AGENT_FILES = ("claude.md", "agents.md")

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_URL = re.compile(r"\w+://\S+")
_HEADING = re.compile(r"^##\s+(.*)$", re.M)
_STEP = re.compile(r"Step\s+(\d+):", re.I)
_PATH = re.compile(r"[\w./\\-]*\w\.[A-Za-z]\w*")


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
    address and a path that leaves the repository."""
    body = _URL.sub(" ", body)
    found: list[str] = []
    for m in _PATH.finditer(body):
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
    """A documentation file by its name alone: README.md, CHANGELOG.md, or a `.md` file
    under a folder called `docs` or `doc`."""
    parts = rel.lower().split("/")
    if not parts[-1].endswith(".md") or parts[-1] in AGENT_FILES:
        return False
    return parts[-1][:-3] in DOC_NAMES or any(p in DOC_FOLDERS for p in parts[:-1])


def is_doc_file(adapter: LanguageAdapter, rel: str) -> bool:
    """A documentation file a plan step may hand to the implementer. The name rule
    (is_doc_name), and none of: a dot folder (`.claude/`, `.github/`, `.framegate/`), the
    tests, the specs, the run and metrics folders, the code map and the knowledge file (other
    steps write those), a machine file, a setup file, a deploy / CI file, and every path the
    frame check protects (`[protected] extra` / `paths` included)."""
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


def step_docs(adapter: LanguageAdapter, text: str) -> dict[int, list[str]]:
    """plan.md `text` -> per step, the documentation files it names."""
    docs: dict[int, list[str]] = {}
    for num, body in sorted(step_bodies(text).items()):
        found = [rel for rel in named_files(body) if is_doc_file(adapter, rel)]
        if found:
            docs[num] = found
    return docs


def outside_problems(adapter: LanguageAdapter, text: str) -> list[str]:
    """A plan step that names an existing code file outside the source and test folders: the
    implementer could not write it (seen live: the step was parked, the folder added later,
    and the spec approved again by hand)."""
    cfg = adapter.cfg
    suffixes = tuple(adapter.source_suffixes)
    test_dirs = [cfg.paths.tests, cfg.paths.review_tests]
    out = []
    for num, body in sorted(step_bodies(text).items()):
        for rel in named_files(body):
            if not rel.endswith(suffixes) or not (cfg.root / rel).is_file():
                continue
            if cfg.in_src(rel) or any(_under(rel, t) for t in test_dirs):
                continue
            folder = rel.rsplit("/", 1)[0] if "/" in rel else "."
            out.append(
                f"plan.md step {num} changes {rel}, which is outside the source folders "
                f"([paths] src: {', '.join(cfg.src_roots)}). Add {folder} to [paths] src "
                "(/framegate:setup) before the spec is approved, or change the plan"
            )
    return out


def docs_line(adapter: LanguageAdapter, feature: str) -> str:
    """One line for `spec-check`: what the approval of plan.md (as it is now) makes
    writable, per step."""
    path = adapter.cfg.spec_dir(feature) / "plan.md"
    docs = step_docs(adapter, path.read_text(encoding="utf-8") if path.is_file() else "")
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
    docs = step_docs(adapter, text)
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
