"""The files a plan step names. A step may mention any file; an existing code file outside
the source and test folders is refused at the spec check (outside_problems). No step makes a
documentation file writable: docs are proposed, never edited (doc_proposals.py)."""

from __future__ import annotations

import re

from framegate.adapters.base import LanguageAdapter

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
