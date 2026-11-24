"""The escaped-defect log: bugs found after a task was accepted, against the task that
caused them. `{metrics}/escaped_defects.md` (tracked, committed by the command), one row per
defect with a stable id:

    | X3 | open | 2026-11-24 | released-lock | high | user | lock kept after a crash | ... |

- `framegate defect <task> --what TEXT [--found-by ...] [--severity ...] [--fix ...]
  [--file PATH ...] [--id SPEC-ID ...]`: record one (a `--fix` closes it at once).
- `framegate defect --close X3 [--fix <task or commit>]`: the fix is in.
- `framegate defect [--all]`: list the open ones (--all: every one).

`framegate stats` counts them per accepted task; a later task's retro input lists the open
ones on its files or spec IDs (retro_section).
"""

from __future__ import annotations

import datetime
import pathlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from framegate.checks.spec_lint import tables
from framegate.config import Config

FILE = "escaped_defects.md"
SEVERITIES = ("low", "medium", "high")
STATUSES = ("open", "fixed")
HEADER = """\
# Escaped defects

Bugs found after a task was accepted, one row each, against the task that caused it. Written
by `framegate defect`; `framegate stats` counts them. Status: open = not fixed yet; fixed.

| ID | Status | Date | Task | Severity | Found by | What | Files | Spec IDs | Fix |
|---|---|---|---|---|---|---|---|---|---|
"""


class DefectError(Exception):
    pass


@dataclass
class Defect:
    num: int
    status: str
    date: str
    task: str
    severity: str
    found_by: str
    what: str
    files: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    fix: str = ""

    @property
    def id(self) -> str:
        return f"X{self.num}"

    def line(self) -> str:
        where = f" ({', '.join([*self.files, *self.ids])})" if self.files or self.ids else ""
        by = f", found by {self.found_by}" if self.found_by else ""
        fix = f"; fix: {self.fix}" if self.fix else ""
        return (
            f"{self.id} [{self.status}, {self.severity}] {self.task}: {self.what}{where} "
            f"({self.date}{by}{fix})"
        )


def path(cfg: Config) -> pathlib.Path:
    return cfg.root / cfg.paths.metrics / FILE


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _list(cell: str) -> list[str]:
    return [x.strip() for x in cell.split(",") if x.strip()]


def load(cfg: Config) -> list[Defect]:
    p = path(cfg)
    if not p.is_file():
        return []
    out = []
    for table in tables(p.read_text(encoding="utf-8")):
        if table.column("id") != 0 or table.column("task") is None:
            continue
        for row in table.rows:
            c = [*row, *[""] * 10][:10]
            m = re.match(r"^X(\d+)$", c[0])
            if not m:
                continue
            status = c[1].lower() if c[1].lower() in STATUSES else "open"
            severity = c[4].lower() if c[4].lower() in SEVERITIES else "medium"
            out.append(
                Defect(
                    int(m.group(1)),
                    status,
                    c[2],
                    c[3],
                    severity,
                    c[5],
                    c[6],
                    _list(c[7]),
                    _list(c[8]),
                    c[9],
                )
            )
    return out


