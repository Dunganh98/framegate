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
from typing import Any, ClassVar

from framegate import procs
from framegate.agent_commands import (
    FILES_ARG,
    command_rules,
    command_text,
    lint_line,
    rule_allows,
)
from framegate.config import GATE_COMMAND_KEYS, Config

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
    """status: PASS | FAIL | SKIPPED | NOT MEASURED. A SKIPPED or NOT MEASURED check never
    fails the gate but is reported."""

    status: str
    output: str = ""
    #: optional one-line result (e.g. a score) shown under a [PASS] line in the gate report
    summary: str = ""
    #: a FAIL because the tool gave no result (it crashed, could not build its own copy, was
    #: cut short): it says nothing about the code or the tests, so no agent is sent to fix it
    tool_failure: bool = False


#: status of a check whose tool cannot run on this machine (not_measured)
NOT_MEASURED = "NOT MEASURED"


def not_measured(reason: str, fix: str, log: str = "") -> CheckResult:
    """The check's tool cannot run on this machine, so nothing was measured. That is not a
    FAIL (it says nothing about the code or the tests) and not a SKIPPED (nobody chose to
    leave the check out). The gate passes; the gate report, the brief and the risk line show
    the reason and the one-line fix, and the risk rating is not raised for it (risk.py)."""
    return CheckResult(NOT_MEASURED, log, summary=f"{reason}. Fix: {fix}")


@dataclass
class Check:
    name: str
    run: Callable[[], CheckResult]
    #: only meaningful on a green build and green tests (mutation testing): the gate runner
    #: skips it, with the reason, when one of those checks failed before it
    needs_green: bool = False


TIMEOUT_CODE = 124


