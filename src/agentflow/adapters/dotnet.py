""".NET adapter: xUnit via `dotnet test` (VSTest), `[Trait("Req", ...)]` tagging, dotnet format,
dotnet build, coverlet + diff-cover, Stryker.NET. See docs/adapters.md."""

from __future__ import annotations

import contextlib
import functools
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from agentflow.adapters import csharp
from agentflow.adapters.base import (
    GATE_NAMES,
    TIMEOUT_CODE,
    Check,
    CheckResult,
    LanguageAdapter,
    TestCase,
    TestRun,
    TestScope,
    diff_coverage_summary,
    diff_hunks,
    incomplete_reason,
    incomplete_result,
    parse_junit,
    run_command,
)
from agentflow.config import GATE_COMMAND_KEYS, ConfigError

#: English CLI output (the gates parse a few messages), no banners or telemetry
DOTNET_ENV = {
    "DOTNET_CLI_UI_LANGUAGE": "en",
    "VSLANG": "1033",
    "DOTNET_NOLOGO": "1",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
}
SKIP_DIRS = {"bin", "obj", "TestResults", "node_modules", ".git"}
WARNING_MODES = ("changed", "any", "ignore")
TRX_NS = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"
TRX_OUTCOMES = {
    "Passed": "passed",
    "Failed": "failed",
    "Error": "error",
    "Timeout": "error",
    "Aborted": "error",
    "NotExecuted": "skipped",
    "NotRunnable": "skipped",
    "Inconclusive": "skipped",
}
STUB_EXCEPTION = "NotImplementedException"
_DIAG = re.compile(r"^\s*(?P<file>[^\s(][^(]*?)\((?P<line>\d+),\d+\): (?P<kind>error|warning) ")
_STRYKER_DETECTED = {"Killed", "Timeout"}
_STRYKER_UNDETECTED = {"Survived", "NoCoverage"}

BRIEF = """\
Language: C# / .NET; tests: xUnit via `dotnet test` ({configuration}).
- Language level: as in the project's CLAUDE.md / .csproj LangVersion; safe default C# 9
  (block namespaces, nullable on).
- Stubs (SPEC phase): full signatures, XML doc comments naming the spec IDs, body
  `throw new NotImplementedException();`. Stubs must compile.
- Tag tests, one attribute per ID, string literals: `[Trait("Req", "XXX-R-001")]` on the
  [Fact]/[Theory] (on the class = all its tests).
- Tests must fail against the stubs because of the NotImplementedException.
- Edge cases: exact values or `Assert.Throws<T>(...)`.
- Invariants (I-IDs): {property}
- Every test asserts something concrete. No `Skip =`, `return;`-only bodies, `Assert.True(true)`.
- Run tests: `{run_all}`; only some IDs: `{run_ids}`.
- Build `dotnet build {target} -c {configuration}` (no new warnings); format
  `dotnet format whitespace|style {target} --include <files>`.
- Forbidden in source: `#pragma warning disable`, `SuppressMessage`, `ExcludeFromCodeCoverage`,
  `Skip =`, swallowing `catch (Exception)` / `catch {{`. Don't edit *.csproj, *.sln(x),
  Directory.*, global.json, NuGet.config, .editorconfig; no `dotnet add` / `dotnet new`.
- Tests in {tests}/; reviewer tests in {review_tests}/.
"""

PROPERTY_FSCHECK = "FsCheck `[Property]` tests (the project references FsCheck)."
PROPERTY_THEORY = (
    "`[Theory]` + `[MemberData]` over ~100 inputs from a fixed seed\n"
    "  (the project's seeded RNG if any, else `new Random(<seed>)`)."
)

#: Stryker.NET --mutation-level values (each includes the lower ones)
MUTATION_LEVELS = ("Basic", "Standard", "Advanced", "Complete")

CONFIG_TEMPLATE = """\
[dotnet]                           # .NET adapter; every key optional, defaults shown
# executable = "dotnet"            # default: dotnet on PATH, else DOTNET_ROOT / Program Files
# solution = "App.slnx"            # default: the single *.sln / *.slnx in the root
# test_projects = []               # e.g. ["tests/App.Tests/App.Tests.csproj"]; default: solution
# configuration = "Release"
# base_filter = ""                 # VSTest filter of every run, e.g. "Category!=Slow"
# final_filter = ""                # filter of the final full suite + coverage ("" = all)
# build_before_test = true         # dotnet build first, then dotnet test --no-build
# warnings = "changed"             # typecheck: warnings in changed files fail | "any" | "ignore"
# build_timeout = 900              # seconds
# test_timeout = 3600
# mutation_timeout = 3600          # default: [gates] mutation_timeout
# mutation_level = ""              # Stryker -l: Basic | Standard | Advanced | Complete ("" =
#                                  # Stryker's default, Standard); Basic is faster, fewer mutants
"""


@dataclass
class DotnetSettings:
    executable: str = ""
    solution: str = ""
    test_projects: list[str] = field(default_factory=list)
    configuration: str = "Release"
    base_filter: str = ""
    final_filter: str = ""
    build_before_test: bool = True
    warnings: str = "changed"
    build_timeout: int = 900
    test_timeout: int = 3600
    #: None = [gates] mutation_timeout
    mutation_timeout: int | None = None
    #: Stryker --mutation-level ("" = Stryker's default, Standard)
    mutation_level: str = ""

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> DotnetSettings:
        s = cls(**options)
        if s.warnings not in WARNING_MODES:
            raise ConfigError(f"[dotnet] warnings must be one of {', '.join(WARNING_MODES)}")
        for key in ("build_timeout", "test_timeout", "mutation_timeout"):
            if getattr(s, key) is not None and getattr(s, key) < 1:
                raise ConfigError(f"[dotnet] {key} must be >= 1")
        if s.mutation_level and s.mutation_level not in MUTATION_LEVELS:
            raise ConfigError(
                f"[dotnet] mutation_level must be one of {', '.join(MUTATION_LEVELS)} (or empty)"
            )
        s.test_projects = [p.replace("\\", "/") for p in s.test_projects]
        return s


# ---------- pure helpers (unit-tested) ----------
def combine_filters(*parts: str) -> str:
    """AND the non-empty VSTest filter expressions, each in parentheses when there are several."""
    parts = tuple(p.strip() for p in parts if p and p.strip())
    if len(parts) <= 1:
        return parts[0] if parts else ""
    return "&".join(f"({p})" for p in parts)


