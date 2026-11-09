"""Language adapter interface.

The core (orchestrator, phases, progress, frame diff, path protection, gate runner, CLI) is
language-neutral. Everything that depends on the project's language and test framework lives
behind `LanguageAdapter`. See docs/adapters.md.
"""

from __future__ import annotations

import abc
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import ClassVar

from agentflow.config import GATE_COMMAND_KEYS, Config

READ_TOOLS = ["Read", "Glob", "Grep"]
WRITE_TOOLS = ["Write", "Edit", "MultiEdit"]
GIT_READ_TOOLS = ["Bash(git diff *)", "Bash(git log *)"]

GATE_NAMES = {
    "lint": "lint",
    "format": "format",
    "typecheck": "typecheck",
    "imports": "import rules",
}


@dataclass
class TestCase:
    """One test result. outcome: passed | failed | error | skipped."""

    __test__ = False  # not a pytest test class

    name: str
    outcome: str
    #: failure/error message (and stack trace when the report has one); "" if none
    message: str = ""


@dataclass
class TestRun:
    """Result of a test-framework run.

    status: passed | failed | no_tests | error (collection/usage/internal problem).
    cases: per-test outcomes when a junit report was requested and produced, else None.
    """

    __test__ = False

    status: str
    returncode: int
    output: str
    cases: list[TestCase] | None = None


@dataclass
class CheckResult:
    """status: PASS | FAIL | SKIPPED. A SKIPPED check never fails the gate but is reported."""

    status: str
    output: str = ""
    #: optional one-line result (e.g. a score) shown under a [PASS] line in the gate report
    summary: str = ""


@dataclass
class Check:
    name: str
    run: Callable[[], CheckResult]


TIMEOUT_CODE = 124


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill a process and its children (test hosts, build nodes)."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def run_command(
    cmd: Sequence[str],
    cwd: pathlib.Path,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> tuple[int, str]:
    """Run a command, return (exit code, stdout+stderr). Missing executables -> code 127,
    timeout (seconds) -> code 124 after killing the process tree."""
    try:
        proc = subprocess.Popen(
            list(cmd),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            start_new_session=timeout is not None and sys.platform != "win32",
        )
    except FileNotFoundError as e:
        return 127, f"Tool not found: {e}"
    except OSError as e:
        return 127, f"Could not start {cmd[0]}: {e}"
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        msg = f"TIMEOUT: {cmd[0]} did not finish within {timeout:g} s (process tree killed)"
        return TIMEOUT_CODE, ((out or "") + (err or "")).strip() + "\n" + msg
    return proc.returncode, (out + err).strip()


def command_check(name: str, cmd: Sequence[str], cwd: pathlib.Path) -> Check:
    def run() -> CheckResult:
        code, out = run_command(cmd, cwd)
        return CheckResult("PASS" if code == 0 else "FAIL", out)

    return Check(name, run)


def parse_junit(path: pathlib.Path) -> list[TestCase]:
    """Parse a JUnit XML report (pytest --junitxml, dotnet JUnit logger) into outcomes."""
    cases = []
    for tc in ET.parse(path).getroot().iter("testcase"):
        cls = tc.get("classname") or ""
        name = f"{cls}::{tc.get('name')}" if cls else str(tc.get("name"))
        by_tag = {child.tag: child for child in tc}
        outcome, message = "passed", ""
        for tag, result in (("error", "error"), ("skipped", "skipped"), ("failure", "failed")):
            if tag in by_tag:
                node = by_tag[tag]
                outcome = result
                message = "\n".join(x for x in (node.get("message"), node.text) if x).strip()
                break
        cases.append(TestCase(name, outcome, message))
    return cases


