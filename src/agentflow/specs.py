"""Spec parsing: IDs in specs/<feature>/*.md and the steps of plan.md."""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

from agentflow.config import Config

#: what a task is; decides the first proof of its tests (checks/baseline.py)
MODES = ("feature", "bugfix", "refactor")
MODE_RE = re.compile(r"^Mode:\s*([\w-]+)", re.M | re.I)
STEP_RE = re.compile(r"^##\s+Step\s+(\d+):\s*(.+)$", re.M)
COVERS_RE = re.compile(r"^Covers:\s*(.+)$", re.M)
#: optional "Depends on: Step 1, 2" line: a later step that needs a parked step is noted
DEPENDS_RE = re.compile(r"^Depends on:\s*(.+)$", re.M | re.I)


@dataclass
class PlanStep:
    num: int
    title: str
    reqs: list[str] = field(default_factory=list)
    depends: list[int] = field(default_factory=list)


def _ids_in(folder: pathlib.Path, id_re: re.Pattern[str]) -> set[str]:
    ids: set[str] = set()
    if not folder.is_dir():
        return ids
    for p in folder.rglob("*.md"):
        if not any(part.startswith("_template") for part in p.parts):
            ids |= set(id_re.findall(p.read_text(encoding="utf-8")))
    return ids


def feature_ids(cfg: Config, feature: str) -> set[str]:
    return _ids_in(cfg.spec_dir(feature), re.compile(cfg.id_pattern))


def spec_mode_line(cfg: Config, feature: str) -> str:
    """The value of the `Mode:` line of requirements.md, lower case ("" = no such line)."""
    path = cfg.spec_dir(feature) / "requirements.md"
    m = MODE_RE.search(path.read_text(encoding="utf-8")) if path.is_file() else None
    return m.group(1).lower() if m else ""


def feature_mode(cfg: Config, feature: str) -> str:
    """feature | bugfix | refactor: the `Mode:` line of requirements.md (written by
    `agentflow spec --mode`); no line, or an unknown word: feature."""
    mode = spec_mode_line(cfg, feature)
    return mode if mode in MODES else "feature"


def template_dir(mode: str) -> str:
    """The spec template folder of a mode, under [paths] specs or in the package."""
    return "_template" if mode == "feature" else f"_template_{mode}"


def all_spec_ids(cfg: Config) -> set[str]:
    return _ids_in(cfg.root / cfg.paths.specs, re.compile(cfg.id_pattern))


def parse_plan(text: str) -> list[PlanStep]:
    """Parse '## Step N: title' sections with a 'Covers: ID, ID' line each and an optional
    'Depends on: Step 1, 2' line."""
    parts = STEP_RE.split(text)
    steps = []
    for i in range(1, len(parts), 3):
        body = parts[i + 2]
        m = COVERS_RE.search(body)
        ids = [x.strip() for x in m.group(1).split(",") if x.strip()] if m else []
        d = DEPENDS_RE.search(body)
        deps = [int(x) for x in re.findall(r"\d+", d.group(1))] if d else []
        steps.append(PlanStep(int(parts[i]), parts[i + 1].strip(), ids, deps))
    return steps


def plan_steps(cfg: Config, feature: str) -> list[PlanStep]:
    path = cfg.spec_dir(feature) / "plan.md"
    if not path.is_file():
        return []
    return parse_plan(path.read_text(encoding="utf-8"))