def req_filter(ids: Sequence[str]) -> str:
    return "|".join(f"Req={i}" for i in ids)


def _escape_filter_value(value: str) -> str:
    return re.sub(r"([\\()&|=!~])", r"\\\1", value)


def class_filter(full_names: Sequence[str]) -> str:
    """Select test classes by fully qualified name (`Ns.Class.` prefix match)."""
    return "|".join(f"FullyQualifiedName~{_escape_filter_value(n)}." for n in full_names)


def _split_test_name(name: str) -> tuple[str, str]:
    """`Ns.Class.Method(x: 1)` -> (`Ns.Class`, `Method(x: 1)`)."""
    head = name.split("(", 1)[0]
    dot = head.rfind(".")
    if dot < 0:
        return "", name
    return name[:dot], name[dot + 1 :]


def parse_trx(path: pathlib.Path) -> list[TestCase]:
    """VSTest TRX report -> cases named `Ns.Class::Method`, with failure message + stack."""
    cases = []
    for r in ET.parse(path).getroot().iter(f"{TRX_NS}UnitTestResult"):
        cls, method = _split_test_name(r.get("testName") or "")
        name = f"{cls}::{method}" if cls else method
        outcome = TRX_OUTCOMES.get(r.get("outcome") or "", "error")
        texts = [
            (node.text or "").strip()
            for tag in ("Message", "StackTrace")
            for node in r.iter(f"{TRX_NS}{tag}")
        ]
        cases.append(TestCase(name, outcome, "\n".join(t for t in texts if t)))
    return cases


def write_junit(cases: Sequence[TestCase], path: pathlib.Path) -> None:
    """Write cases as a JUnit XML report (the core's red-stub artifact)."""
    suite = ET.Element(
        "testsuite",
        name="dotnet",
        tests=str(len(cases)),
        failures=str(sum(c.outcome == "failed" for c in cases)),
        errors=str(sum(c.outcome == "error" for c in cases)),
        skipped=str(sum(c.outcome == "skipped" for c in cases)),
    )
    for c in cases:
        cls, _, method = c.name.rpartition("::")
        tc = ET.SubElement(suite, "testcase", classname=cls, name=method)
        tag = {"failed": "failure", "error": "error", "skipped": "skipped"}.get(c.outcome)
        if tag:
            node = ET.SubElement(tc, tag, message=c.message.split("\n", 1)[0])
            node.text = c.message
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("testsuites")
    root.append(suite)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def diagnostics(output: str, kind: str) -> list[tuple[str, int, str]]:
    """MSBuild `file(line,col): error|warning CODE: text` lines -> unique (file, line, text)."""
    seen: dict[tuple[str, int, str], None] = {}
    for line in output.splitlines():
        m = _DIAG.match(line)
        if m and m.group("kind") == kind:
            text = re.sub(r"\s*\[[^\]]*\.csproj[^\]]*\]\s*$", "", line.strip())
            seen[(m.group("file").strip(), int(m.group("line")), text)] = None
    return list(seen)


def run_summary(cases: Sequence[TestCase]) -> str:
    counts = {
        k: sum(c.outcome == k for c in cases) for k in ("passed", "failed", "error", "skipped")
    }
    return ", ".join(f"{v} {k}" for k, v in counts.items())


STRYKER_DISABLE = "// Stryker disable all"


def disable_mutations(path: pathlib.Path) -> None:
    """Prepend `// Stryker disable all` (after a BOM, in the file's line ending style): Stryker
    neither compiles in nor tests the file's mutants."""
    data = path.read_bytes()
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    eol = b"\r\n" if b"\r\n" in data else b"\n"
    path.write_bytes(bom + STRYKER_DISABLE.encode() + eol + data[len(bom) :])


STRYKER_RESTORE = "// Stryker restore all"


def scope_mutations(text: str, hunks: Sequence[tuple[int, int]]) -> tuple[str, list[int]]:
    """`text` with Stryker comments so that only the changed hunks are mutated (and compiled
    with mutants): `// Stryker disable all` on top, `// Stryker restore all` before each hunk,
    `// Stryker disable all` after it.

    Stryker reads these comments only in front of a statement or member, so each hunk widens
    to whole statements (a comment line goes only where the previous code line ends with `;`,
    `{`, `}`, `]` or `:`, or is blank), and never into a string literal or block comment.
    Returns (new text, original line number of each new line, 0 for an inserted line)."""
    bom = "﻿" if text.startswith("﻿") else ""
    body = text[len(bom) :]
    eol = "\r\n" if "\r\n" in body else "\n"
    lines = body.splitlines(keepends=True)
    n = len(lines)
    enclosed = csharp.enclosed_lines(body)
    mask_lines = csharp.strip(body)[1].split("\n")

    def opens_statement(k: int) -> bool:  # a comment may go before line k (1-based)
        if k in enclosed:
            return False
        prev = k - 1
        while prev >= 1 and not mask_lines[prev - 1].strip():
            if prev - 1 < len(lines) and lines[prev - 1].strip():  # a comment line
                return True
            prev -= 1
        return prev < 1 or mask_lines[prev - 1].rstrip()[-1:] in (";", "{", "}", "]", ":")

    before: dict[int, str] = {1: STRYKER_DISABLE}
    for start, end in sorted(hunks):
        a = min(max(start, 1), n)
        while a > 1 and not opens_statement(a):
            a -= 1
        b = end + 1
        while b <= n and not opens_statement(b):
            b += 1
        if before.get(a) == STRYKER_DISABLE and a != 1:
            del before[a]  # touches the previous hunk: one region
        elif a == 1:
            before[1] = STRYKER_DISABLE + eol + STRYKER_RESTORE
        else:
            before[a] = STRYKER_RESTORE
        for k in [k for k in before if a < k < b]:
            del before[k]
        if b <= n:
            before[b] = STRYKER_DISABLE
    out: list[str] = []
    orig: list[int] = []
    for k, line in enumerate(lines, 1):
        if k in before:
            for comment in before[k].split(eol):
                out.append(comment + eol)
                orig.append(0)
        out.append(line)
        orig.append(k)
    return bom + "".join(out), orig


