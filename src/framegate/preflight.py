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
(resume). The difference is only named (`KEPT:`).

Output: one line `Start check: ok ...` when everything is in line, else `Start check:` and one
line per point: `UPDATED:` (done here), `RUN:` (a command for the chat), `TELL THE USER:`,
`KEPT:` (nothing to do), `PROBLEM:` (a failed short setup check; `framegate doctor` has the
detail). The exit code is always 0: the lines are the result. (A skill command that exits
non-zero while the skill loads aborts the whole skill, docs/plugin.md; the skills run this as
their first step instead, because a tool older than this command does not know it.)
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from collections.abc import Callable

from framegate import SOURCE_URL, __version__
from framegate.adapters import get_adapter
from framegate.adapters.base import LanguageAdapter
from framegate.config import CONFIG_FILE, Config, ConfigError, find_root, load_config
from framegate.state import GitError, feature_finished, tag_exists

PLUGIN_NAME = "framegate"


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version))


def installed_plugin_version(home: pathlib.Path | None = None) -> str | None:
    """The newest version of the framegate plugin Claude Code has installed on this machine
    (`<claude config>/plugins/installed_plugins.json`); None = not installed or not readable.
    A session that started before an update still runs the older skills."""
    base = home or pathlib.Path(
        os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude"
    )
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


def version_note() -> str:
    """One line for `framegate status` when the plugin on this machine and the command-line
    tool differ ("" when they are in line or no plugin is installed)."""
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

    # --- the plugin: the skills of this session against what is installed on the machine ---
    on_disk = installed_plugin_version()
    if plugin_version and on_disk and version_key(on_disk) > version_key(plugin_version):
        # nothing else is updated for skills that are about to be replaced
        echo("Start check:")
        echo(
            f"TELL THE USER: the framegate plugin was updated to {on_disk}, but this session "
            f"still runs the skills of {plugin_version}. Restart Claude Code (exit and start "
            "it again) to load the new skills, then repeat the command."
        )
        return 0

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
    working = cfg is not None and in_progress(cfg, feature)
    keep = not update and (working or check_only or not feature)
    why_kept = (
        f"{feature} is in progress and keeps what it was started with"
        if working
        else "nothing is updated from here; /framegate:update does it, or the next task"
    )

    # --- the command-line tool at the plugin's version ---
    if plugin_version and version_key(plugin_version) != version_key(cli):
        if keep:
            lines.append(
                f"KEPT: the command-line tool is {cli}, the plugin {plugin_version} ({why_kept})."
            )
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
            if keep:
                lines.append(
                    f"{'KEPT' if have else 'PROBLEM'}: the project environment has {what}, the "
                    f"command-line tool is {cli} ({why_kept}). If a test run fails on an "
                    "unknown option: framegate install-tools --update"
                )
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
