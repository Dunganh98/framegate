"""procs: child process trees die with framegate (timeout, Ctrl+C/SIGTERM, hard kill on Windows),
the run lock, and stop signals."""

from __future__ import annotations

import _thread
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

import pytest

from framegate import procs

# a child that starts a grandchild sleeper, writes the grandchild's pid, then sleeps
PARENT = (
    "import json, subprocess, sys, time\n"
    "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "open(sys.argv[1] + '.tmp', 'w').write(json.dumps({'child': c.pid}))\n"
    "import os; os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
    "time.sleep(120)\n"
)


def wait_for(pred: object, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if pred():  # type: ignore[operator]
            return True
        time.sleep(0.1)
    return False


def read_pid(path: pathlib.Path, key: str = "child") -> int:
    assert wait_for(path.is_file), "the child never wrote its pid file"
    return int(json.loads(path.read_text(encoding="utf-8"))[key])


def test_pid_alive() -> None:
    assert procs.pid_alive(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert not procs.pid_alive(p.pid) and not procs.pid_alive(0)


def test_run_timeout_kills_the_tree(tmp_path: pathlib.Path) -> None:
    pid_file = tmp_path / "pid.json"
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        procs.run([sys.executable, "-c", PARENT, str(pid_file)], timeout=5)
    assert time.monotonic() - t0 < 60
    child = read_pid(pid_file)
    assert wait_for(lambda: not procs.pid_alive(child)), "grandchild survived the timeout"


def test_run_command_timeout_reports_124(tmp_path: pathlib.Path) -> None:
    from framegate.adapters.base import TIMEOUT_CODE, run_command

    code, out = run_command(
        [sys.executable, "-c", "print('partial', flush=True); import time; time.sleep(60)"],
        tmp_path,
        timeout=3,
    )
    assert code == TIMEOUT_CODE and "partial" in out and "did not finish within 3 s" in out


@pytest.mark.parametrize("job", [False, True])
def test_interrupt_kills_the_tree(tmp_path: pathlib.Path, job: bool) -> None:
    pid_file = tmp_path / "pid.json"

    def interrupt() -> None:
        if wait_for(pid_file.is_file):
            _thread.interrupt_main()

    threading.Thread(target=interrupt, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        procs.run([sys.executable, "-c", PARENT, str(pid_file)], job=job)
    child = read_pid(pid_file)
    assert wait_for(lambda: not procs.pid_alive(child)), "grandchild survived Ctrl+C"


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows-only")
def test_hard_kill_of_framegate_kills_its_children(tmp_path: pathlib.Path) -> None:
    """A process that started a child with procs.popen is killed with TerminateProcess
    (taskkill /F, no /T): the kill-on-close job takes the child down too."""
    pid_file = tmp_path / "pid.json"
    script = (
        "import json, os, sys, time\n"
        "from framegate import procs\n"
        "c = procs.popen([sys.executable, '-c', 'import time; time.sleep(120)'], job=True)\n"
        "open(sys.argv[1] + '.tmp', 'w').write(json.dumps({'child': c.pid, 'me': os.getpid()}))\n"
        "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
        "time.sleep(120)\n"
    )
    holder = subprocess.Popen([sys.executable, "-c", script, str(pid_file)])
    try:
        child, me = read_pid(pid_file), read_pid(pid_file, "me")
        assert procs.pid_alive(child)
        subprocess.run(["taskkill", "/F", "/PID", str(me)], capture_output=True, check=False)
        assert wait_for(lambda: not procs.pid_alive(child)), "child outlived a hard-killed parent"
    finally:
        procs.kill_tree(holder)


def test_stop_signals_raise_interrupted() -> None:
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(procs.Interrupted) as exc, procs.stop_signals():
        signal.raise_signal(signal.SIGTERM)
        time.sleep(2)
    assert exc.value.how == "SIGTERM" and procs.interrupt_name(exc.value) == "SIGTERM"
    assert procs.interrupt_name(KeyboardInterrupt()) == "SIGINT"
    assert signal.getsignal(signal.SIGTERM) == before


def test_run_lock(tmp_path: pathlib.Path) -> None:
    assert procs.read_lock(tmp_path) is None and not procs.lock_alive(None)
    procs.write_lock(tmp_path, "demo")
    lock = procs.read_lock(tmp_path)
    assert lock is not None and lock["pid"] == os.getpid() and lock["feature"] == "demo"
    assert procs.lock_alive(lock)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (tmp_path / procs.LOCK_FILE).write_text(json.dumps({"pid": dead.pid}), encoding="utf-8")
    assert not procs.lock_alive(procs.read_lock(tmp_path))
    procs.remove_lock(tmp_path)  # not ours: kept
    assert procs.read_lock(tmp_path) is not None
    procs.remove_lock(tmp_path, force=True)
    assert procs.read_lock(tmp_path) is None
    (tmp_path / procs.LOCK_FILE).write_text("{broken", encoding="utf-8")
    assert procs.read_lock(tmp_path) == {"pid": 0}
