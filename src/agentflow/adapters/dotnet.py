""".NET adapter: xUnit via `dotnet test` (VSTest), `[Trait("Req", ...)]` tagging, dotnet format,
dotnet build, coverlet + diff-cover, Stryker.NET. See docs/adapters.md."""

from __future__ import annotations

import functools
import json
import os
import pathlib
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
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
    diff_coverage_summary,
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
# mutation_timeout = 7200
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
    mutation_timeout: int = 7200

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> DotnetSettings:
        s = cls(**options)
        if s.warnings not in WARNING_MODES:
            raise ConfigError(f"[dotnet] warnings must be one of {', '.join(WARNING_MODES)}")
        for key in ("build_timeout", "test_timeout", "mutation_timeout"):
            if getattr(s, key) < 1:
                raise ConfigError(f"[dotnet] {key} must be >= 1")
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


def stryker_results(
    report: dict[str, Any], root: pathlib.Path, files: Sequence[str]
) -> tuple[int, int, list[str]]:
    """(detected, undetected, survivor lines) for the given repo-relative files from a Stryker
    mutation-report.json."""
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
                survivors.append(
                    f"- `{rel}:{start.get('line', '?')}:{start.get('column', '?')}` "
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
class DotnetAdapter(LanguageAdapter):
    name = "dotnet"
    source_suffixes = (".cs",)

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
        for t in targets:
            args = ["test", t, "-c", self.settings.configuration]
            if self.settings.build_before_test:
                args.append("--no-build")
            args += ["--results-directory", str(results), "--logger", logger]
            if filt:
                args += ["--filter", filt]
            if coverage is not None:
                args += ["--collect", "XPlat Code Coverage"]
            code, out = self._exec(args, self.settings.test_timeout)
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
        if TIMEOUT_CODE in codes:
            return TestRun("error", TIMEOUT_CODE, output, cases or None)
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

    def coverage_checks(self, base_ref: str) -> list[Check]:
        cov_dir = self.root / self.cfg.paths.out / "coverage"

        def full_suite() -> CheckResult:
            shutil.rmtree(cov_dir, ignore_errors=True)
            collect = self.references("coverlet.collector")
            run = self._dotnet_test(
                self.settings.final_filter, "final", coverage=cov_dir if collect else None
            )
            note = (
                ""
                if collect
                else "\nSKIPPED coverage: the test project(s) do not reference coverlet.collector"
            )
            return CheckResult("PASS" if run.status == "passed" else "FAIL", run.output + note)

        def diff_coverage() -> CheckResult:
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
            Check("full test suite + coverage", full_suite),
            Check(f"diff coverage >= {self.cfg.diff_coverage:g}%", diff_coverage),
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
        self, feature: str, base_ref: str, changed_src: Sequence[str]
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
        config_file = self._stryker_config(work)
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
                cmd += ["-tp", str(self.root / tp)]
            cmd += ["--project", proj.name]
            for f in files:
                rel = (self.root / f).resolve().relative_to(proj.parent.resolve()).as_posix()
                cmd += ["--mutate", f"**/{rel}"]
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
            code, out = run_command(
                cmd, self.root, env=self.env(), timeout=self.settings.mutation_timeout
            )
            (work / f"{proj.stem}.log").write_text(out, encoding="utf-8")
            report = out_dir / "reports" / "mutation-report.json"
            if code != 0 or not report.is_file():
                tail = "\n".join(out.splitlines()[-25:])
                return CheckResult("FAIL", f"Stryker failed for {proj.name} (exit {code}):\n{tail}")
            d, u, s = stryker_results(
                json.loads(report.read_text(encoding="utf-8-sig")), self.root, files
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

    def _stryker_config(self, work: pathlib.Path) -> pathlib.Path | None:
        """A config file carrying [dotnet] base_filter as the test-case filter, merged into the
        project's stryker-config.json when it has one."""
        if not self.settings.base_filter:
            return None
        data: dict[str, Any] = {}
        own = self.root / "stryker-config.json"
        if own.is_file():
            try:
                data = json.loads(own.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                data = {}
        section = data.setdefault("stryker-config", {})
        section.setdefault("test-case-filter", self.settings.base_filter)
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
