"""PreToolUse hook of a replay: nothing outside the run's own folder (stdlib only).

    python -m framegate.replay.guard [--up N]

The root is Claude Code's project folder (CLAUDE_PROJECT_DIR, else the call's cwd), N folders
up. No path in the command: a hook command runs in bash or PowerShell, and a quoted path with
spaces broke it in PowerShell. `python` is the sandbox's (first on PATH in a replay).

Claude Code passes the tool call as JSON on stdin; exit 2 blocks it and stderr tells the
agent why. Windows has no folder sandbox for a process, so this is the check that keeps a
replay's chat, its engine agents and its driver inside `<root>`:

- Edit / Write / NotebookEdit: the file must be under `<root>`.
- Bash / PowerShell: no command that pushes, reaches the network, publishes, changes the
  machine's or the user's setup; no path outside `<root>` (absolute, `~`, home variables,
  `..` that leaves it). Reading the installed plugins (`~/.claude/plugins`) is allowed.
"""

from __future__ import annotations

import json
import os
import re
import sys

#: commands a replay never runs (first word or two, case-insensitive)
BLOCKED = re.compile(
    r"(?:^|[;&|({\s])(?:"
    r"git\s+(?:push|pull|fetch|clone|remote|submodule|config\s+--(?:global|system))"
    r"|gh|az|curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod|Start-BitsTransfer"
    r"|ssh|scp|winget|choco|scoop|setx|reg|Set-ItemProperty|New-ItemProperty"
    r"|Start-Process|runas|Set-ExecutionPolicy|npm|npx|uv\s+tool|pipx|claude"
    r"|framegate\s+(?:publish|cleanup|update)"
    r")(?=$|[\s;&|)])",
    re.I,
)
WIN_PATH = re.compile(r"(?<![\w/\\])([A-Za-z]:[\\/][^\s\"'|;,<>()`]*)")
POSIX_DRIVE = re.compile(r"(?<![\w.\\/-])/(?:mnt/)?([A-Za-z])/([^\s\"'|;,<>()`]*)")
HOME = re.compile(
    r"(?:(?<![\w])~(?=[\\/\s\"']|$)|\$HOME\b|\$env:(?:USERPROFILE|HOMEPATH|APPDATA|LOCALAPPDATA)"
    r"|%(?:USERPROFILE|HOMEPATH|APPDATA|LOCALAPPDATA)%)([\\/][^\s\"'|;,<>()`]*)?",
    re.I,
)
PARENT = re.compile(r"[^\s\"'|;,<>()`]*\.\.(?:[\\/][^\s\"'|;,<>()`]*)?")


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def inside(path: str, root: str) -> bool:
    p = norm(path)
    return p == root or p.startswith(root + os.sep)


def home() -> str:
    return os.path.expanduser("~")


def readable(path: str) -> bool:
    """Outside the root, but only read by the setup: the installed plugins."""
    return inside(path, norm(os.path.join(home(), ".claude", "plugins")))


def outside_paths(command: str, cwd: str, root: str) -> list[str]:
    found = [m.group(1) for m in WIN_PATH.finditer(command)]
    found += [f"{m.group(1)}:/{m.group(2)}" for m in POSIX_DRIVE.finditer(command)]
    found += [home() + (m.group(1) or "") for m in HOME.finditer(command)]
    found += [os.path.join(cwd, m.group(0)) for m in PARENT.finditer(command)]
    return [p for p in found if not inside(p, root) and not readable(p)]


def verdict(call: dict, root: str) -> str:
    """'' when the call may run, else why not."""
    tool = str(call.get("tool_name") or "")
    args = call.get("tool_input") or {}
    cwd = str(call.get("cwd") or os.getcwd())
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(args.get("file_path") or args.get("notebook_path") or "")
        if path and not inside(os.path.join(cwd, path), root):
            return f"a replay writes only inside {root}: {path}"
        return ""
    if tool not in ("Bash", "PowerShell"):
        return ""
    command = str(args.get("command") or "")
    m = BLOCKED.search(command)
    if m:
        what = m.group(0).strip(" ;&|({")
        return f"a replay never runs this (network, publish, the machine's setup): {what}"
    bad = outside_paths(command, cwd, root)
    if bad:
        return f"a replay stays inside {root}; this command names {bad[0]}"
    return ""


def main(argv: list[str]) -> int:
    try:
        call = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        call = {}
    up = int(argv[argv.index("--up") + 1]) if "--up" in argv else 0
    root = os.environ.get("CLAUDE_PROJECT_DIR") or str(call.get("cwd") or os.getcwd())
    for _ in range(up):
        root = os.path.dirname(norm(root))
    root = norm(root)
    why = verdict(call, root)
    if why:
        print(f"Blocked by the replay guard: {why}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
