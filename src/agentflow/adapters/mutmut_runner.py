"""Standalone mutmut runner (stdlib only).

The Python adapter copies this file to `<out>/.mutmut/runner.py` and runs it with
`<python> runner.py <spec.json>`: natively with the project interpreter, or inside WSL with the
distribution's `python3` (mutmut needs fork, so it can't run on native Windows). It never imports
agentflow, so it works in an interpreter that doesn't have agentflow installed.

spec.json keys:
  project            directory to run mutmut in (paths as the target interpreter sees them)
  result             where to write the result JSON
  patterns           mutant-name globs for `mutmut run` (e.g. "demo.x*"); [] = all mutants
  show_modules       modules whose surviving mutants get a `mutmut show` diff (max show_limit)
  show_limit         max number of diffs (default 50)
  timeout            seconds for `mutmut run` (default 7200)
  keep_mutants       keep the `mutants/` copy (default: deleted before and after the run)
  venv               optional: virtualenv to create/reuse and run mutmut in (`~` is expanded)
  stamp              venv content key; the venv is rebuilt when it changes
  requirements       requirement strings installed into the venv
  requirement_files  `-r` files installed into the venv

result.json keys: ok, stage ("setup" | "run"), error, hint, setup_log, venv_reused, python,
run_code, run_output, results (`mutmut results --all true`), shows {name: diff}, seconds.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time

STAMP_FILE = ".agentflow-stamp"
INSTALL_HINT = (
    "install uv once inside WSL: curl -LsSf https://astral.sh/uv/install.sh | sh "
    "(or: sudo apt install python3-venv)"
)
SURVIVOR_STATUSES = ("survived", "no tests", "suspicious")
LINE = re.compile(r"^\s*(\S+__mutmut_\d+):\s*(.+?)\s*$")


def _run(cmd: list[str], cwd: str | None = None, timeout: float | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        return 127, f"not found: {e}"
    except subprocess.TimeoutExpired as e:
        out = e.output.decode("utf-8", "replace") if isinstance(e.output, bytes) else ""
        return 124, f"TIMEOUT after {timeout} s\n{out}"
    return r.returncode, r.stdout.decode("utf-8", "replace")


def _tail(text: str, lines: int = 40) -> str:
    """Last lines of a console log; progress spinners (\\r) are collapsed."""
    parts = [p.rstrip() for p in re.split(r"[\r\n]+", text) if p.strip()]
    return "\n".join(parts[-lines:])


def _find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    for cand in ("~/.local/bin/uv", "~/.cargo/bin/uv"):
        path = os.path.expanduser(cand)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def venv_python(venv: str) -> str:
    return os.path.join(venv, "bin", "python")


def ensure_venv(spec: dict) -> tuple[bool, bool, str]:
    """Create or reuse the venv. Returns (ok, reused, log)."""
    venv = os.path.expanduser(spec["venv"])
    stamp = str(spec.get("stamp", ""))
    stamp_path = os.path.join(venv, STAMP_FILE)
    py = venv_python(venv)
    if os.path.isfile(py) and os.path.isfile(stamp_path):
        with open(stamp_path, encoding="utf-8") as fh:
            if fh.read().strip() == stamp:
                return True, True, f"reusing {venv}"
    shutil.rmtree(venv, ignore_errors=True)
    os.makedirs(os.path.dirname(venv), exist_ok=True)
    reqs = list(spec.get("requirements", []))
    files: list[str] = []
    for f in spec.get("requirement_files", []):
        files += ["-r", f]
    log = []
    uv = _find_uv()
    if uv:
        steps = [
            [uv, "venv", "-q", "--python", sys.executable, venv],
            [uv, "pip", "install", "-q", "--python", py, *reqs, *files],
        ]
    else:
        steps = [
            [sys.executable, "-m", "venv", venv],
            [py, "-m", "pip", "install", "-q", "--disable-pip-version-check", *reqs, *files],
        ]
    for cmd in steps:
        code, out = _run(cmd, timeout=1800)
        log.append(f"$ {' '.join(cmd)}\n{_tail(out, 30)}")
        if code != 0:
            shutil.rmtree(venv, ignore_errors=True)
            return False, False, "\n".join(log)
    with open(stamp_path, "w", encoding="utf-8") as fh:
        fh.write(stamp)
    return True, False, "\n".join(log)


def run_mutmut(spec: dict, python: str) -> dict:
    project = spec["project"]
    mutants = os.path.join(project, "mutants")
    shutil.rmtree(mutants, ignore_errors=True)
    patterns = list(spec.get("patterns", []))
    try:
        code, out = _run(
            [python, "-m", "mutmut", "run", *patterns],
            cwd=project,
            timeout=float(spec.get("timeout", 7200)),
        )
        _, results = _run([python, "-m", "mutmut", "results", "--all", "true"], cwd=project)
        modules = set(spec.get("show_modules", []))
        shows: dict[str, str] = {}
        limit = int(spec.get("show_limit", 50))
        for line in results.splitlines():
            m = LINE.match(line)
            if not m or m.group(2) not in SURVIVOR_STATUSES:
                continue
            if m.group(1).rsplit(".", 1)[0] not in modules or len(shows) >= limit:
                continue
            _, diff = _run([python, "-m", "mutmut", "show", m.group(1)], cwd=project)
            shows[m.group(1)] = diff.strip()
    finally:
        if not spec.get("keep_mutants"):
            shutil.rmtree(mutants, ignore_errors=True)
    return {
        "ok": True,
        "stage": "run",
        "run_code": code,
        "run_output": _tail(out),
        "results": results,
        "shows": shows,
    }


def write_result(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def main(argv: list[str]) -> int:
    spec_path = argv[0]
    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    t0 = time.time()
    result: dict = {"python": sys.executable}
    python = sys.executable
    if spec.get("venv"):
        ok, reused, log = ensure_venv(spec)
        result.update(setup_log=log, venv_reused=reused)
        if not ok:
            result.update(
                ok=False, stage="setup", error="could not create the mutmut venv", hint=INSTALL_HINT
            )
            result["seconds"] = round(time.time() - t0, 1)
            write_result(spec["result"], result)
            return 0
        python = venv_python(os.path.expanduser(spec["venv"]))
        result["python"] = python
    result.update(run_mutmut(spec, python))
    result["seconds"] = round(time.time() - t0, 1)
    write_result(spec["result"], result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