class LanguageAdapter(abc.ABC):
    """Everything language- or framework-specific. One instance per loaded project config."""

    name: ClassVar[str]
    #: file suffixes of source files; new untracked files with these suffixes under src are
    #: scanned for forbidden patterns by the frame-diff check.
    source_suffixes: ClassVar[tuple[str, ...]] = ()

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.root = cfg.root

    # ---------- detection ----------
    @classmethod
    @abc.abstractmethod
    def detect(cls, root: pathlib.Path) -> bool:
        """True if the repository at `root` looks like a project of this language."""

    # ---------- tests ----------
    @abc.abstractmethod
    def run_tests(
        self,
        req_ids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
        fail_fast: bool = False,
        junit: pathlib.Path | None = None,
    ) -> TestRun:
        """Run tests. req_ids: only tests tagged with at least one of these IDs (empty/None =
        all). paths: restrict to these test paths (e.g. the review tests). junit: write a JUnit
        XML report there and fill TestRun.cases."""

    @abc.abstractmethod
    def test_command(self, req_ids: Sequence[str] | None = None) -> str:
        """The shell command line an agent runs to check the given IDs (for task prompts)."""

    @abc.abstractmethod
    def collect_req_tags(self) -> dict[str, list[str]]:
        """Static scan of the test sources: spec ID -> list of test locations tagged with it."""

    @abc.abstractmethod
    def test_smells(self) -> list[str]:
        """Static scan: tests without an assertion, skipped/ignored tests. One line per problem."""

    def red_stub_run(self, req_ids: Sequence[str], report: pathlib.Path) -> TestRun:
        """Run the feature's tests against the stubs with a JUnit report. Every case must fail."""
        return self.run_tests(req_ids=req_ids, junit=report)

    def stub_failure_problem(self, case: TestCase) -> str | None:
        """For a case that failed against the stubs: a reason why the failure does not count
        (e.g. it did not fail because of the stub), else None. Default: every failure counts."""
        return None

    # ---------- gates ----------
    @abc.abstractmethod
    def default_gate_commands(self) -> dict[str, list[str]]:
        """Default lint / format / typecheck / imports commands (omit a key to skip it).
        Overridden per key by [gates.commands] in agentflow.toml."""

    def resolve_command(self, cmd: Sequence[str]) -> list[str]:
        """Expand {src}, {tests}, {out} placeholders in a configured command."""
        p = self.cfg.paths
        return [a.format(src=p.src, tests=p.tests, out=p.out) for a in cmd]

    def lint_checks(self, base_ref: str | None = None) -> list[Check]:
        """The lint/format/typecheck/imports checks. base_ref (the tests-approved tag, when the
        gate runner knows it) lets an adapter limit checks to files changed since then."""
        defaults = self.default_gate_commands()
        checks = []
        for key in GATE_COMMAND_KEYS:
            cmd = self.cfg.gate_commands.get(key, defaults.get(key))
            if cmd:
                checks.append(command_check(GATE_NAMES[key], self.resolve_command(cmd), self.root))
        return checks

    @abc.abstractmethod
    def coverage_checks(self, base_ref: str) -> list[Check]:
        """Final scope: full test suite with coverage, then diff coverage against base_ref
        (>= cfg.diff_coverage). Report SKIPPED (not FAIL) when a coverage tool is missing."""

    @abc.abstractmethod
    def mutation_check(
        self, feature: str, base_ref: str, changed_src: Sequence[str]
    ) -> CheckResult:
        """Mutation score on the changed source files (>= cfg.mutation_score); write survivors
        to <out>/<feature>/mutation_survivors.md. SKIPPED with a reason if unavailable."""

    # ---------- frame ----------
    @abc.abstractmethod
    def default_forbidden_patterns(self) -> list[str]:
        """Regexes that must not appear in source lines added since the tests were approved
        (suppressions, skips)."""

    @abc.abstractmethod
    def default_protected_paths(self) -> list[str]:
        """Dependency / build / tool-config paths (prefixes) that must not change after the
        tests were approved."""

    # ---------- agents ----------
    @abc.abstractmethod
    def test_tools(self) -> list[str]:
        """--allowedTools rules that let an agent run the tests."""

    @abc.abstractmethod
    def lint_tools(self) -> list[str]:
        """--allowedTools rules that let an agent run lint/format/typecheck."""

    def allowed_tools(self, phase: str) -> list[str]:
        """--allowedTools rules per phase (core tools + this adapter's commands)."""
        tests, lint = self.test_tools(), self.lint_tools()
        table = {
            "TESTS": READ_TOOLS + WRITE_TOOLS + tests + lint,
            "IMPLEMENT": READ_TOOLS + WRITE_TOOLS + tests + lint,
            "REVIEW": READ_TOOLS + WRITE_TOOLS + tests + GIT_READ_TOOLS,
            "PR": READ_TOOLS + ["Write"] + GIT_READ_TOOLS,
            "RETRO": READ_TOOLS + ["Write"],
        }
        return list(table.get(phase, READ_TOOLS))

    @abc.abstractmethod
    def conventions_brief(self) -> str:
        """Short text (< ~1.5 KB): how stubs look, how tests are tagged with spec IDs, how to
        run tests/lint, what is forbidden. Injected into TESTS/IMPLEMENT/REVIEW tasks."""

    def ci_setup(self) -> str:
        """GitHub Actions steps (YAML, indented 6 spaces) that install the toolchain."""
        return "      # install the project's toolchain and agentflow here\n"

    # ---------- init ----------
    def config_template(self) -> str:
        """Commented agentflow.toml section(s) for this language, appended by `agentflow init`."""
        return ""

    def gitignore_entries(self) -> list[str]:
        """Extra .gitignore lines `agentflow init` adds (build outputs the workflow must never
        commit: the orchestrator commits with `git add -A`)."""
        return []

    def init_note(self) -> str:
        """Setup advice printed at the end of `agentflow init`."""
        return ""

    # ---------- helpers ----------
    def tool_path(self, name: str, near: str | None = None) -> str | None:
        """Find an executable next to `near` (e.g. a venv's python) or on PATH."""
        if near:
            d = pathlib.Path(near).parent
            for cand in (d / name, d / f"{name}.exe", d / f"{name}.cmd"):
                if cand.is_file():
                    return str(cand)
        return shutil.which(name)

    def rel(self, path: pathlib.Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return os.fspath(path)
