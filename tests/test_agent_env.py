"""The environment of an agent run: the project's interpreter first on PATH, no chat memory.

Seen on a real run: the agents' `python -m pytest --req ...` ran the machine's global Python,
which has no framegate pytest plugin (`unrecognized arguments: --req`), while the gates used
the project's .venv; and a retro agent quoted Claude Code's auto-memory of an earlier chat."""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

from conftest import DemoRepo
from framegate import agent_env, pytools
from framegate.adapters import get_adapter
from framegate.config import load_config, parse_config
from framegate.orchestrator import EXIT_GATE


def fake_interpreter(folder: pathlib.Path, venv: bool) -> pathlib.Path:
    """A folder laid out like a venv (or a plain install) with an empty `python` file."""
    scripts = folder / ("Scripts" if os.name == "nt" else "bin") if venv else folder
    scripts.mkdir(parents=True)
    exe = scripts / ("python.exe" if os.name == "nt" else "python")
    exe.write_text("", encoding="utf-8")
    if venv:
        (folder / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    return exe


def test_path_with_puts_folders_first_once() -> None:
    sep = os.pathsep
    assert pytools.path_with(["b", "x"], base=sep.join(["a", "b", "c"])) == sep.join(
        ["b", "x", "a", "c"]
    )
    assert pytools.path_with(["a"], base="") == "a"


def test_a_venv_interpreter_leads_the_path_and_sets_virtual_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", os.pathsep.join(["/global/python", "/usr/bin"]))
    exe = fake_interpreter(tmp_path / ".venv", venv=True)
    env = pytools.interpreter_env(str(exe))
    assert env["PATH"].split(os.pathsep) == [str(exe.parent), "/global/python", "/usr/bin"]
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_a_plain_interpreter_brings_its_scripts_folder_and_no_virtual_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    exe = fake_interpreter(tmp_path / "Python311", venv=False)
    (exe.parent / "Scripts").mkdir()  # where a Windows install keeps pytest.exe, ruff.exe
    env = pytools.interpreter_env(str(exe))
    assert env == {
        "PATH": os.pathsep.join([str(exe.parent), str(exe.parent / "Scripts"), "/usr/bin"])
    }
    # an interpreter that is not there changes nothing
    assert pytools.interpreter_env(str(tmp_path / "nope" / "python")) == {}
    assert pytools.interpreter_env("no-such-python-xyz") == {}


def test_python_adapter_uses_the_projects_venv_and_other_adapters_nothing(
    tmp_path: pathlib.Path,
) -> None:
    exe = fake_interpreter(tmp_path / ".venv", venv=True)
    adapter = get_adapter(parse_config(tmp_path, {"project": {"language": "python"}}))
    env = adapter.agent_env()
    assert env["PATH"].split(os.pathsep)[0] == str(exe.parent)
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")
    found = agent_env.which("python", adapter)
    assert found is not None and found.parent == exe.parent
    dotnet = get_adapter(parse_config(tmp_path, {"project": {"language": "dotnet"}}))
    assert dotnet.agent_env() == {}
    whole = agent_env.build(dotnet, "implement", base={"PATH": "p", "X": "1"})
    assert whole == {
        "PATH": "p",
        "X": "1",
        "FRAMEGATE_AGENT": "implement",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    }


def test_the_environment_reaches_the_agent(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fake claude records what its own process sees: `python` is the interpreter the
    gates use although another folder leads framegate's PATH, and the memory is off."""
    r = demo_repo
    adapter = get_adapter(load_config(r.root))
    project = pathlib.Path(adapter.python)  # type: ignore[attr-defined]
    other = r.root.parent / "global-python"
    fake_interpreter(other, venv=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(other), os.environ["PATH"]]))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", raising=False)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    calls = r.calls()
    assert calls
    for call in calls:
        env = call["env"]
        assert pathlib.Path(env["path0"]) == project.parent
        assert pathlib.Path(env["python"]).parent == project.parent
        assert env["no_memory"] == "1"
        if (project.parent.parent / "pyvenv.cfg").is_file():  # the tests run from a venv
            assert pathlib.Path(env["virtual_env"]) == project.parent.parent


def test_doctor_checks_the_agents_python(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = get_adapter(load_config(demo_repo.root))
    name = "agents' python (`python -m pytest --req` in an agent's shell)"
    report = {n: (found, detail) for n, found, detail in adapter.tool_report()}
    found, detail = report[name]
    assert found and pathlib.Path(detail).parent == pathlib.Path(sys.executable).parent
    # an interpreter whose pytest lacks the plugin (here: one that cannot run at all)
    monkeypatch.setattr(pytools, "interpreter_env", lambda python: {"PATH": ""})
    report = {n: (found, detail) for n, found, detail in adapter.tool_report()}
    assert report[name][0] is False and "was not found" in report[name][1]
