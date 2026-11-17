"""The run folder of one candidate.

  <out>/runs/<name>/sandbox/   clone cut at the parent commit, no remote, its own .venv
  <out>/runs/<name>/driver/    the driver agent's files (task.md, turns, log, report)
  <out>/answers/<name>/        the hidden answer: the commit's tests, patch and message, and
                               which tests prove the task. No agent may read it.

The mechanical check: the commit's tests run on the parent and on the commit. A test that
fails on the parent (or cannot be collected there) and passes on the commit proves the task.
A candidate without such a test is dropped.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import pathlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from framegate.replay.candidates import Candidate
from framegate.replay.guard import BLOCKED

UPLOAD = "git -c uploadpack.allowAnySHA1InWant=true upload-pack"
SKIP = shutil.ignore_patterns(
    ".venv", ".git", "out", "__pycache__", ".pytest_cache", ".ruff_cache", ".hypothesis"
)
TEST_TIMEOUT = 1800


class ReplayError(Exception):
    pass


@dataclass(frozen=True)
class Layout:
    out: pathlib.Path
    name: str

    @property
    def run(self) -> pathlib.Path:
        return self.out / "runs" / self.name

    @property
    def sandbox(self) -> pathlib.Path:
        return self.run / "sandbox"

    @property
    def driver(self) -> pathlib.Path:
        return self.run / "driver"

    @property
    def answer(self) -> pathlib.Path:
        return self.out / "answers" / self.name

    @property
    def sources(self) -> pathlib.Path:
        return self.out / "sources"


def read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def write_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def sh(
    cmd: Sequence[str | pathlib.Path],
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    args = [str(c) for c in cmd]
    try:
        r = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as e:
        raise ReplayError(f"{args[0]}: {e}") from None
    if r.returncode:
        raise ReplayError(f"{' '.join(args)}\n{(r.stdout + r.stderr).strip()[-2000:]}")
    return r.stdout.strip()


def rmtree(path: pathlib.Path) -> None:
    def writable(func: Callable[..., Any], target: str, _exc: Any) -> None:
        os.chmod(target, stat.S_IWRITE)  # git's object files are read-only on Windows
        func(target)

    if path.exists():
        shutil.rmtree(path, onerror=writable)


def source(repo: str, base: pathlib.Path, cache: pathlib.Path) -> pathlib.Path:
    """The local repository a candidate is cut from: the path (relative to the candidates
    file), or a bare clone of the URL in `cache`."""
    local = (base / pathlib.Path(repo).expanduser()).resolve()
    if local.is_dir():
        return local
    if "://" not in repo and "@" not in repo:
        raise ReplayError(f"repository not found: {local}")
    bare = cache / (re.sub(r"[^A-Za-z0-9]+", "-", repo).strip("-") + ".git")
    if not bare.is_dir():
        cache.mkdir(parents=True, exist_ok=True)
        sh(["git", "clone", "-q", "--bare", repo, bare], env=_git_env())
    return bare


def _git_env() -> dict[str, str]:
    return dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")


def cut_clone(src: pathlib.Path, sha: str, dest: pathlib.Path) -> None:
    """`dest` holds `sha` and its ancestors only: no remote, no later commit."""
    rmtree(dest)
    dest.mkdir(parents=True)
    sh(["git", "-C", dest, "init", "-q", "-b", "replay"])
    fetch = ["fetch", "-q", "--no-tags", f"--upload-pack={UPLOAD}", str(src), sha]
    sh(["git", "-C", dest, *fetch], env=_git_env())
    sh(["git", "-C", dest, "reset", "-q", "--hard", "FETCH_HEAD"], env=_git_env())


def is_test_path(path: str) -> bool:
    p = pathlib.PurePosixPath(path)
    return bool({"tests", "test"} & set(p.parts[:-1])) or runnable(path)


def runnable(path: str) -> bool:
    name = pathlib.PurePosixPath(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def rule_path(path: pathlib.Path) -> str:
    """An absolute path as a Claude Code permission rule writes it: //c/Users/x, //home/x."""
    text = path.resolve().as_posix()
    if len(text) > 1 and text[1] == ":":
        text = text[0].lower() + text[2:]
    return "//" + text.lstrip("/")


def deny_rules(hidden: Sequence[str]) -> list[str]:
    """`hidden`: folders as rule paths (rule_path)."""
    rules = [f"Read({p}/**)" for p in hidden] + ["WebFetch", "WebSearch"]
    return rules + [f"{tool}({cmd} *)" for tool in ("Bash", "PowerShell") for cmd in BLOCKED]


#: what the setup would add to the settings on a yes. The chat may not edit that file in a
#: headless session, so it is there from the start.
CHAT_RULES = ["Bash(framegate *)", "PowerShell(framegate *)"]