def run_command(
    cmd: Sequence[str],
    cwd: pathlib.Path,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> tuple[int, str]:
    """Run a command, return (exit code, stdout+stderr). Missing executables -> code 127,
    timeout (seconds) -> code 124 after killing the process tree. The process tree is also
    killed when framegate is interrupted (procs.run), and on Windows when framegate dies."""
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


def cobertura_hits(
    reports: Sequence[pathlib.Path], root: pathlib.Path
) -> dict[str, dict[int, int]]:
    """Cobertura reports -> {repo-relative posix file: {line: hits}} (the most hits a line has
    in any class or report: a lambda or an async method is a class of its own there). A file
    name is resolved against the report's <source> folders and `root`; one that is not a file
    under `root` is left out."""
    out: dict[str, dict[int, int]] = {}
    root = root.resolve()
    for report in reports:
        try:
            top = ET.parse(report).getroot()
        except (OSError, ET.ParseError):
            continue
        bases = [pathlib.Path(s.text.strip()) for s in top.iter("source") if s.text]
        names: dict[str, str | None] = {}
        for cls in top.iter("class"):
            fname = cls.get("filename") or ""
            if fname not in names:
                names[fname] = None
                for cand in [pathlib.Path(fname), *(b / fname for b in bases), root / fname]:
                    try:
                        if cand.is_absolute() and cand.is_file():
                            names[fname] = cand.resolve().relative_to(root).as_posix()
                            break
                    except (OSError, ValueError):
                        continue
            rel = names[fname]
            if rel is None:
                continue
            lines = out.setdefault(rel, {})
            for line in cls.iter("line"):
                try:
                    n, hits = int(line.get("number") or 0), int(line.get("hits") or 0)
                except ValueError:
                    continue
                lines[n] = max(lines.get(n, 0), hits)
    return out


def cobertura_without(
    src: pathlib.Path, dst: pathlib.Path, root: pathlib.Path, prefix: str
) -> None:
    """Copy a Cobertura report without the files under `prefix` (repo-relative, e.g.
    `tests/`). The coverage run may record the test files (which assertions ran); diff
    coverage must not count them: review tests are new lines that all run, and would hide
    uncovered source lines behind a high percentage."""
    tree = ET.parse(src)
    top = tree.getroot()
    bases = [pathlib.Path(s.text.strip()) for s in top.iter("source") if s.text]
    root = root.resolve()

    def under_prefix(fname: str) -> bool:
        for cand in [pathlib.Path(fname), *(b / fname for b in bases), root / fname]:
            try:
                if cand.is_absolute() and cand.is_file():
                    return cand.resolve().relative_to(root).as_posix().startswith(prefix)
            except (OSError, ValueError):
                continue
        return fname.replace("\\", "/").startswith(prefix)

    for parent in top.iter():
        for cls in [c for c in parent if c.tag == "class"]:
            if under_prefix(cls.get("filename") or ""):
                parent.remove(cls)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tree.write(dst, encoding="utf-8", xml_declaration=True)


@dataclass
class TestAssertions:
    """A test of a test file: its lines and the lines of its assertions (1-based)."""

    __test__ = False  # not a pytest class

    name: str
    first: int
    last: int
    lines: list[int] = field(default_factory=list)


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
    from framegate.state import GitError, git

    try:
        if not git(cfg, "ls-files", "--", rel).strip():
            return None
        diff = git(cfg, "diff", "-U0", base_ref, "--", rel)
    except GitError:
        return None
    return {n for a, b in diff_hunks(diff) for n in range(a, b + 1)}


def lines_beyond(before: str, after: str, lines: set[int]) -> int:
    """How many lines of `before` a formatter rewrote (`after` is its output) outside
    `lines` (1-based lines of `before`: the ones the task changed). That is formatting
    debt from before the task, fixed with it."""
    import difflib

    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    count = 0
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            count += sum(1 for k in range(i1 + 1, i2 + 1) if k not in lines)
        elif i1 not in lines and i1 + 1 not in lines:  # lines put in between two old ones
            count += 1
    return count


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

    # ---------- ports (ported.py; None = this language has no support: said, not guessed) ----
    def reference_stub(self, source: bytes) -> tuple[bytes, list[str]] | None:
        """The stub of a ported code file: the reference file's text with every function body
        replaced by "not implemented" and everything else byte for byte, plus notes about
        what was left whole. None: not supported (the stub is written by hand)."""
        return None

    def stub_import_problems(self, target: str, text: bytes, ported: dict[str, str]) -> list[str]:
        """Imports of a generated stub (repo-relative `target`) that will not resolve in this
        repository, one line each. `ported`: reference path -> target path of the port's code
        files (a module that is ported under another name)."""
        return []

    def test_names(self, source: bytes) -> list[str] | None:
        """The names of the test functions of a test file's text, in file order. None: not
        supported for this language."""
        return None

    # ---------- gates ----------
    @abc.abstractmethod
    def default_gate_commands(self) -> dict[str, list[str]]:
        """Default lint / format / typecheck / imports commands (omit a key to skip it).
        Overridden per key by [gates.commands] in framegate.toml."""

    def resolve_command(self, cmd: Sequence[str]) -> list[str]:
        """Expand {src}, {tests}, {out} placeholders in a configured command. An argument that
        is exactly "{src}" becomes one argument per source folder ([paths] src as a list);
        inside a longer argument it is the first one."""
        p = self.cfg.paths
        out: list[str] = []
        for a in cmd:
            if a == "{src}":
                out += self.cfg.src_roots
            else:
                out.append(a.format(src=p.src, tests=p.tests, out=p.out))
        return out

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

    #: the gates count a lint / format / typecheck / imports finding only in a file the task
    #: changed (gates.lint_checks); the checks themselves run on the whole tree
    file_scoped_lint: ClassVar[bool] = False

    def changed_since(self, base_ref: str | None) -> set[str] | None:
        """Files changed since base_ref (incl. untracked); None when that is not known (no
        ref, no such marker)."""
        from framegate.checks.frame_diff import changed_files
        from framegate.state import GitError, tag_exists

        try:
            if not base_ref or not tag_exists(self.cfg, base_ref):
                return None
            return changed_files(self.cfg, base_ref)[0]
        except GitError:
            return None

    def tool_env(self) -> dict[str, str] | None:
        """Environment for the adapter's tool commands (None = inherit)."""
        return None

    def agent_env(self) -> dict[str, str]:
        """Environment variables set for every agent run on top of framegate's own (whole
        values, e.g. a full PATH), so the commands an agent types mean the tools the gates
        use. Default: nothing."""
        return {}

    def format_fix_commands(self, files: list[str]) -> list[list[str]]:
        """Commands that format `files` (changed source files) in place; [] = none."""
        return []

    #: the format / lint checks report only findings on changed lines, and the format fix keeps
    #: only the formatter's changes on changed lines (no churn in code nobody touched)
    line_scoped_format: ClassVar[bool] = False
    #: set by format_files: file -> lines the formatter rewrote outside the task's own
    format_beyond: dict[str, int] = {}  # noqa: RUF012 (replaced per call, not changed)

    def format_fix_enabled(self) -> bool:
        """[gates] format_fix, with the adapter's own format check or with a configured
        [gates.commands] format_fix (a configured [gates.commands] format alone means the
        project runs its own formatter, which the adapter's fix would fight)."""
        if not self.cfg.format_fix:
            return False
        return bool(self.cfg.format_fix_command) or "format" not in self.cfg.gate_commands

    def configured_format_fix(self, files: list[str]) -> list[list[str]]:
        """[gates.commands] format_fix with "{files}" replaced by `files`; [] = not configured."""
        cmd = self.cfg.format_fix_command
        if not cmd:
            return []
        i = cmd.index("{files}")
        resolved = self.resolve_command([*cmd[:i], *cmd[i + 1 :]])
        k = len(self.resolve_command(cmd[:i])) if i else 0
        return [[*resolved[:k], *files, *resolved[k:]]]

    def format_fix(self, base_ref: str | None) -> str:
        """Before the checks of a gate: format the source files changed since base_ref, so a
        formatting slip costs no implementer attempt, and the changed review tests (the frame
        exceptions: written by the reviewer, which nobody formats otherwise and the implementer
        may not edit). Frozen tests are never touched. A changed file is formatted whole: old
        formatting debt in it is fixed with the work (format_beyond counts those lines for
        the final summary). Returns a one-line note ("" = nothing)."""
        self.format_beyond = {}
        if not self.format_fix_enabled() or not base_ref:
            return ""
        from framegate.checks.frame_diff import changed_files, path_matches
        from framegate.state import GitError, tag_exists

        allowed = [*(r + "/" for r in self.cfg.src_roots), *self.cfg.frame_exceptions()]
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
        the formatter's changes on lines changed since base_ref are kept. Returns a note.
        Sets format_beyond: file -> lines the formatter rewrote that were not changed since
        base_ref (formatting debt from before the task, fixed with it)."""
        self.format_beyond = {}
        files = [
            f
            for f in files
            if (self.root / f).is_file()
            and (not self.source_suffixes or f.endswith(self.source_suffixes))
        ]
        cmds = (
            (self.configured_format_fix(list(files)) or self.format_fix_commands(list(files)))
            if files
            else []
        )
        if not cmds:
            return ""
        scoped = self.line_scoped_format and bool(base_ref)
        before = {f: (self.root / f).read_bytes() for f in files} if base_ref else {}
        task_lines = {f: changed_lines(self.cfg, str(base_ref), f) for f in before}
        failed = []
        for cmd in cmds:
            code, out = run_command(cmd, self.root, env=self.tool_env(), timeout=900)
            if code != 0:
                failed.append(f"{cmd[0]} exit {code}: {out.strip().splitlines()[-1:] or ''}")
        for f, old in before.items():
            path = self.root / f
            new = path.read_bytes()
            lines = task_lines[f] if new != old else None
            if lines is None:
                continue
            try:
                was, now = old.decode("utf-8"), new.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if scoped:
                path.write_bytes(merge_scoped(was, now, lines).encode("utf-8"))
            elif n := lines_beyond(was, now, lines):
                self.format_beyond = {**self.format_beyond, f: n}
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
    def regression_run(self) -> TestRun:
        """The whole suite (the adapter's final selection) once, without coverage
        instrumentation (cfg.regression_timeout), with per-test results (TestRun.cases) where the
        framework reports them: the final gate tells a parked step's expected red tests from
        real regressions by name."""

    def regression_check(self) -> Check:
        """Final scope with a feature-scoped coverage run: regression_run as a check."""

        def run() -> CheckResult:
            res = self.regression_run()
            if res.incomplete:
                return incomplete_result(res.incomplete, res.output)
            return CheckResult("PASS" if res.status == "passed" else "FAIL", res.output)

        return Check("regression: whole suite, no coverage", run)

    @abc.abstractmethod
    def coverage_checks(
        self,
        base_ref: str,
        scope: TestScope | None = None,
        cover_tests: Sequence[str] = (),
    ) -> list[Check]:
        """Final scope: the tests of `scope` (None = the whole suite) with coverage, then diff
        coverage against base_ref (>= cfg.diff_coverage). Report SKIPPED (not FAIL) when a
        coverage tool is missing; a cut-short run is FAIL `ERROR: ... incomplete`.
        cover_tests: test files (repo-relative) whose own lines the run records too, where
        the adapter can (test_line_hits reads them)."""

    def test_line_hits(self) -> tuple[dict[str, dict[int, int]], str]:
        """({test file: {line: hits}} from the last coverage run of the final gates, a note:
        why there is none). The default: this adapter does not record the tests' lines."""
        return {}, f"the {self.name} adapter does not record which test lines ran"

    def test_assertions(self, rel: str) -> list[TestAssertions]:
        """The tests of a test file with the lines of their assertions; calls that always
        fail (`pytest.fail`, `Assert.Fail`, `assert False`) are left out: they are meant
        not to run. The default: none known."""
        return []

    def scope_coverage_reports(
        self, req_ids: Sequence[str], files: Sequence[str]
    ) -> tuple[list[pathlib.Path], str]:
        """A refactor, before any code moves: run the tests tagged with `req_ids` with
        coverage of `files` (repo-relative source files) and return (Cobertura reports,
        a note: why there is none). The default: not measured by this adapter."""
        return [], "this adapter does not measure coverage per file"

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
        <out>/<feature>/mutation_survivors.md. SKIPPED with a reason when the config leaves
        it out; `not_measured(reason, fix)` when the tool cannot run on this machine."""

    # ---------- frame ----------
    @abc.abstractmethod
    def default_forbidden_patterns(self) -> list[str]:
        """Regexes that must not appear in source lines added since the tests were approved
        (suppressions, skips)."""

    @abc.abstractmethod
    def default_protected_paths(self) -> list[str]:
        """Dependency / build / tool-config paths (prefixes) that must not change after the
        tests were approved."""

    def setup_paths(self) -> list[str]:
        """The setup files of a project in this language (prefixes / globs): packaging, test
        and tool configuration. The spec phase may change them and `approve-spec` commits
        them with the spec (frame_diff.is_setup_file). The adapter's protected defaults hold
        nothing else, so they are the list; an adapter whose defaults ever mix in other files
        (deploy, CI) overrides this with the subset. Never `[protected] extra`: what a project
        adds there (deploy, CI, generated files) is no feature's to change."""
        return self.default_protected_paths()

    # ---------- agents ----------
    @abc.abstractmethod
    def test_tools(self) -> list[str]:
        """--allowedTools rules that let an agent run the tests."""

    def agent_form(self, cmd: Sequence[str]) -> list[str]:
        """A gate command as an agent types it: placeholders expanded (`{src}` = every
        source folder), `{files}` / `{changed}` = `<files>`, the tool by its name instead
        of the path framegate found it at."""
        out = self.resolve_command([FILES_ARG if a in ("{files}", "{changed}") else a for a in cmd])
        if out and os.path.isabs(out[0]):
            out[0] = pathlib.Path(out[0]).stem
        return out

    def agent_format_fix(self) -> list[list[str]]:
        """The format command(s) an agent may run, on `<files>`: the project's own
        ([gates.commands] format_fix), else the adapter's while the format check is the
        adapter's too; [] = none (the project's check command is named instead)."""
        if self.cfg.format_fix_command:
            return self.configured_format_fix([FILES_ARG])
        if "format" in self.cfg.gate_commands:
            return []
        return self.format_fix_commands([FILES_ARG])

    def agent_commands(self) -> list[list[str]]:
        """The lint / format / typecheck / imports commands an agent may run, as it types
        them: the effective gate commands ([gates.commands], else the adapter's defaults; a
        disabled one, `[]`, is left out), the format one in its fix form where there is one.
        The conventions brief names exactly these, and lint_tools allows exactly these."""
        defaults = self.default_gate_commands()
        fix = self.agent_format_fix()
        out: list[list[str]] = []
        for key in GATE_COMMAND_KEYS:
            cmd = self.cfg.gate_commands.get(key, defaults.get(key))
            if key == "format" and fix:
                out += [self.agent_form(c) for c in fix]
            elif cmd:
                out.append(self.agent_form(cmd))
        return out

    def command_paths(self) -> list[str]:
        """The folders and files a command is run on (where an allow rule's prefix ends)."""
        p = self.cfg.paths
        return [*self.cfg.src_roots, p.tests, p.review_tests, p.out]

    def lint_tools(self) -> list[str]:
        """--allowedTools rules that let an agent run lint/format/typecheck: one per command
        of agent_commands (the brief names no command these do not allow)."""
        paths = self.command_paths()
        rules = [r for cmd in self.agent_commands() for r in command_rules(cmd, paths)]
        return list(dict.fromkeys(rules))

    def lint_brief(self, allowed: Sequence[str] | None = None) -> str:
        """The conventions brief's line with the lint / format / type commands (`allowed`:
        the tool rules of the run that reads the brief; None = all of them). "" = the
        project has none."""
        return lint_line([command_text(c) for c in self.agent_commands()], allowed)

    def brief_commands(self, allowed: Sequence[str] | None = None) -> list[str]:
        """Every shell command the conventions brief names for a run with these tool rules
        (the test commands, then the lint / format / type commands it may use). Each must be
        allowed by those rules: tests/test_agent_commands.py and `framegate doctor` check it."""
        lint = [command_text(c) for c in self.agent_commands()]
        return [
            self.test_command(),
            self.test_command(["ID1", "ID2"]),
            *(c for c in lint if allowed is None or rule_allows(allowed, c)),
        ]

    def allowed_tools(self, phase: str) -> list[str]:
        """--allowedTools rules per phase (core tools + this adapter's commands)."""
        tests, lint = self.test_tools(), self.lint_tools()
        table = {
            "TESTS": READ_TOOLS + WRITE_TOOLS + tests + lint,
            "TEST_REVIEW": READ_TOOLS + ["Write"] + tests,
            "IMPLEMENT": READ_TOOLS + WRITE_TOOLS + tests + lint,
            "REVIEW": READ_TOOLS + WRITE_TOOLS + tests + GIT_READ_TOOLS,
            "TRIAGE": READ_TOOLS + WRITE_TOOLS + tests + GIT_READ_TOOLS,
            "DIAGNOSIS": READ_TOOLS + ["Write"] + tests + GIT_READ_TOOLS,
            "SUMMARY": READ_TOOLS + WRITE_TOOLS + GIT_READ_TOOLS,  # WRITE_TOOLS: the code map
            "RETRO": READ_TOOLS + ["Write"],
            "SPEC_UPDATE": READ_TOOLS + WRITE_TOOLS,
        }
        return with_powershell(table.get(phase, READ_TOOLS))

    @abc.abstractmethod
    def conventions_brief(self, allowed: Sequence[str] | None = None) -> str:
        """Short text (< ~1.5 KB): how stubs look, how tests are tagged with spec IDs, how to
        run tests/lint, what is forbidden. Injected into TESTS/IMPLEMENT/REVIEW tasks.
        `allowed`: the tool rules of the run that gets it (None = no run: the chat's spec
        brief); the lint / format / type commands come from lint_brief(allowed), so the text
        names only commands that run may use."""

    def ci_setup(self) -> str:
        """GitHub Actions steps (YAML, indented 6 spaces) that install the toolchain."""
        return "      # install the project's toolchain and framegate here\n"

    # ---------- doctor ----------
    def tool_report(self) -> list[tuple[str, bool, str]]:
        """`framegate doctor`: (tool, found, detail or install hint) for the tools the gates use
        beyond the configured commands (coverage, diff coverage, mutation)."""
        return []

    def team_checks(self) -> list[tuple[str, bool, str]]:
        """`framegate doctor` with `[git] state_branch`: (what, ok, detail or fix) for what
        the feature's tests need in an environment WITHOUT framegate (the team's CI, a
        colleague's checkout), because the pull request carries the tests and nothing of
        framegate. python: the `req` marker registered in the project's own pytest config.
        dotnet: nothing (`[Trait("Req", ...)]` is xUnit's own attribute)."""
        return []

    def tools_version(self) -> str | None:
        """The version of framegate's own part in the project's environment (python: the
        pytest plugin), "" when it is missing there; None = this language has no such part.
        The start step (`framegate preflight`) keeps it at the command-line tool's version."""
        return None

    def install_tools(
        self, dry_run: bool, echo: Callable[[str], None], update: bool = False
    ) -> int:
        """`framegate install-tools`: install what the workflow needs into the project's
        environment (0 = fine); `update`: also bring framegate's own part there to this
        version. Default: nothing framegate can install for this language."""
        echo(
            f"Nothing framegate installs for a {self.name} project: `framegate doctor` lists "
            "the tools it looks for and how to get them."
        )
        return 0

    # ---------- probe (`framegate probe`: instant facts, nothing is run on the code) ----------
    @classmethod
    def guess_layout(cls, root: pathlib.Path) -> tuple[list[str], str]:
        """(source folders, test folder) of a repository with no framegate.toml yet, guessed
        from its files. Default: framegate's own defaults."""
        return ["src"], "tests"

    def interpreter(self) -> tuple[str, str] | None:
        """(the interpreter / toolchain executable every run uses, where it comes from);
        None = this language has none to name."""
        return None

    def env_facts(self) -> dict[str, Any]:
        """One instant question to the project's environment. Keys, all optional: `version`
        (of the interpreter / toolchain; absent = it did not answer), `editable` (the
        project's packages installed there from the source tree), `refresh` (the
        `[env] refresh` command that redoes that install)."""
        return {}

    def test_runner(self) -> list[str]:
        """The command whose `--version` shows that the test runner is there ([] = unknown)."""
        return []

    def detected_tools(self) -> dict[str, tuple[list[str], str]]:
        """Per role (`test`, a `[gates.commands]` key, `format_fix`): the command the
        project's own config files point to, as it is written in framegate.toml, and the file
        that says so. A role the project's files do not settle is left out."""
        return {}

    # ---------- init ----------
    def config_template(self) -> str:
        """Commented framegate.toml section(s) for this language, appended by `framegate init`."""
        return ""

    def gitignore_entries(self) -> list[str]:
        """Extra .gitignore lines `framegate init` adds (build outputs the workflow must never
        commit: the orchestrator commits with `git add -A`)."""
        return []

    def init_note(self) -> str:
        """Setup advice printed at the end of `framegate init`."""
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
