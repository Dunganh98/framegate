"""PreToolUse hook of a replay: nothing outside the run's own folder (stdlib only).

    python -m framegate.replay.guard [--up N]

The root is Claude Code's project folder (CLAUDE_PROJECT_DIR, else the call's cwd), N folders
up. No path in the command: a hook command runs in bash or PowerShell, and a quoted path with
spaces broke it in PowerShell. `python` is the sandbox's (first on PATH in a replay); a hook
that cannot start lets the call through, so the sandbox build tries it first (sandbox.py).

Claude Code passes the tool call as JSON on stdin; exit 2 blocks it and stderr tells the
agent why. Windows has no folder sandbox for a process, so this is the check that keeps a
replay's chat, its engine agents and its driver inside `<root>`:

- Edit / Write / NotebookEdit: the file must be under `<root>`.
- Bash / PowerShell: no command of BLOCKED (network, publish, the machine's or the user's
  setup), where a command starts; no path outside `<root>`: absolute (with spaces where the
  whole path is inside), UNC, `~`, home and temp variables, `/tmp`, `..` that leaves it.
  Reading the installed plugins (`~/.claude/plugins`) is allowed, writing there is not.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile

#: commands a replay never runs. Also the deny rules of its settings (sandbox.deny_rules).
BLOCKED = (
    "git push", "git pull", "git fetch", "git clone", "git remote", "git submodule",
    "git config --global", "git config --system",
    "gh", "az", "curl", "wget", "iwr", "irm", "Invoke-WebRequest", "Invoke-RestMethod",
    "Start-BitsTransfer", "ssh", "scp", "winget", "choco", "scoop", "setx", "reg",
    "Set-ItemProperty", "New-ItemProperty", "Start-Process", "runas", "Set-ExecutionPolicy",
    "npm", "npx", "uv tool", "pipx", "claude",
    "framegate publish", "framegate cleanup", "framegate update",
)  # fmt: skip


def _command(text: str) -> str:
    first, *rest = text.split()
    if first == "git":  # `git -C <path> push` is a push too
        first = r"git(?:\s+-[Cc]\s+(?:\"[^\"]*\"|'[^']*'|\S+))*"
    return r"\s+".join([first, *map(re.escape, rest)])


#: a BLOCKED command where a command starts (not a word in a message or a pattern)
START = re.compile(
    r"(?:^|[;&|({`\n])\s*(?P<cmd>" + "|".join(map(_command, BLOCKED)) + r")(?=$|[\s;&|)}`])",
    re.I,
)
BARE = r"[^\s\"'|;,<>()`]"  # a path up to a space
SPACED = r"[^\"'|;,<>()`&\r\n]"  # a path with spaces (quoted, or a folder name)


def _drive(chars: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w/\\])[A-Za-z]:[\\/]{chars}*")


def _posix(chars: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w.\\/-])/(?:mnt/)?[A-Za-z]/{chars}*")


PATHS = [(_drive(BARE), _drive(SPACED)), (_posix(BARE), _posix(SPACED))]
UNC = re.compile(r"(?<![\w\\])\\\\[\w.$-]+\\")
VARS = re.compile(
    r"(?:(?<![\w])~(?=[\\/\s\"']|$)|\$(?:env:)?\{?(?P<a>HOME|USERPROFILE|HOMEPATH|APPDATA"
    r"|LOCALAPPDATA|TEMP|TMP)\}?(?!\w)|%(?P<b>USERPROFILE|HOMEPATH|APPDATA|LOCALAPPDATA|TEMP"
    r"|TMP)%|(?<![\w.\\/-])/tmp(?=[\\/\s\"']|$))(?P<rest>[\\/][^\s\"'|;,<>()`]*)?",
    re.I,
)
PARENT = re.compile(r"[^\s\"'|;,<>()`]*\.\.(?:[\\/][^\s\"'|;,<>()`]*)?")
#: a command that changes files: the plugins may not be named in it
WRITES = re.compile(
    r"(?:^|[\s;&|(])(?:rm|rmdir|del|erase|mv|cp|tee|touch|chmod|Remove-Item|Move-Item"
    r"|Copy-Item|Rename-Item|Set-Content|Add-Content|Out-File|New-Item|Clear-Content"
    r"|sed\s+-i)(?=$|\s)|(?<![\d&])>",
    re.I,
)


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def inside(path: str, root: str) -> bool:
    p = norm(path)
    return p == root or p.startswith(root + os.sep)


def home() -> str:
    return os.path.expanduser("~")


def plugins(path: str) -> bool:
    """The installed plugins: outside the root, read by the setup."""
    return inside(path, norm(os.path.join(home(), ".claude", "plugins")))


def as_path(text: str) -> str:
    """`/c/x` and `/mnt/c/x` as `c:/x`."""
    m = re.match(r"/(?:mnt/)?([A-Za-z])/(.*)", text)
    return f"{m.group(1)}:/{m.group(2)}" if m else text


def _var(m: re.Match[str]) -> str:
    name = (m.group("a") or m.group("b") or "").upper()
    if m.group(0).startswith("/tmp") or name in ("TEMP", "TMP"):
        base = tempfile.gettempdir()
    else:
        base = home() if name in ("", "HOME") else os.environ.get(name) or home()
    return base + (m.group("rest") or "")


def outside_paths(command: str, cwd: str, root: str) -> list[str]:
    found = []
    for bare, spaced in PATHS:
        # a path with spaces that is inside as a whole is not cut at its first space
        whole = {
            m.start() for m in spaced.finditer(command) if inside(as_path(m[0].rstrip()), root)
        }
        found += [as_path(m[0]) for m in bare.finditer(command) if m.start() not in whole]
    found += [m[0] for m in UNC.finditer(command)]
    found += [_var(m) for m in VARS.finditer(command)]
    found += [os.path.join(cwd, m[0]) for m in PARENT.finditer(command)]
    reads = not WRITES.search(command)
    return [p for p in found if not inside(p, root) and not (reads and plugins(p))]


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
    m = START.search(command)
    if m:
        return f"a replay never runs this (network, publish, the machine's setup): {m['cmd']}"
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
