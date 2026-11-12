"""Python adapter: pytest (+ the agentflow `req` plugin), ruff, mypy, hypothesis, pytest-cov +
diff-cover, mutmut."""

from __future__ import annotations

import ast
import hashlib
import importlib.resources
import json
import os
import pathlib
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

from agentflow import __version__, pytools, release_requirement
from agentflow.adapters import wsl
from agentflow.adapters.base import (
    Check,
    CheckResult,
    LanguageAdapter,
    TestRun,
    TestScope,
    diff_coverage_summary,
    incomplete_reason,
    incomplete_result,
    parse_junit,
    run_command,
)

SKIP_MARKS = {"skip", "skipif", "xfail"}
#: bump to force the WSL mutmut venvs to be rebuilt
MUTMUT_ENV_VERSION = 2  # 2: pytest plugin --req-paths
#: files whose content keys the WSL venv (besides the requirement list itself)
LOCK_FILES = ("pyproject.toml", "uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock")
#: mutmut 3 statuses (mutmut/stats.py); "skipped" / "not checked" don't count
DETECTED = {"killed", "timeout", "caught by type check", "segfault"}
SURVIVED = {"survived", "no tests", "suspicious"}
MUTANT_LINE = re.compile(r"^\s*(\S+__mutmut_\d+):\s*(.+?)\s*$")
PYTEST_STATUS = {0: "passed", 1: "failed", 5: "no_tests"}
#: pytest exit codes of a run that did not reach the end
PYTEST_INCOMPLETE = {2: "pytest interrupted (exit 2)", 3: "pytest internal error (exit 3)"}

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


def _picked(node: ast.AST, names: set[str]) -> bool:
    """A collection of cases picked from data: a comprehension with an `if`, `filter(...)`, or
    a name bound to one of those."""
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return any(g.ifs for g in node.generators)
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in {"list", "tuple", "set", "sorted"} and node.args:
            return _picked(node.args[0], names)
        return name == "filter"
    return isinstance(node, ast.Name) and node.id in names


def _vacuous_loop(fn: ast.AST) -> bool:
    """A loop / all() over picked cases without anything that checks there is at least one
    (len(), any(), `assert picked`): passes when the data has no such case."""
    names = {
        t.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign) and _picked(n.value, set())
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    loops = [
        n
        for n in ast.walk(fn)
        if (isinstance(n, (ast.For, ast.AsyncFor)) and _picked(n.iter, names))
        or (
            isinstance(n, ast.Call)
            and _call_name(n) == "all"
            and bool(n.args)
            and _picked(n.args[0], names)
        )
    ]
    if not loops:
        return False
    guarded = any(
        (isinstance(n, ast.Call) and _call_name(n) in {"len", "any", "next"})
        or (isinstance(n, ast.Assert) and isinstance(n.test, ast.Name))
        for n in ast.walk(fn)
    )
    return not guarded


def _is_skip_call(node: ast.Call) -> bool:
    """pytest.skip(...) / skip(...), but not the @pytest.mark.skip(...) decorator (reported as
    SKIP/XFAIL already)."""
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr == "skip" and not (
            isinstance(f.value, ast.Attribute) and f.value.attr == "mark"
        )
    return isinstance(f, ast.Name) and f.id == "skip"


def _shell_quote(arg: str) -> str:
    """An argument as it is typed on a command line (and read back by PYTEST_ADDOPTS)."""
    return arg if re.fullmatch(r"[\w@%+=:,./-]+", arg) else '"' + arg.replace('"', '\\"') + '"'


