"""Python adapter: pytest (+ the agentflow `req` plugin), ruff, mypy, hypothesis, pytest-cov +
diff-cover, mutmut."""

from __future__ import annotations

import ast
import os
import pathlib
import re
import sys
from collections.abc import Sequence

from agentflow.adapters.base import (
    Check,
    CheckResult,
    LanguageAdapter,
    TestRun,
    parse_junit,
    run_command,
)

SKIP_MARKS = {"skip", "skipif", "xfail"}
PYTEST_STATUS = {0: "passed", 1: "failed", 5: "no_tests"}

BRIEF = """\
Language: Python. Test framework: pytest (agentflow plugin provides the `req` marker).
- Stubs (SPEC phase): full type-annotated signatures, docstrings naming the spec IDs they
  serve, body `raise NotImplementedError`. Stubs must import cleanly.
- Tag every test with the spec IDs it covers: `@pytest.mark.req("XXX-R-001", "XXX-E-002")`
  directly on the test function (or class). Do not use module-level `pytestmark`.
- Edge cases: assert the exact value, or `pytest.raises(ExcType, match="...")`.
- Invariants (I-IDs): property-based tests with hypothesis (`@given`).
- Every test needs a concrete assertion. No skip/skipif/xfail, no pytest.skip().
- Run tests: `python -m pytest -q` ; only some IDs: `python -m pytest -q --req ID1,ID2`.
- Lint/format/types: `ruff format {src} {tests}`, `ruff check {src} {tests}`,
  `python -m mypy {src}`.
- Forbidden in source code: `# type: ignore`, `noqa`, `pragma: no cover`, broad
  `except Exception`, skips.
- Tests live in {tests}/ (files test_*.py); reviewer tests in {review_tests}/.
"""


