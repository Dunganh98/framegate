"""Python adapter: pytest (+ the framegate `req` plugin), ruff, mypy, hypothesis, pytest-cov +
diff-cover, mutmut."""

from __future__ import annotations

import ast
import hashlib
import importlib.resources
import json
import os
import pathlib
import re
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

from framegate import __version__, known_red, pytools, release_requirement
from framegate.adapters import python_port, python_probe, wsl
from framegate.adapters.base import (
    Check,
    CheckResult,
    LanguageAdapter,
    TestAssertions,
    TestRun,
    TestScope,
    changed_lines,
    cobertura_hits,
    cobertura_without,
    diff_coverage_summary,
    incomplete_reason,
    incomplete_result,
    not_measured,
    parse_junit,
    run_command,
    without_moved,
)

SKIP_MARKS = {"skip", "skipif", "xfail"}
#: seconds the project interpreter gets to answer `framegate probe`
PROBE_TIMEOUT = 30
#: an allow rule for a tool run as a module (lint_tools adds the tool's own name)
MODULE_RULE = "Bash(python -m "
#: bump to force the WSL mutmut venvs to be rebuilt
MUTMUT_ENV_VERSION = 2  # 2: pytest plugin --req-paths
#: files whose content keys the WSL venv (besides the requirement list itself)
LOCK_FILES = ("pyproject.toml", "uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock")
#: mutmut needs fork: (reason, fix) when it is told to run with a Windows interpreter
NATIVE_WINDOWS = (
    "mutmut does not run on native Windows",
    'set [gates] mutation_runner = "auto" (it then runs through WSL)',
)
#: mutmut 3 statuses (mutmut/stats.py); "skipped" / "not checked" don't count
DETECTED = {"killed", "timeout", "caught by type check", "segfault"}
SURVIVED = {"survived", "no tests", "suspicious"}
MUTANT_LINE = re.compile(r"^\s*(\S+__mutmut_\d+):\s*(.+?)\s*$")
PYTEST_STATUS = {0: "passed", 1: "failed", 5: "no_tests"}
ENV_STAMP = (
    "import importlib.metadata as m; "
    "print(sorted(f\"{d.metadata['Name']}=={d.version}\" for d in m.distributions()))"
)


_IMPORTORSKIP = re.compile(r"""importorskip\(\s*["']([\w.]+)["']""")


def deselect_args(entries: Sequence[str]) -> list[str]:
    """pytest options that leave out these test files / node ids."""
    return [a for e in entries for a in ("--deselect", e)]


#: pytest exit codes of a run that did not reach the end
PYTEST_INCOMPLETE = {2: "pytest interrupted (exit 2)", 3: "pytest internal error (exit 3)"}

