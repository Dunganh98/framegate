"""Standalone mutmut runner (stdlib only).

The Python adapter copies this file to `<out>/.mutmut/runner.py` and runs it with
`<python> runner.py <spec.json>`: natively with the project interpreter, or inside WSL with the
distribution's `python3` (mutmut needs fork, so it can't run on native Windows). It never imports
framegate, so it works in an interpreter that doesn't have framegate installed.

spec.json keys:
  project            directory to run mutmut in (paths as the target interpreter sees them)
  result             where to write the result JSON
  patterns           mutant-name globs for `mutmut run` (e.g. "demo.x*"); [] = all mutants
  show_modules       modules whose surviving mutants get a `mutmut show` diff (max show_limit)
  show_functions     the same for single functions (mangled names, e.g. "demo.x_add")
  show_limit         max number of diffs (default 50)
  timeout            seconds for `mutmut run` (default 7200)
  keep_mutants       keep the `mutants/` copy (default: deleted before and after the run)
  venv               optional: virtualenv to create/reuse and run mutmut in (`~` is expanded)
  stamp              venv content key; the venv is rebuilt when it changes
  requirements       requirement strings installed into the venv
  requirement_files  `-r` files installed into the venv
  pytest_addopts     extra pytest options for every test run of mutmut (PYTEST_ADDOPTS), e.g.
                     the feature scope `--req ID,ID --req-paths tests/review`
  pythonpath         a folder put on PYTHONPATH (framegate's pytest plugin as a file)
  also_copy          files (relative to project) copied into `mutants/` before the run: what
                     the tests read outside the source folders
  source_paths       the folders to mutate; written to a [mutmut] section of setup.cfg for
                     the run (and taken out after) unless the project configures mutmut
  line_scopes        {module: {"file": <source file>, "functions": {<mangled name>:
                     {"changed": [lines] | null, "logging": [lines]}}}}: which lines of a
                     function count (0 = its `def` line; null = all). A mutant on other lines
                     only, or inside a logging call only, is left out of the score.

The runner's test runs also load a pytest plugin of its own (HYPOTHESIS_PLUGIN), which
switches off hypothesis's differing-executors health check for these runs only.

result.json keys: ok, stage ("setup" | "run"), error, hint, setup_log, venv_reused, python,
run_code, run_output, results (`mutmut results --all true`), shows {name: diff}, seconds,
left_out {mutant name: "logging" | "unchanged"} (from line_scopes; mutmut's results name no
line, so the lines come from its mutants/ copy of the file: every mutant is a copy of the
function there).
"""

from __future__ import annotations

import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time

STAMP_FILE = ".framegate-stamp"
INSTALL_HINT = (
    "install uv once inside WSL: curl -LsSf https://astral.sh/uv/install.sh | sh "
    "(or: sudo apt install python3-venv)"
)
SURVIVOR_STATUSES = ("survived", "no tests", "suspicious")
#: an `also_copy` file larger than this stays out of mutmut's folder (data, not a test input)
COPY_MAX_BYTES = 5_000_000
LINE = re.compile(r"^\s*(\S+__mutmut_\d+):\s*(.+?)\s*$")
#: a pytest plugin for the runner's own test runs only (`-p`, written beside the spec)
HYPOTHESIS_PLUGIN = "framegate_mutation_hypothesis"
#: mutmut runs the tests several times in one process; hypothesis takes a test method called
#: with a new instance each time for a test defect (health check "differing executors")
HYPOTHESIS_PLUGIN_TEXT = '''\
"""Loaded by framegate's mutation runner only: hypothesis's health check for differing
executors is off for these runs (the tool repeats the tests in one process, not the test)."""
try:
    from hypothesis import HealthCheck, settings
except ImportError:  # a project without hypothesis
    settings = None


def pytest_sessionstart(session):
    check = getattr(HealthCheck, "differing_executors", None) if settings else None
    if check is None:
        return
    now = settings()  # the profile the project loaded, if any
    off = [c for c in now.suppress_health_check if c is not check] + [check]
    settings.register_profile("framegate-mutation", parent=now, suppress_health_check=off)
    settings.load_profile("framegate-mutation")
'''


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


def own_config(project: str) -> bool:
    """Does the project configure mutmut itself ([tool.mutmut] or setup.cfg [mutmut])?"""
    for name, head in (("pyproject.toml", "[tool.mutmut]"), ("setup.cfg", "[mutmut]")):
        try:
            with open(os.path.join(project, name), encoding="utf-8") as fh:
                if any(line.strip() == head for line in fh):
                    return True
        except OSError:
            pass
    return False