def _call_name(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _has_check(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Assert):
            return True
        if isinstance(n, ast.Call):
            name = _call_name(n)
            if name in {"raises", "warns", "fail"} or name.startswith("assert"):
                return True  # pytest.raises, np.testing.assert_allclose, assert_frame_equal...
    return False


def _is_skip_call(node: ast.Call) -> bool:
    """pytest.skip(...) / skip(...), but not the @pytest.mark.skip(...) decorator (reported as
    SKIP/XFAIL already)."""
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr == "skip" and not (
            isinstance(f.value, ast.Attribute) and f.value.attr == "mark"
        )
    return isinstance(f, ast.Name) and f.id == "skip"


class PythonAdapter(LanguageAdapter):
    name = "python"
    source_suffixes = (".py",)

    @classmethod
    def detect(cls, root: pathlib.Path) -> bool:
        markers = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
        return any((root / m).exists() for m in markers)

    # ---------- interpreter ----------
    @property
    def python(self) -> str:
        """[env] python, else the project's .venv interpreter, else the current interpreter."""
        if self.cfg.python:
            p = self.cfg.python
            if os.sep in p or "/" in p:
                return str((self.root / p).resolve()) if not os.path.isabs(p) else p
            return p
        for cand in (
            self.root / ".venv" / "Scripts" / "python.exe",
            self.root / ".venv" / "bin" / "python",
        ):
            if cand.is_file():
                return str(cand)
        return sys.executable

    def resolve_command(self, cmd: Sequence[str]) -> list[str]:
        out = super().resolve_command(cmd)
        if out and out[0] in ("python", "python3", "{python}"):
            out[0] = self.python
        return out

    def _has_module(self, module: str) -> bool:
        code, _ = run_command(
            [
                self.python,
                "-c",
                "import importlib.util,sys; "
                f"sys.exit(0 if importlib.util.find_spec({module!r}) else 1)",
            ],
            self.root,
        )
        return code == 0

    # ---------- tests ----------
    def run_tests(
        self,
        req_ids: Sequence[str] | None = None,
        paths: Sequence[str] | None = None,
        fail_fast: bool = False,
        junit: pathlib.Path | None = None,
    ) -> TestRun:
        cmd = [self.python, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
        ids = [i for i in (req_ids or []) if i]
        if ids:
            cmd += ["--req", ",".join(ids)]
        if fail_fast:
            cmd.append("-x")
        if junit is not None:
            junit.parent.mkdir(parents=True, exist_ok=True)
            junit.unlink(missing_ok=True)
            cmd.append(f"--junitxml={junit}")
        cmd += list(paths or [])
        code, out = run_command(cmd, self.root)
        cases = parse_junit(junit) if junit is not None and junit.exists() else None
        return TestRun(PYTEST_STATUS.get(code, "error"), code, out, cases)

    def test_command(self, req_ids: Sequence[str] | None = None) -> str:
        ids = [i for i in (req_ids or []) if i]
        return "python -m pytest -q" + (f" --req {','.join(ids)}" if ids else "")

    def _test_files(self) -> list[pathlib.Path]:
        tests = self.root / self.cfg.paths.tests
        if not tests.is_dir():
            return []
        files = set(tests.rglob("test_*.py")) | set(tests.rglob("*_test.py"))
        return sorted(files)

    def collect_req_tags(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for p in self._test_files():
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) == "req":
                        for arg in dec.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                found.setdefault(arg.value, []).append(
                                    f"{self.rel(p)}::{node.name}"
                                )
        return found

    def test_smells(self) -> list[str]:
        problems = []
        for p in self._test_files():
            where_file = self.rel(p)
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and node.name.startswith("test"):
                    where = f"{where_file}::{node.name}"
                    if not _has_check(node):
                        problems.append(f"NO ASSERTION: {where}")
                    for dec in node.decorator_list:
                        target = dec.func if isinstance(dec, ast.Call) else dec
                        if getattr(target, "attr", None) in SKIP_MARKS:
                            problems.append(f"SKIP/XFAIL: {where}")
                if isinstance(node, ast.Call) and _is_skip_call(node):
                    problems.append(f"pytest.skip() call in {where_file} line {node.lineno}")
        return problems

    # ---------- gates ----------
    def default_gate_commands(self) -> dict[str, list[str]]:
        p = self.cfg.paths
        cmds = {
            "lint": [self.python, "-m", "ruff", "check", p.src, p.tests],
            "format": [self.python, "-m", "ruff", "format", "--check", p.src, p.tests],
            "typecheck": [self.python, "-m", "mypy", p.src],
        }
        if (self.root / ".importlinter").exists():
            cmds["imports"] = [self.tool_path("lint-imports", self.python) or "lint-imports"]
        return cmds

    def coverage_checks(self, base_ref: str) -> list[Check]:
        out_dir = self.root / self.cfg.paths.out
        xml = out_dir / "coverage.xml"

        def full_suite() -> CheckResult:
            out_dir.mkdir(parents=True, exist_ok=True)
            xml.unlink(missing_ok=True)
            cmd = [self.python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
            note = ""
            if self._has_module("pytest_cov"):
                cmd += [f"--cov={self.cfg.paths.src}", "--cov-branch", f"--cov-report=xml:{xml}"]
            else:
                note = "\nSKIPPED coverage: pytest-cov is not installed in the project environment"
            code, out = run_command(cmd, self.root)
            return CheckResult("PASS" if code == 0 else "FAIL", out + note)

        def diff_coverage() -> CheckResult:
            if not xml.exists():
                return CheckResult("SKIPPED", "SKIPPED: no coverage report (see previous check)")
            tool = self.tool_path("diff-cover", self.python)
            if not tool:
                return CheckResult("SKIPPED", "SKIPPED: diff-cover is not installed")
            code, out = run_command(
                [
                    tool,
                    str(xml),
                    f"--compare-branch={base_ref}",
                    f"--fail-under={self.cfg.diff_coverage}",
                ],
                self.root,
            )
            return CheckResult("PASS" if code == 0 else "FAIL", out)

        return [
            Check("full test suite + coverage", full_suite),
            Check(f"diff coverage >= {self.cfg.diff_coverage:g}%", diff_coverage),
        ]

    def mutation_check(
        self, feature: str, base_ref: str, changed_src: Sequence[str]
    ) -> CheckResult:
        tool = self.cfg.mutation_tool or "mutmut"
        if tool == "none":
            return CheckResult(
                "SKIPPED", "SKIPPED: mutation testing disabled ([gates] mutation_tool)"
            )
        if tool != "mutmut":
            return CheckResult("SKIPPED", f"SKIPPED: mutation tool '{tool}' is not supported yet")
        changed = [f for f in changed_src if f.endswith(".py")]
        if not changed:
            return CheckResult("PASS", "No changed Python source files to mutate.")
        if os.name == "nt":
            return CheckResult(
                "SKIPPED",
                "SKIPPED: mutmut does not support native Windows; run the final gates in CI or WSL",
            )
        mutmut = self.tool_path("mutmut", self.python)
        if not mutmut:
            return CheckResult("SKIPPED", "SKIPPED: mutmut is not installed")
        return self._run_mutmut(mutmut, feature, changed)

    def _run_mutmut(self, mutmut: str, feature: str, changed: Sequence[str]) -> CheckResult:
        """mutmut >= 3: `mutmut run` (configured via [tool.mutmut]), then `mutmut results`.
        Mutant names look like `pkg.mod.x_func__mutmut_3`; filtered to the changed modules."""
        code, out = run_command([mutmut, "run"], self.root)
        if code not in (0, 1):
            return CheckResult("FAIL", f"mutmut run failed:\n{out}")
        code, res = run_command([mutmut, "results", "--all", "true"], self.root)
        src = self.cfg.paths.src.rstrip("/") + "/"
        modules = [
            f[len(src) :][:-3].replace("/", ".") if f.startswith(src) else f[:-3].replace("/", ".")
            for f in changed
        ]
        modules = [m[: -len(".__init__")] if m.endswith(".__init__") else m for m in modules]
        total = killed = 0
        survivors = []
        for line in res.splitlines():
            m = re.match(r"\s*([\w.]+__mutmut_\d+):\s*(\w[\w ]*)", line)
            if not m or not any(m.group(1).startswith(mod + ".") for mod in modules):
                continue
            total += 1
            status = m.group(2).strip()
            if status == "killed":
                killed += 1
            elif status in ("survived", "no tests"):
                survivors.append(
                    f"- `{m.group(1)}`: {status} (show with `mutmut show {m.group(1)}`)"
                )
        surv_file = self.root / self.cfg.paths.out / feature / "mutation_survivors.md"
        surv_file.parent.mkdir(parents=True, exist_ok=True)
        surv_file.write_text(
            "# Mutation survivors\n\n" + ("\n".join(survivors) or "None.") + "\n", encoding="utf-8"
        )
        if total == 0:
            return CheckResult("PASS", "No mutants generated for the changed files.")
        score = 100.0 * killed / total
        msg = (
            f"Mutation score {score:.1f}% ({killed}/{total} killed), "
            f"threshold {self.cfg.mutation_score:g}%."
        )
        return CheckResult("PASS" if score >= self.cfg.mutation_score else "FAIL", msg)

    # ---------- frame ----------
    def default_forbidden_patterns(self) -> list[str]:
        return [r"type:\s*ignore", r"noqa", r"pragma:\s*no cover", r"pytest\.skip", r"xfail"]

    def default_protected_paths(self) -> list[str]:
        return [
            "pyproject.toml",
            "setup.cfg",
            "setup.py",
            "requirements",
            "uv.lock",
            "poetry.lock",
            "Pipfile",
            ".importlinter",
            "mypy.ini",
            "ruff.toml",
            ".ruff.toml",
            "pytest.ini",
            "tox.ini",
            "conftest.py",
        ]

    # ---------- agents ----------
    def test_tools(self) -> list[str]:
        return ["Bash(python -m pytest *)", "Bash(pytest *)"]

    def lint_tools(self) -> list[str]:
        return [
            "Bash(ruff check *)",
            "Bash(ruff format *)",
            "Bash(python -m ruff *)",
            "Bash(python -m mypy *)",
        ]

    def conventions_brief(self) -> str:
        p = self.cfg.paths
        return BRIEF.format(src=p.src, tests=p.tests, review_tests=p.review_tests)

    def init_note(self) -> str:
        return (
            "add agentflow (pinned) to the project's dev dependencies; its pytest plugin "
            "provides the `req` marker and `--req`. Remove any conftest.py that defines `--req`."
        )

    def ci_setup(self) -> str:
        return (
            "      - uses: actions/setup-python@v5\n"
            "        with:\n"
            '          python-version: "3.12"\n'
            "      # adapt: install the project with its dev dependencies (agentflow pinned,\n"
            "      # pytest, ruff, mypy, pytest-cov, diff-cover, hypothesis, mutmut)\n"
            '      - run: pip install -e ".[dev]"\n'
        )