BRIEF = """\
Language: Python. Test framework: pytest (framegate plugin provides the `req` marker).
- Stubs (SPEC phase): full type-annotated signatures, docstrings naming the spec IDs they
  serve, body `raise NotImplementedError`. Stubs must import cleanly. Except the code files
  of a port (the spec's "Ported from"): their stubs are the reference's text with the bodies
  replaced, no spec IDs added (written in the SPEC phase by `framegate port-stubs`).
- Tag every test with the spec IDs it covers: `@pytest.mark.req("XXX-R-001", "XXX-E-002")`
  directly on the test function (or class). Do not use module-level `pytestmark`.
- Edge cases: assert the exact value, or `pytest.raises(ExcType, match="...")`.
- Invariants (I-IDs): property-based tests with hypothesis (`@given`).
- Every test needs a concrete assertion. No skip/skipif/xfail, no pytest.skip().
- Run tests: `{run_all}` ; only some IDs: `{run_ids}`.
{lint}
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


def _always_fails(node: ast.Assert) -> bool:
    """`assert False` / `assert 0, "not reached"`: a guard that is meant not to run."""
    return isinstance(node.test, ast.Constant) and not node.test.value


def assertion_lines(fn: ast.AST) -> list[int]:
    """The lines of a test's assertions: `assert ...`, `pytest.raises` / `warns` and
    `assert_*` helper calls. Not `pytest.fail` and `assert False` (guards meant not to run)."""
    lines = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assert) and not _always_fails(n):
            lines.add(n.lineno)
        elif isinstance(n, ast.Call):
            name = _call_name(n)
            if name in {"raises", "warns"} or name.startswith("assert"):
                lines.add(n.lineno)
    return sorted(lines)


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
    file_scoped_lint = True
    line_suppression = r"#\s*(?:noqa|type:\s*ignore|pragma:\s*no\s*cover)\b"

    @classmethod
    def detect(cls, root: pathlib.Path) -> bool:
        markers = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
        return any((root / m).exists() for m in markers)

    @property
    def pytest_args(self) -> list[str]:
        """[python] pytest_args: added to every pytest run framegate starts and to the command
        the agents are told to use (e.g. ["-m", "not integration and not spark"] for tests
        that need services the machine does not have)."""
        return list(self.cfg.language_options.get("python", {}).get("pytest_args", []))

    # ---------- interpreter ----------
    def interpreter(self) -> tuple[str, str]:
        """[env] python, else the project's .venv interpreter, else the current interpreter;
        with where it comes from (`framegate probe` shows it)."""
        if self.cfg.python:
            p = self.cfg.python
            if (os.sep in p or "/" in p) and not os.path.isabs(p):
                p = str((self.root / p).resolve())
            return p, "framegate.toml [env] python"
        for cand in (
            self.root / ".venv" / "Scripts" / "python.exe",
            self.root / ".venv" / "bin" / "python",
        ):
            if cand.is_file():
                return str(cand), "the project's .venv"
        return sys.executable, "framegate's own interpreter: the project has no .venv"

    @property
    def python(self) -> str:
        return self.interpreter()[0]

    def env_facts(self) -> dict[str, Any]:
        code, out = run_command(
            [self.python, "-c", python_probe.ENV_SCRIPT], self.root, timeout=PROBE_TIMEOUT
        )
        try:
            data = json.loads(out.strip().splitlines()[-1]) if code == 0 else None
        except (ValueError, IndexError):
            data = None
        if not isinstance(data, dict):
            return {}
        here = os.path.normcase(str(self.root.resolve()))
        mine = []
        for name, url in data.get("editable", []):
            path = url2pathname(urlparse(url).path)
            if os.path.normcase(os.path.abspath(path)) == here:
                mine.append(name)
        facts: dict[str, Any] = {"version": f"Python {data.get('version', '?')}", "editable": mine}
        if mine:
            pip = ["python", "-m", "pip"] if data.get("pip") else ["uv", "pip"]
            facts["refresh"] = [*pip, "install", "-e", ".", "--no-deps"]
        return facts

    def missing_frameworks(self) -> dict[str, str] | None:
        declared = python_probe.declared(self.root)
        guarded: dict[str, str] = {}
        for p in self._test_files():
            text = p.read_text(encoding="utf-8", errors="replace")
            for mod in _IMPORTORSKIP.findall(text):
                guarded.setdefault(mod, f"importorskip in {self.rel(p)}")
        if not declared and not guarded:
            return {}
        args = [json.dumps(list(declared)), json.dumps(list(guarded))]
        code, out = run_command(
            [self.python, "-c", python_probe.MISSING_SCRIPT, *args],
            self.root,
            timeout=PROBE_TIMEOUT,
        )
        found = [x for x in out.splitlines() if x.startswith("MISSING ")]
        if code != 0 or not found:
            return None
        reqs, mods = json.loads(found[-1].removeprefix("MISSING "))
        gaps: dict[str, str] = {}
        for text in reqs:
            name = re.split(r"[\s<>=!~;\[(]", text, maxsplit=1)[0]
            gaps.setdefault(name.lower().replace("_", "-"), declared[text])
        for mod in mods:
            if mod.split(".")[0].lower().replace("_", "-") not in gaps:
                gaps.setdefault(mod, guarded[mod])
        return gaps

    def test_runner(self) -> list[str]:
        return [self.python, "-m", "pytest"]

    def detected_tools(self) -> dict[str, tuple[list[str], str]]:
        return python_probe.detected_tools(self.root)

    @classmethod
    def guess_layout(cls, root: pathlib.Path) -> tuple[list[str], str]:
        return python_probe.guess_layout(root)

    def resolve_command(self, cmd: Sequence[str]) -> list[str]:
        out = super().resolve_command(cmd)
        if out and out[0] in ("python", "python3", "{python}"):
            out[0] = self.python
        return out

    def agent_env(self) -> dict[str, str]:
        """The project interpreter's folder first on PATH (VIRTUAL_ENV for a venv): `python`,
        `pytest`, `ruff`, `black` in an agent's shell are the gates' ones."""
        return pytools.interpreter_env(self.python)

    def tool_requirements(self) -> list[pytools.ToolReq]:
        """What the workflow needs in the project interpreter. Always: the framegate pytest
        plugin (the `req` marker, `--req`), pytest, pytest-cov, hypothesis, diff-cover. With
        a framegate.toml also the lint / format / type tools its gate commands name (before
        it exists the commands are not settled: `/framegate:setup` may switch one off)."""
        src = framegate_source()
        # from a checkout: that source tree; else this version from its git tag (the package
        # is not published on PyPI)
        own = ("-e", str(src)) if src is not None else (release_requirement(),)
        reqs = [
            pytools.ToolReq("framegate", own, "pytest plugin: req marker, --req"),
            pytools.ToolReq("pytest", ("pytest",), "tests"),
            pytools.ToolReq("pytest_cov", ("pytest-cov",), "coverage"),
            pytools.ToolReq("hypothesis", ("hypothesis",), "property tests"),
            pytools.ToolReq("diff_cover", ("diff-cover",), "diff coverage"),
        ]
        if self.cfg.loaded_from_file:
            commands = {**self.default_gate_commands(), **self.cfg.gate_commands}
            reqs += pytools.gate_tools([*commands.values(), self.cfg.format_fix_command])
        return reqs

    #: the line the project's own pytest config needs so that the feature's tests run
    #: without framegate's plugin (pytest.ini / setup.cfg / tox.ini `markers =`, pyproject.toml
    #: `[tool.pytest.ini_options] markers = [...]`)
    MARKER_LINE = "req(*ids): spec IDs this test proves"

    def team_checks(self) -> list[tuple[str, bool, str]]:
        """The tests carry `@pytest.mark.req(...)`. framegate's pytest plugin registers the
        marker; without the plugin pytest warns about an unknown marker and fails with
        `--strict-markers`. Asked of pytest itself, with the plugin switched off."""
        code, out = run_command(
            [self.python, "-m", "pytest", "--markers", "-p", "no:framegate"], self.root, timeout=300
        )
        name = "pytest without framegate knows the `req` marker"
        if code != 0:
            tail = " / ".join(out.strip().splitlines()[-2:])
            return [(name, False, f"`pytest --markers -p no:framegate` failed: {tail}")]
        if "@pytest.mark.req" in out:
            return [(name, True, "registered in the project's pytest config")]
        return [
            (
                name,
                False,
                f'add "{self.MARKER_LINE}" to `markers` in the pytest config of the project '
                "(pytest.ini, or [tool.pytest.ini_options] in pyproject.toml): the pull "
                "request carries the tests without framegate, and an unknown marker is a "
                "warning there (an error with --strict-markers). A setup file: the next "
                "approved spec commits it with the product",
            )
        ]

    def tools_version(self) -> str | None:
        """The version of the framegate package (the pytest plugin) in the project
        interpreter; "" = not installed there."""
        code, out = run_command(
            [
                self.python,
                "-c",
                "import importlib.metadata as m; print(m.version('framegate'))",
            ],
            self.root,
        )
        return out.strip().splitlines()[-1].strip() if code == 0 and out.strip() else ""

    def install_tools(
        self, dry_run: bool, echo: Callable[[str], None], update: bool = False
    ) -> int:
        stale = update and self.tools_version() not in ("", __version__)
        code = pytools.install(
            self.python,
            self.root,
            self.tool_requirements(),
            self._has_module,
            dry_run,
            echo,
            update=("framegate",) if stale else (),
        )
        if code == 0 and stale and not dry_run and self.tools_version() != __version__:
            echo(
                f"error: the project interpreter still has framegate {self.tools_version()}, "
                f"not {__version__}"
            )
            return 1
        return code

    def tool_report(self) -> list[tuple[str, bool, str]]:
        out = []
        ok = self._has_module("framegate")
        hint = "run: framegate install-tools"
        out.append(("framegate (pytest plugin: --req)", ok, self.python if ok else hint))
        out.append(self._agent_python_report())
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
            # the same probe as the mutation check: what doctor says is what the gate will do
            found = wsl.probe(self.root, self.cfg.wsl_distro)
            where = f"runs in WSL {found.distro}" if found.distro else "runs in WSL"
            detail = f"{where} ({found.note})" if found.note else where
            if found.root is None:
                detail = f"mutation testing cannot run here: {found.problem}. Fix: {found.fix}"
            out.append(("mutmut (mutation)", found.root is not None, detail))
        elif os.name == "nt":
            out.append(("mutmut (mutation)", False, ". Fix: ".join(NATIVE_WINDOWS)))
        else:
            ok = self._has_module("mutmut")
            out.append(("mutmut (mutation)", ok, self.python if ok else "install mutmut"))
        return out

    def _agent_python_report(self) -> tuple[str, bool, str]:
        """What `python` is in an agent's shell (agent_env) and whether its pytest knows
        `--req`: the agents are told `python -m pytest -q --req <IDs>`."""
        name = "agents' python (`python -m pytest --req` in an agent's shell)"
        path = self.agent_env().get("PATH")
        found = shutil.which("python", path=path) if path else None
        if not found:
            return name, False, f"the project interpreter {self.python} was not found"
        code, out = run_command([found, "-m", "pytest", "--help"], self.root)
        if code == 0 and "--req" in out:
            return name, True, found
        return name, False, f"{found}: its pytest has no --req; run: framegate install-tools"

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
        extra: Sequence[str] = (),
    ) -> TestRun:
        cmd = [self.python, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
        cmd += [*self.pytest_args, *extra]
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

    # ---------- ports (adapters/python_port.py) ----------
    def reference_stub(self, source: bytes) -> tuple[bytes, list[str]] | None:
        return python_port.stub_source(source)

    def stub_import_problems(self, target: str, text: bytes, ported: dict[str, str]) -> list[str]:
        roots = [self.root / r for r in self.cfg.src_roots]
        bases = list(dict.fromkeys([self.root, *roots, *(r.parent for r in roots)]))
        tops = sorted(
            {m.split(".")[0] for _, level, m in python_port.imported_modules(text) if not level}
            - set(sys.stdlib_module_names)  # known without asking the interpreter
        )
        return python_port.import_problems(
            self.root, bases, target, text, ported, self.importable(tops)
        )

    def importable(self, names: Sequence[str]) -> set[str] | None:
        """The top-level module names the project's interpreter can import (None: it could
        not be asked)."""
        if not names:
            return set()
        probe = (
            "import importlib.util, sys\n"
            "print('FOUND', *[m for m in sys.argv[1:] if importlib.util.find_spec(m)])"
        )
        code, out = run_command([self.python, "-c", probe, *names], self.root)
        found = [x for x in out.splitlines() if x.startswith("FOUND")]
        return set(found[-1].split()[1:]) if code == 0 and found else None

    def red_stub_run(self, req_ids: Sequence[str], report: pathlib.Path) -> TestRun:
        # a test file that cannot load must not stop the run: the check judges the others
        return self.run_tests(
            req_ids=req_ids, junit=report, extra=["--continue-on-collection-errors"]
        )

    def broken_at(self, tree: pathlib.Path, names: Sequence[str]) -> set[str] | None:
        files = {n.split("::")[0].replace(".", "/") + ".py": n for n in names}
        files = {rel: n for rel, n in files.items() if (tree / rel).is_file()}
        if not files:
            return set()
        # the tree's own sources, not an editable install of the working tree
        roots = [*(str(tree / r) for r in self.cfg.src_roots), str(tree)]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(roots)}
        cmd = [self.python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
        code, out = run_command([*cmd, *self.pytest_args, *files], tree, env=env, timeout=600)
        if code == 0:
            return set()
        bad = {m.replace("\\", "/") for m in re.findall(r"^ERROR\s+(\S+)", out, re.M)}
        return {n for rel, n in files.items() if rel in bad}

    def test_names(self, source: bytes) -> list[str] | None:
        return python_port.test_names(source)

    def test_lines(self, source: str, names: Sequence[str]) -> set[int] | None:
        try:
            body = ast.parse(source).body
        except SyntaxError:
            return None
        node = None
        for name in names:
            kinds = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            node = next((n for n in body if isinstance(n, kinds) and n.name == name), None)
            if node is None:
                return set()
            body = node.body
        if node is None:
            return set()
        first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        return set(range(first, (node.end_lineno or node.lineno) + 1))

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
        """pytest options selecting the tests of a scope (framegate plugin)."""
        if scope is None or scope.whole_suite:
            return []
        args = ["--req", ",".join(scope.req_ids)] if scope.req_ids else []
        return args + (["--req-paths", ",".join(scope.paths)] if scope.paths else [])

    def regression_run(self, skip: Sequence[str] = ()) -> TestRun:
        timeout = self.cfg.regression_timeout
        junit = self.root / self.cfg.paths.out / "regression.xml"
        junit.parent.mkdir(parents=True, exist_ok=True)
        junit.unlink(missing_ok=True)
        cmd = [self.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"]
        cmd += self.pytest_args + deselect_args(skip)
        code, out = run_command(cmd, self.root, timeout=timeout)
        reason = incomplete_reason(code, out, timeout) or PYTEST_INCOMPLETE.get(code, "")
        cases = parse_junit(junit) if junit.is_file() else None
        status = "error" if reason else PYTEST_STATUS.get(code, "error")
        return TestRun(status, code, out, cases, incomplete=reason)

    def suite_command(self) -> list[str] | None:
        return [self.python, *self.pytest_args]

    def env_stamp(self) -> str:
        """The installed packages (name-version of every dist-info / egg-info)."""
        code, out = run_command([self.python, "-c", ENV_STAMP], self.root, timeout=60)
        return out if code == 0 else ""

    def _coverage_env(self) -> dict[str, str]:
        """A coverage run's environment: its data file in the out folder (seen live: a
        `.coverage` left untracked in the project root when the task was done)."""
        data = self.root / self.cfg.paths.out / ".coverage"
        return {**os.environ, "COVERAGE_FILE": str(data)}

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
        code, out = run_command(cmd, self.root, env=self._coverage_env(), timeout=timeout)
        reason = incomplete_reason(code, out, timeout) or PYTEST_INCOMPLETE.get(code, "")
        if reason or not xml.is_file():
            return [], f"coverage run incomplete ({reason or 'no report'})"
        return [xml], ""

    def test_line_hits(self) -> tuple[dict[str, dict[int, int]], str]:
        xml = self.root / self.cfg.paths.out / "coverage.xml"
        if not xml.is_file():
            return {}, "no coverage report (see the coverage run)"
        tests = self.cfg.paths.tests.rstrip("/") + "/"
        hits = {f: v for f, v in cobertura_hits([xml], self.root).items() if f.startswith(tests)}
        return hits, "" if hits else "the coverage run did not record the test files"

    def test_assertions(self, rel: str) -> list[TestAssertions]:
        try:
            tree = ast.parse((self.root / rel).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return []
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test") or not node.body:
                continue
            first = node.body[0].lineno  # (the `def` line runs when the file is imported)
            out.append(
                TestAssertions(node.name, first, node.end_lineno or first, assertion_lines(node))
            )
        return out

    def coverage_checks(
        self,
        base_ref: str,
        scope: TestScope | None = None,
        cover_tests: Sequence[str] = (),
    ) -> list[Check]:
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
            if scope is None or scope.whole_suite:
                cmd += deselect_args(known_red.entries(self.cfg))
            note = ""
            collect = self._has_module("pytest_cov")
            if collect:
                cmd += [f"--cov={root}" for root in self.cfg.src_roots]
                if cover_tests:  # the tests' own lines: which assertions ran (unreached.py)
                    cmd.append(f"--cov={self.cfg.paths.tests}")
                cmd += ["--cov-branch", f"--cov-report=xml:{xml}"]
            else:
                note = "\nSKIPPED coverage: pytest-cov is not installed in the project environment"
            timeout = self.cfg.coverage_timeout
            code, out = run_command(cmd, self.root, env=self._coverage_env(), timeout=timeout)
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
            report = xml
            if cover_tests:  # diff coverage is about the source files only
                report = out_dir / "coverage_src.xml"
                cobertura_without(xml, report, self.root, self.cfg.paths.tests.rstrip("/") + "/")
            reports, moved = without_moved(self.cfg, base_ref, [report], out_dir)
            code, out = run_command(
                [
                    tool,
                    *map(str, reports),
                    f"--compare-branch={base_ref}",
                    # the base itself, not its merge base with HEAD (task_base.py)
                    "--diff-range-notation=..",
                    f"--fail-under={self.cfg.diff_coverage}",
                ],
                self.root,
            )
            summary = diff_coverage_summary(out, self.cfg.diff_coverage, moved)
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
        modules, functions = self._mutmut_targets(base_ref, changed)
        if not modules and not functions:
            return CheckResult("PASS", "No changed functions to mutate.")
        return self._mutmut(feature, modules, scope, functions=functions)

    def mutation_trial(self) -> CheckResult | None:
        """The setup's trial (doctor --tree): mutmut finds the code and pytest loads
        framegate's plugin, through the final gate's path; no mutant runs. The first trial
        on a machine builds the WSL venv once (cached for the gates)."""
        if (self.cfg.mutation_tool or "mutmut") != "mutmut":
            return None
        return self._mutmut("", [], None, trial=True)

    def _mutmut(
        self,
        feature: str,
        modules: Sequence[str],
        scope: TestScope | None,
        trial: bool = False,
        functions: Sequence[str] = (),
    ) -> CheckResult:
        if self.mutation_runner() == "wsl":
            return self._mutmut_wsl(feature, modules, scope, trial, functions)
        if os.name == "nt":
            return not_measured(*NATIVE_WINDOWS)
        if not self._has_module("mutmut"):
            return not_measured(
                f"mutmut is not installed for {self.python}",
                "add mutmut to the project's dev dependencies and install them",
            )
        work = self._mutmut_dir()
        result = str(work / "result.json")
        spec = self._mutmut_spec(modules, str(self.root), result, scope, functions)
        spec["trial"] = trial
        (work / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        cmd = [self.python, str(work / "runner.py"), str(work / "spec.json")]
        return self._mutmut_finish(feature, modules, cmd, "native", trial, functions)

    # ---------- mutmut internals ----------
    def _mutmut_module(self, f: str) -> str:
        """A source file's module as mutmut names it: the path from the root, a leading
        `src.` dropped (mutmut 3 drops only that one)."""
        m = f[:-3].replace("/", ".").removeprefix("src.")
        return m[: -len(".__init__")] if m.endswith(".__init__") else m

    def _mutmut_modules(self, changed: Sequence[str]) -> list[str]:
        return sorted({self._mutmut_module(f) for f in changed})

    def _mutmut_targets(self, base_ref: str, changed: Sequence[str]) -> tuple[list[str], list[str]]:
        """(modules mutated whole: their changed lines are unknown, the changed functions)."""
        modules: set[str] = set()
        functions: list[str] = []
        for f in changed:
            module = self._mutmut_module(f)
            lines = changed_lines(self.cfg, base_ref, f)
            try:
                text = (self.root / f).read_text(encoding="utf-8")
                found = None if lines is None else mutmut_functions(module, text, lines)
            except (OSError, ValueError, SyntaxError):
                found = None
            if found is None:
                modules.add(module)
            else:
                functions += found
        return sorted(modules), sorted(set(functions))

    def _mutmut_dir(self) -> pathlib.Path:
        """out/.mutmut/ with a fresh copy of the runner script."""
        work = self.root / self.cfg.paths.out / ".mutmut"
        work.mkdir(parents=True, exist_ok=True)
        runner = importlib.resources.files("framegate.adapters").joinpath("mutmut_runner.py")
        (work / "runner.py").write_text(runner.read_text(encoding="utf-8"), encoding="utf-8")
        return work

    def _mutmut_spec(
        self,
        modules: Sequence[str],
        project: str,
        result: str,
        scope: TestScope | None = None,
        functions: Sequence[str] = (),
    ) -> dict[str, Any]:
        return {
            "project": project,
            "result": result,
            # top-level functions (x_name) and methods (x<sep>Class<sep>name) of the modules
            "patterns": [f"{m}.x*" for m in modules] + [f"{f}*" for f in functions],
            "show_modules": list(modules),
            "show_functions": list(functions),
            "show_limit": 50,
            "timeout": self.cfg.mutation_timeout,
            "pytest_addopts": " ".join(
                _shell_quote(a) for a in [*self.pytest_args, *self.scope_args(scope)]
            ),
            "also_copy": self._mutmut_also_copy(),
            # mutmut guesses its folder (src/ or one named like the project); seen live: a
            # project with packages at the root, and mutmut found nothing to mutate
            "source_paths": [r.rstrip("/") for r in self.cfg.src_roots],
        }

    def _mutmut_also_copy(self) -> list[str]:
        """The tracked files outside the source folders. mutmut runs the tests in its own
        folder with the sources, tests/ and the packaging files only; a test that reads
        another file of the repository (`docs/x.md`) would fail there."""
        cmd = ["git", "-c", "core.quotepath=off", "ls-files"]
        code, out = run_command(cmd, self.root, timeout=120)
        own = (*(r.rstrip("/") + "/" for r in self.cfg.src_roots), "tests/", "test/")
        return [f for f in out.splitlines() if not f.startswith(own)] if code == 0 else []

    def _mutmut_wsl(
        self,
        feature: str,
        modules: Sequence[str],
        scope: TestScope | None = None,
        trial: bool = False,
        functions: Sequence[str] = (),
    ) -> CheckResult:
        found = wsl.probe(self.root, self.cfg.wsl_distro)
        if found.root is None:
            return not_measured(
                f"mutation testing via WSL is unavailable: {found.problem} (mutmut does not "
                "run on native Windows)",
                found.fix,
            )
        work = self._mutmut_dir()
        wsl_root = root_wsl = found.root
        distro = found.distro

        def w(path: pathlib.Path) -> str:
            return wsl.to_wsl(path, self.root, root_wsl, distro)

        reqs, files, stamp = self._wsl_requirements(w)
        spec = self._mutmut_spec(modules, wsl_root, w(work / "result.json"), scope, functions)
        if scope_plugin_missing(reqs):
            # seen live: an installed framegate and a project that does not name it left
            # the venv without the plugin, and pytest refused `--req` (a run without a result)
            plugin = importlib.resources.files("framegate").joinpath("pytest_plugin.py")
            (work / f"{SCOPE_PLUGIN}.py").write_text(
                plugin.read_text(encoding="utf-8"), encoding="utf-8"
            )
            spec["pythonpath"] = w(work)
            spec["pytest_addopts"] = f"{spec['pytest_addopts']} -p {SCOPE_PLUGIN}".strip()
        repo_key = hashlib.sha256(wsl_root.encode("utf-8")).hexdigest()[:16]
        spec.update(
            venv=f"~/.cache/framegate/venvs/{repo_key}",
            stamp=stamp,
            requirements=reqs,
            requirement_files=files,
            trial=trial,
        )
        (work / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        cmd = wsl.wsl_command(["python3", w(work / "runner.py"), w(work / "spec.json")], distro)
        where = f"WSL {distro}" if distro else "WSL"
        return self._mutmut_finish(feature, modules, cmd, where, trial, functions)

    def _wsl_requirements(
        self, w: Callable[[pathlib.Path], str]
    ) -> tuple[list[str], list[str], str]:
        """What the WSL venv needs: the project's dependencies (all extras and dependency
        groups, requirements*.txt), mutmut, pytest, hypothesis (the review tests use it), and
        framegate itself (its pytest plugin) from this source tree when framegate runs from
        one. The project itself is not installed: mutmut puts its mutated copy on sys.path.
        Returns (requirements, -r files, stamp)."""
        reqs, files = collect_requirements(self.root)
        own = framegate_source()
        reqs = [r for r in reqs if not (own and req_name(r) == "framegate")]
        reqs += ["mutmut>=3", "pytest", "hypothesis"] + ([w(own)] if own else [])
        reqs = [translate_drive_paths(r, w) for r in reqs]
        digest = hashlib.sha256()
        digest.update(json.dumps([MUTMUT_ENV_VERSION, __version__, reqs]).encode("utf-8"))
        for path in [self.root / n for n in LOCK_FILES] + files:
            if path.is_file():
                digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes())
        return reqs, [w(f) for f in files], digest.hexdigest()

    def _mutmut_finish(
        self,
        feature: str,
        modules: Sequence[str],
        cmd: list[str],
        where: str,
        trial: bool = False,
        functions: Sequence[str] = (),
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
            return not_measured(
                f"{res.get('error')} ({where})",
                str(res.get("hint") or "see the setup log under the check"),
                log=str(res.get("setup_log", "")),
            )
        output = str(res.get("run_output", ""))
        if trial:
            secs = f"{where}, {res.get('seconds')} s" + (
                ", venv reused" if res.get("venv_reused") else ", venv built"
            )
            if res.get("run_code") == 0:
                return CheckResult(
                    "PASS", output, output.strip().replace(chr(10), "; ") + f" ({secs})"
                )
            last = output.strip().splitlines()[-1:] or ["mutmut cannot run here"]
            return CheckResult("FAIL", output, f"{last[0]} ({secs})")
        reason = incomplete_reason(int(res.get("run_code") or 0), output, self.cfg.mutation_timeout)
        if reason:
            return incomplete_result(reason, output, f"mutation run ({where})")
        if res.get("run_code") != 0:
            if "nothing matches" in output:  # no mutants in the changed modules
                return CheckResult("PASS", "No mutants generated for the changed files.")
            return CheckResult("FAIL", f"mutmut run failed ({where}):\n{output}")
        tally = tally_mutmut(str(res.get("results", "")), modules, functions)
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
        n = len(functions)
        scope = [f"the {n} function{'s' * (n != 1)} this task changed"] if n else []
        if modules:
            scope.append(f"module{'s' * (len(modules) != 1)} {', '.join(modules)}")
        msg = (
            f"Mutation score {tally.score:.1f}% ({tally.detected}/{tally.total} killed) in "
            f"{' and '.join(scope)}, threshold {self.cfg.mutation_score:g}% ({timing})."
        )
        return CheckResult(
            "PASS" if tally.score >= self.cfg.mutation_score else "FAIL", msg, summary=msg
        )

    # ---------- frame ----------
    def default_forbidden_patterns(self) -> list[str]:
        return [r"type:\s*ignore", r"noqa", r"pragma:\s*no cover", r"pytest\.skip", r"xfail"]

    def definition_pattern(self, name: str) -> str:
        n = re.escape(name)
        return (
            rf"^\s*(?:async\s+)?(?:def|class)\s+{n}\b"
            rf"|^\s*(?:\w+\.)*{n}\s*(?::[^=]+)?=(?!=)"
        )

    def guard_patterns(self) -> dict[str, str]:
        return {
            "# noqa": r"#\s*noqa\b(?::\s*[A-Z]+\d*(?:\s*,\s*[A-Z]+\d*)*)?",
            "# type: ignore": r"#\s*type:\s*ignore\b(?:\[[^\]]*\])?",
            "pragma: no cover": r"#\s*pragma:\s*no\s*cover\b",
            "except Exception": r"^\s*except\s+\(?\s*(?:Base)?Exception\b",
            "bare except": r"^\s*except\s*:",
        }

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
        own = [f'"{p}"' if " " in p else p for p in self._interpreter_forms()]
        rules = ["Bash(python -m pytest *)", "Bash(pytest *)"]
        return list(dict.fromkeys(rules + [f"Bash({p} -m pytest *)" for p in own]))

    def _interpreter_forms(self) -> list[str]:
        """The project interpreter as an agent may type it (relative inside the project)."""
        p = pathlib.Path(self.python)
        if not p.is_absolute():
            return [self.python]
        base = next((b for b in (self.root, self.root.resolve()) if p.is_relative_to(b)), None)
        if base is None:
            return list(dict.fromkeys([self.python, p.as_posix()]))
        rel = p.relative_to(base).as_posix()
        forms = [rel, "./" + rel]
        return forms + [f.replace("/", "\\") for f in forms]

    def agent_form(self, cmd: Sequence[str]) -> list[str]:
        """The project interpreter is `python` for an agent, as in its test command."""
        own = bool(cmd) and self.resolve_command(cmd[:1])[:1] == [self.python]
        out = super().agent_form(cmd)
        if own and out:
            out[0] = "python"
        return out

    def lint_tools(self) -> list[str]:
        """The rules of the effective commands (base), and for a `python -m <tool> ...` one
        the tool's own name too (`ruff check ...`): the same tool, typed the short way."""
        rules = super().lint_tools()
        short = ["Bash(" + r[len(MODULE_RULE) :] for r in rules if r.startswith(MODULE_RULE)]
        return list(dict.fromkeys([*rules, *short]))

    def conventions_brief(self, allowed: Sequence[str] | None = None) -> str:
        p = self.cfg.paths
        text = BRIEF.format(
            tests=p.tests,
            review_tests=p.review_tests,
            run_all=self.test_command(),
            run_ids=self.test_command(["ID1", "ID2"]),
            lint=self.lint_brief(allowed),
        )
        return "\n".join(line for line in text.splitlines() if line.strip()) + "\n"

    def gitignore_entries(self) -> list[str]:
        return ["mutants/"]  # mutmut's working copy (removed after each run)

    def init_note(self) -> str:
        return (
            "add framegate (pinned) to the project's dev dependencies; its pytest plugin "
            "provides the `req` marker and `--req`. Remove any conftest.py that defines `--req`."
        )

    def ci_setup(self) -> str:
        return (
            "      - uses: actions/setup-python@v5\n"
            "        with:\n"
            '          python-version: "3.12"\n'
            "      # adapt: install the project with its dev dependencies (framegate pinned,\n"
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


def mutmut_functions(module: str, source: str, lines: set[int]) -> list[str]:
    """mutmut's names (`mod.x_f`, `mod.xǁClsǁm`) of the functions and methods `lines` touch."""
    found = []

    def touched(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        return any(n in lines for n in range(first, (node.end_lineno or node.lineno) + 1))

    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.parse(source).body:
        if isinstance(node, functions) and touched(node):
            found.append(f"{module}.x_{node.name}")
        elif isinstance(node, ast.ClassDef):
            found += [
                f"{module}.xǁ{node.name}ǁ{item.name}"
                for item in node.body
                if isinstance(item, functions) and touched(item)
            ]
    return found


def tally_mutmut(
    results: str, modules: Sequence[str], functions: Sequence[str] = ()
) -> MutmutTally:
    """Count `mutmut results --all true` lines (`    pkg.mod.x_func__mutmut_3: killed`) of the
    given modules (everything before the last dot: mangled names have none) or functions."""
    wanted, funcs = set(modules), set(functions)
    t = MutmutTally()
    for line in results.splitlines():
        m = MUTANT_LINE.match(line)
        if not m:
            continue
        name = m.group(1)
        if name.rsplit(".", 1)[0] not in wanted and name.rsplit("__mutmut_", 1)[0] not in funcs:
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


#: framegate's pytest plugin (`--req`), as a file of its own beside the mutmut runner
SCOPE_PLUGIN = "framegate_req_plugin"


def scope_plugin_missing(reqs: Sequence[str]) -> bool:
    """Do the WSL venv's requirements leave framegate out (not run from a source tree, not
    named by the project)? Then its pytest plugin goes along as a file."""
    return framegate_source() is None and "framegate" not in map(req_name, reqs)


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


def framegate_source() -> pathlib.Path | None:
    """framegate's source tree when it runs from one (editable install / checkout), else None."""
    root = pathlib.Path(__file__).resolve().parents[3]
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and re.search(
        r'^name\s*=\s*"framegate"', pyproject.read_text(encoding="utf-8"), re.M
    ):
        return root
    return None


def translate_drive_paths(req: str, w: Callable[[pathlib.Path], str]) -> str:
    """Windows paths in a requirement (`x @ file:///D:/x`, `D:\\x`) -> WSL paths."""

    def sub(m: re.Match[str]) -> str:
        return ("file://" if m.group(1) else "") + w(pathlib.Path(m.group(2)))

    return re.sub(r"(file:///?)?([A-Za-z]:[\\/][^\s;,]*)", sub, req)