def guard_hook(up: int) -> dict[str, Any]:
    """The replay guard (guard.py) as a PreToolUse hook: nothing outside the folder `up`
    levels above the project folder."""
    hook = {"type": "command", "command": f"python -m framegate.replay.guard --up {up}"}
    tools = "Bash|PowerShell|Edit|Write|MultiEdit|NotebookEdit"
    return {"PreToolUse": [{"matcher": tools, "hooks": [hook]}]}


def write_deny(
    folder: pathlib.Path, rules: list[str], allow: Sequence[str] = (), up: int = 0
) -> None:
    """The folder's settings: deny rules, and the guard that keeps every tool call inside
    the folder (`up` levels above it). Claude Code reads them for every agent started there."""
    permissions = {**({"allow": list(allow)} if allow else {}), "deny": rules}
    data = {"permissions": permissions, "hooks": guard_hook(up)}
    write_json(folder / ".claude" / "settings.local.json", data)


def check_guard(py: pathlib.Path, lay: Layout) -> None:
    """The driver's hook as Claude Code starts it, with the sandbox's python: it must let a
    write in the run folder through and block one outside. A hook that cannot start blocks
    nothing (Claude Code goes on after any exit code but 2)."""
    # `python` as the hook finds it: the run puts py's folder first on PATH (run.run_env)
    python = shutil.which("python", path=os.pathsep.join([str(py.parent), os.environ["PATH"]]))
    for target, expected in ((lay.sandbox / "x.txt", 0), (lay.answer / "x.txt", 2)):
        call = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
        r = subprocess.run(
            [python or "python", "-m", "framegate.replay.guard", "--up", "1"],
            input=json.dumps({**call, "cwd": str(lay.driver)}),
            capture_output=True,
            text=True,
            cwd=lay.driver,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=str(lay.driver)),
        )
        if r.returncode != expected:
            raise ReplayError(
                f"the replay guard (python -m framegate.replay.guard, python = {python}) gave exit "
                f"{r.returncode} for a write to {target}, not {expected}: {r.stderr.strip()}"
            )


def venv_python(sandbox: pathlib.Path) -> pathlib.Path:
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return sandbox.joinpath(".venv", *sub)


def framegate_source() -> str:
    """What installs the framegate that is running: its checkout, where it was installed
    from, or its release tag."""
    import framegate
    from framegate.preflight import SOURCE_URL

    root = pathlib.Path(framegate.__file__).resolve().parents[2]
    if (root / "pyproject.toml").is_file() and (root / "src" / "framegate").is_dir():
        return str(root)
    try:
        origin = json.loads(
            importlib.metadata.distribution("framegate").read_text("direct_url.json") or ""
        )
        vcs = origin.get("vcs_info")
        return f"{vcs['vcs']}+{origin['url']}@{vcs['commit_id']}" if vcs else str(origin["url"])
    except (importlib.metadata.PackageNotFoundError, ValueError, KeyError, AttributeError):
        return f"git+{SOURCE_URL}@v{framegate.__version__}"


def make_env(c: Candidate, sandbox: pathlib.Path) -> pathlib.Path:
    """The sandbox's own .venv with the project, pytest and the running framegate (uv, else
    venv + pip). Returns its python."""
    uv = shutil.which("uv")
    if c.python and not uv:
        raise ReplayError("`- python:` needs uv")
    if uv:
        sh(
            [uv, "venv", "-q", "--seed", *(["--python", c.python] if c.python else []), ".venv"],
            sandbox,
        )
    else:
        sh([sys.executable, "-m", "venv", ".venv"], sandbox)
    py = venv_python(sandbox)
    pip = (
        [uv, "pip", "install", "-q", "--python", str(py)]
        if uv
        else [str(py), "-m", "pip", "install", "-q"]
    )
    commands = [[a.replace("{py}", str(py)) for a in shlex.split(line)] for line in c.install]
    if not c.install:
        if (sandbox / "requirements.txt").is_file():
            commands.append([*pip, "-r", "requirements.txt"])
        if (sandbox / "pyproject.toml").is_file() or (sandbox / "setup.py").is_file():
            commands.append([*pip, "-e", "."])
    for cmd in [*commands, [*pip, framegate_source(), "pytest"]]:
        sh(cmd, sandbox)
    return py


def copy_tree(src: pathlib.Path, dest: pathlib.Path, hidden: pathlib.Path) -> None:
    """A copy of the working tree with the hidden test files laid over it."""
    rmtree(dest)
    shutil.copytree(src, dest, ignore=SKIP)
    shutil.copytree(hidden, dest, dirs_exist_ok=True)


