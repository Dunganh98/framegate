"""`framegate preflight`: the start step of the chat skills brings the plugin, the command-line
tool and the project environment's workflow tools to one version, or says what is left.

Seen on a real run: plugin 0.9.0 and tool 0.9.0, but the project's .venv still had the
framegate pytest plugin 0.8.0; nothing re-ran `install-tools`, nothing noticed."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable

import pytest

import framegate
from conftest import DemoRepo
from framegate import cli, preflight, pytools
from framegate.adapters import get_adapter
from framegate.adapters.python import PythonAdapter
from framegate.config import load_config

V = framegate.__version__
OTHER = "0.1.0"
NEWER = "99.0.0"


@pytest.fixture(autouse=True)
def claude_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Never the developer's own ~/.claude: an empty Claude Code config folder."""
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


def plugin_on_disk(home: pathlib.Path, *versions: str) -> None:
    entries = [{"scope": "user", "installPath": f"x/{v}", "version": v} for v in versions]
    (home / "plugins").mkdir(parents=True, exist_ok=True)
    (home / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"framegate@framegate": entries, "o@m": []}}),
        encoding="utf-8",
    )


def check(*args: object, **kw: object) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = preflight.run(*args, echo=lines.append, **kw)  # type: ignore[arg-type]
    return code, lines


class Tools:
    """The project environment's framegate part at a version; records the updates."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, version: str, works: bool = True) -> None:
        self.version = version
        self.updates = 0
        self.works = works
        monkeypatch.setattr(PythonAdapter, "tools_version", lambda adapter: self.version)
        monkeypatch.setattr(PythonAdapter, "install_tools", self.install)

    def install(self, dry_run: bool, echo: Callable[[str], None], update: bool = False) -> int:
        assert update and not dry_run
        self.updates += 1
        if not self.works:
            echo("error: the install failed:\nno network")
            return 1
        self.version = V
        echo("CHANGED versions of packages that were already installed: pluggy 1.5 -> 1.6")
        return 0


def test_everything_in_line_is_one_line(demo_repo: DemoRepo) -> None:
    code, lines = check("demo", V)
    assert code == 0
    assert lines == [f"Start check: ok (framegate {V}: command-line tool, plugin, project tools)."]
    # run by hand, without a skill's version: the plugin is not part of the check
    code, lines = check()
    assert code == 0 and lines == [
        f"Start check: ok (framegate {V}: command-line tool, project tools)."
    ]


def test_new_work_gets_the_tool_of_the_plugin_version_from_the_skill(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tool cannot replace itself while it runs (Windows locks the exe): it names the
    command, the skill runs it and asks again; nothing else is touched before that."""
    tools = Tools(monkeypatch, OTHER)
    code, lines = check("demo", OTHER)
    assert code == 0 and lines[0] == "Start check:" and len(lines) == 2
    assert lines[1].startswith(
        f'RUN: uv tool install --force "git+{framegate.SOURCE_URL}@v{OTHER}"'
    )
    assert f"`framegate preflight demo --plugin-version {OTHER}` again" in lines[1]
    assert tools.updates == 0