def add_source_paths(project: str, paths: list) -> str | None:
    """Write `paths` into setup.cfg for mutmut; returns setup.cfg's old text ('' = there was
    none), None when nothing was written."""
    if not paths or own_config(project):
        return None
    cfg = os.path.join(project, "setup.cfg")
    old = ""
    if os.path.isfile(cfg):
        with open(cfg, encoding="utf-8") as fh:
            old = fh.read()
    sep = "\n" if old and not old.endswith("\n") else ""
    body = "".join(f"    {p}\n" for p in paths)
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(f"{old}{sep}\n[mutmut]\nsource_paths =\n{body}")
    return old


def restore_setup_cfg(project: str, old: str | None) -> None:
    if old is None:
        return
    cfg = os.path.join(project, "setup.cfg")
    if old:
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write(old)
    else:
        os.remove(cfg)


#: the trial's first look: mutmut's config loads and every folder it would mutate exists
TRIAL = (
    "from mutmut.configuration import config\n"
    "paths = config().source_paths\n"
    "missing = [str(p) for p in paths if not p.exists()]\n"
    "assert not missing, f'source folders not found: {missing}'\n"
    "print('mutmut mutates:', ' '.join(str(p) for p in paths))\n"
)


#: the trial's second look: collect the tests (nothing runs) and compare the names the code
#: is imported under with the names mutmut gives it (the path from the root, `src.` dropped).
#: Seen live: a conftest put orchestrator/ on sys.path, tests imported `controller.cli`,
#: mutmut expected `orchestrator.controller.cli`, and the run stopped after minutes.
IMPORTS = r"""
import os, sys
import pytest
root, paths = os.getcwd(), [p.rstrip("/") for p in sys.argv[1:]]
sys.path.insert(0, root)
code = int(pytest.main(["--collect-only", "-q", "-p", "no:cacheprovider"]))
bad = set()
for name, mod in list(sys.modules.items()):
    path = getattr(mod, "__file__", None) or ""
    rel = os.path.relpath(path, root).replace(os.sep, "/") if path else ".."
    if rel.startswith("..") or not rel.endswith(".py"):
        continue
    if not any(rel.startswith(p + "/") for p in paths if p not in ("", ".")):
        continue
    want = rel[:-3].replace("/", ".").removesuffix(".__init__")
    want = want[4:] if want.startswith("src.") else want
    if name != want:
        bad.add(f"{name} (mutmut names it {want})")
if bad:
    print("FRAMEGATE: the tests import the code under other names than mutmut gives it: "
          + ", ".join(sorted(bad)[:3]) + ". mutmut cannot run on this project as it is.")
    sys.exit(3)
if code not in (0, 5):
    print(f"FRAMEGATE: pytest cannot collect the tests here (exit {code})")
    sys.exit(code)
print("FRAMEGATE: the tests import the code as mutmut names it")
"""


def trial_mutmut(spec: dict, python: str) -> dict:
    """The setup's trial (spec `trial`): mutmut finds the code, pytest loads framegate's
    plugin (`--req`) and collects the tests under the module names mutmut uses. No test and
    no mutant runs: seconds, not minutes."""
    project = spec["project"]
    old_cfg = add_source_paths(project, list(spec.get("source_paths", [])))
    try:
        code, out = _run([python, "-c", TRIAL], cwd=project, timeout=60)
        if code == 0:
            hcode, help_text = _run([python, "-m", "pytest", "--help"], cwd=project, timeout=60)
            if hcode != 0 or "--req" not in help_text:
                code = hcode or 1
                out = "pytest does not load framegate's plugin (--req unknown):\n" + _tail(
                    help_text
                )
        if code == 0:
            paths = [str(p) for p in spec.get("source_paths", [])]
            icode, found = _run([python, "-c", IMPORTS, *paths], cwd=project, timeout=180)
            said = [x for x in found.splitlines() if x.startswith("FRAMEGATE: ")]
            verdict = said[-1].removeprefix("FRAMEGATE: ") if said else _tail(found, 15)
            out, code = f"{out.strip()}\n{verdict}", icode
    finally:
        restore_setup_cfg(project, old_cfg)
    return {"ok": True, "stage": "trial", "run_code": code, "run_output": _tail(out)}


def mutant_lines(text: str) -> dict[str, list[int]]:
    """mutmut's copy of a source file -> {mutant function: the lines of the original
    function it changes, 0 = the `def` line}."""
    lines = text.splitlines()
    funcs = {
        n.name: lines[n.lineno - 1 : n.end_lineno]
        for n in ast.walk(ast.parse(text))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and "__mutmut_" in n.name
    }
    out: dict[str, list[int]] = {}
    for name, body in funcs.items():
        base, _, n = name.rpartition("__mutmut_")
        orig = funcs.get(base + "__mutmut_orig")
        if not n.isdigit() or orig is None or not body:
            continue
        body = [body[0].replace(name, base + "__mutmut_orig", 1), *body[1:]]
        ops = difflib.SequenceMatcher(None, orig, body, autojunk=False).get_opcodes()
        out[name] = sorted(
            {i for tag, i1, i2, _, _ in ops if tag != "equal" for i in range(i1, max(i2, i1 + 1))}
        )
    return out


