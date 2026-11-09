"""Child processes that never outlive the orchestrator, and the orchestrator's run lock.

- `popen` starts a child in its own process group (Ctrl+C reaches only agentflow, which then
  kills the child's tree). With job=True (agent runs), on Windows also inside a job object
  with KILL_ON_JOB_CLOSE: when agentflow dies for any reason, even TerminateProcess /
  `taskkill /F`, Windows kills every process in the job (claude, node, shells, the agent's
  dotnet/pytest). POSIX: a new session, killed with `killpg`; a SIGKILL of agentflow itself
  can't be caught there.
- `run` / `wait` poll in short sleeps with pipe-reader threads, so Ctrl+C / SIGTERM are
  handled while a child runs (CPython's communicate/wait can hold Ctrl+C on Windows until the
  child exits).
- `run_lock` writes `<out>/.run.lock` (pid, feature, start time) for `agentflow status` /
  `agentflow unlock` to tell a running orchestrator from a stale `.phase`.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from types import FrameType
from typing import Any

LOCK_FILE = ".run.lock"
SLICE = 0.25  # seconds between signal checks while waiting for a child


class Interrupted(KeyboardInterrupt):
    """agentflow was asked to stop (Ctrl+C, Ctrl+Break, SIGTERM)."""

    def __init__(self, how: str = "SIGINT") -> None:
        super().__init__(how)
        self.how = how


def interrupt_name(exc: BaseException) -> str:
    return exc.how if isinstance(exc, Interrupted) else "SIGINT"


# ---------- Windows job object ----------
_job: int | None = None


def _win_job() -> int | None:
    """A process-wide job object with KILL_ON_JOB_CLOSE (None if it can't be created)."""
    global _job
    if _job is not None or sys.platform != "win32":
        return _job
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return None
    _job = int(job)
    return _job


def _assign_and_resume(pid: int) -> bool:
    """Put a process created with CREATE_SUSPENDED into the job, then resume it. Resuming
    only after the assignment matters: launchers (`claude.cmd` -> cmd.exe, venv trampolines)
    start their real child within milliseconds, and a child started before the assignment
    would not be in the job. Returns False if the process could not be resumed."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    # PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME
    handle = k32.OpenProcess(0x0101 | 0x0800, False, pid)
    if not handle:
        return False
    try:
        job = _win_job()
        if job is not None:
            k32.AssignProcessToJobObject(job, handle)
        return ntdll.NtResumeProcess(handle) == 0
    finally:
        k32.CloseHandle(handle)


# ---------- children ----------
def popen(cmd: Sequence[str], job: bool = False, **kw: Any) -> subprocess.Popen[str]:
    """subprocess.Popen in a new process group. job=True (Windows): inside the kill-on-close
    job, so the tree dies with agentflow even when agentflow is killed hard. Used for agent
    runs; gate commands stay outside so build servers (MSBuild, Roslyn) can stay warm."""
    win_job = job and sys.platform == "win32"
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | (0x4 if win_job else 0)  # CREATE_SUSPENDED
        kw["creationflags"] = kw.get("creationflags", 0) | flags
    else:
        kw.setdefault("start_new_session", True)
    proc: subprocess.Popen[str] = subprocess.Popen(list(cmd), **kw)
    if win_job and not _assign_and_resume(proc.pid):
        proc.kill()
        raise OSError(f"could not start {cmd[0]} inside the agentflow job object")
    return proc


def kill_tree(proc: subprocess.Popen[Any]) -> None:
    """Kill a child and everything it started."""
    if proc.poll() is not None:  # gone; its pid may already belong to someone else
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            with contextlib.suppress(OSError):
                proc.kill()


class _Pump:
    """Feeds stdin and drains stdout/stderr of a child on daemon threads, so the main thread
    only polls and sleeps. (CPython's Popen.communicate/wait handle KeyboardInterrupt with an
    extra wait whose timeout can go negative = INFINITE on Windows: Ctrl+C was held until
    the child exited.)"""

    def __init__(self, proc: subprocess.Popen[str], input: str | None) -> None:
        self.out: list[str] = []
        self.err: list[str] = []
        self.threads: list[threading.Thread] = []
        for stream, sink in ((proc.stdout, self.out), (proc.stderr, self.err)):
            if stream is not None:
                self._start(self._drain, stream, sink)
        if proc.stdin is not None:
            self._start(self._feed, proc.stdin, input or "")

    def _start(self, target: Any, *args: Any) -> None:
        th = threading.Thread(target=target, args=args, daemon=True)
        th.start()
        self.threads.append(th)

    @staticmethod
    def _drain(stream: Any, sink: list[str]) -> None:
        with contextlib.suppress(OSError, ValueError):
            for chunk in iter(lambda: stream.read(4096), ""):
                sink.append(chunk)

    @staticmethod
    def _feed(stream: Any, text: str) -> None:
        with contextlib.suppress(OSError, ValueError):
            if text:
                stream.write(text)
        with contextlib.suppress(OSError, ValueError):
            stream.close()

    def collect(self, grace: float) -> tuple[str, str]:
        """Output so far, after waiting up to `grace` s for the pipes to close (a grandchild
        that inherited them can keep them open after the child exited)."""
        deadline = time.monotonic() + grace
        for th in self.threads:
            th.join(max(0.0, deadline - time.monotonic()))
        return "".join(self.out), "".join(self.err)


def wait(proc: subprocess.Popen[Any], timeout: float | None = None) -> int:
    """Wait for the child in short sleeps (Ctrl+C / SIGTERM handled at once). Raises
    subprocess.TimeoutExpired after `timeout` seconds."""
    deadline = None if timeout is None else time.monotonic() + timeout
    nap = 0.005  # short commands return fast; long ones are checked every SLICE
    while True:
        code = proc.poll()
        if code is not None:
            return code
        if deadline is not None and time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(proc.args, timeout or 0)
        time.sleep(nap)
        nap = min(SLICE, nap * 2)


def run(
    cmd: Sequence[str],
    input: str | None = None,
    timeout: float | None = None,
    job: bool = False,
    **kw: Any,
) -> tuple[int, str, str]:
    """Run a child to completion (stdout/stderr as text). Its whole tree is killed when the
    wait is interrupted (Ctrl+C, SIGTERM) and the exception re-raised; a timeout raises
    subprocess.TimeoutExpired (with the partial output) after the kill."""
    proc = popen(
        cmd,
        job=job,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kw,
    )
    pump = _Pump(proc, input)
    try:
        code = wait(proc, timeout)
    except BaseException as e:
        kill_tree(proc)
        out, err = pump.collect(10)
        if isinstance(e, subprocess.TimeoutExpired):
            raise subprocess.TimeoutExpired(cmd, e.timeout, output=out, stderr=err) from None
        raise
    out, err = pump.collect(30)
    return code, out, err


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: exists
        try:
            code = wintypes.DWORD()
            ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------- signals ----------
@contextlib.contextmanager
def stop_signals() -> Iterator[None]:
    """SIGTERM (and SIGBREAK = Ctrl+Break on Windows) raise Interrupted like Ctrl+C does."""
    names = ["SIGTERM"] + (["SIGBREAK"] if sys.platform == "win32" else [])
    old: dict[int, Any] = {}

    def handler(signum: int, frame: FrameType | None) -> None:
        raise Interrupted(signal.Signals(signum).name)

    for name in names:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            old[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):  # not the main thread
            pass
    try:
        yield
    finally:
        for sig, h in old.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, h)


# ---------- run lock ----------
def lock_path(out_dir: pathlib.Path) -> pathlib.Path:
    return out_dir / LOCK_FILE


def read_lock(out_dir: pathlib.Path) -> dict[str, Any] | None:
    p = lock_path(out_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"pid": 0}
    return data if isinstance(data, dict) else {"pid": 0}


def lock_alive(lock: dict[str, Any] | None) -> bool:
    return bool(lock) and pid_alive(int((lock or {}).get("pid") or 0))


def write_lock(out_dir: pathlib.Path, feature: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "pid": os.getpid(),
        "feature": feature,
        "started": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    lock_path(out_dir).write_text(json.dumps(data), encoding="utf-8")


def remove_lock(out_dir: pathlib.Path, force: bool = False) -> None:
    """Remove the lock if it is ours (or unreadable); force: whoever holds it."""
    lock = read_lock(out_dir)
    if lock is not None and (force or int(lock.get("pid") or 0) in (0, os.getpid())):
        lock_path(out_dir).unlink(missing_ok=True)
