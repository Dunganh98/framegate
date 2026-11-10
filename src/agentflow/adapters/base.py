"""Language adapter interface.

The core (orchestrator, phases, progress, frame diff, path protection, gate runner, CLI) is
language-neutral. Everything that depends on the project's language and test framework lives
behind `LanguageAdapter`. See docs/adapters.md.
"""

from __future__ import annotations

import abc
import os
import pathlib
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from agentflow import procs
from agentflow.config import GATE_COMMAND_KEYS, Config

READ_TOOLS = ["Read", "Glob", "Grep"]
WRITE_TOOLS = ["Write", "Edit", "MultiEdit"]
GIT_READ_TOOLS = ["Bash(git diff *)", "Bash(git log *)"]


def with_powershell(rules: Sequence[str]) -> list[str]:
    """Each `Bash(<cmd>)` rule followed by its `PowerShell(<cmd>)` twin. On Windows, Claude Code
    agents run commands through the PowerShell tool, which `Bash(...)` rules don't cover
    (checked live: `PowerShell(dotnet nuget *)` allows `dotnet nuget --help`)."""
    out: list[str] = []
    for rule in rules:
        out.append(rule)
        if rule.startswith("Bash(") and rule.endswith(")"):
            twin = "PowerShell(" + rule[len("Bash(") :]
            if twin not in rules:
                out.append(twin)
    return out


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
    #: why the run was cut short (timeout, crashed host, killed, missing report or coverage
    #: file); "" = it ran to the end. An incomplete run has status "error".
    incomplete: str = ""


@dataclass
class TestScope:
    """The tests a final coverage / mutation run uses: tagged with one of `req_ids` OR located
    under one of `paths` (the review tests). Both empty = the whole suite."""

    __test__ = False

    req_ids: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)

    @property
    def whole_suite(self) -> bool:
        return not self.req_ids and not self.paths

    def describe(self) -> str:
        if self.whole_suite:
            return "the whole suite"
        parts = []
        if self.req_ids:
            n = len(self.req_ids)
            parts.append(f"tagged with the feature's {n} spec ID{'s' if n != 1 else ''}")
        if self.paths:
            parts.append("in " + ", ".join(p + "/" for p in self.paths))
        return "the feature's tests (" + " or ".join(parts) + ")"


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


