"""One chat message of a replay run, sent by the driver from its folder:

  python -m framegate.replay.chat "<message>" [--resume ID] [--allow PATTERN ...]
  python -m framegate.replay.chat --file task.md --resume ID
  python -m framegate.replay.chat --wait [SECONDS]

Sending starts headless Claude Code in ../sandbox as a process of its own and returns at once:
the call goes on when the driver's tool call is cut at its time limit (seen: a call cut after
10 minutes took the framegate run the chat had started with it). The answer goes to the next
turn-NN.json here. `--wait` waits for it and prints the session id, the cost, the permission
denials and the answer; exit code 3 = still running, wait again.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from typing import Any

from framegate import procs

#: what the chat may do without asking: everything, since nobody answers a prompt in a
#: replay. What keeps the chat in the sandbox is the sandbox's settings: deny rules and the
#: guard hook (sandbox.write_deny, guard.py)
ALLOWED = ["Bash", "PowerShell", "Read", "Write", "Edit", "Glob", "Grep", "Monitor"]
#: `--wait` without a number: under the 10 minutes a tool call of the driver may take
WAIT_SECONDS = 540.0
STILL_RUNNING = 3


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
    cmd += ["--model", os.environ.get("FRAMEGATE_REPLAY_CHAT_MODEL") or "sonnet"]
    if message.startswith("/"):
        return [*cmd, message], None
    return cmd, message


def _number(path: pathlib.Path) -> int:
    return int(path.stem[5:]) if path.stem[5:].isdigit() else 0


def next_turn(folder: pathlib.Path) -> pathlib.Path:
    taken = [_number(p) for kind in ("json", "pid") for p in folder.glob(f"turn-*.{kind}")]
    return folder / f"turn-{max(taken, default=0) + 1:02d}.json"


def running(folder: pathlib.Path) -> pathlib.Path | None:
    """The turn file of the call that has no answer yet (its .pid file is there)."""
    marks = sorted(folder.glob("turn-*.pid"), key=_number)
    return marks[-1].with_suffix(".json") if marks else None


def read_pid(mark: pathlib.Path) -> int:
    try:
        return int(mark.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def detach(cmd: Sequence[str]) -> int:
    """Start `cmd` outside the caller's process tree and without its pipes; returns the pid."""
    kw: dict[str, Any] = dict.fromkeys(("stdin", "stdout", "stderr"), subprocess.DEVNULL)
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(list(cmd), **kw).pid


def send(argv: Sequence[str], folder: pathlib.Path) -> int:
    pending = running(folder)
    if pending and procs.pid_alive(read_pid(pending.with_suffix(".pid"))):
        print(f"{pending.name} is still running: python -m framegate.replay.chat --wait")
        return 2
    claude_exe()  # fail here, not in the detached call
    turn = next_turn(folder)
    pid = detach([sys.executable, "-m", "framegate.replay.chat", *argv, "--turn", turn.name])
    turn.with_suffix(".pid").write_text(str(pid), encoding="utf-8")
    print(f"{turn.name} started. Its answer: python -m framegate.replay.chat --wait")
    return 0


def work(args: argparse.Namespace, message: str, turn: pathlib.Path) -> int:
    """The detached call itself: claude to its end, then the turn file (whole or not at all)."""
    try:
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
        text = r.stdout
        json.loads(text)
    except (OSError, ValueError) as e:
        output = "" if isinstance(e, OSError) else f"(exit {r.returncode})\n{r.stdout}\n{r.stderr}"
        text = json.dumps({"is_error": True, "result": f"no JSON answer: {e} {output}".strip()})
    tmp = turn.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, turn)
    turn.with_suffix(".pid").unlink(missing_ok=True)
    return 0


def show(turn: pathlib.Path) -> int:
    data = json.loads(turn.read_text(encoding="utf-8"))
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


def wait(folder: pathlib.Path, seconds: float, poll: float = 2.0) -> int:
    """Print the answer of the running call, or of the last one when none is running."""
    turn = running(folder)
    if turn is None:
        done = sorted(folder.glob("turn-*.json"), key=_number)
        if not done:
            print("no chat call yet")
            return 2
        return show(done[-1])
    mark, end = turn.with_suffix(".pid"), time.monotonic() + seconds
    while not turn.is_file():
        if not procs.pid_alive(read_pid(mark)) and not turn.is_file():
            mark.unlink(missing_ok=True)
            print(f"{turn.name}: the call ended without an answer. Send the message again.")
            return 1
        if time.monotonic() >= end:
            print(f"{turn.name} is still running: python -m framegate.replay.chat --wait")
            return STILL_RUNNING
        time.sleep(poll)
    return show(turn)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="python -m framegate.replay.chat")
    ap.add_argument("message", nargs="?")
    ap.add_argument("--file", help="send this file's text")
    ap.add_argument("--resume", default="", help="session id of the conversation")
    ap.add_argument("--allow", action="append", default=[], help="one more allowed tool pattern")
    ap.add_argument("--sandbox", default="../sandbox")
    ap.add_argument("--wait", nargs="?", type=float, const=WAIT_SECONDS, metavar="SECONDS")
    ap.add_argument("--turn", help=argparse.SUPPRESS)  # the detached call: its turn file
    args = ap.parse_args(argv)
    folder = pathlib.Path.cwd()
    if args.wait is not None and not (args.message or args.file):
        return wait(folder, args.wait)
    if bool(args.message) == bool(args.file) or args.wait is not None:
        ap.error("give the message or --file, or --wait alone")
    message = args.message or pathlib.Path(args.file).read_text(encoding="utf-8").strip()
    return work(args, message, folder / args.turn) if args.turn else send(argv, folder)


if __name__ == "__main__":
    sys.exit(main())
