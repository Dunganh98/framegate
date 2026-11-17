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
from framegate import cli, preflight, procs, pytools
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


def other_task(home: pathlib.Path, root: pathlib.Path, feature: str, phase: str) -> pathlib.Path:
    """A task of another repository in this machine's list, as its "spec OK" wrote it."""
    progress = root / "out" / feature / "progress.json"
    progress.parent.mkdir(parents=True, exist_ok=True)
    progress.write_text(json.dumps({"phase": phase, "framegate": OTHER}), encoding="utf-8")
    entry = home / "framegate" / "tasks" / f"{feature}.json"
    entry.parent.mkdir(parents=True, exist_ok=True)
    data = {"root": str(root), "feature": feature, "progress": str(progress)}
    entry.write_text(json.dumps(data), encoding="utf-8")
    return entry


def test_spec_ok_writes_down_the_version_the_task_runs_with(
    demo_repo: DemoRepo, claude_home: pathlib.Path
) -> None:
    r = demo_repo
    assert not list(claude_home.glob("framegate/tasks/*.json"))
    assert r.af("approve-spec", "demo") == 0
    assert r.progress()["framegate"] == V
    [entry] = claude_home.glob("framegate/tasks/*.json")
    data = json.loads(entry.read_text(encoding="utf-8"))
    assert (data["feature"], data["framegate"]) == ("demo", V)
    assert pathlib.Path(data["root"]) == r.root.resolve()
    cfg = load_config(r.root)
    [task] = preflight.tasks_in_progress(cfg)
    assert (task.feature, task.version, task.pid) == ("demo", V, 0)
    # a task an older version approved: a run writes down what is missing, and only that
    progress = r.root / "out" / "demo" / "progress.json"
    progress.write_text(json.dumps({"phase": "IMPLEMENT"}), encoding="utf-8")
    entry.unlink()
    preflight.record_start(cfg, "demo")
    assert r.progress() == {"phase": "IMPLEMENT", "framegate": V}
    progress.write_text(json.dumps({"phase": "IMPLEMENT", "framegate": OTHER}), encoding="utf-8")
    preflight.record_start(cfg, "demo")
    assert r.progress()["framegate"] == OTHER and entry.is_file()
    # DONE: the task leaves the list
    progress.write_text(json.dumps({"phase": "DONE"}), encoding="utf-8")
    assert preflight.tasks_in_progress(cfg) == [] and not entry.exists()
    # no spec OK yet: nothing to write down
    preflight.record_start(cfg, "nothing-here")
    assert not list(claude_home.glob("framegate/tasks/*.json"))