class PythonAdapter(LanguageAdapter):
    name = "python"
    source_suffixes = (".py",)

    @classmethod
    def detect(cls, root: pathlib.Path) -> bool:
        markers = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
        return any((root / m).exists() for m in markers)

    @property
    def pytest_args(self) -> list[str]:
        """[python] pytest_args: added to every pytest run agentflow starts and to the command
        the agents are told to use (e.g. ["-m", "not integration and not spark"] for tests
        that need services the machine does not have)."""
        return list(self.cfg.language_options.get("python", {}).get("pytest_args", []))

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

    def tool_requirements(self) -> list[pytools.ToolReq]:
        """What the workflow needs in the project interpreter. Always: the agentflow pytest
        plugin (the `req` marker, `--req`), pytest, pytest-cov, hypothesis, diff-cover. With
        an agentflow.toml also the lint / format / type tools its gate commands name (before
        it exists the commands are not settled: `/agentflow:setup` may switch one off)."""
        src = agentflow_source()
        # from a checkout: that source tree; else this version from its git tag (the package
        # is not on PyPI, where the name belongs to someone else)
        own = ("-e", str(src)) if src is not None else (release_requirement(),)
        reqs = [
            pytools.ToolReq("agentflow", own, "pytest plugin: req marker, --req"),
            pytools.ToolReq("pytest", ("pytest",), "tests"),
            pytools.ToolReq("pytest_cov", ("pytest-cov",), "coverage"),
            pytools.ToolReq("hypothesis", ("hypothesis",), "property tests"),
            pytools.ToolReq("diff_cover", ("diff-cover",), "diff coverage"),
        ]
        if self.cfg.loaded_from_file:
            commands = {**self.default_gate_commands(), **self.cfg.gate_commands}
            reqs += pytools.gate_tools([*commands.values(), self.cfg.format_fix_command])
        return reqs

    def install_tools(self, dry_run: bool, echo: Callable[[str], None]) -> int:
        return pytools.install(
            self.python, self.root, self.tool_requirements(), self._has_module, dry_run, echo
        )

    def tool_report(self) -> list[tuple[str, bool, str]]:
        out = []
        ok = self._has_module("agentflow")
        hint = "run: agentflow install-tools"
        out.append(("agentflow (pytest plugin: --req)", ok, self.python if ok else hint))
        for module, what in (
            ("pytest", "tests"),
            ("pytest_cov", "coverage"),
            ("ruff", "lint / format"),
            ("mypy", "type check"),
        ):
            ok = self._has_module(module)
            out.append((f"{module} ({what})", ok, self.python if ok else f"install {module}"))
        diff = self.tool_path("diff-cover", self.python)
        out.append(("diff-cover (diff coverage)", bool(diff), diff or "install diff-cover"))
        if self.cfg.mutation_tool == "none":
            out.append(("mutmut (mutation)", True, "mutation off ([gates] mutation_tool = none)"))
        elif self.mutation_runner() == "wsl":
            out.append(
                ("mutmut (mutation)", True, "runs in WSL (checked by the first mutation run)")
            )
        else:
            ok = self._has_module("mutmut")
            out.append(("mutmut (mutation)", ok, self.python if ok else "install mutmut"))
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
        cmd += self.pytest_args
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
        extra = "".join(f" {_shell_quote(a)}" for a in self.pytest_args)
        return "python -m pytest -q" + extra + (f" --req {','.join(ids)}" if ids else "")

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
                    elif _vacuous_loop(node):
                        problems.append(
                            "NOTE: LOOP OVER PICKED CASES WITHOUT A VISIBLE NON-EMPTY CHECK "
                            "(zero cases would "
                            f"pass; the test reviewer decides): {where}"
                        )
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
            "lint": [self.python, "-m", "ruff", "check", *self.cfg.src_roots, p.tests],
            "format": [
                self.python,
                "-m",
                "ruff",
                "format",
                "--check",
                *self.cfg.src_roots,
                p.tests,
            ],
            "typecheck": [self.python, "-m", "mypy", *self.cfg.src_roots],
        }
        if (self.root / ".importlinter").exists():
            cmds["imports"] = [self.tool_path("lint-imports", self.python) or "lint-imports"]
        return cmds

    def format_fix_commands(self, files: list[str]) -> list[list[str]]:
        return [[self.python, "-m", "ruff", "format", *files]]

    def scope_args(self, scope: TestScope | None) -> list[str]:
        """pytest options selecting the tests of a scope (agentflow plugin)."""
        if scope is None or scope.whole_suite:
            return []
        args = ["--req", ",".join(scope.req_ids)] if scope.req_ids else []
        return args + (["--req-paths", ",".join(scope.paths)] if scope.paths else [])

    def regression_run(self) -> TestRun:
        timeout = self.cfg.regression_timeout
        junit = self.root / self.cfg.paths.out / "regression.xml"
        junit.parent.mkdir(parents=True, exist_ok=True)
        junit.unlink(missing_ok=True)
        cmd = [self.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"]
        cmd += self.pytest_args
        code, out = run_command(cmd, self.root, timeout=timeout)
        reason = incomplete_reason(code, out, timeout) or PYTEST_INCOMPLETE.get(code, "")
        cases = parse_junit(junit) if junit.is_file() else None
        status = "error" if reason else PYTEST_STATUS.get(code, "error")
        return TestRun(status, code, out, cases, incomplete=reason)

    def scope_coverage_reports(
        self, req_ids: Sequence[str], files: Sequence[str]
    ) -> tuple[list[pathlib.Path], str]:
        if not self._has_module("pytest_cov"):
            return [], "pytest-cov is not installed in the project environment"
        xml = self.root / self.cfg.paths.out / "scope_coverage.xml"
        xml.parent.mkdir(parents=True, exist_ok=True)
        xml.unlink(missing_ok=True)
        cmd = [self.python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        ids = [i for i in req_ids if i]
        if ids:
            cmd += ["--req", ",".join(ids)]
        cmd += [f"--cov={root}" for root in self.cfg.src_roots]
        cmd += [f"--cov-report=xml:{xml}"]
        timeout = self.cfg.coverage_timeout
        code, out = run_command(cmd, self.root, timeout=timeout)
        reason = incomplete_reason(code, out, timeout) or PYTEST_INCOMPLETE.get(code, "")
        if reason or not xml.is_file():
            return [], f"coverage run incomplete ({reason or 'no report'})"
        return [xml], ""

    def coverage_checks(self, base_ref: str, scope: TestScope | None = None) -> list[Check]:
        out_dir = self.root / self.cfg.paths.out
        xml = out_dir / "coverage.xml"

        #: why the coverage run was cut short ("" = complete; None = not run / no coverage)
        state: dict[str, str | None] = {"incomplete": None}

        def full_suite() -> CheckResult:
            out_dir.mkdir(parents=True, exist_ok=True)
            xml.unlink(missing_ok=True)
            cmd = [self.python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
            cmd += self.pytest_args
            cmd += self.scope_args(scope)
            note = ""
            collect = self._has_module("pytest_cov")
            if collect:
                cmd += [f"--cov={root}" for root in self.cfg.src_roots]
                cmd += ["--cov-branch", f"--cov-report=xml:{xml}"]
            else:
                note = "\nSKIPPED coverage: pytest-cov is not installed in the project environment"
            timeout = self.cfg.coverage_timeout
            code, out = run_command(cmd, self.root, timeout=timeout)
            reason = incomplete_reason(code, out, timeout) or PYTEST_INCOMPLETE.get(code, "")
            if not reason and collect and code in (0, 1) and not xml.is_file():
                reason = "coverage file missing"
            if collect:
                state["incomplete"] = reason
            if reason:
                return incomplete_result(reason, out)
            return CheckResult("PASS" if code == 0 else "FAIL", out + note)

        def diff_coverage() -> CheckResult:
            if state["incomplete"]:
                return incomplete_result(
                    f"{state['incomplete']}; no diff coverage computed", what="coverage run"
                )
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
            summary = diff_coverage_summary(out, self.cfg.diff_coverage)
            return CheckResult("PASS" if code == 0 else "FAIL", out, summary=summary)

        feature = scope is not None and not scope.whole_suite
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

    def mutation_runner(self) -> str:
        """Where mutmut runs: "native" or "wsl" ("auto" = wsl on Windows, else native)."""
        runner = self.cfg.mutation_runner
        if runner == "auto":
            return "wsl" if os.name == "nt" else "native"
        return runner

    def mutation_check(
        self,
        feature: str,
        base_ref: str,
        changed_src: Sequence[str],
        scope: TestScope | None = None,
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
        modules = self._mutmut_modules(changed)
        if self.mutation_runner() == "wsl":
            return self._mutmut_wsl(feature, modules, scope)
        if os.name == "nt":
            return CheckResult(
                "SKIPPED",
                "SKIPPED: mutmut does not support native Windows; set [gates] mutation_runner = "
                '"wsl" (or "auto") or run the final gates in CI',
            )
        if not self._has_module("mutmut"):
            return CheckResult("SKIPPED", "SKIPPED: mutmut is not installed")
        work = self._mutmut_dir()
        spec = self._mutmut_spec(modules, str(self.root), str(work / "result.json"), scope)
        (work / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        cmd = [self.python, str(work / "runner.py"), str(work / "spec.json")]
        return self._mutmut_finish(feature, modules, cmd, "native")

    # ---------- mutmut internals ----------
    def _mutmut_modules(self, changed: Sequence[str]) -> list[str]:
        """Changed files -> module names as mutmut names them (relative to `src/`)."""
        roots = [r.rstrip("/") + "/" for r in self.cfg.src_roots]

        def module(f: str) -> str:
            root = next((r for r in roots if f.startswith(r)), "")
            return f[len(root) : -3].replace("/", ".")

        mods = [module(f) for f in changed]
        return sorted({m[: -len(".__init__")] if m.endswith(".__init__") else m for m in mods})

    def _mutmut_dir(self) -> pathlib.Path:
        """out/.mutmut/ with a fresh copy of the runner script."""
        work = self.root / self.cfg.paths.out / ".mutmut"
        work.mkdir(parents=True, exist_ok=True)
        runner = importlib.resources.files("agentflow.adapters").joinpath("mutmut_runner.py")
        (work / "runner.py").write_text(runner.read_text(encoding="utf-8"), encoding="utf-8")
        return work

    def _mutmut_spec(
        self, modules: Sequence[str], project: str, result: str, scope: TestScope | None = None
    ) -> dict[str, Any]:
        return {
            "project": project,
            "result": result,
            # top-level functions (x_name) and methods (x<sep>Class<sep>name) of the modules
            "patterns": [f"{m}.x*" for m in modules],
            "show_modules": list(modules),
            "show_limit": 50,
            "timeout": self.cfg.mutation_timeout,
            "pytest_addopts": " ".join(
                _shell_quote(a) for a in [*self.pytest_args, *self.scope_args(scope)]
            ),
        }

    def _mutmut_wsl(
        self, feature: str, modules: Sequence[str], scope: TestScope | None = None
    ) -> CheckResult:
        distro = self.cfg.wsl_distro
        wsl_root, problem = wsl.probe(self.root, distro)
        if wsl_root is None:
            return CheckResult(
                "SKIPPED",
                f"SKIPPED: mutation testing via WSL is unavailable: {problem}. mutmut does not "
                "support native Windows; run the final gates in CI or fix WSL.",
            )
        work = self._mutmut_dir()
        root_wsl: str = wsl_root

        def w(path: pathlib.Path) -> str:
            return wsl.to_wsl(path, self.root, root_wsl, distro)

        reqs, files, stamp = self._wsl_requirements(w)
        spec = self._mutmut_spec(modules, wsl_root, w(work / "result.json"), scope)
        repo_key = hashlib.sha256(wsl_root.encode("utf-8")).hexdigest()[:16]
        spec.update(
            venv=f"~/.cache/agentflow/venvs/{repo_key}",
            stamp=stamp,
            requirements=reqs,
            requirement_files=files,
        )
        (work / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        cmd = wsl.wsl_command(["python3", w(work / "runner.py"), w(work / "spec.json")], distro)
        return self._mutmut_finish(feature, modules, cmd, f"WSL {distro}" if distro else "WSL")

    def _wsl_requirements(
        self, w: Callable[[pathlib.Path], str]
    ) -> tuple[list[str], list[str], str]:
        """What the WSL venv needs: the project's dependencies (all extras and dependency
        groups, requirements*.txt), mutmut and pytest, and agentflow itself (its pytest plugin)
        from this source tree when agentflow runs from one. The project itself is not installed:
        mutmut puts its mutated copy on sys.path. Returns (requirements, -r files, stamp)."""
        reqs, files = collect_requirements(self.root)
        own = agentflow_source()
        reqs = [r for r in reqs if not (own and req_name(r) == "agentflow")]
        reqs += ["mutmut>=3", "pytest"] + ([w(own)] if own else [])
        reqs = [translate_drive_paths(r, w) for r in reqs]
        digest = hashlib.sha256()
        digest.update(json.dumps([MUTMUT_ENV_VERSION, __version__, reqs]).encode("utf-8"))
        for path in [self.root / n for n in LOCK_FILES] + files:
            if path.is_file():
                digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes())
        return reqs, [w(f) for f in files], digest.hexdigest()

    def _mutmut_finish(
        self, feature: str, modules: Sequence[str], cmd: list[str], where: str
    ) -> CheckResult:
        result_file = self.root / self.cfg.paths.out / ".mutmut" / "result.json"
        result_file.unlink(missing_ok=True)
        # + 30 min for building the WSL venv
        timeout = self.cfg.mutation_timeout + 1800
        code, out = run_command(cmd, self.root, timeout=timeout)
        if not result_file.is_file():
            reason = incomplete_reason(code, out, timeout)
            if reason:
                return incomplete_result(reason, out, f"mutation run ({where})")
            return CheckResult("FAIL", f"mutmut runner ({where}) failed (exit {code}):\n{out}")
        res = json.loads(result_file.read_text(encoding="utf-8"))
        if not res.get("ok"):
            return CheckResult(
                "SKIPPED",
                f"{res.get('setup_log', '')}\nSKIPPED: {res.get('error')} ({where}); "
                f"{res.get('hint', '')}",
            )
        output = str(res.get("run_output", ""))
        reason = incomplete_reason(int(res.get("run_code") or 0), output, self.cfg.mutation_timeout)
        if reason:
            return incomplete_result(reason, output, f"mutation run ({where})")
        if res.get("run_code") != 0:
            if "nothing matches" in output:  # no mutants in the changed modules
                return CheckResult("PASS", "No mutants generated for the changed files.")
            return CheckResult("FAIL", f"mutmut run failed ({where}):\n{output}")
        tally = tally_mutmut(str(res.get("results", "")), modules)
        write_survivors(
            self.root / self.cfg.paths.out / feature / "mutation_survivors.md",
            tally,
            res.get("shows") or {},
        )
        if tally.total == 0:
            return CheckResult("PASS", "No mutants generated for the changed files.")
        timing = f"{where}, {res.get('seconds')} s" + (
            ", venv reused" if res.get("venv_reused") else ""
        )
        msg = (
            f"Mutation score {tally.score:.1f}% ({tally.detected}/{tally.total} killed), "
            f"threshold {self.cfg.mutation_score:g}% ({timing})."
        )
        return CheckResult(
            "PASS" if tally.score >= self.cfg.mutation_score else "FAIL", msg, summary=msg
        )

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

    def gitignore_entries(self) -> list[str]:
        return ["mutants/"]  # mutmut's working copy (removed after each run)

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


# ---------- mutmut helpers (module level: unit-tested on any OS) ----------
@dataclass
class MutmutTally:
    detected: int = 0
    total: int = 0
    #: (mutant name, status) of survived / no tests / suspicious mutants
    survivors: list[tuple[str, str]] = field(default_factory=list)
    #: skipped / not checked mutants (excluded from the score)
    unchecked: int = 0

    @property
    def score(self) -> float:
        return 100.0 * self.detected / self.total if self.total else 100.0


def tally_mutmut(results: str, modules: Sequence[str]) -> MutmutTally:
    """Count `mutmut results --all true` lines (`    pkg.mod.x_func__mutmut_3: killed`) of the
    given modules. The module is everything before the last dot (mangled names have none)."""
    wanted = set(modules)
    t = MutmutTally()
    for line in results.splitlines():
        m = MUTANT_LINE.match(line)
        if not m or m.group(1).rsplit(".", 1)[0] not in wanted:
            continue
        status = m.group(2)
        if status in DETECTED:
            t.detected += 1
            t.total += 1
        elif status in SURVIVED:
            t.total += 1
            t.survivors.append((m.group(1), status))
        else:
            t.unchecked += 1
    return t


def write_survivors(path: pathlib.Path, tally: MutmutTally, shows: Mapping[str, str]) -> None:
    lines = ["# Mutation survivors", ""]
    for name, status in tally.survivors:
        lines.append(f"## `{name}`: {status}")
        diff = shows.get(name, "")
        diff = "\n".join(x for x in diff.splitlines() if not x.startswith("# "))
        lines += ["", "```diff", diff.strip() or "(no diff captured)", "```", ""]
    if not tally.survivors:
        lines.append("None.")
    if tally.unchecked:
        lines.append(f"\n{tally.unchecked} mutant(s) skipped or not checked (not in the score).")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def req_name(req: str) -> str:
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
    return re.sub(r"[-_.]+", "-", m.group(1)).lower() if m else ""


def collect_requirements(root: pathlib.Path) -> tuple[list[str], list[pathlib.Path]]:
    """The project's requirement strings (pyproject: dependencies, every optional extra, every
    dependency group, [tool.uv] dev-dependencies; the project itself is left out) and its
    requirements*.txt files."""
    reqs: list[str] = []
    files = sorted(root.glob("requirements*.txt"))
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data.get("project", {})
        reqs += list(project.get("dependencies", []))
        for extra in project.get("optional-dependencies", {}).values():
            reqs += list(extra)
        for group in data.get("dependency-groups", {}).values():
            reqs += [r for r in group if isinstance(r, str)]  # {include-group} entries: all
        reqs += list(data.get("tool", {}).get("uv", {}).get("dev-dependencies", []))
        own = req_name(str(project.get("name", "")))
        reqs = [r for r in reqs if not own or req_name(r) != own]
    return list(dict.fromkeys(reqs)), files


def agentflow_source() -> pathlib.Path | None:
    """agentflow's source tree when it runs from one (editable install / checkout), else None."""
    root = pathlib.Path(__file__).resolve().parents[3]
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and re.search(
        r'^name\s*=\s*"agentflow"', pyproject.read_text(encoding="utf-8"), re.M
    ):
        return root
    return None


def translate_drive_paths(req: str, w: Callable[[pathlib.Path], str]) -> str:
    """Windows paths in a requirement (`x @ file:///D:/x`, `D:\\x`) -> WSL paths."""

    def sub(m: re.Match[str]) -> str:
        return ("file://" if m.group(1) else "") + w(pathlib.Path(m.group(2)))

    return re.sub(r"(file:///?)?([A-Za-z]:[\\/][^\s;,]*)", sub, req)