def stryker_results(
    report: dict[str, Any],
    root: pathlib.Path,
    files: Sequence[str],
    line_maps: dict[str, list[int]] | None = None,
) -> tuple[int, int, list[str]]:
    """(detected, undetected, survivor lines) for the given repo-relative files from a Stryker
    mutation-report.json. line_maps: a scoped copy's line -> the original line (survivors are
    listed with the line in the repository's file)."""
    wanted = {f.lower() for f in files}
    detected = undetected = 0
    survivors = []
    for fname, body in (report.get("files") or {}).items():
        rel = _rel_to(root, pathlib.Path(fname))
        if rel.lower() not in wanted:
            continue
        for m in body.get("mutants", []):
            status = m.get("status", "")
            if status in _STRYKER_DETECTED:
                detected += 1
            elif status in _STRYKER_UNDETECTED:
                undetected += 1
                start = (m.get("location") or {}).get("start") or {}
                line = start.get("line", "?")
                lmap = (line_maps or {}).get(rel.lower())
                if lmap and isinstance(line, int) and 0 < line <= len(lmap) and lmap[line - 1]:
                    line = lmap[line - 1]
                survivors.append(
                    f"- `{rel}:{line}:{start.get('column', '?')}` "
                    f"{m.get('mutatorName', '?')} -> `{m.get('replacement', '')}` ({status})"
                )
    return detected, undetected, survivors


def normalize_cobertura(src: pathlib.Path, dst: pathlib.Path, root: pathlib.Path) -> int:
    """Rewrite coverlet's Cobertura report with repo-relative posix file names (no <sources>),
    so diff-cover matches them against `git diff` paths on every OS. Returns the class count."""
    tree = ET.parse(src)
    top = tree.getroot()
    sources = [(s.text or "").strip() for s in top.iter("source")]
    count = 0
    for cls in top.iter("class"):
        fname = cls.get("filename") or ""
        candidates = [pathlib.Path(fname)] + [pathlib.Path(s) / fname for s in sources if s]
        for cand in candidates:
            if cand.is_absolute() and cand.exists():
                cls.set("filename", _rel_to(root, cand))
                count += 1
                break
    for node in list(top):
        if node.tag == "sources":
            top.remove(node)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tree.write(dst, encoding="utf-8", xml_declaration=True)
    return count