def test_a_new_task_does_not_replace_the_tool_under_a_task_in_progress_here(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seen on a replay: tasks went from 0.12.0 to 0.12.2 during their runs. The check kept
    the versions of the feature it was called for only; the start check of the next task
    got the install command."""
    r = demo_repo
    assert r.af("approve-spec", "demo") == 0
    tools = Tools(monkeypatch, OTHER)
    code, lines = check("next-task", NEWER)
    assert code == 0 and tools.updates == 0 and len(lines) == 3
    assert not any(line.startswith("RUN:") for line in lines)
    assert lines[1].startswith(f"KEPT: the command-line tool is {V}, the plugin {NEWER} (demo is")
    assert "The update waits until it is DONE; /framegate:update does it now." in lines[1]
    assert lines[2].startswith(f"KEPT: the project environment has framegate {OTHER}")
    assert "(demo is in progress" in lines[2]
    # the task itself: the version it was started with is named
    code, lines = check("demo", NEWER)
    assert f"(demo is in progress (started with framegate {V}) and keeps" in lines[1]
    assert "The update waits until it is DONE" in lines[1]


def test_a_task_in_another_repository_keeps_the_tool_of_this_machine(
    demo_repo: DemoRepo,
    monkeypatch: pytest.MonkeyPatch,
    claude_home: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """One command-line tool per machine: a task in progress elsewhere keeps it. The
    workflow tools of this project are its own and follow the tool."""
    tools = Tools(monkeypatch, OTHER)
    elsewhere = tmp_path / "elsewhere"
    entry = other_task(claude_home, elsewhere, "port-x", "IMPLEMENT")
    other_task(claude_home, elsewhere, "second", "REVIEW")
    code, lines = check("demo", NEWER)
    assert code == 0 and not any(line.startswith("RUN:") for line in lines)
    assert lines[1].startswith(
        f"KEPT: the command-line tool is {V}, the plugin {NEWER} (port-x in {elsewhere} and 1 "
        "more is in progress and keeps the tool it was started with). The update waits"
    )
    assert tools.updates == 1 and lines[2].startswith("UPDATED: the project environment")
    # resume / status of this project: nothing is updated from there, as before
    code, lines = check(None, NEWER)
    assert "nothing is updated from here" in lines[1]
    # both DONE (or cleaned up): the next start check updates, and the list is cleared
    for name in ("port-x", "second"):
        other_task(claude_home, elsewhere, name, "DONE")
    code, lines = check("demo", NEWER)
    assert lines[1].startswith("RUN: uv tool install --force") and not entry.exists()
    other_task(claude_home, elsewhere, "port-x", "IMPLEMENT")
    (elsewhere / "out" / "port-x" / "progress.json").unlink()
    assert check("demo", NEWER)[1][1].startswith("RUN:") and not entry.exists()


def test_update_waits_for_a_run_that_is_going(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`framegate update` keeps nothing back, but replacing a running tool breaks the run
    (seen: a `status` failed on an import)."""
    r = demo_repo
    assert r.af("approve-spec", "demo") == 0
    cfg = load_config(r.root)
    procs.write_lock(cfg.out_dir, "demo")  # this process: alive
    tools = Tools(monkeypatch, OTHER)
    code, lines = check(None, NEWER, update=True)
    assert code == 0 and len(lines) == 2
    assert lines[1].startswith("PROBLEM: a framegate run is active (demo, pid ")
    assert "replacing the command-line tool now would break it" in lines[1]
    code, lines = check(None, V, update=True)
    assert tools.updates == 0 and "replacing the project's workflow tools now" in lines[1]
    # everything in line: nothing to say about the run
    tools.version = V
    assert check(None, V, update=True)[1][0].startswith("Start check: ok")
    procs.remove_lock(cfg.out_dir)
    tools.version = OTHER
    assert check(None, NEWER, update=True)[1][1].startswith("RUN: uv tool install")


@pytest.mark.parametrize("how", ["env", "toml"])
def test_the_update_check_can_be_switched_off(
    demo_repo: DemoRepo,
    monkeypatch: pytest.MonkeyPatch,
    claude_home: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    how: str,
) -> None:
    """A pinned setup, a replay of old tasks: nothing is compared, nothing updated."""
    r = demo_repo
    if how == "env":
        monkeypatch.setenv("FRAMEGATE_UPDATE_CHECK", "0")
    else:
        r.add_toml("[update]\ncheck = false\n")
    assert load_config(r.root).update_check is False
    tools = Tools(monkeypatch, OTHER)
    plugin_on_disk(claude_home, "100.0.0")  # no restart notice either
    for feature, only in (("demo", False), (None, False), ("demo", True)):
        assert check(feature, NEWER, check_only=only) == (
            0,
            [f"Start check: ok (framegate {V}; the update check is off)."],
        )
    assert tools.updates == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert "/framegate:update" not in capsys.readouterr().out
    # what would stop a run at once is still said
    tools.version = ""
    assert check("demo", NEWER)[1][1].startswith("PROBLEM: the project environment has no")
    # the user's own `framegate update` still works
    plugin_on_disk(claude_home, NEWER)
    tools.version = OTHER
    code, lines = check(None, NEWER, update=True)
    assert lines[1].startswith("RUN: uv tool install --force")


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