def left_out(scopes: dict, mutants: str) -> dict[str, str]:
    """{mutant name: why it is not scored} (spec `line_scopes`); a mutant whose lines
    cannot be told is scored."""
    out: dict[str, str] = {}
    for module, info in scopes.items():
        try:
            with open(os.path.join(mutants, info["file"]), encoding="utf-8") as fh:
                found = mutant_lines(fh.read())
        except (OSError, SyntaxError, ValueError, KeyError):
            continue
        for name, lines in found.items():
            scope = info.get("functions", {}).get(name.rsplit("__mutmut_", 1)[0])
            if not scope or not lines:
                continue
            changed = scope.get("changed")
            if all(n in scope.get("logging", []) for n in lines):
                out[f"{module}.{name}"] = "logging"
            elif changed is not None and not any(n in changed for n in lines):
                out[f"{module}.{name}"] = "unchanged"
    return out


def run_mutmut(spec: dict, python: str) -> dict:
    project = spec["project"]
    mutants = os.path.join(project, "mutants")
    shutil.rmtree(mutants, ignore_errors=True)
    patterns = list(spec.get("patterns", []))
    old_cfg = add_source_paths(project, list(spec.get("source_paths", [])))
    try:
        for rel in spec.get("also_copy", []):
            src, dst = os.path.join(project, rel), os.path.join(mutants, rel)
            if os.path.isfile(src) and os.path.getsize(src) <= COPY_MAX_BYTES:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        code, out = _run(
            [python, "-m", "mutmut", "run", *patterns],
            cwd=project,
            timeout=float(spec.get("timeout", 7200)),
        )
        _, results = _run([python, "-m", "mutmut", "results", "--all", "true"], cwd=project)
        skipped = left_out(spec.get("line_scopes") or {}, mutants)
        modules = set(spec.get("show_modules", []))
        functions = set(spec.get("show_functions", []))
        shows: dict[str, str] = {}
        limit = int(spec.get("show_limit", 50))
        for line in results.splitlines():
            m = LINE.match(line)
            if not m or m.group(2) not in SURVIVOR_STATUSES or len(shows) >= limit:
                continue
            name = m.group(1)
            if name in skipped:
                continue
            if (
                name.rsplit(".", 1)[0] not in modules
                and name.rsplit("__mutmut_", 1)[0] not in functions
            ):
                continue
            _, diff = _run([python, "-m", "mutmut", "show", m.group(1)], cwd=project)
            shows[m.group(1)] = diff.strip()
    finally:
        restore_setup_cfg(project, old_cfg)
        if not spec.get("keep_mutants"):
            shutil.rmtree(mutants, ignore_errors=True)
    return {
        "ok": True,
        "stage": "run",
        "run_code": code,
        "run_output": _tail(out),
        "results": results,
        "shows": shows,
        "left_out": skipped,
    }


def load_hypothesis_plugin(folder: str) -> None:
    """Every pytest run started from here loads HYPOTHESIS_PLUGIN (PYTEST_ADDOPTS)."""
    with open(os.path.join(folder, HYPOTHESIS_PLUGIN + ".py"), "w", encoding="utf-8") as fh:
        fh.write(HYPOTHESIS_PLUGIN_TEXT)
    rest = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = folder + (os.pathsep + rest if rest else "")
    extra = os.environ.get("PYTEST_ADDOPTS", "")
    os.environ["PYTEST_ADDOPTS"] = f"{extra} -p {HYPOTHESIS_PLUGIN}".strip()


def write_result(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def main(argv: list[str]) -> int:
    spec_path = argv[0]
    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    t0 = time.time()
    if spec.get("pytest_addopts"):
        extra = os.environ.get("PYTEST_ADDOPTS", "")
        os.environ["PYTEST_ADDOPTS"] = (extra + " " + spec["pytest_addopts"]).strip()
    if spec.get("pythonpath"):
        rest = os.environ.get("PYTHONPATH", "")
        os.environ["PYTHONPATH"] = spec["pythonpath"] + (os.pathsep + rest if rest else "")
    load_hypothesis_plugin(os.path.dirname(os.path.abspath(spec_path)))
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
    result.update((trial_mutmut if spec.get("trial") else run_mutmut)(spec, python))
    result["seconds"] = round(time.time() - t0, 1)
    write_result(spec["result"], result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