def _rel_to(root: pathlib.Path, path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


# ---------- adapter ----------
#: a loop / All() over cases picked from data (`foreach (var r in xs.Where(...))`,
#: `xs.Where(...).All(...)`): passes vacuously when the data has no such case
VACUOUS_LOOP = re.compile(
    r"\bforeach\s*\([^;{]*?\.\s*Where\s*\(|\.\s*Where\s*\([^;]*\)\s*\.\s*All\s*\("
)
#: anything that shows the test checks that the picked cases are not zero
NON_EMPTY = re.compile(r"\bNotEmpty\b|\bSingle\s*\(|\.\s*Any\s*\(|\bCount\b|\bLength\b")


class DotnetAdapter(LanguageAdapter):
    name = "dotnet"
    source_suffixes = (".cs",)
    line_scoped_format = True

    def __init__(self, cfg: Any) -> None:
        super().__init__(cfg)
        self.settings = DotnetSettings.from_options(cfg.language_options.get("dotnet", {}))
        self._scan_cache: dict[pathlib.Path, tuple[float, csharp.CsFile, str]] = {}

    @classmethod
    def detect(cls, root: pathlib.Path) -> bool:
        return any(any(root.glob(pat)) for pat in ("*.sln", "*.slnx", "*.csproj"))

    # ---------- toolchain ----------
    @property
    def dotnet(self) -> str:
        if self.settings.executable:
            return self.settings.executable
        found = shutil.which("dotnet")
        if found:
            return found
        exe = "dotnet.exe" if sys.platform == "win32" else "dotnet"
        roots = [os.environ.get("DOTNET_ROOT", ""), str(pathlib.Path.home() / ".dotnet")]
        if sys.platform == "win32":
            roots.append(
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "dotnet")
            )
        else:
            roots += ["/usr/share/dotnet", "/usr/local/share/dotnet", "/usr/lib/dotnet"]
        for r in roots:
            if r and (pathlib.Path(r) / exe).is_file():
                return str(pathlib.Path(r) / exe)
        return "dotnet"

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(DOTNET_ENV)
        return env

    def tool_env(self) -> dict[str, str] | None:
        return self.env()

    def format_fix_commands(self, files: list[str]) -> list[list[str]]:
        """`dotnet format whitespace` (and `style`, while the lint check is the default one) on
        the changed files: the same rules the format / lint checks verify."""
        target = self.build_target()
        cmds = [[self.dotnet, "format", "whitespace", target, "--include", *files]]
        if "lint" not in self.cfg.gate_commands:
            cmds.append([self.dotnet, "format", "style", target, "--include", *files])
        return cmds

    def _exec(self, args: Sequence[str], timeout: int) -> tuple[int, str]:
        return run_command([self.dotnet, *args], self.root, env=self.env(), timeout=timeout)

    # ---------- projects ----------
    @property
    def solution(self) -> str | None:
        if self.settings.solution:
            return self.settings.solution
        found = sorted(p.name for pat in ("*.sln", "*.slnx") for p in self.root.glob(pat))
        if len(found) > 1:
            raise ConfigError(
                f"several solution files in the root ({', '.join(found)}); set [dotnet] solution"
            )
        return found[0] if found else None

    def _csproj_files(self) -> list[pathlib.Path]:
        out = []
        for p in self.root.rglob("*.csproj"):
            rel = p.relative_to(self.root).parts
            if not SKIP_DIRS & set(rel[:-1]) and not any(x.startswith(".") for x in rel[:-1]):
                out.append(p)
        return sorted(out)

    def detected_test_projects(self) -> list[str]:
        """Configured test projects, else the .csproj files under [paths] tests that reference
        Microsoft.NET.Test.Sdk (or set IsTestProject)."""
        if self.settings.test_projects:
            return list(self.settings.test_projects)
        tests = (self.root / self.cfg.paths.tests).resolve()
        found = []
        for p in self._csproj_files():
            text = p.read_text(encoding="utf-8-sig", errors="replace")
            in_tests = tests in p.resolve().parents
            if in_tests and ("Microsoft.NET.Test.Sdk" in text or "<IsTestProject>true" in text):
                found.append(self.rel(p))
        return found

    def test_targets(self) -> list[str]:
        if self.settings.test_projects:
            return list(self.settings.test_projects)
        sln = self.solution
        return [sln] if sln else self.detected_test_projects() or ["."]

    def build_target(self) -> str:
        return self.solution or (self.test_targets()[0])

    def _project_texts(self) -> str:
        texts = []
        for rel in self.detected_test_projects():
            p = self.root / rel
            if p.is_file():
                texts.append(p.read_text(encoding="utf-8-sig", errors="replace"))
        for name in ("Directory.Build.props", "Directory.Packages.props"):
            for p in (self.root / name, self.root / self.cfg.paths.tests / name):
                if p.is_file():
                    texts.append(p.read_text(encoding="utf-8-sig", errors="replace"))
        return "\n".join(texts)

    def references(self, package: str) -> bool:
        """True if a test project (or a Directory.*.props) mentions the package."""
        return package.lower() in self._project_texts().lower()

    def uses_junit_logger(self) -> bool:
        projects = self.detected_test_projects()
        return bool(projects) and all(
            "junitxml.testlogger"
            in (self.root / p).read_text(encoding="utf-8-sig", errors="replace").lower()
            for p in projects
            if (self.root / p).is_file()
        )

    # ---------- build ----------
    def build(self, targets: Sequence[str], extra: Sequence[str] = ()) -> tuple[int, str]:
        outputs = []
        for t in targets:
            code, out = self._exec(
                ["build", t, "-c", self.settings.configuration, *extra],
                self.settings.build_timeout,
            )
            outputs.append(out)
            if code != 0:
                return code, "\n".join(outputs) + self._build_failure_summary(out)
        return 0, "\n".join(outputs)

    def _build_failure_summary(self, output: str) -> str:
        errors = diagnostics(output, "error")
        if not errors:
            return "\nBUILD FAILED (see the output above)"
        lines = [self._relativize(text) for _, _, text in errors[:15]]
        more = f"\n... and {len(errors) - 15} more" if len(errors) > 15 else ""
        return "\nBUILD FAILED (compile errors):\n" + "\n".join(lines) + more

    def _relativize(self, text: str) -> str:
        root = str(self.root.resolve())
        for variant in (root, root.replace("\\", "/")):
            text = text.replace(variant + os.sep, "").replace(variant + "/", "")
        return text

    # ---------- tests ----------
    def _results_dir(self, kind: str) -> pathlib.Path:
        d = self.root / self.cfg.paths.out / ".dotnet" / kind
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _dotnet_test(
        self,
        filt: str,
        kind: str,
        junit: pathlib.Path | None = None,
        coverage: pathlib.Path | None = None,
        timeout: int | None = None,
    ) -> TestRun:
        targets = self.test_targets()
        outputs = []
        if junit is not None:
            junit.unlink(missing_ok=True)
        if self.settings.build_before_test:
            code, out = self.build(targets)
            if code != 0:
                return TestRun("error", code, out, None)
        # with coverage, the reports land next to the coverage attachments
        results = coverage if coverage is not None else self._results_dir(kind)
        results.mkdir(parents=True, exist_ok=True)
        junit_logger = self.uses_junit_logger()
        logger = (
            f"junit;LogFilePath={results / '{assembly}.junit.xml'};"
            "MethodFormat=Class;FailureBodyFormat=Verbose"
            if junit_logger
            else "trx"
        )
        codes = []
        limit = timeout or self.settings.test_timeout
        for t in targets:
            args = ["test", t, "-c", self.settings.configuration]
            if self.settings.build_before_test:
                args.append("--no-build")
            args += ["--results-directory", str(results), "--logger", logger]
            if filt:
                args += ["--filter", filt]
            if coverage is not None:
                args += ["--collect", "XPlat Code Coverage"]
            code, out = self._exec(args, limit)
            codes.append(code)
            outputs.append(out)
        report_files = sorted(results.rglob("*.junit.xml" if junit_logger else "*.trx"))
        cases: list[TestCase] = []
        for p in report_files:
            cases += parse_junit(p) if junit_logger else parse_trx(p)
        output = "\n".join(outputs)
        reports = bool(report_files)
        if junit is not None and cases:
            write_junit(cases, junit)
        output += f"\n{kind} run: {run_summary(cases)}" + (f" (filter: {filt})" if filt else "")
        reason = next((r for r in (incomplete_reason(c, output, limit) for c in codes) if r), "")
        if not reason and not reports and any(codes) and not diagnostics(output, "error"):
            reason = f"no test report (exit {max(codes)})"
        if not reason and coverage is not None and cases:
            if not any(coverage.rglob("coverage.cobertura.xml")):
                reason = "coverage file missing"
        if reason:
            code = TIMEOUT_CODE if TIMEOUT_CODE in codes else max(codes) or 1
            return TestRun("error", code, output, cases or None, incomplete=reason)
        if any(c.outcome in ("failed", "error") for c in cases):
            return TestRun("failed", max(codes), output, cases)
        if any(codes):
            if not self.settings.build_before_test:
                output += self._build_failure_summary(output)
            return TestRun("error", max(codes), output, cases if reports else None)
        if not cases:
            return TestRun("no_tests", 0, output, [] if junit is not None else None)
        return TestRun("passed", 0, output, cases)

    def run_tests(
        self,
        req_ids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
        fail_fast: bool = False,
        junit: pathlib.Path | None = None,
    ) -> TestRun:
        # fail_fast: VSTest has no stop-on-first-failure switch; the run is complete instead.
        ids = [i for i in (req_ids or []) if i]
        path_part = ""
        if paths:
            classes = self.test_classes(paths)
            if not classes:
                return TestRun("no_tests", 0, f"No xUnit test classes found under {list(paths)}")
            path_part = class_filter(classes)
        filt = combine_filters(req_filter(ids), path_part, self.settings.base_filter)
        kind = "review" if paths else ("ids" if ids else "all")
        return self._dotnet_test(filt, kind, junit=junit)

    def test_command(self, req_ids: Sequence[str] | None = None) -> str:
        ids = [i for i in (req_ids or []) if i]
        filt = combine_filters(req_filter(ids), self.settings.base_filter)
        targets = self.test_targets()
        target = targets[0] if len(targets) == 1 else self.build_target()
        cmd = f"dotnet test {target} -c {self.settings.configuration}"
        return cmd + (f' --filter "{filt}"' if filt else "")

    def stub_failure_problem(self, case: TestCase) -> str | None:
        if STUB_EXCEPTION in case.message:
            return None
        first = case.message.strip().splitlines()[0] if case.message.strip() else "no message"
        return f"expected a {STUB_EXCEPTION} from a stub, got: {first[:200]}"

    # ---------- static scans ----------
    def _test_files(self) -> list[pathlib.Path]:
        tests = self.root / self.cfg.paths.tests
        if not tests.is_dir():
            return []
        return sorted(
            p
            for p in tests.rglob("*.cs")
            if not SKIP_DIRS & set(p.relative_to(self.root).parts[:-1])
        )

    def _scan(self, path: pathlib.Path) -> tuple[csharp.CsFile, str]:
        mtime = path.stat().st_mtime
        hit = self._scan_cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1], hit[2]
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        parsed, mask = csharp.scan(text), csharp.strip(text)[1]
        self._scan_cache[path] = (mtime, parsed, mask)
        return parsed, mask

    def collect_req_tags(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for p in self._test_files():
            parsed, _ = self._scan(p)
            for m in parsed.tests:
                attrs = list(m.attrs)
                for c in m.class_chain():
                    attrs += c.attrs
                for a in attrs:
                    args = a.string_args() if a.name == "Trait" else None
                    if args and len(args) == 2 and args[0] == "Req":
                        where = f"{self.rel(p)}::{m.qualified}"
                        if where not in found.setdefault(args[1], []):
                            found[args[1]].append(where)
        return found

    def test_smells(self) -> list[str]:
        files = self._test_files()
        index: dict[str, list[str]] = {}
        parsed_files = []
        for p in files:
            parsed, mask = self._scan(p)
            parsed_files.append((p, parsed))
            for name, bodies in csharp.method_bodies(mask).items():
                index.setdefault(name, []).extend(bodies)
        problems = []
        for p, parsed in parsed_files:
            for m in parsed.tests:
                where = f"{self.rel(p)}::{m.qualified}"
                test_attrs = [
                    a
                    for a in m.attrs
                    if a.name in csharp.TEST_ATTR_NAMES
                    or a.name.endswith(csharp.TEST_ATTR_SUFFIXES)
                ]
                if any(re.search(r"\bSkip\s*=", a.args) for a in test_attrs):
                    problems.append(f"SKIPPED (Skip =): {where}")
                if re.match(r"\s*return\s*;", m.body):
                    problems.append(f"EARLY RETURN (test body starts with return;): {where}")
                elif not csharp.has_assertion(m.body, index):
                    problems.append(f"NO ASSERTION: {where}")
                if re.search(
                    r"\bAssert\s*\.\s*(?:True\s*\(\s*true|False\s*\(\s*false)\s*\)", m.body
                ):
                    problems.append(f"TRIVIAL ASSERTION (Assert.True(true)): {where}")
                if re.search(r"\bAssert\s*\.\s*Skip\w*\s*\(|\bSkip\s*\.\s*If\w*\s*\(", m.body):
                    problems.append(f"INLINE SKIP: {where}")
                if VACUOUS_LOOP.search(m.body) and not NON_EMPTY.search(m.body):
                    problems.append(
                        "LOOP OVER PICKED CASES WITHOUT A NON-EMPTY CHECK (passes with zero "
                        f"cases; assert there is at least one): {where}"
                    )
        return problems

    def test_classes(self, paths: Sequence[str]) -> list[str]:
        """Fully qualified names of the classes with tests in the .cs files under `paths`."""
        names: list[str] = []
        for raw in paths:
            base = (self.root / raw).resolve()
            files = (
                [base]
                if base.is_file()
                else sorted(
                    p
                    for p in base.rglob("*.cs")
                    if not SKIP_DIRS & set(p.relative_to(base).parts[:-1])
                )
                if base.is_dir()
                else []
            )
            for p in files:
                parsed, _ = self._scan(p)
                for m in parsed.tests:
                    if m.cls is not None and m.cls.full_name not in names:
                        names.append(m.cls.full_name)
        return names

    # ---------- gates ----------
    def default_gate_commands(self) -> dict[str, list[str]]:
        target = self.build_target()
        fmt = [self.dotnet, "format"]
        tail = [target, "--verify-no-changes", "--include", "{changed}"]
        cmds = {
            "lint": [*fmt, "style", *tail],
            "format": [*fmt, "whitespace", *tail],
            "typecheck": [self.dotnet, "build", target, "-c", self.settings.configuration],
        }
        if self.settings.warnings != "ignore":
            cmds["typecheck"].append("--no-incremental")
        return cmds

    def changed_cs(self, base_ref: str | None) -> list[str] | None:
        """Existing .cs files changed since base_ref (incl. untracked); None if unknown."""
        if not base_ref:
            return None
        from agentflow.checks.frame_diff import changed_files
        from agentflow.state import GitError, tag_exists

        try:
            if not tag_exists(self.cfg, base_ref):
                return None
            changed, _ = changed_files(self.cfg, base_ref)
        except GitError:
            return None
        return sorted(f for f in changed if f.endswith(".cs") and (self.root / f).is_file())

    def expand(self, cmd: Sequence[str], changed: list[str] | None) -> list[str] | None:
        """Resolve placeholders; `{changed}` -> the changed .cs files (or the src and tests
        folders when unknown). None if there is nothing to check."""
        out: list[str] = []
        for arg in cmd:
            if arg != "{changed}":
                out += self.resolve_command([arg])
            elif changed is None:
                out += [self.cfg.paths.src, self.cfg.paths.tests]
            elif not changed:
                return None
            else:
                out += changed
        return out

    def lint_checks(self, base_ref: str | None = None) -> list[Check]:
        """lint / format run on the .cs files changed since base_ref (`{changed}`); the
        default typecheck is a build that also fails on warnings in changed files."""
        defaults = self.default_gate_commands()
        checks = []
        for key in GATE_COMMAND_KEYS:
            configured = key in self.cfg.gate_commands
            cmd = self.cfg.gate_commands.get(key, defaults.get(key))
            if not cmd:
                continue
            if key == "typecheck" and not configured:
                checks.append(Check("typecheck", functools.partial(self._typecheck, cmd, base_ref)))
            else:
                run = functools.partial(self._gate_cmd, cmd, base_ref)
                checks.append(Check(GATE_NAMES[key], run))
        return checks

    def _gate_cmd(self, cmd: Sequence[str], base_ref: str | None) -> CheckResult:
        changed = self.changed_cs(base_ref) if "{changed}" in cmd else None
        argv = self.expand(cmd, changed)
        if argv is None:
            return CheckResult("PASS", "No changed C# files.")
        env = self.env() if pathlib.Path(argv[0]).stem.lower() == "dotnet" else None
        code, out = run_command(argv, self.root, env=env, timeout=self.settings.build_timeout)
        if code != 0 and "--verify-no-changes" in argv:
            # findings on lines the feature did not change are not its business: they would
            # make agents reformat old code (or loop on a file they may not edit)
            scoped, kept, ignored = self.scope_diagnostics(out, base_ref)
            if ignored and not kept:
                return CheckResult(
                    "PASS",
                    f"{ignored} finding(s) only on lines this feature did not change (ignored).",
                )
            if ignored:
                out = scoped + f"\n({ignored} finding(s) on unchanged lines ignored)"
        return CheckResult("PASS" if code == 0 else "FAIL", out)

    def _typecheck(self, cmd: Sequence[str], base_ref: str | None) -> CheckResult:
        code, out = run_command(
            self.resolve_command(cmd),
            self.root,
            env=self.env(),
            timeout=self.settings.build_timeout,
        )
        if code != 0:
            return CheckResult("FAIL", out + self._build_failure_summary(out))
        mode = self.settings.warnings
        warnings = diagnostics(out, "warning")
        if mode == "ignore" or not warnings:
            return CheckResult("PASS", out)
        if mode == "any":
            bad = warnings
        else:
            changed = self.changed_cs(base_ref)
            if changed is None:
                return CheckResult("PASS", out + "\n(warnings not checked: no base ref)")
            wanted = {c.lower() for c in changed}
            bad = [w for w in warnings if _rel_to(self.root, pathlib.Path(w[0])).lower() in wanted]
        if not bad:
            return CheckResult("PASS", out)
        lines = "\n".join(self._relativize(t) for _, _, t in bad[:20])
        what = "in changed files" if mode == "changed" else "(warnings = any)"
        return CheckResult("FAIL", f"{out}\nNEW WARNINGS {what}:\n{lines}")

    def scope_filter(self, scope: TestScope | None, base: str) -> str:
        """VSTest filter for the tests of a scope (IDs OR review classes) ANDed with `base`."""
        if scope is None or scope.whole_suite:
            return base
        parts = [req_filter(scope.req_ids)] if scope.req_ids else []
        classes = self.test_classes(scope.paths) if scope.paths else []
        if classes:
            parts.append(class_filter(classes))
        return combine_filters("|".join(parts), base)

    def regression_check(self) -> Check:
        def run() -> CheckResult:
            res = self._dotnet_test(
                self.settings.final_filter, "regression", timeout=self.cfg.regression_timeout
            )
            if res.incomplete:
                return incomplete_result(res.incomplete, res.output)
            return CheckResult("PASS" if res.status == "passed" else "FAIL", res.output)

        return Check("regression: whole suite, no coverage", run)

    def coverage_checks(self, base_ref: str, scope: TestScope | None = None) -> list[Check]:
        cov_dir = self.root / self.cfg.paths.out / "coverage"

        #: why the coverage run was cut short ("" = complete; None = not run / no coverage)
        state: dict[str, str | None] = {"incomplete": None}
        feature = scope is not None and not scope.whole_suite

        def full_suite() -> CheckResult:
            shutil.rmtree(cov_dir, ignore_errors=True)
            collect = self.references("coverlet.collector")
            run = self._dotnet_test(
                self.scope_filter(scope, self.settings.final_filter),
                "coverage" if feature else "final",
                coverage=cov_dir if collect else None,
                timeout=self.cfg.coverage_timeout,
            )
            if collect:
                state["incomplete"] = run.incomplete
            if run.incomplete:
                return incomplete_result(run.incomplete, run.output)
            note = (
                ""
                if collect
                else "\nSKIPPED coverage: the test project(s) do not reference coverlet.collector"
            )
            return CheckResult("PASS" if run.status == "passed" else "FAIL", run.output + note)

        def diff_coverage() -> CheckResult:
            if state["incomplete"]:
                return incomplete_result(
                    f"{state['incomplete']}; no diff coverage computed", what="coverage run"
                )
            reports = sorted(cov_dir.rglob("coverage.cobertura.xml")) if cov_dir.is_dir() else []
            if not reports:
                return CheckResult("SKIPPED", "SKIPPED: no Cobertura report (see previous check)")
            tool = self.diff_cover_command()
            if tool is None:
                return CheckResult(
                    "SKIPPED",
                    "SKIPPED: diff-cover not found (pip install 'agentflow[dotnet]', or "
                    "uv tool install diff-cover)",
                )
            normalized = []
            for i, r in enumerate(reports):
                dst = cov_dir / f"normalized-{i}.xml"
                normalize_cobertura(r, dst, self.root)
                normalized.append(str(dst))
            code, out = run_command(
                [
                    *tool,
                    *normalized,
                    f"--compare-branch={base_ref}",
                    f"--fail-under={self.cfg.diff_coverage}",
                ],
                self.root,
                timeout=self.settings.build_timeout,
            )
            summary = diff_coverage_summary(out, self.cfg.diff_coverage)
            return CheckResult("PASS" if code == 0 else "FAIL", out, summary=summary)

        return [
            Check(
                "coverage run (feature tests)" if feature else "full test suite + coverage",
                full_suite,
            ),
            Check(
                f"diff coverage >= {self.cfg.diff_coverage:g}%"
                + (" (feature tests)" if feature else ""),
                diff_coverage,
            ),
        ]

    def diff_cover_command(self) -> list[str] | None:
        exe = self.tool_path("diff-cover", sys.executable)
        if exe:
            return [exe]
        uvx = shutil.which("uvx")
        return [uvx, "--from", "diff-cover", "diff-cover"] if uvx else None

    # ---------- mutation ----------
    def stryker_command(self) -> list[str] | None:
        exe = shutil.which("dotnet-stryker")
        if not exe:
            name = "dotnet-stryker.exe" if sys.platform == "win32" else "dotnet-stryker"
            cand = pathlib.Path.home() / ".dotnet" / "tools" / name
            exe = str(cand) if cand.is_file() else None
        if exe:
            return [exe]
        manifest = self.root / ".config" / "dotnet-tools.json"
        if manifest.is_file() and "dotnet-stryker" in manifest.read_text(encoding="utf-8"):
            return [self.dotnet, "tool", "run", "dotnet-stryker", "--"]
        return None

    def owning_project(self, rel_file: str) -> pathlib.Path | None:
        d = (self.root / rel_file).parent
        root = self.root.resolve()
        while True:
            projects = sorted(d.glob("*.csproj"))
            if projects:
                return projects[0]
            if d.resolve() == root or d.parent == d:
                return None
            d = d.parent

    def mutation_check(
        self,
        feature: str,
        base_ref: str,
        changed_src: Sequence[str],
        scope: TestScope | None = None,
    ) -> CheckResult:
        tool = self.cfg.mutation_tool or "stryker"
        if tool == "none":
            return CheckResult(
                "SKIPPED", "SKIPPED: mutation testing disabled ([gates] mutation_tool)"
            )
        if tool != "stryker":
            return CheckResult("SKIPPED", f"SKIPPED: mutation tool '{tool}' is not supported")
        changed = [f for f in changed_src if f.endswith(".cs")]
        if not changed:
            return CheckResult("PASS", "No changed C# source files to mutate.")
        stryker = self.stryker_command()
        if stryker is None:
            return CheckResult(
                "SKIPPED",
                "SKIPPED: Stryker.NET is not installed (dotnet tool install -g dotnet-stryker)",
            )
        test_projects = self.detected_test_projects()
        if not test_projects:
            return CheckResult("SKIPPED", "SKIPPED: no test project found ([dotnet] test_projects)")
        groups: dict[pathlib.Path, list[str]] = {}
        for f in changed:
            proj = self.owning_project(f)
            if proj is not None:
                groups.setdefault(proj, []).append(f)
        work = self.root / self.cfg.paths.out / feature / "stryker"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        config_file = self._stryker_config(
            work, self.scope_filter(scope, self.settings.base_filter)
        )
        with self._mutation_tree(groups, base_ref) as (tree, note, line_maps):
            res = self._run_stryker(
                feature, stryker, groups, test_projects, config_file, tree, line_maps
            )
        if note:
            res.output = f"{res.output}\n{note}"
        return res

    @contextlib.contextmanager
    def _mutation_tree(
        self, groups: dict[pathlib.Path, list[str]], base_ref: str
    ) -> Iterator[tuple[pathlib.Path, str, dict[str, list[int]]]]:
        """A temporary git worktree of the working tree in which Stryker can mutate only the
        changed code: every C# file of the mutated projects starts with
        `// Stryker disable all`, and in a changed file `// Stryker restore all` /
        `// Stryker disable all` frame each hunk changed since base_ref (scope_mutations; a
        file without a diff, e.g. untracked, is mutated whole).

        Stryker.NET's --mutate only filters the report: it still compiles a mutant into every
        file of the project ("Removed by mutate filter"), and every instrumented line slows the
        tests that run it (40,829 mutants for one changed file: the coverage run did not end in
        an hour; hot code such as a match engine's inner loop is slow even when only its file
        is instrumented). Mutants disabled by comment are not compiled in at all. The
        repository's own files are never touched. Yields (root, note, line maps: repo-relative
        lower-case path -> original line of each line of the scoped copy); when no worktree can
        be made, (self.root, a note that the whole project is instrumented, {})."""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="agentflow-stryker-"))
        tree = tmp / self.root.name
        created = False
        line_maps: dict[str, list[int]] = {}
        try:
            try:
                self._git("worktree", "add", "--detach", "--quiet", str(tree), "HEAD")
                created = True
                self._mirror_working_tree(tree)
                keep = {
                    (self.root / f).resolve(): f.replace("\\", "/")
                    for fs in groups.values()
                    for f in fs
                }
                for proj in groups:
                    pdir = tree / proj.parent.resolve().relative_to(self.root.resolve())
                    for cs in pdir.rglob("*.cs"):
                        rel = cs.relative_to(tree)
                        if SKIP_DIRS.intersection(rel.parts[:-1]):
                            continue
                        changed = keep.get((self.root / rel).resolve())
                        if changed is None:
                            disable_mutations(cs)
                            continue
                        try:
                            diff = self._git("diff", "-U0", base_ref, "--", changed)
                        except subprocess.SubprocessError:  # unknown ref: the whole file
                            diff = ""
                        hunks = diff_hunks(diff)
                        if hunks:
                            text = cs.read_bytes().decode("utf-8")
                            scoped, orig = scope_mutations(text, hunks)
                            cs.write_bytes(scoped.encode("utf-8"))
                            line_maps[changed.lower()] = orig
            except (OSError, subprocess.SubprocessError, ValueError) as e:
                yield (
                    self.root,
                    (
                        f"Note: Stryker ran in the repository itself ({e}); it instrumented every "
                        "file of the project, not only the changed code."
                    ),
                    {},
                )
                return
            yield tree, "", line_maps
        finally:
            if created:
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    self._git("worktree", "remove", "--force", str(tree))
            shutil.rmtree(tmp, ignore_errors=True)
            if created:
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    self._git("worktree", "prune")

    def _git(self, *args: str) -> str:
        r = subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if r.returncode != 0:
            raise subprocess.SubprocessError(
                f"git {args[0]} failed: {(r.stderr or r.stdout).strip()}"
            )
        return r.stdout

    def _mirror_working_tree(self, tree: pathlib.Path) -> None:
        """Bring uncommitted changes (modified, deleted, untracked-not-ignored) into `tree`."""
        listed = self._git("ls-files", "-z", "--modified", "--others", "--exclude-standard")
        deleted = set(self._git("ls-files", "-z", "--deleted").split("\0"))
        for rel in filter(None, listed.split("\0")):
            dst = tree / rel
            if rel in deleted:
                dst.unlink(missing_ok=True)
            elif (self.root / rel).is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.root / rel, dst)

    def _run_stryker(
        self,
        feature: str,
        stryker: list[str],
        groups: dict[pathlib.Path, list[str]],
        test_projects: Sequence[str],
        config_file: pathlib.Path | None,
        tree: pathlib.Path,
        line_maps: dict[str, list[int]] | None = None,
    ) -> CheckResult:
        work = self.root / self.cfg.paths.out / feature / "stryker"
        detected = undetected = 0
        survivors: list[str] = []
        logs = []
        for proj, files in groups.items():
            refs = [
                tp
                for tp in test_projects
                if proj.name.lower()
                in (self.root / tp).read_text(encoding="utf-8-sig", errors="replace").lower()
            ] or test_projects
            out_dir = work / proj.stem
            cmd = [*stryker]
            if config_file is not None:
                cmd += ["-f", str(config_file)]
            for tp in refs:
                cmd += ["-tp", str(tree / tp)]
            cmd += ["--project", proj.name]
            for f in files:
                rel = (self.root / f).resolve().relative_to(proj.parent.resolve()).as_posix()
                cmd += ["--mutate", f"**/{rel}"]
            if self.settings.mutation_level:
                cmd += ["--mutation-level", self.settings.mutation_level]
            cmd += [
                "--configuration",
                self.settings.configuration,
                "-r",
                "Json",
                "-O",
                str(out_dir),
                "--skip-version-check",
                "--break-at",
                "0",
            ]
            timeout = self.settings.mutation_timeout or self.cfg.mutation_timeout
            code, out = run_command(cmd, tree, env=self.env(), timeout=timeout)
            (work / f"{proj.stem}.log").write_text(out, encoding="utf-8")
            report = out_dir / "reports" / "mutation-report.json"
            reason = incomplete_reason(code, out, timeout)
            if not reason and code == 0 and not report.is_file():
                reason = "no mutation report"
            if reason:
                return incomplete_result(reason, out, f"mutation run ({proj.name})")
            if code != 0 or not report.is_file():
                tail = "\n".join(out.splitlines()[-25:])
                return CheckResult("FAIL", f"Stryker failed for {proj.name} (exit {code}):\n{tail}")
            d, u, s = stryker_results(
                json.loads(report.read_text(encoding="utf-8-sig")), tree, files, line_maps
            )
            detected, undetected, survivors = detected + d, undetected + u, survivors + s
            logs.append(f"{proj.name}: {d} detected, {u} undetected")
        surv_file = self.root / self.cfg.paths.out / feature / "mutation_survivors.md"
        surv_file.write_text(
            "# Mutation survivors (Stryker.NET)\n\n" + ("\n".join(survivors) or "None.") + "\n",
            encoding="utf-8",
        )
        total = detected + undetected
        if total == 0:
            return CheckResult("PASS", "No mutants generated for the changed files.")
        score = 100.0 * detected / total
        summary = (
            f"Mutation score {score:.1f}% ({detected}/{total} killed), "
            f"threshold {self.cfg.mutation_score:g}%."
        )
        msg = summary + " " + "; ".join(logs)
        return CheckResult(
            "PASS" if score >= self.cfg.mutation_score else "FAIL", msg, summary=summary
        )

    def _stryker_config(self, work: pathlib.Path, filt: str) -> pathlib.Path | None:
        """A config file carrying `filt` (the feature scope's tests, or [dotnet] base_filter)
        as the test-case filter, merged into the project's stryker-config.json when it has one
        (a filter there is ANDed)."""
        if not filt:
            return None
        data: dict[str, Any] = {}
        own = self.root / "stryker-config.json"
        if own.is_file():
            try:
                data = json.loads(own.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                data = {}
        section = data.setdefault("stryker-config", {})
        section["test-case-filter"] = combine_filters(
            str(section.get("test-case-filter") or ""), filt
        )
        path = work / "stryker-config.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    # ---------- frame ----------
    def default_forbidden_patterns(self) -> list[str]:
        return [
            r"#\s*pragma\s+warning\s+disable",
            r"\bSuppressMessage\b",
            r"\bExcludeFromCodeCoverage\b",
            r"\bSkip\s*=",
            r"\bcatch\s*\(\s*(?:System\.)?Exception\s*\)",
            r"\bcatch\s*\{",
            r"\bcatch\s*\([^)]*\)\s*\{\s*\}",
        ]

    def default_protected_paths(self) -> list[str]:
        return [
            "*.csproj",
            "*.sln",
            "*.slnx",
            "*Directory.Build.props",
            "*Directory.Build.targets",
            "*Directory.Packages.props",
            "global.json",
            "*NuGet.config",
            "*.editorconfig",
            "*.runsettings",
            ".config/dotnet-tools.json",
            "stryker-config.json",
        ]

    # ---------- agents ----------
    def test_tools(self) -> list[str]:
        return ["Bash(dotnet test *)", "Bash(dotnet build *)"]

    def lint_tools(self) -> list[str]:
        return ["Bash(dotnet format *)"]

    def conventions_brief(self) -> str:
        p = self.cfg.paths
        try:
            target = self.build_target()
        except ConfigError:
            target = "<solution>"
        return BRIEF.format(
            configuration=self.settings.configuration,
            property=PROPERTY_FSCHECK if self.references("FsCheck") else PROPERTY_THEORY,
            run_all=self.test_command(),
            run_ids=self.test_command(["ID1", "ID2"]),
            target=target,
            tests=p.tests,
            review_tests=p.review_tests,
        )

    def ci_setup(self) -> str:
        sdk = (
            "          global-json-file: global.json\n"
            if (self.root / "global.json").is_file()
            else '          dotnet-version: "10.0.x"\n'
        )
        return (
            "      - uses: actions/setup-dotnet@v4\n"
            "        with:\n" + sdk + "      - uses: actions/setup-python@v5\n"
            "        with:\n"
            '          python-version: "3.12"\n'
            "      # adapt: pin agentflow (the [dotnet] extra brings diff-cover)\n"
            '      - run: pip install "agentflow[dotnet]"\n'
            "      - run: dotnet tool install -g dotnet-stryker\n"
        )

    # ---------- init ----------
    def config_template(self) -> str:
        return CONFIG_TEMPLATE

    def gitignore_entries(self) -> list[str]:
        return ["bin/", "obj/", "TestResults/"]

    def init_note(self) -> str:
        projects = self.detected_test_projects()
        review = (
            f"{pathlib.PurePosixPath(projects[0]).parent}/Review"
            if projects
            else "tests/<TestProject>/Review"
        )
        return (
            'tag tests with [Trait("Req", "<ID>")]. Set [paths] review_tests to a folder '
            f'inside a test project (e.g. review_tests = "{review}"): .cs files outside a '
            "project folder are not compiled. Set [dotnet] base_filter to your dev-loop filter. "
            "Final gates need diff-cover (pip install 'agentflow[dotnet]') and Stryker.NET "
            "(dotnet tool install -g dotnet-stryker)."
        )