def save(cfg: Config, defects: Sequence[Defect]) -> None:
    rows = [
        f"| {d.id} | {d.status} | {d.date} | {_cell(d.task)} | {d.severity} | "
        f"{_cell(d.found_by)} | {_cell(d.what)} | {_cell(', '.join(d.files))} | "
        f"{_cell(', '.join(d.ids))} | {_cell(d.fix)} |"
        for d in sorted(defects, key=lambda d: d.num)
    ]
    p = path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(HEADER + "\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


def accepted_tasks(cfg: Config) -> set[str]:
    """The tasks that passed the final gate: a metrics row, or an approved final gate in
    the events (the run folder may be cleaned up, the metrics row stays)."""
    from framegate.state import read_events

    found: set[str] = set()
    metrics = cfg.root / cfg.paths.metrics / "workflow_metrics.csv"
    if metrics.is_file():
        for line in metrics.read_text(encoding="utf-8").splitlines()[1:]:
            cells = line.split(",")
            if len(cells) > 1 and cells[1].strip():
                found.add(cells[1].strip())
    for e in read_events(cfg):
        if e.get("kind") == "human_gate" and e.get("gate") == "final" and e.get("approved"):
            found.add(str(e.get("feature") or ""))
    return found - {""}


def add(
    cfg: Config,
    task: str,
    what: str,
    found_by: str = "",
    severity: str = "medium",
    fix: str = "",
    files: Sequence[str] = (),
    ids: Sequence[str] = (),
    force: bool = False,
) -> Defect:
    """Record one defect against `task`. A task that was never accepted here is refused
    (`force`: recorded anyway, e.g. a task accepted before framegate kept metrics)."""
    if not what.strip():
        raise DefectError("a defect needs one line of what is wrong (--what)")
    if severity not in SEVERITIES:
        raise DefectError(f"--severity is one of {', '.join(SEVERITIES)}")
    if not force and task not in accepted_tasks(cfg):
        known = ", ".join(sorted(accepted_tasks(cfg))) or "none"
        raise DefectError(
            f"'{task}' is no accepted task of this repository (accepted: {known}). Name the "
            "task that caused the bug, or pass --force to record it anyway."
        )
    defects = load(cfg)
    d = Defect(
        max((x.num for x in defects), default=0) + 1,
        "fixed" if fix.strip() else "open",
        datetime.date.today().isoformat(),
        task,
        severity,
        found_by.strip(),
        " ".join(what.split()),
        [f.replace("\\", "/").strip() for f in files if f.strip()],
        [i.strip() for i in ids if i.strip()],
        fix.strip(),
    )
    save(cfg, [*defects, d])
    return d


def close(cfg: Config, defect_id: str, fix: str = "") -> Defect:
    defects = load(cfg)
    hit = next((d for d in defects if d.id.lower() == defect_id.strip().lower()), None)
    if hit is None:
        raise DefectError(f"no defect {defect_id} in {FILE} (framegate defect --all lists them)")
    hit.status = "fixed"
    hit.fix = fix.strip() or hit.fix
    save(cfg, defects)
    return hit


def per_task(cfg: Config) -> dict[str, list[Defect]]:
    out: dict[str, list[Defect]] = {}
    for d in load(cfg):
        out.setdefault(d.task, []).append(d)
    return out


def touching(cfg: Config, files: Sequence[str], ids: Sequence[str]) -> list[Defect]:
    """The open defects that name one of `files` (the file, or a folder above it) or `ids`."""
    paths = {f.replace("\\", "/") for f in files}
    wanted = set(ids)

    def hits(d: Defect) -> bool:
        on_file = any(
            p == f.rstrip("/") or p.startswith(f.rstrip("/") + "/") for f in d.files for p in paths
        )
        return on_file or bool(wanted & set(d.ids))

    return [d for d in load(cfg) if d.status == "open" and hits(d)]


def retro_section(cfg: Config, feature: str, files: Sequence[str], ids: Sequence[str]) -> str:
    """For retro_input.md: the open escaped defects on this task's files or spec IDs, and
    the ones logged against the task itself ("" = none)."""
    found = {d.num: d for d in touching(cfg, files, ids)}
    found.update({d.num: d for d in load(cfg) if d.task == feature and d.status == "open"})
    if not found:
        return ""
    return (
        "## Open escaped defects (bugs found after an accepted task) on this task's files "
        "or spec IDs\n\n" + "\n".join(f"- {found[n].line()}" for n in sorted(found)) + "\n"
    )


def stats_lines(cfg: Config, accepted: Sequence[str]) -> list[str]:
    """`framegate stats`: the total, and one line per task that has an escaped defect."""
    by_task = per_task(cfg)
    total = sum(len(v) for v in by_task.values())
    tasks = len(set(accepted) | set(by_task))
    if not total:
        return [f"Escaped defects: 0 in {tasks} accepted task(s)."]
    still = sum(1 for v in by_task.values() for d in v if d.status == "open")
    out = [
        f"Escaped defects: {total} in {tasks} accepted task(s) ({still} open); "
        f"{len(by_task)} task(s) with at least one."
    ]
    for task, found in sorted(by_task.items()):
        out.append(f"  {task}: {len(found)} ({', '.join(d.id for d in found)})")
    return out


def commit(cfg: Config, message: str) -> None:
    """Commit the defect log, nothing else (no git repository: the file is written only)."""
    from framegate.state import GitError, git

    rel = path(cfg).relative_to(cfg.root).as_posix()
    try:
        git(cfg, "add", "--", rel)
        if git(cfg, "diff", "--cached", "--name-only", "--", rel).strip():
            git(cfg, "commit", "-q", "-m", message, "--", rel)
    except GitError:
        return


def run(cfg: Config, args: Any, echo: Callable[[str], None] = print) -> int:
    """`framegate defect`: record, close or list."""
    from framegate.state import log_event

    rel = path(cfg).relative_to(cfg.root).as_posix()
    try:
        if args.close:
            d = close(cfg, args.close, args.fix)
            commit(cfg, f"escaped defect {d.id} fixed")
            log_event(cfg, d.task, "escaped_defect_fixed", id=d.id, fix=d.fix)
            echo(f"Fixed: {d.line()}")
            return 0
        if not args.feature:
            found = [d for d in load(cfg) if args.all or d.status == "open"]
            what = "escaped defect(s)" if args.all else "open escaped defect(s)"
            echo(f"{len(found)} {what} in {rel}" + (":" if found else "."))
            for d in found:
                echo(f"- {d.line()}")
            return 0
        d = add(
            cfg,
            args.feature,
            args.what,
            args.found_by,
            args.severity,
            args.fix,
            args.file,
            args.id,
            args.force,
        )
    except DefectError as e:
        echo(f"error: {e}")
        return 1
    commit(cfg, f"{d.task}: escaped defect {d.id}")
    log_event(cfg, d.task, "escaped_defect", id=d.id, severity=d.severity, what=d.what)
    echo(f"Recorded in {rel}: {d.line()}")
    return 0


def task_section(adapter: Any, feature: str) -> str:
    """retro_section for a task of this checkout: its files are what it changed against
    its base, its spec IDs the ones of its spec ("" = none, or git cannot tell)."""
    from framegate.checks.existing_members import base_ref
    from framegate.specs import feature_ids
    from framegate.state import GitError, git

    cfg: Config = adapter.cfg
    if not load(cfg):
        return ""
    try:
        ref = base_ref(adapter, feature)
        files = git(cfg, "diff", "--name-only", ref).split() if ref else []
    except GitError:
        files = []
    return retro_section(cfg, feature, files, sorted(feature_ids(cfg, feature)))
