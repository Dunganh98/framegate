"""`framegate install-tools` for a Python project: put what the workflow needs into the
project's interpreter (the framegate pytest plugin, pytest, pytest-cov, hypothesis, diff-cover
and the lint / format / type tools the gate commands name), and say what that changed."""

from __future__ import annotations

import os
import pathlib
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from framegate.adapters.base import run_command


@dataclass(frozen=True)
class ToolReq:
    #: importable module that proves the tool is there
    module: str
    #: pip arguments that install it (one requirement, or `-e <path>`)
    pip: tuple[str, ...]
    why: str

    @property
    def label(self) -> str:
        return " ".join(self.pip)


#: lint / format / type tools installed when a gate command names them
GATE_TOOLS = {"ruff": "lint / format", "mypy": "type check", "black": "format"}


def gate_tools(commands: Sequence[Sequence[str]]) -> list[ToolReq]:
    """The known tools the given gate commands run (by an argument that is the tool's name or
    path, e.g. `ruff`, `python -m black`, `.venv/Scripts/mypy.exe`)."""
    found: list[ToolReq] = []
    for tool, why in GATE_TOOLS.items():
        pattern = re.compile(rf"(^|[\\/]){tool}(\.exe)?$", re.I)
        if any(pattern.search(arg) for cmd in commands for arg in cmd):
            found.append(ToolReq(tool, (tool,), why))
    return found


def path_with(first: Sequence[str], base: str | None = None) -> str:
    """PATH with the folders `first` in front (each once; `base` = the current PATH)."""
    current = os.environ.get("PATH", "") if base is None else base
    seen = {os.path.normcase(f) for f in first}
    rest = [p for p in current.split(os.pathsep) if p and os.path.normcase(p) not in seen]
    return os.pathsep.join([*first, *rest])


def interpreter_env(python: str) -> dict[str, str]:
    """What makes `python`, `pytest`, `ruff`, ... in a shell mean this interpreter and its
    tools: its folder first on PATH (plus `Scripts` next to a Windows interpreter that is not
    in a venv), and VIRTUAL_ENV when it is a venv's. {} when the interpreter is not found."""
    found = python if os.path.isabs(python) else shutil.which(python)
    if not found or not os.path.isfile(found):
        return {}
    folder = pathlib.Path(found).parent  # as named: a venv's python may be a link
    front = [str(folder)]
    scripts = folder / "Scripts"
    if scripts.is_dir():
        front.append(str(scripts))
    env = {"PATH": path_with(front)}
    if (folder.parent / "pyvenv.cfg").is_file():
        env["VIRTUAL_ENV"] = str(folder.parent)
    return env


def _freeze(python: str, installer: list[str], cwd: pathlib.Path) -> dict[str, str]:
    """Installed distributions of the interpreter: name (lower case) -> version."""
    cmd = [*installer, "freeze"] + (["--python", python] if installer[0] != python else [])
    code, out = run_command(cmd, cwd)
    if code != 0:
        return {}
    pairs = (line.split("==", 1) for line in out.splitlines() if "==" in line)
    return {name.strip().lower().replace("_", "-"): version.strip() for name, version in pairs}


def installer_for(python: str, has_pip: bool) -> list[str] | None:
    """The command prefix that installs into `python`: its own pip, else uv (a uv-made venv
    has no pip); None = neither."""
    if has_pip:
        return [python, "-m", "pip"]
    uv = shutil.which("uv")
    return [uv, "pip"] if uv else None


def install(
    python: str,
    cwd: pathlib.Path,
    reqs: Sequence[ToolReq],
    has_module: Callable[[str], bool],
    dry_run: bool,
    echo: Callable[[str], None],
    update: Sequence[str] = (),
) -> int:
    """Install the missing ones of `reqs` into `python`, and those of the modules `update`
    again though they are there (another version than this framegate's). 0 = everything is
    there (or, with dry_run, was listed); 1 = an install failed or no installer was found."""
    absent = [r for r in reqs if not has_module(r.module)]
    missing = [r for r in reqs if r in absent or r.module in update]
    echo(f"Project interpreter: {python}")
    for r in reqs:
        state = "missing" if r in absent else "update" if r in missing else "ok"
        echo(f"  [{state:7}] {r.module} ({r.why})")
    if not missing:
        echo("Nothing to install: every tool the workflow needs is there.")
        return 0
    wanted = ", ".join(r.label for r in missing)
    if dry_run:
        echo(f"Would install: {wanted}")
        return 0
    installer = installer_for(python, has_module("pip"))
    if installer is None:
        echo("error: this interpreter has no pip and uv is not on PATH; install by hand: " + wanted)
        return 1
    own = installer[0] == python
    before = _freeze(python, installer, cwd)
    args = [a for r in missing for a in r.pip]
    cmd = [*installer, "install", *([] if own else ["--python", python]), *args]
    echo(f"Installing: {wanted}")
    code, out = run_command(cmd, cwd, timeout=1800)
    if code != 0:
        echo("error: the install failed:\n" + "\n".join(out.splitlines()[-25:]))
        return 1
    after = _freeze(python, installer, cwd)
    added = sorted(n for n in after if n not in before)
    changed = sorted(n for n in after if n in before and after[n] != before[n])
    if added:
        echo("Added: " + ", ".join(f"{n} {after[n]}" for n in added))
    if changed:
        echo(
            "CHANGED versions of packages that were already installed (check them against "
            "the project's pins): " + ", ".join(f"{n} {before[n]} -> {after[n]}" for n in changed)
        )
    elif before:
        echo("No package that was already installed changed its version.")
    still = [r.module for r in missing if not has_module(r.module)]
    if still:
        echo("error: still missing after the install: " + ", ".join(still))
        return 1
    echo("Tools installed.")
    return 0