def run_command(
    cmd: Sequence[str],
    cwd: pathlib.Path,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> tuple[int, str]:
    """Run a command, return (exit code, stdout+stderr). Missing executables -> code 127,
    timeout (seconds) -> code 124 after killing the process tree. The process tree is also
    killed when agentflow is interrupted (procs.run), and on Windows when agentflow dies."""
    try:
        code, out, err = procs.run(cmd, cwd=cwd, env=env, timeout=timeout)
    except FileNotFoundError as e:
        return 127, f"Tool not found: {e}"
    except subprocess.TimeoutExpired as e:
        partial = "".join(
            x.decode("utf-8", "replace") if isinstance(x, bytes) else x
            for x in (e.output, e.stderr)
            if x
        )
        msg = f"TIMEOUT: {cmd[0]} did not finish within {timeout:g} s (process tree killed)"
        return TIMEOUT_CODE, partial.strip() + "\n" + msg
    except OSError as e:
        return 127, f"Could not start {cmd[0]}: {e}"
    return code, (out + err).strip()


def command_check(name: str, cmd: Sequence[str], cwd: pathlib.Path) -> Check:
    def run() -> CheckResult:
        code, out = run_command(cmd, cwd)
        return CheckResult("PASS" if code == 0 else "FAIL", out)

    return Check(name, run)


#: console lines of a test run that was aborted part-way (VSTest; English CLI output)
ABORT_MARKERS = (
    "test run aborted",
    "the active test run was aborted",
    "test host process crashed",
)
#: exit codes of a killed process: Ctrl+C / Ctrl+Break on Windows, SIGKILL / SIGTERM via a shell
KILLED_CODES = {3221225786: "Ctrl+C", -1073741510: "Ctrl+C", 137: "SIGKILL", 143: "SIGTERM"}


def incomplete_reason(code: int, output: str, timeout: float | None = None) -> str:
    """Why a test/coverage/mutation run did not run to the end ("" = it did, as far as the
    exit code and output tell)."""
    if code == TIMEOUT_CODE:
        return f"timed out after {timeout:g} s" if timeout else "timed out"
    if code in KILLED_CODES:
        return f"killed ({KILLED_CODES[code]}, exit {code})"
    if code < 0:
        return f"killed by signal {-code}"
    low = output.lower()
    if any(m in low for m in ABORT_MARKERS):
        return "test host crashed, run aborted"
    return ""


def incomplete_result(reason: str, output: str = "", what: str = "test run") -> CheckResult:
    """A FAIL that says the run was cut short; never a coverage or mutation figure."""
    head = f"ERROR: {what} incomplete ({reason})"
    tail = "\n".join(output.strip().splitlines()[-25:])
    return CheckResult("FAIL", head + ("\n" + tail if tail else ""), summary=head)


def diff_coverage_summary(output: str, threshold: float) -> str:
    """One line from diff-cover's console report: percent, covered/total changed lines and the
    threshold ("" when the output has no coverage figures, e.g. diff-cover crashed)."""
    pct = re.search(r"^Coverage:\s*([\d.]+)%", output, re.M)
    if pct:
        total = re.search(r"^Total:\s*(\d+) lines?", output, re.M)
        missing = re.search(r"^Missing:\s*(\d+) lines?", output, re.M)
        lines = ""
        if total:
            n = int(total.group(1))
            covered = n - (int(missing.group(1)) if missing else 0)
            lines = f" ({covered}/{n} changed lines covered)"
        return f"Diff coverage {pct.group(1)}%{lines}, threshold {threshold:g}%."
    if "No lines with coverage information" in output:
        return f"Diff coverage: no coverable changed lines, threshold {threshold:g}%."
    return ""


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def diff_hunks(diff: str) -> list[tuple[int, int]]:
    """Changed line ranges (1-based, inclusive, new file) of a `git diff -U0` of one file. A
    pure deletion marks the lines around the gap."""
    hunks = []
    for m in _HUNK.finditer(diff):
        start, count = int(m.group(1)), int(m.group(2) or "1")
        hunks.append((max(start, 1), start + 1) if count == 0 else (start, start + count - 1))
    return hunks


def changed_lines(cfg: Config, base_ref: str, rel: str) -> set[int] | None:
    """The lines of `rel` (1-based, current content) changed since base_ref; None = all of it
    (an untracked file, or git cannot tell)."""
    from agentflow.state import GitError, git

    try:
        if not git(cfg, "ls-files", "--", rel).strip():
            return None
        diff = git(cfg, "diff", "-U0", base_ref, "--", rel)
    except GitError:
        return None
    return {n for a, b in diff_hunks(diff) for n in range(a, b + 1)}


def merge_scoped(before: str, after: str, lines: set[int]) -> str:
    """`after` (a formatter's output for `before`) applied only where it touches `lines` (1-based
    lines of `before`): formatting a changed file must not reformat the code nobody touched."""
    import difflib

    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "replace" and i2 - i1 == j2 - j1:  # line for line (e.g. re-indented block)
            out += [b[j1 + k] if i1 + k + 1 in lines else a[i1 + k] for k in range(i2 - i1)]
            continue
        touched = range(i1 + 1, i2 + 1) if i2 > i1 else range(i1, i1 + 2)
        out += b[j1:j2] if tag != "equal" and any(k in lines for k in touched) else a[i1:i2]
    return "".join(out)


#: `path(line,col): error CODE: message` (MSBuild / dotnet format / Roslyn diagnostics)
DIAGNOSTIC = re.compile(
    r"^\s*(?P<path>[^\s(][^(]*?)\((?P<line>\d+),\d+\):\s+(?:error|warning|info)\s"
)


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

    def tool_env(self) -> dict[str, str] | None:
        """Environment for the adapter's tool commands (None = inherit)."""
        return None

    def format_fix_commands(self, files: list[str]) -> list[list[str]]:
        """Commands that format `files` (changed source files) in place; [] = none."""
        return []

    #: the format / lint checks report only findings on changed lines, and the format fix keeps
    #: only the formatter's changes on changed lines (no churn in code nobody touched)
    line_scoped_format: ClassVar[bool] = False

    def format_fix_enabled(self) -> bool:
        """[gates] format_fix, and only with the adapter's own format check (a configured
        [gates.commands] format means the project runs its own formatter)."""
        return self.cfg.format_fix and "format" not in self.cfg.gate_commands

    def format_fix(self, base_ref: str | None) -> str:
        """Before the checks of a gate: format the source files changed since base_ref, so a
        formatting slip costs no implementer attempt, and the changed review tests (the frame
        exceptions: written by the reviewer, which nobody formats otherwise and the implementer
        may not edit). Frozen tests are never touched. Returns a one-line note ("" = nothing)."""
        if not self.format_fix_enabled() or not base_ref:
            return ""
        from agentflow.checks.frame_diff import changed_files, path_matches
        from agentflow.state import GitError, tag_exists

        src = self.cfg.paths.src.rstrip("/") + "/"
        allowed = [src, *self.cfg.frame_exceptions()]
        try:
            if not tag_exists(self.cfg, base_ref):
                return ""
            changed, _ = changed_files(self.cfg, base_ref)
        except GitError:
            return ""
        files = sorted(f for f in changed if path_matches(f, allowed))
        return self.format_files(files, base_ref)

    def format_files(self, files: Sequence[str], base_ref: str | None = None) -> str:
        """Format `files` (posix, relative to the root; other suffixes and missing files are
        left out) with the adapter's formatter. With line_scoped_format and a base_ref, only
        the formatter's changes on lines changed since base_ref are kept. Returns a note."""
        files = [
            f
            for f in files
            if (self.root / f).is_file()
            and (not self.source_suffixes or f.endswith(self.source_suffixes))
        ]
        cmds = self.format_fix_commands(list(files)) if files else []
        if not cmds:
            return ""
        scoped = self.line_scoped_format and bool(base_ref)
        before = {f: (self.root / f).read_bytes() for f in files} if scoped else {}
        failed = []
        for cmd in cmds:
            code, out = run_command(cmd, self.root, env=self.tool_env(), timeout=900)
            if code != 0:
                failed.append(f"{cmd[0]} exit {code}: {out.strip().splitlines()[-1:] or ''}")
        for f, old in before.items():
            path = self.root / f
            new = path.read_bytes()
            lines = changed_lines(self.cfg, str(base_ref), f) if new != old else None
            if lines is not None:
                merged = merge_scoped(old.decode("utf-8"), new.decode("utf-8"), lines)
                path.write_bytes(merged.encode("utf-8"))
        note = f"formatted {len(files)} changed file(s)" + (
            " (changed lines only)" if scoped else ""
        )
        return note + (f" (formatter problems: {'; '.join(failed)})" if failed else "")

    def scope_diagnostics(self, output: str, base_ref: str | None) -> tuple[str, int, int]:
        """A failing lint / format check's output with only the diagnostics on lines changed
        since base_ref: (output, kept, ignored). Lines that are not diagnostics stay."""
        if not base_ref:
            return output, 0, 0
        cache: dict[str, set[int] | None] = {}
        kept_lines, kept, ignored = [], 0, 0
        root = self.root.resolve()
        for line in output.splitlines():
            m = DIAGNOSTIC.match(line)
            if not m:
                kept_lines.append(line)
                continue
            path = pathlib.Path(m.group("path").strip())
            try:
                rel = (path if path.is_absolute() else root / path).resolve().relative_to(root)
            except (ValueError, OSError):
                kept_lines.append(line)
                kept += 1
                continue
            key = rel.as_posix()
            if key not in cache:
                cache[key] = changed_lines(self.cfg, base_ref, key)
            lines = cache[key]
            if lines is None or int(m.group("line")) in lines:
                kept_lines.append(line)
                kept += 1
            else:
                ignored += 1
        return "\n".join(kept_lines), kept, ignored

    @abc.abstractmethod
    def regression_check(self) -> Check:
        """Final scope with a feature-scoped coverage run: the whole suite (the adapter's final
        selection) once, without coverage instrumentation (cfg.regression_timeout)."""

    @abc.abstractmethod
    def coverage_checks(self, base_ref: str, scope: TestScope | None = None) -> list[Check]:
        """Final scope: the tests of `scope` (None = the whole suite) with coverage, then diff
        coverage against base_ref (>= cfg.diff_coverage). Report SKIPPED (not FAIL) when a
        coverage tool is missing; a cut-short run is FAIL `ERROR: ... incomplete`."""

    @abc.abstractmethod
    def mutation_check(
        self,
        feature: str,
        base_ref: str,
        changed_src: Sequence[str],
        scope: TestScope | None = None,
    ) -> CheckResult:
        """Mutation score on the changed source files (>= cfg.mutation_score), killing mutants
        only with the tests of `scope` (None = the whole suite); write survivors to
        <out>/<feature>/mutation_survivors.md. SKIPPED with a reason if unavailable."""

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
            "TEST_REVIEW": READ_TOOLS + ["Write"] + tests,
            "IMPLEMENT": READ_TOOLS + WRITE_TOOLS + tests + lint,
            "REVIEW": READ_TOOLS + WRITE_TOOLS + tests + GIT_READ_TOOLS,
            "TRIAGE": READ_TOOLS + WRITE_TOOLS + tests + GIT_READ_TOOLS,
            "PR": READ_TOOLS + ["Write"] + GIT_READ_TOOLS,
            "RETRO": READ_TOOLS + ["Write"],
            "SPEC_UPDATE": READ_TOOLS + WRITE_TOOLS,
        }
        return with_powershell(table.get(phase, READ_TOOLS))

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