def test_work_in_progress_keeps_its_tool_and_its_project_tools(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = demo_repo
    assert r.af("approve-spec", "demo") == 0
    tools = Tools(monkeypatch, OTHER)
    code, lines = check("demo", NEWER)
    assert code == 0 and tools.updates == 0
    assert lines[0] == "Start check:"
    assert lines[1].startswith(f"KEPT: the command-line tool is {V}, the plugin {NEWER} (demo is")
    assert lines[2].startswith(f"KEPT: the project environment has framegate {OTHER}, the")
    assert "framegate install-tools --update" in lines[2]
    assert len(lines) == 3


def test_project_tools_are_brought_to_the_tools_version(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = Tools(monkeypatch, OTHER)
    code, lines = check("demo", V)
    assert code == 0 and tools.updates == 1
    assert lines[1].startswith(
        f"UPDATED: the project environment's workflow tools (framegate {OTHER} -> framegate {V})."
    )
    assert "CHANGED versions" in lines[1] and "pluggy 1.5 -> 1.6" in lines[1]
    # in line now
    assert check("demo", V)[0] == 0 and tools.updates == 1
    # missing altogether: installed the same way
    tools.version = ""
    code, lines = check("demo", V)
    assert code == 0 and tools.updates == 2 and "(no framegate part -> framegate" in lines[1]
    # a failed update is a problem, with the installer's last lines
    tools = Tools(monkeypatch, OTHER, works=False)
    code, lines = check("demo", V)
    assert code == 0 and lines[1].startswith("PROBLEM: the project environment has framegate")
    assert "no network" in lines[1]


def test_check_only_and_no_feature_update_nothing(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resume and status: they report, the skills that start work update."""
    tools = Tools(monkeypatch, OTHER)
    for args, kw in ((("demo", NEWER), {"check_only": True}), ((None, NEWER), {})):
        code, lines = check(*args, **kw)
        assert code == 0 and tools.updates == 0, lines
        assert (
            lines[1].startswith("KEPT: the command-line tool") and "nothing is updated" in lines[1]
        )
        assert lines[2].startswith("KEPT: the project environment has framegate")
    # no framegate part at all cannot be kept: the tests would not run
    tools.version = ""
    code, lines = check("demo", V, check_only=True)
    assert code == 0 and lines[1].startswith("PROBLEM: the project environment has no framegate")


def test_a_session_with_the_old_skills_is_told_to_restart(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch, claude_home: pathlib.Path
) -> None:
    """Claude Code updated the plugin in the background; the running session still has the
    old skills. Nothing is updated for them."""
    tools = Tools(monkeypatch, OTHER)
    plugin_on_disk(claude_home, OTHER, NEWER)
    assert preflight.installed_plugin_version() == NEWER
    code, lines = check("demo", V)
    assert code == 0 and tools.updates == 0 and len(lines) == 2
    assert lines[1].startswith(f"TELL THE USER: the framegate plugin was updated to {NEWER}")
    assert f"skills of {V}" in lines[1] and "Restart Claude Code" in lines[1]
    # the session runs what is installed: nothing to say
    plugin_on_disk(claude_home, OTHER, V)
    tools.version = V
    assert check("demo", V) == (
        0,
        [f"Start check: ok (framegate {V}: command-line tool, plugin, project tools)."],
    )
    # an unreadable or foreign registry is no finding
    (claude_home / "plugins" / "installed_plugins.json").write_text("{", encoding="utf-8")
    assert preflight.installed_plugin_version() is None
    assert preflight.version_key("0.10.0") > preflight.version_key("0.9.1")


def test_setup_problems_are_named(
    demo_repo: DemoRepo, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRAMEGATE_CLAUDE", "no-such-claude-xyz")
    code, lines = check("demo", V)
    assert code == 0 and len(lines) == 2
    assert lines[1].startswith("PROBLEM: claude CLI not found") and "framegate doctor" in lines[1]
    empty = tmp_path / "empty"
    empty.mkdir()
    code, lines = check("demo", V, start=empty)
    assert code == 0 and lines[1].startswith("PROBLEM: no framegate.toml here")


def test_the_commands(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    assert cli.main(["preflight", "demo", "--plugin-version", V]) == 0
    assert capsys.readouterr().out.startswith("Start check: ok (framegate")
    assert cli.main(["preflight", "--check", "--plugin-version", NEWER]) == 0
    assert "KEPT: the command-line tool" in capsys.readouterr().out
    # the real project interpreter is at this version: --update has nothing to do
    assert cli.main(["install-tools", "--update", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "[ok     ] framegate" in out and "[update ]" not in out
    adapter = get_adapter(load_config(demo_repo.root))
    assert adapter.tools_version() == V


def test_install_tools_update_installs_the_stale_framegate_again(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = get_adapter(load_config(demo_repo.root))
    versions = iter([OTHER, V])
    monkeypatch.setattr(adapter, "tools_version", lambda: next(versions))
    seen: dict[str, object] = {}

    def install(
        python: str,
        cwd: pathlib.Path,
        reqs: object,
        has: object,
        dry: bool,
        echo: Callable[[str], None],
        update: tuple[str, ...] = (),
    ) -> int:
        seen.update(update=update, dry=dry)
        return 0

    monkeypatch.setattr(pytools, "install", install)
    assert adapter.install_tools(False, lambda s: None, update=True) == 0
    assert seen == {"update": ("framegate",), "dry": False}
    # the version did not move: an error
    versions = iter([OTHER, OTHER, OTHER])
    lines: list[str] = []
    assert adapter.install_tools(False, lines.append, update=True) == 1
    assert f"still has framegate {OTHER}, not {V}" in lines[-1]
    # without --update a present plugin is left alone
    versions = iter([])
    assert adapter.install_tools(False, lambda s: None) == 0 and seen["update"] == ()


def test_pytools_installs_an_update_though_the_module_is_there(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def run(cmd: list[str], cwd: pathlib.Path, **kw: object) -> tuple[int, str]:
        commands.append(list(cmd))
        return 0, ""

    monkeypatch.setattr(pytools, "run_command", run)
    reqs = [
        pytools.ToolReq("framegate", ("framegate @ git+x@v1",), "pytest plugin"),
        pytools.ToolReq("pytest", ("pytest",), "tests"),
    ]
    lines: list[str] = []
    code = pytools.install(
        "py", tmp_path, reqs, lambda m: True, False, lines.append, update=("framegate",)
    )
    assert code == 0 and "  [update ] framegate (pytest plugin)" in lines
    assert ["py", "-m", "pip", "install", "framegate @ git+x@v1"] in commands


def test_a_finished_task_is_updated_like_new_work(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seen live: after a release the wrap-up of a DONE task (publish, pipeline task) ran
    with the old tool, because "spec approved" counted as in progress for ever."""
    r = demo_repo
    assert r.af("approve-spec", "demo") == 0
    cfg = load_config(r.root)
    metrics = r.root / cfg.paths.metrics / "workflow_metrics.csv"
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_text("date,feature\n2026-11-16,demo\n", encoding="utf-8")
    assert not preflight.in_progress(cfg, "demo")
    tools = Tools(monkeypatch, OTHER)
    code, lines = check("demo", NEWER)
    assert code == 0 and lines[1].startswith("RUN: uv tool install --force")
    code, lines = check("demo", V)
    assert tools.updates == 1 and lines[1].startswith("UPDATED: the project environment")


def test_update_keeps_nothing(
    demo_repo: DemoRepo,
    monkeypatch: pytest.MonkeyPatch,
    claude_home: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`framegate update`: the user asked, so also with work in progress and without a
    feature; the target is the plugin on this machine."""
    r = demo_repo
    assert r.af("approve-spec", "demo") == 0
    tools = Tools(monkeypatch, OTHER)
    code, lines = check(None, NEWER, update=True)
    assert code == 0 and tools.updates == 0
    assert lines[1].startswith(
        f'RUN: uv tool install --force "git+{framegate.SOURCE_URL}@v{NEWER}"'
    )
    assert f"`framegate update --plugin-version {NEWER}` again" in lines[1]
    code, lines = check(None, V, update=True)
    assert tools.updates == 1 and lines[1].startswith("UPDATED: the project environment")
    # the command: the plugin on disk is the target; status says when they differ
    assert preflight.version_note() == ""
    plugin_on_disk(claude_home, NEWER)
    capsys.readouterr()
    assert cli.main(["update"]) == 0
    assert f"@v{NEWER}" in capsys.readouterr().out
    note = f"framegate: the plugin is {NEWER}, the command-line tool {V}: /framegate:update"
    assert cli.main(["status"]) == 0
    assert capsys.readouterr().out.startswith(note)
    plugin_on_disk(claude_home, V)
    assert cli.main(["status", "demo", "--brief"]) == 0
    assert "/framegate:update" not in capsys.readouterr().out
