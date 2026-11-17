"""One chat message of a replay run, sent by the driver from its folder:

  python -m framegate.replay.chat "<message>" [--resume ID] [--allow PATTERN ...]
  python -m framegate.replay.chat --file task.md --resume ID

Runs headless Claude Code in ../sandbox, writes its JSON answer to the next turn-NN.json here
and prints the session id, the cost, the permission denials and the answer.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Sequence

#: what the chat may do without asking (the plugin's skills call framegate, git and python)
ALLOWED = [
    *(
        f"{tool}({cmd} *)"
        for cmd in ("framegate", "git", "python", "uv")
        for tool in ("Bash", "PowerShell")
    ),
    "Bash(.venv/Scripts/python* *)",
    "Bash(.venv/bin/python* *)",
    "PowerShell(.venv\\Scripts\\python* *)",
    "PowerShell(.\\.venv\\Scripts\\python* *)",
    "PowerShell(& *python* *)",
    *("Read", "Write", "Edit", "Glob", "Grep", "Monitor"),
]


def claude_exe() -> str:
    name = os.environ.get("FRAMEGATE_CLAUDE", "claude")
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"claude CLI not found: '{name}' (set FRAMEGATE_CLAUDE)")
    return found


def command(
    exe: str, message: str, resume: str = "", allow: Sequence[str] = ()
) -> tuple[list[str], str | None]:
    """The call and its stdin. A slash command must be the argument; any other text goes
    through stdin, which keeps many lines and quotes whole on every platform."""
    cmd = [exe, "-p", "--output-format", "json", "--permission-mode", "acceptEdits"]
    cmd += ["--allowedTools", ",".join([*ALLOWED, *allow])]
    if resume:
        cmd += ["--resume", resume]
    if plugin := os.environ.get("FRAMEGATE_REPLAY_PLUGIN"):  # a plugin folder under test
        cmd += ["--plugin-dir", plugin]
    if message.startswith("/"):
        return [*cmd, message], None
    return cmd, message


def next_turn(folder: pathlib.Path) -> pathlib.Path:
    taken = [int(p.stem[5:]) for p in folder.glob("turn-*.json") if p.stem[5:].isdigit()]
    return folder / f"turn-{max(taken, default=0) + 1:02d}.json"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m framegate.replay.chat")
    ap.add_argument("message", nargs="?")
    ap.add_argument("--file", help="send this file's text")
    ap.add_argument("--resume", default="", help="session id of the conversation")
    ap.add_argument("--allow", action="append", default=[], help="one more allowed tool pattern")
    ap.add_argument("--sandbox", default="../sandbox")
    args = ap.parse_args(argv)
    if bool(args.message) == bool(args.file):
        ap.error("give the message or --file")
    message = args.message or pathlib.Path(args.file).read_text(encoding="utf-8").strip()
    cmd, stdin = command(claude_exe(), message, args.resume, args.allow)
    r = subprocess.run(
        cmd,
        cwd=args.sandbox,
        **({"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin}),  # type: ignore[call-overload]
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    turn = next_turn(pathlib.Path.cwd())
    turn.write_text(r.stdout, encoding="utf-8")
    try:
        data = json.loads(r.stdout)
    except ValueError:
        print(f"{turn.name}: no JSON answer (exit {r.returncode})\n{r.stdout}\n{r.stderr}")
        return 1
    denials = data.get("permission_denials") or []
    print(
        f"{turn.name}  session: {data.get('session_id')}  "
        f"cost of the session so far: {data.get('total_cost_usd')} USD  "
        f"error: {data.get('is_error')}  permission denials: {len(denials)}"
    )
    for d in denials:
        print(f"denied: {json.dumps(d, ensure_ascii=False)[:400]}")
    print(data.get("result") or "")
    return 1 if data.get("is_error") else 0


if __name__ == "__main__":
    sys.exit(main())
