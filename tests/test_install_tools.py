"""`framegate install-tools`: what the workflow needs goes into the project's interpreter, before
the setup starts (seen on a repo with its own .venv: the framegate pytest plugin was missing
there and nothing said so until the first test run failed on `--req`)."""

from __future__ import annotations

import pathlib
from collections.abc import Callable

import pytest

from conftest import DemoRepo, init_git
from framegate import cli, pytools
from framegate.adapters import get_adapter
from framegate.adapters.python import framegate_source
from framegate.config import load_config, parse_config
from framegate.scaffold import install_tools


class FakeEnv:
    """An interpreter with a set of importable modules; records the commands run on it."""

    def __init__(self, modules: set[str], freeze: dict[str, str] | None = None) -> None:
        self.modules = set(modules)
        self.freeze = dict(freeze or {})
        self.commands: list[list[str]] = []
        self.fail = False
        #: what an install adds / changes: module -> (dist, version)
        self.provides: dict[str, tuple[str, str]] = {}
        self.bumps: dict[str, str] = {}

    def has(self, module: str) -> bool:
        return module in self.modules

    def run(self, cmd: list[str], cwd: pathlib.Path, **kw: object) -> tuple[int, str]:
        self.commands.append(list(cmd))
        if "freeze" in cmd:
            return 0, "\n".join(f"{n}=={v}" for n, v in self.freeze.items())
        if self.fail:
            return 1, "ERROR: no matching distribution"
        for module, (dist, version) in self.provides.items():
            self.modules.add(module)
            self.freeze[dist] = version
        self.freeze.update(self.bumps)
        return 0, "Successfully installed"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeEnv]:
    def make(modules: set[str], freeze: dict[str, str] | None = None) -> FakeEnv:
        fake = FakeEnv(modules, freeze)
        monkeypatch.setattr(pytools, "run_command", fake.run)
        return fake

    return make


REQS = [
    pytools.ToolReq("framegate", ("-e", "/src/framegate"), "pytest plugin"),
    pytools.ToolReq("pytest", ("pytest",), "tests"),
    pytools.ToolReq("diff_cover", ("diff-cover",), "diff coverage"),
]


def test_installs_only_what_is_missing_and_reports_changes(
    env: Callable[..., FakeEnv], tmp_path: pathlib.Path
) -> None:
    fake = env({"pip", "pytest"}, {"pytest": "9.1.1", "jinja2": "3.1.0"})
    fake.provides = {"framegate": ("framegate", "0.1.0"), "diff_cover": ("diff-cover", "9.2")}
    fake.bumps = {"jinja2": "3.1.6"}
    lines: list[str] = []
    assert pytools.install("py", tmp_path, REQS, fake.has, False, lines.append) == 0
    install = next(c for c in fake.commands if "install" in c)
    assert install == ["py", "-m", "pip", "install", "-e", "/src/framegate", "diff-cover"]
    out = "\n".join(lines)
    assert "Project interpreter: py" in out
    assert "[ok     ] pytest (tests)" in out and "[missing] diff_cover (diff coverage)" in out
    assert "Added: diff-cover 9.2, framegate 0.1.0" in out
    assert "CHANGED versions" in out and "jinja2 3.1.0 -> 3.1.6" in out
    assert lines[-1] == "Tools installed."


def test_nothing_missing_runs_nothing(env: Callable[..., FakeEnv], tmp_path: pathlib.Path) -> None:
    fake = env({"pip", "framegate", "pytest", "diff_cover"})
    lines: list[str] = []
    assert pytools.install("py", tmp_path, REQS, fake.has, False, lines.append) == 0
    assert fake.commands == [] and "Nothing to install" in lines[-1]


def test_dry_run_lists_and_installs_nothing(
    env: Callable[..., FakeEnv], tmp_path: pathlib.Path
) -> None:
    fake = env({"pip", "pytest"})
    lines: list[str] = []
    assert pytools.install("py", tmp_path, REQS, fake.has, True, lines.append) == 0
    assert fake.commands == []
    assert lines[-1] == "Would install: -e /src/framegate, diff-cover"


def test_unchanged_pins_are_said_so(env: Callable[..., FakeEnv], tmp_path: pathlib.Path) -> None:
    fake = env({"pip", "framegate", "pytest"}, {"pytest": "9.1.1"})
    fake.provides = {"diff_cover": ("diff-cover", "9.2")}
    lines: list[str] = []
    assert pytools.install("py", tmp_path, REQS, fake.has, False, lines.append) == 0
    assert "No package that was already installed changed its version." in lines