def run_tests(
    py: pathlib.Path,
    tree: pathlib.Path,
    roots: Sequence[str],
    files: Sequence[str],
    xml: pathlib.Path,
) -> tuple[dict[str, str], str]:
    """pytest on `files` in `tree`: {test: pass | fail | skip} and the last output line.
    The tree's own code is imported first, not what the environment has installed."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = os.pathsep.join(str(tree / r) for r in roots)
    xml.unlink(missing_ok=True)
    cmd = [str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:framegate"]
    try:
        r = subprocess.run(
            [*cmd, f"--junitxml={xml}", *files],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TEST_TIMEOUT,
        )
        output = r.stdout + "\n" + r.stderr
    except subprocess.TimeoutExpired:
        output = f"timeout after {TEST_TIMEOUT} s"
    xml.with_suffix(".log").write_text(output, encoding="utf-8")
    out: dict[str, str] = {}
    if xml.is_file():
        for tc in ET.parse(xml).getroot().iter("testcase"):
            kinds = {child.tag for child in tc}
            bad = kinds & {"failure", "error"}
            out[f"{tc.get('classname')}::{tc.get('name')}"] = (
                "fail" if bad else "skip" if "skipped" in kinds else "pass"
            )
    return out, (output.strip().splitlines() or ["(no output)"])[-1]


def build(
    c: Candidate,
    lay: Layout,
    src: pathlib.Path,
    env: Callable[[Candidate, pathlib.Path], pathlib.Path] = make_env,
) -> None:
    """Build and check the run folder. Raises ReplayError with the reason to drop the
    candidate."""

    def git(*args: str) -> str:
        return sh(["git", "-C", src, *args])

    try:
        full = git("rev-parse", "--verify", "-q", f"{c.commit}^{{commit}}")
        parent = git("rev-parse", "--verify", "-q", f"{full}^")
    except ReplayError:
        raise ReplayError(f"commit {c.commit} or its parent is not in {src}") from None
    rmtree(lay.run)
    rmtree(lay.answer)
    cut_clone(src, parent, lay.sandbox)
    seen = subprocess.run(
        ["git", "-C", str(lay.sandbox), "cat-file", "-e", full], capture_output=True
    )
    if seen.returncode == 0:
        raise ReplayError("the commit is reachable in the sandbox")

    changed = git("diff", "--name-only", "--diff-filter=d", parent, full).splitlines()
    tests = c.tests or [p for p in changed if is_test_path(p)]
    files = [t for t in tests if runnable(t)]
    if not files:
        raise ReplayError("the commit changes no test file (name them with `- tests:`)")
    hidden = lay.answer / "hidden"
    for t in tests:
        shown = subprocess.run(["git", "-C", str(src), "show", f"{full}:{t}"], capture_output=True)
        if shown.returncode:
            raise ReplayError(f"test file {t} is not in the commit")
        (hidden / t).parent.mkdir(parents=True, exist_ok=True)
        (hidden / t).write_bytes(shown.stdout)
    message = git("log", "-1", "--format=%B", full)
    (lay.answer / "message.txt").write_text(message, encoding="utf-8")
    (lay.answer / "commit.patch").write_text(git("diff", parent, full), encoding="utf-8")
    lay.driver.mkdir(parents=True)
    (lay.driver / "task.md").write_text((c.task or message) + "\n", encoding="utf-8")
    # a file, not a shell argument: the shells mangle quotes in the candidate's setup text
    setup = f"/framegate:setup {c.setup}".strip()
    (lay.driver / "setup.txt").write_text(setup + "\n", encoding="utf-8")

    with open(lay.sandbox / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
        f.write("\n.venv/\n.claude/settings.local.json\n")
    secret = [rule_path(p) for p in (lay.answer.parent, lay.sources, src)]
    write_deny(
        lay.sandbox, deny_rules([*secret, rule_path(lay.out / "runs") + "/*/driver"]), CHAT_RULES
    )
    # the driver works in its folder and talks to the sandbox: both are under the run folder
    write_deny(lay.driver, deny_rules(secret), up=1)

    py = env(c, lay.sandbox)
    check_guard(py, lay)
    roots = c.pythonpath or (["src", "."] if (lay.sandbox / "src").is_dir() else ["."])
    check = lay.answer / "check"
    copy_tree(lay.sandbox, check / "base", hidden)
    cut_clone(src, full, check / "commit")
    found: dict[str, dict[str, str]] = {}
    tails: dict[str, str] = {}
    for name in ("base", "commit"):
        found[name], tails[name] = run_tests(py, check / name, roots, files, check / f"{name}.xml")
        rmtree(check / name)
    proving = sorted(
        n
        for n, v in found["commit"].items()
        if v == "pass" and found["base"].get(n, "fail") == "fail"
    )
    if not proving:
        raise ReplayError(
            "no test fails on the parent and passes on the commit "
            f"(parent: {tails['base']}; commit: {tails['commit']})"
        )
    write_json(
        lay.answer / "exercise.json",
        {
            "commit": full,
            "parent": parent,
            "tests": tests,
            "files": files,
            "pythonpath": roots,
            "proving": proving,
            "base_pass": sorted(n for n, v in found["base"].items() if v == "pass"),
        },
    )
    write_json(lay.run / "run.json", {"state": "prepared", "python": str(py)})
