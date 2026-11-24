"""`framegate preflight`: the start step of the chat skills (feature, bugfix, refactor;
resume with `--check`). One command brings the three pieces to one version and says what
is left to do:

- the **plugin** (the skills of this chat session; Claude Code updates it),
- the **command-line tool** (this process; the skill installs it with `uv tool install`),
- the **project environment's workflow tools** (python: the framegate pytest plugin in the
  project interpreter; `framegate install-tools` puts it there).

Seen on a real run: plugin 0.9.0, tool 0.9.0, pytest plugin 0.8.0 in the project's .venv, and
nothing noticed. What this command does itself, and what it only says:

- It updates the project environment's tools to its own version (`install-tools --update`).
- It never updates itself: `uv tool install --force` cannot replace a running `framegate.exe`
  on Windows (checked: "Failed to install entrypoint ... os error 32", and the half-replaced
  package then broke the running process). It prints the command (`RUN:`); the skill runs it
  and then this command again.
- It cannot restart Claude Code: when the plugin on this machine is newer than the skills of
  the session that asks, it says so (`TELL THE USER:`).

Work in progress (the feature's spec is approved) keeps what it was started with: neither the
tool nor the project's tools are updated then, nor with `--check` or without a feature
(resume). The difference is only named (`KEPT:`: an update is waiting).

The same holds for every other task in progress, not only the one named. Seen on a replay
over three repositories: tasks went from 0.12.0 to 0.12.1 and 0.12.2 during their runs and a
`status` then failed on an import. The check looked only at the feature it was called for,
but there is one command-line tool per machine: the start check of the next task (here or in
another repository) replaced it under the tasks still running. So "spec OK" writes the
version into the task's progress.json and the task into a list for this machine
(`<claude config>/framegate/tasks/`); a task leaves the list when it is DONE or cleaned up.
While the list names a task, no start check prints the install command, and none updates
the workflow tools of a project that has a task in progress. `framegate update` (the user
asked) still does, except while a run is active: replacing the files of a running tool
breaks it. A task without activity for STALE_DAYS (abandoned, forgotten) no longer holds an
update back; `framegate status --all` lists every task of the list, stale ones marked.

`[update] check = false` in framegate.toml or env FRAMEGATE_UPDATE_CHECK=0 turns the
comparison off altogether (a pinned setup, a replay of old tasks).

Output: one line `Start check: ok ...` when everything is in line, else `Start check:` and one
line per point: `UPDATED:` (done here), `RUN:` (a command for the chat), `TELL THE USER:`,
`KEPT:` (nothing to do), `PROBLEM:` (a failed short setup check; `framegate doctor` has the
detail). The exit code is always 0: the lines are the result. (A skill command that exits
non-zero while the skill loads aborts the whole skill, docs/plugin.md; the skills run this as
their first step instead, because a tool older than this command does not know it.)
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import re
from collections.abc import Callable
from dataclasses import dataclass

from framegate import SOURCE_URL, __version__, procs
from framegate.adapters import get_adapter
from framegate.adapters.base import LanguageAdapter
from framegate.config import (
    CONFIG_FILE,
    Config,
    ConfigError,
    find_root,
    load_config,
    update_check_off,
)
from framegate.state import GitError, feature_finished, replace_file, tag_exists

PLUGIN_NAME = "framegate"
PROGRESS_FILE = "progress.json"
#: a task without activity for this many days no longer holds an update back
STALE_DAYS = 14


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version))


def claude_home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")


# ---------- the tasks in progress on this machine ----------
@dataclass
class Task:
    """A task between "spec OK" and DONE, here or in another repository."""

    root: pathlib.Path
    feature: str
    #: the framegate version it was started with ("" = started by a version that did not
    #: write it down)
    version: str
    #: its run lock when a `framegate run` of it is alive
    pid: int = 0
    phase: str = ""
    #: its newest event; without one, when its progress.json was written
    last: datetime.datetime | None = None

    @property
    def stale(self) -> bool:
        """No activity for STALE_DAYS: it no longer holds an update back."""
        age = datetime.datetime.now() - self.last if self.last else datetime.timedelta()
        return not self.pid and age.days >= STALE_DAYS

    def where(self, here: pathlib.Path | None) -> str:
        return (
            self.feature
            if here is not None and same(self.root, here)
            else (f"{self.feature} in {self.root}")
        )


def same(a: pathlib.Path, b: pathlib.Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def tasks_dir() -> pathlib.Path:
    """One small file per task: no file is shared, so two runs never write the same one."""
    return claude_home() / "framegate" / "tasks"


def task_file(root: pathlib.Path, feature: str) -> pathlib.Path:
    key = os.path.normcase(str(root.resolve())) + "\n" + feature
    return tasks_dir() / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".json")


def _progress(path: pathlib.Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _running(out_dir: pathlib.Path, feature: str) -> int:
    lock = procs.read_lock(out_dir)
    if procs.lock_alive(lock) and (lock or {}).get("feature") == feature:
        return int((lock or {}).get("pid") or 0)
    return 0


def last_activity(progress: pathlib.Path) -> datetime.datetime | None:
    """`progress` = {out}/<feature>/progress.json: the time of the feature's newest event in
    {out}/events.jsonl; without one, when progress.json was written."""
    feature = progress.parent.name
    try:
        lines = (progress.parent.parent / "events.jsonl").read_text("utf-8", "replace").split("\n")
    except OSError:
        lines = []
    for line in reversed(lines):
        if f'"{feature}"' not in line:
            continue
        try:
            rec = json.loads(line)
            if rec.get("feature") == feature:
                return datetime.datetime.fromisoformat(str(rec.get("ts")))
        except (ValueError, AttributeError):
            continue
    try:
        return datetime.datetime.fromtimestamp(progress.stat().st_mtime)
    except OSError:
        return None


def _task(
    root: pathlib.Path, progress: pathlib.Path, prog: dict[str, object], version: str
) -> Task:
    feature = progress.parent.name
    return Task(
        root,
        feature,
        version,
        _running(progress.parent.parent, feature),
        str(prog.get("phase") or ""),
        last_activity(progress),
    )


def record_start(cfg: Config, feature: str, restart: bool = False) -> None:
    """Write down which framegate version the task runs with: in its progress.json
    (`framegate`) and in this machine's task list. Called at "spec OK" (`restart`: the task
    starts over, with this version) and when a run starts (a task approved by an older
    version: only what is missing). Never fails a command: the list is a courtesy to the
    other tasks, not state of this one."""
    path = cfg.feature_out(feature) / PROGRESS_FILE
    prog = _progress(path)
    if prog is None:
        return
    try:
        if restart or not prog.get("framegate"):
            prog["framegate"] = __version__
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(prog, indent=2), encoding="utf-8")
            replace_file(tmp, path)
        entry = task_file(cfg.root, feature)
        if restart or not entry.is_file():
            entry.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "root": str(cfg.root.resolve()),
                "feature": feature,
                "progress": str(path.resolve()),
                "framegate": str(prog["framegate"]),
                "since": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            entry.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def listed_tasks() -> list[Task]:
    """The tasks of this machine's list that are still in progress. An entry whose task is
    DONE or gone (cleaned up, the repository deleted) is removed here."""
    found = []
    folder = tasks_dir()
    for entry in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = _progress(entry) or {}
        path = pathlib.Path(str(data.get("progress") or ""))
        prog = _progress(path) if data.get("progress") else None
        if prog is None or prog.get("phase") == "DONE":
            try:
                entry.unlink()
            except OSError:
                pass
            continue
        found.append(
            _task(
                pathlib.Path(str(data.get("root") or path.parent.parent.parent)),
                path,
                prog,
                str(prog.get("framegate") or data.get("framegate") or ""),
            )
        )
    return found


def local_tasks(cfg: Config) -> list[Task]:
    """The tasks in progress in this checkout, from its own files: also the ones an older
    version started, which are in no list."""
    found = []
    out = cfg.out_dir
    for path in sorted(out.glob(f"*/{PROGRESS_FILE}")) if out.is_dir() else []:
        feature = path.parent.name
        prog = _progress(path)
        if prog is None or prog.get("phase") == "DONE" or not in_progress(cfg, feature):
            continue
        found.append(_task(cfg.root, path, prog, str(prog.get("framegate") or "")))
    return found


def tasks_in_progress(cfg: Config | None) -> list[Task]:
    tasks = local_tasks(cfg) if cfg is not None else []
    for task in listed_tasks():
        if not any(same(task.root, t.root) and task.feature == t.feature for t in tasks):
            tasks.append(task)
    return tasks


def _age(then: datetime.datetime | None) -> str:
    if then is None:
        return "?"
    s = max((datetime.datetime.now() - then).total_seconds(), 0)
    age = f"{int(s // 86400)} d" if s >= 86400 else f"{int(s // 3600)} h {int(s % 3600 // 60)} min"
    return f"{then:%Y-%m-%d %H:%M} ({age} ago)"


def status_all(echo: Callable[[str], None] = print) -> int:
    """`framegate status --all`: every open task of this machine's list, one row each."""
    from framegate.specs import feature_mode

    rows = [("Repository", "Task", "Mode", "Phase", "Last activity", "")]
    for t in sorted(listed_tasks(), key=lambda t: (str(t.root).lower(), t.feature)):
        try:
            mode = feature_mode(load_config(t.root), t.feature)
        except (ConfigError, OSError):
            mode = "?"
        rows.append(
            (str(t.root), t.feature, mode, t.phase, _age(t.last), "stale" if t.stale else "")
        )
    if len(rows) == 1:
        echo("No open framegate task on this machine.")
        return 0
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        echo("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip())
    if any(r[5] for r in rows):
        echo(f"stale: no activity for {STALE_DAYS} days; it no longer holds an update back.")
    return 0


def installed_plugin_version(home: pathlib.Path | None = None) -> str | None:
    """The newest version of the framegate plugin Claude Code has installed on this machine
    (`<claude config>/plugins/installed_plugins.json`); None = not installed or not readable.
    A session that started before an update still runs the older skills."""
    base = home or claude_home()
    try:
        data = json.loads((base / "plugins" / "installed_plugins.json").read_text("utf-8"))
        plugins = data["plugins"]
        found = [
            str(entry["version"])
            for key, entries in plugins.items()
            if str(key).split("@")[0] == PLUGIN_NAME
            for entry in entries
            if version_key(str(entry.get("version", "")))
        ]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return max(found, key=version_key) if found else None


def install_command(version: str) -> str:
    return f'uv tool install --force "git+{SOURCE_URL}@v{version}"'


def again(feature: str | None, plugin_version: str, update: bool = False) -> str:
    version = f"--plugin-version {plugin_version}" if plugin_version else ""
    command = "framegate update" if update else "framegate preflight"
    return " ".join(a for a in (command, None if update else feature, version) if a)


def in_progress(cfg: Config, feature: str | None) -> bool:
    """Between "spec OK" and DONE: the run keeps the versions it was started with. A finished
    task (its wrap-up, its publish, its pipeline task) is brought up to date like new work."""
    return (
        bool(feature) and spec_approved(cfg, feature) and not feature_finished(cfg, feature or "")
    )


def version_note(cfg: Config | None = None) -> str:
    """One line for `framegate status` when the plugin on this machine and the command-line
    tool differ ("" when they are in line, no plugin is installed or the check is off)."""
    if update_check_off() or (cfg is not None and not cfg.update_check):
        return ""
    on_disk = installed_plugin_version()
    if not on_disk or version_key(on_disk) == version_key(__version__):
        return ""
    return (
        f"framegate: the plugin is {on_disk}, the command-line tool {__version__}: "
        "/framegate:update brings them in line (in a terminal: framegate update)."
    )


def spec_approved(cfg: Config, feature: str | None) -> bool:
    if not feature:
        return False
    try:
        return tag_exists(cfg, f"spec-approved/{feature}")
    except GitError:
        return False


def setup_problems(cfg: Config, adapter: LanguageAdapter) -> list[str]:
    """The short setup check: what would stop a run at once and costs no test run to see."""
    from framegate.orchestrator import Orchestrator, WorkflowError

    out = []
    try:
        Orchestrator(cfg, adapter, echo=lambda s: None).claude_cmd()
    except WorkflowError as e:
        out.append(str(e))
    return out


def run(
    feature: str | None = None,
    plugin_version: str = "",
    check_only: bool = False,
    echo: Callable[[str], None] = print,
    start: pathlib.Path | None = None,
    update: bool = False,
) -> int:
    """`update` (`framegate update`, /framegate:update): the user asked for it, so nothing is
    kept, whatever is in progress."""
    cli = __version__
    lines: list[str] = []

    root = find_root(start)
    cfg: Config | None = None
    adapter: LanguageAdapter | None = None
    if root is None:
        lines.append(
            f"PROBLEM: no {CONFIG_FILE} here or in a parent folder: /framegate:setup sets the "
            "project up."
        )
    else:
        try:
            cfg = load_config(root)
            adapter = get_adapter(cfg)
        except ConfigError as e:
            lines.append(f"PROBLEM: {CONFIG_FILE}: {e}")
    # `update` is the user's own request: the switch is for the checks nobody asked for
    off = not update and (update_check_off() or (cfg is not None and not cfg.update_check))

    # --- the plugin: the skills of this session against what is installed on the machine ---
    on_disk = installed_plugin_version()
    if (
        not off
        and plugin_version
        and on_disk
        and version_key(on_disk) > version_key(plugin_version)
    ):
        # nothing else is updated for skills that are about to be replaced
        echo("Start check:")
        echo(
            f"TELL THE USER: the framegate plugin was updated to {on_disk}, but this session "
            f"still runs the skills of {plugin_version}. Restart Claude Code (exit and start "
            "it again) to load the new skills, then repeat the command."
        )
        return 0

    tasks = [] if off else tasks_in_progress(cfg)
    working = cfg is not None and in_progress(cfg, feature)
    named = [t for t in tasks if root is not None and t.feature == feature and same(t.root, root)]
    mine = named[0] if named and working else None
    others = [t for t in tasks if t not in named and not t.stale]
    here = [t for t in others if root is not None and same(t.root, root)]
    waits = "The update waits until it is DONE; /framegate:update does it now"

    def why_kept(blocking: list[Task]) -> str:
        if working:
            since = f" (started with framegate {mine.version})" if mine and mine.version else ""
            return f"{feature} is in progress{since} and keeps what it was started with). {waits}"
        if blocking and not check_only and feature:
            more = f" and {len(blocking) - 1} more" if len(blocking) > 1 else ""
            return (
                f"{blocking[0].where(root)}{more} is in progress and keeps the tool it was "
                f"started with). {waits}"
            )
        return "nothing is updated from here; /framegate:update does it, or the next task)"

    def active(running: list[Task], what: str) -> str:
        """`update` keeps nothing back, except from a run that is going right now (checked:
        `uv tool install --force` replaces the package of a running tool, then fails on its
        exe; the run breaks on its next import)."""
        t = running[0]
        return (
            f"PROBLEM: a framegate run is active ({t.where(root)}, pid {t.pid}): replacing "
            f"{what} now would break it. Wait until it stops (or stop it), then update again."
        )

    asked = not update and not off and (working or check_only or not feature)
    keep_tool = off or asked or (not update and bool(others))
    keep_project = off or asked or (not update and bool(here))
    running = [t for t in tasks if t.pid]
    running_here = [t for t in running if root is not None and same(t.root, root)]

    # --- the command-line tool at the plugin's version ---
    if plugin_version and version_key(plugin_version) != version_key(cli):
        if off:
            pass
        elif keep_tool:
            lines.append(
                f"KEPT: the command-line tool is {cli}, the plugin {plugin_version} "
                f"({why_kept(others)}."
            )
        elif running:
            echo("Start check:")
            echo(active(running, "the command-line tool"))
            return 0
        else:
            echo("Start check:")
            repeat = again(feature, plugin_version, update)
            echo(
                f"RUN: {install_command(plugin_version)}   (the command-line tool is {cli}, the "
                f"plugin {plugin_version}; then run `{repeat}` again; "
                "if the install fails, say so in one line and go on with the installed tool)"
            )
            return 0

    # --- the project environment's workflow tools at the tool's version ---
    have: str | None = None
    if cfg is not None and adapter is not None:
        have = adapter.tools_version()
        if have is not None and have != cli:
            what = f"framegate {have}" if have else "no framegate part"
            if off and have:
                pass
            elif keep_project:
                lines.append(
                    f"{'KEPT' if have else 'PROBLEM'}: the project environment has {what}, the "
                    f"command-line tool is {cli} ({why_kept(here)}. If a test run fails on an "
                    "unknown option: framegate install-tools --update"
                )
            elif running_here:
                lines.append(active(running_here, "the project's workflow tools"))
            else:
                log: list[str] = []
                if adapter.install_tools(False, log.append, update=True) == 0:
                    changed = next((x for x in log if x.startswith("CHANGED versions")), "")
                    have = cli
                    lines.append(
                        f"UPDATED: the project environment's workflow tools ({what} -> "
                        f"framegate {cli})." + (f" {changed}" if changed else "")
                    )
                else:
                    lines.append(
                        f"PROBLEM: the project environment has {what} and the update to {cli} "
                        "failed: " + " / ".join(x.strip() for x in log[-3:])
                    )
        for problem in setup_problems(cfg, adapter):
            lines.append(f"PROBLEM: {problem} (detail: framegate doctor)")

    if not lines and off:
        echo(f"Start check: ok (framegate {cli}; the update check is off).")
        return 0
    if not lines:
        parts = ["command-line tool"]
        parts += ["plugin"] if plugin_version else []
        parts += ["project tools"] if have else []
        echo(f"Start check: ok (framegate {cli}: {', '.join(parts)}).")
        return 0
    echo("Start check:")
    for line in lines:
        echo(line)
    return 0