def test_failed_install_and_missing_installer(
    env: Callable[..., FakeEnv], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = env({"pip"})
    fake.fail = True
    lines: list[str] = []
    assert pytools.install("py", tmp_path, REQS, fake.has, False, lines.append) == 1
    assert "the install failed" in lines[-1] and "no matching distribution" in lines[-1]
    # an install that "worked" but left a tool unimportable is a failure too
    fake = env({"pip"})
    lines = []
    assert pytools.install("py", tmp_path, REQS, fake.has, False, lines.append) == 1
    assert "still missing after the install: framegate, pytest, diff_cover" in lines[-1]
    # a venv without pip (made by uv): uv installs into it; neither: say what to install
    monkeypatch.setattr(pytools.shutil, "which", lambda name: "/bin/uv")
    fake = env(set())
    fake.provides = {r.module: (r.module, "1") for r in REQS}
    assert pytools.install("py", tmp_path, REQS, fake.has, False, lambda s: None) == 0
    install = next(c for c in fake.commands if "install" in c)
    assert install[:5] == ["/bin/uv", "pip", "install", "--python", "py"]
    monkeypatch.setattr(pytools.shutil, "which", lambda name: None)
    fake = env(set())
    lines = []
    assert pytools.install("py", tmp_path, REQS, fake.has, False, lines.append) == 1
    assert "no pip and uv is not on PATH" in lines[-1] and fake.commands == []


def test_gate_tools_by_command() -> None:
    names = lambda cmds: [r.module for r in pytools.gate_tools(cmds)]  # noqa: E731
    assert names([["py", "-m", "ruff", "check", "src"], ["py", "-m", "mypy", "src"]]) == [
        "ruff",
        "mypy",
    ]
    assert names([["C:\\venv\\Scripts\\black.exe", "--check", "src"], []]) == ["black"]
    assert names([["py", "-m", "flake8", "ruffian/"], ["lint-imports"]]) == []


def test_python_requirements_before_and_after_the_config(demo_repo: DemoRepo) -> None:
    r = demo_repo
    core = ["framegate", "pytest", "pytest_cov", "hypothesis", "diff_cover"]
    # before framegate.toml exists: only what every Python project needs
    bare = get_adapter(parse_config(r.root, {"project": {"language": "python"}}))
    reqs = bare.tool_requirements()  # type: ignore[attr-defined]
    assert [x.module for x in reqs] == core
    src = framegate_source()
    assert src is not None and reqs[0].pip == ("-e", str(src))  # the tests run from a checkout
    # with the config: the tools of the gate commands in use
    adapter = get_adapter(load_config(r.root))
    assert [x.module for x in adapter.tool_requirements()] == [*core, "ruff", "mypy"]  # type: ignore[attr-defined]
    r.add_toml(
        "[gates.commands]\n"
        'format = ["python", "-m", "black", "--check", "{src}"]\n'
        'format_fix = ["python", "-m", "black", "{files}"]\n'
        "typecheck = []\n"
    )
    adapter = get_adapter(load_config(r.root))
    assert [x.module for x in adapter.tool_requirements()] == [*core, "ruff", "black"]  # type: ignore[attr-defined]


def test_command_works_before_init_and_in_a_set_up_repo(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    demo_repo: DemoRepo,
) -> None:
    # a set-up repo, real interpreter: the dev environment has every tool
    assert demo_repo.af("install-tools", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Project interpreter:" in out and "[ok     ] framegate" in out
    # a fresh Python repo without framegate.toml
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    init_git(fresh)
    (fresh / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n', "utf-8")
    monkeypatch.chdir(fresh)
    assert cli.main(["install-tools", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "[ok     ] framegate" in out and "ruff" not in out
    assert not (fresh / "framegate.toml").exists()  # it sets nothing up
    # no language to detect
    empty = tmp_path / "empty"
    empty.mkdir()
    lines: list[str] = []
    assert install_tools(dry_run=True, start=empty, echo=lines.append) == 1
    assert "could not be detected" in lines[-1]
    # a language framegate installs nothing for
    (empty / "App.sln").write_text("", "utf-8")
    lines = []
    assert install_tools(dry_run=True, start=empty, echo=lines.append) == 0
    assert "Nothing framegate installs for a dotnet project" in lines[-1]


def test_doctor_names_the_missing_plugin(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = get_adapter(load_config(demo_repo.root))
    monkeypatch.setattr(adapter, "_has_module", lambda module: module != "framegate")
    report = {name: (found, detail) for name, found, detail in adapter.tool_report()}
    assert report["framegate (pytest plugin: --req)"] == (False, "run: framegate install-tools")
