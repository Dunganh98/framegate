"""`framegate run` that outlives the shell that started it. A replay's chat is one headless
`claude -p --resume` per turn, and the next turn kills the background tasks of the one before:
the run with them. With FRAMEGATE_DETACH_RUN=1 (set by the replay) `framegate run` starts
itself detached and returns once that run holds the run lock."""

from __future__ import annotations

import os
import sys
import time
from typing import TYPE_CHECKING

from framegate import procs

if TYPE_CHECKING:
    from framegate.orchestrator import Orchestrator

ENV = "FRAMEGATE_DETACH_RUN"
#: in out/<f>/: the detached run's output
RUN_LOG = "run.log"
#: how long the caller waits for the detached run to take the run lock
LOCK_WAIT = 60.0


def wanted() -> bool:
    return os.environ.get(ENV) == "1"


def run(orch: Orchestrator, feature: str, wait: float = LOCK_WAIT, poll: float = 0.5) -> int | None:
    """0 once the detached run holds the lock; its exit code (output printed) when it ended
    first (a gate, an error). None: a run is active, the caller runs as usual and says so."""
    if procs.lock_alive(procs.read_lock(orch.cfg.out_dir)):
        return None
    log = orch.fdir(feature) / RUN_LOG
    start = log.stat().st_size if log.is_file() else 0
    env = {k: v for k, v in os.environ.items() if k != ENV}  # the child runs, never detaches
    env["PYTHONUNBUFFERED"] = "1"
    with open(log, "ab") as out:
        proc = procs.detach([sys.executable, "-m", "framegate", "run", feature], out, env=env)
    end, pid = time.monotonic() + wait, proc.pid
    while proc.poll() is None and time.monotonic() < end:
        lock = procs.read_lock(orch.cfg.out_dir) or {}
        if procs.lock_alive(lock) and lock.get("feature") == feature:
            pid = int(lock["pid"])
            break
        time.sleep(poll)
    if proc.poll() is not None:
        with open(log, "rb") as fh:
            fh.seek(start)
            print(fh.read().decode("utf-8", "replace"), end="")
        return int(proc.returncode)
    print(
        f"framegate run {feature} runs detached (pid {pid}; output: {log}). Follow it: "
        f"framegate watch {feature}; its state: framegate status {feature} --brief"
    )
    return 0
