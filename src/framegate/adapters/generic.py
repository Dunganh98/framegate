"""Generic adapter: any language, configured entirely in framegate.toml ([generic]).

A new language needs no framegate code: the project names its test commands (with placeholders),
how tests carry spec IDs (a regex), what a not-implemented stub prints, and optionally coverage,
mutation and format-fix commands. What it cannot do compared with a dedicated adapter: no
static test-smell scan beyond a configurable skip pattern, no per-hunk mutation scoping, and the
locations of tagged tests are file:line (not test names).

Placeholders in commands: {junit} (the JUnit XML report framegate reads), {ids} (spec IDs joined
by `ids_separator`), {ids_regex} (A|B), {paths} (as its own argument: one argument per path),
{files} (same, for format_fix), {coverage} (the coverage report framegate reads), {src},
{tests}, {out}.
"""

from __future__ import annotations

import pathlib
import re
import shlex
import uuid
from collections.abc import Sequence
from typing import Any

from framegate.adapters.base import (
    Check,
    CheckResult,
    LanguageAdapter,
    TestCase,
    TestRun,
    TestScope,
    diff_coverage_summary,
    incomplete_reason,
    incomplete_result,
    parse_junit,
    run_command,
)
from framegate.config import ConfigError

#: [generic] keys -> accepted python types (validated by the adapter; config.py stores them raw)
KEYS: dict[str, tuple[type, ...]] = {
    "source_suffixes": (list,),
    "test_files": (list,),
    "tag_pattern": (str,),
    "skip_pattern": (str,),
    "test_all": (list,),
    "test_ids": (list,),
    "test_paths": (list,),
    "ids_separator": (str,),
    "stub_marker": (str,),
    "coverage": (list,),
    "coverage_ids": (list,),
    "coverage_report": (str,),
    "mutation": (list,),
    "mutation_score_pattern": (str,),
    "format_fix": (list,),
    "forbidden_patterns": (list,),
    "protected": (list,),
    "conventions": (str,),
    "test_timeout": (int,),
}
REQUIRED = ("test_all", "test_ids", "tag_pattern")
INIT_NOTE = (
    "The generic adapter needs the [generic] section filled in (test commands, tag pattern, "
    "stub marker); /framegate:setup drafts it from the repository. Then run `framegate doctor` "
    "to check every command."
)

TEMPLATE = """\
# [generic]: any language, configured here (no framegate code needed). Example: TypeScript + vitest.
[generic]
source_suffixes = [".ts"]
test_files = ["**/*.test.ts"]                 # globs under [paths] tests
# group 1 holds a test's spec IDs, e.g. it("...", req("X-R-001"), ...)
tag_pattern = 'req\\(([^)]*)\\)'
test_all = ["npx", "vitest", "run", "--reporter=junit", "--outputFile={junit}"]
test_ids = ["npx", "vitest", "run", "-t", "{ids_regex}", "--reporter=junit", "--outputFile={junit}"]
test_paths = ["npx", "vitest", "run", "{paths}", "--reporter=junit", "--outputFile={junit}"]
stub_marker = "not implemented"               # in the failure message of a test that hit a stub
# skip_pattern = '\\b(it|test|describe)\\.(skip|todo)\\b'
# coverage = ["npx", "vitest", "run", "--coverage", "--coverage.reporter=cobertura",
#             "--coverage.reportsDirectory={out}"]
# coverage_report = "{out}/cobertura-coverage.xml"   # Cobertura XML or LCOV, read by diff-cover
# mutation = ["npx", "stryker", "run"]
# mutation_score_pattern = 'Final mutation score of ([\\d.]+)'
# format_fix = ["npx", "prettier", "--write", "{files}"]
# forbidden_patterns = ['@ts-ignore', 'eslint-disable']
# protected = ["package.json", "package-lock.json", "tsconfig.json"]
conventions = \"\"\"
Stubs: full typed signatures, a doc comment naming the spec IDs,
body `throw new Error("not implemented")`.
Tag every test with its spec IDs: it("...", req("XXX-R-001"), () => { ... }).
\"\"\"
"""


def _fill(cmd: Sequence[str], values: dict[str, Any]) -> list[str]:
    """Expand placeholders; a token that is exactly "{paths}" / "{files}" becomes one argument
    per item, other tokens use str.replace (braces in regexes stay as they are)."""
    out: list[str] = []
    for tok in cmd:
        if tok in ("{paths}", "{files}"):
            out += [str(x) for x in values.get(tok[1:-1], [])]
            continue
        if tok == "{src}" and isinstance(values.get("src_roots"), list):
            out += [str(x) for x in values["src_roots"]]  # one argument per source folder
            continue
        for key, value in values.items():
            if isinstance(value, str):
                tok = tok.replace("{" + key + "}", value)
        out.append(tok)
    return out


class GenericAdapter(LanguageAdapter):
    name = "generic"

    def __init__(self, cfg: Any) -> None:
        super().__init__(cfg)
        opts = dict(cfg.language_options.get("generic", {}))
        for key, value in opts.items():
            if key not in KEYS:
                raise ConfigError(f"[generic] unknown key '{key}' (allowed: {', '.join(KEYS)})")
            if not isinstance(value, KEYS[key]):
                raise ConfigError(f"[generic] {key} has the wrong type")
        missing = [k for k in REQUIRED if k not in opts]
        if missing:
            raise ConfigError(
                f"[generic] needs {', '.join(missing)} (see `framegate init --language generic`)"
            )
        self.opts = opts
        self.source_suffixes = tuple(opts.get("source_suffixes", []))  # type: ignore[misc]

    @classmethod
    def detect(cls, root: pathlib.Path) -> bool:
        return False  # chosen explicitly: `[project] language = "generic"`

    # ---------- helpers ----------
    def values(self, **extra: Any) -> dict[str, Any]:
        p = self.cfg.paths
        roots = list(self.cfg.src_roots)
        return {"src": p.src, "src_roots": roots, "tests": p.tests, "out": p.out, **extra}

    def _id_values(self, ids: Sequence[str]) -> dict[str, str]:
        sep = self.opts.get("ids_separator", ",")
        # IDs are letters, digits and hyphens: escape anything else, keep "-" readable
        esc = [re.sub(r"([^\w-])", r"\\\1", i) for i in ids]
        return {"ids": sep.join(ids), "ids_regex": "|".join(esc)}

    def _junit_path(self) -> pathlib.Path:
        return self.cfg.out_dir / f"junit-{uuid.uuid4().hex[:8]}.xml"

    # ---------- tests ----------
    def run_tests(
        self,
        req_ids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
        fail_fast: bool = False,
        junit: pathlib.Path | None = None,
    ) -> TestRun:
        ids = [i for i in (req_ids or []) if i]
        if paths:
            if "test_paths" not in self.opts:
                return TestRun("error", 2, "[generic] test_paths is not configured")
            template = self.opts["test_paths"]
        elif ids:
            template = self.opts["test_ids"]
        else:
            template = self.opts["test_all"]
        report = junit or self._junit_path()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.unlink(missing_ok=True)
        cmd = _fill(
            template,
            self.values(junit=str(report), paths=list(paths or []), **self._id_values(ids)),
        )
        timeout = self.opts.get("test_timeout")
        code, out = run_command(cmd, self.root, timeout=timeout)
        cases = parse_junit(report) if report.is_file() else None
        if junit is None:
            report.unlink(missing_ok=True)
        reason = incomplete_reason(code, out, timeout)
        if reason:
            return TestRun("error", code, out, cases, incomplete=reason)
        if cases is not None and not cases:
            status = "no_tests"
        elif code == 0:
            status = "passed"
        elif cases and any(c.outcome in ("failed", "error") for c in cases):
            status = "failed"
        else:
            status = "error"
        return TestRun(status, code, out, cases)

    def test_command(self, req_ids: Sequence[str] | None = None) -> str:
        ids = [i for i in (req_ids or []) if i]
        template = self.opts["test_ids"] if ids else self.opts["test_all"]
        junit = f"{self.cfg.paths.out}/agent-junit.xml"
        cmd = _fill(template, self.values(junit=junit, paths=[], **self._id_values(ids)))
        return shlex.join(cmd)

    def _test_files(self) -> list[pathlib.Path]:
        tests = self.root / self.cfg.paths.tests
        if not tests.is_dir():
            return []
        globs = self.opts.get("test_files") or ["**/*"]
        found: set[pathlib.Path] = set()
        for g in globs:
            found |= {p for p in tests.glob(g) if p.is_file()}
        return sorted(found)

    def collect_req_tags(self) -> dict[str, list[str]]:
        tag = re.compile(self.opts["tag_pattern"])
        idre = re.compile(self.cfg.id_pattern)
        found: dict[str, list[str]] = {}
        for p in self._test_files():
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in tag.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                where = f"{self.rel(p)}:{line}"
                for req in idre.findall(m.group(1) if m.groups() else m.group(0)):
                    if where not in found.setdefault(req, []):
                        found[req].append(where)
        return found

    def test_smells(self) -> list[str]:
        pattern = self.opts.get("skip_pattern")
        if not pattern:
            return []
        rx = re.compile(pattern)
        problems = []
        for p in self._test_files():
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
                if rx.search(line):
                    problems.append(f"SKIPPED: {self.rel(p)}:{n + 1}")
        return problems

    def stub_failure_problem(self, case: TestCase) -> str | None:
        marker = self.opts.get("stub_marker")
        if not marker or marker.lower() in case.message.lower():
            return None
        first = case.message.strip().splitlines()[0] if case.message.strip() else "no message"
        return f"expected '{marker}' from a stub, got: {first[:200]}"

    # ---------- gates ----------
    def default_gate_commands(self) -> dict[str, list[str]]:
        return {}  # lint / format / typecheck come from [gates.commands]

    def format_fix_commands(self, files: list[str]) -> list[list[str]]:
        cmd = self.opts.get("format_fix")
        return [_fill(cmd, self.values(files=files))] if cmd else []

    def format_fix_enabled(self) -> bool:
        # [gates.commands] format is how a generic project checks formatting; the fix is its own
        # [generic] format_fix command, so a configured format check does not turn it off
        return self.cfg.format_fix and bool(self.opts.get("format_fix"))

    def regression_run(self) -> TestRun:
        return self.run_tests()

    def coverage_checks(self, base_ref: str, scope: TestScope | None = None) -> list[Check]:
        feature = scope is not None and not scope.whole_suite
        cov = self.opts.get("coverage")
        if not cov:
            tests = Check(
                "full test suite" if not feature else "feature tests",
                lambda: self.regression_check().run(),
            )
            skipped = Check(
                "diff coverage",
                lambda: CheckResult("SKIPPED", "SKIPPED: [generic] coverage is not configured"),
            )
            return [tests, skipped]
        template = cov
        ids: list[str] = []
        if feature and self.opts.get("coverage_ids") and scope is not None and scope.req_ids:
            template, ids = self.opts["coverage_ids"], list(scope.req_ids)
        report_rel = _fill([self.opts.get("coverage_report", "{out}/coverage.xml")], self.values())[
            0
        ]
        report = self.root / report_rel
        state: dict[str, str | None] = {"incomplete": None}

        def run_cov() -> CheckResult:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.unlink(missing_ok=True)
            cmd = _fill(
                template,
                self.values(
                    coverage=report_rel, junit=str(self._junit_path()), **self._id_values(ids)
                ),
            )
            timeout = self.cfg.coverage_timeout
            code, out = run_command(cmd, self.root, timeout=timeout)
            reason = incomplete_reason(code, out, timeout)
            if not reason and not report.is_file():
                reason = f"coverage report {report_rel} missing"
            state["incomplete"] = reason
            if reason:
                return incomplete_result(reason, out)
            return CheckResult("PASS" if code == 0 else "FAIL", out)

        def diff_coverage() -> CheckResult:
            if state["incomplete"]:
                return incomplete_result(
                    f"{state['incomplete']}; no diff coverage computed", what="coverage run"
                )
            tool = self.tool_path("diff-cover")
            if not tool:
                return CheckResult("SKIPPED", "SKIPPED: diff-cover is not installed")
            code, out = run_command(
                [
                    tool,
                    str(report),
                    f"--compare-branch={base_ref}",
                    f"--fail-under={self.cfg.diff_coverage}",
                ],
                self.root,
            )
            summary = diff_coverage_summary(out, self.cfg.diff_coverage)
            return CheckResult("PASS" if code == 0 else "FAIL", out, summary=summary)

        suffix = " (feature tests)" if feature and ids else ""
        return [
            Check(f"coverage run{suffix}", run_cov),
            Check(f"diff coverage >= {self.cfg.diff_coverage:g}%{suffix}", diff_coverage),
        ]

    def mutation_check(
        self,
        feature: str,
        base_ref: str,
        changed_src: Sequence[str],
        scope: TestScope | None = None,
    ) -> CheckResult:
        cmd = self.opts.get("mutation")
        if not cmd or self.cfg.mutation_tool == "none":
            return CheckResult("SKIPPED", "SKIPPED: [generic] mutation is not configured")
        if not changed_src:
            return CheckResult("PASS", "No changed source files.")
        timeout = self.cfg.mutation_timeout
        code, out = run_command(
            _fill(cmd, self.values(files=list(changed_src))), self.root, timeout=timeout
        )
        reason = incomplete_reason(code, out, timeout)
        if reason:
            return incomplete_result(reason, out, what="mutation run")
        pattern = self.opts.get("mutation_score_pattern")
        m = re.search(pattern, out) if pattern else None
        if not m:
            return CheckResult("FAIL", out + "\nno mutation score found (mutation_score_pattern)")
        score = float(m.group(1))
        ok = score >= self.cfg.mutation_score
        summary = f"Mutation score {score:g}%, threshold {self.cfg.mutation_score:g}%."
        return CheckResult("PASS" if ok else "FAIL", out, summary=summary)

    # ---------- frame ----------
    def default_forbidden_patterns(self) -> list[str]:
        return [str(x) for x in self.opts.get("forbidden_patterns", [])]

    def default_protected_paths(self) -> list[str]:
        return [str(x) for x in self.opts.get("protected", [])]

    # ---------- agents ----------
    def _rules(self, cmds: Sequence[Sequence[str]]) -> list[str]:
        heads = dict.fromkeys(str(c[0]) for c in cmds if c)
        return [f"Bash({h} *)" for h in heads]

    def test_tools(self) -> list[str]:
        keys = ("test_all", "test_ids", "test_paths")
        return self._rules([self.opts[k] for k in keys if k in self.opts])

    def lint_tools(self) -> list[str]:
        cmds = [c for c in self.cfg.gate_commands.values() if c]
        if self.opts.get("format_fix"):
            cmds.append(self.opts["format_fix"])
        return self._rules(cmds)

    def conventions_brief(self) -> str:
        p = self.cfg.paths
        text = str(self.opts.get("conventions", "")).strip()
        lines = [
            "Language: configured in framegate.toml ([generic]).",
            text,
            f"- Run tests: `{self.test_command()}`; only some IDs: "
            f"`{self.test_command(['ID1', 'ID2'])}`.",
            f"- Tests live in {p.tests}/; reviewer tests in {p.review_tests}/.",
        ]
        forbidden = self.default_forbidden_patterns()
        if forbidden:
            lines.append("- Forbidden in source code (regexes): " + ", ".join(forbidden))
        return "\n".join(x for x in lines if x)

    def tool_report(self) -> list[tuple[str, bool, str]]:
        out = []
        for key in ("test_all", "coverage", "mutation", "format_fix"):
            cmd = self.opts.get(key)
            if cmd:
                exe = self.tool_path(str(cmd[0]))
                out.append((f"{cmd[0]} ([generic] {key})", bool(exe), exe or "not found on PATH"))
        if self.opts.get("coverage"):
            diff = self.tool_path("diff-cover")
            out.append(("diff-cover (diff coverage)", bool(diff), diff or "install diff-cover"))
        return out

    def config_template(self) -> str:
        return TEMPLATE

    def init_note(self) -> str:
        return INIT_NOTE
