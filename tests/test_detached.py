"""`framegate run` detached (FRAMEGATE_DETACH_RUN=1, set by a replay), without a real run."""

from __future__ import annotations

import os
import pathlib
import types
from typing import Any

import pytest

from framegate import cli, detached, procs


class FakeChild:
    def __init__(self, code: int | None) -> None:
        self.pid, self.returncode = 4242, code

    def poll(self) -> int | None:
        return self.returncode


def fake_orch(tmp_path: pathlib.Path) -> Any:
    out = tmp_path / "out"

    def fdir(f: str) -> pathlib.Path:
        (out / f).mkdir(parents=True, exist_ok=True)
        return out / f

    return types.SimpleNamespace(cfg=types.SimpleNamespace(out_dir=out), fdir=fdir)


def spawn(monkeypatch: pytest.MonkeyPatch, orch: Any, code: int | None, text: bytes = b"") -> list:
    """procs.detach replaced: the child writes `text` to its log and, still running, the lock."""
    calls: list[tuple[list[str], dict[str, str]]] = []

    def detach(cmd: list[str], out: Any, env: dict[str, str]) -> FakeChild:
        calls.append((cmd, env))
        out.write(text)
        if code is None:
            procs.write_lock(orch.cfg.out_dir, "demo")  # our own pid: alive
        return FakeChild(code)

    monkeypatch.setattr(detached.procs, "detach", detach)
    monkeypatch.setenv(detached.ENV, "1")
    return calls


def test_wanted_only_with_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(detached.ENV, raising=False)
    assert not detached.wanted()
    monkeypatch.setenv(detached.ENV, "1")
    assert detached.wanted()


def test_returns_once_the_child_holds_the_lock(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    orch = fake_orch(tmp_path)
    calls = spawn(monkeypatch, orch, None)
    assert detached.run(orch, "demo", wait=5, poll=0.01) == 0
    ((cmd, env),) = calls
    assert cmd[-3:] == ["framegate", "run", "demo"]
    assert detached.ENV not in env  # the child runs; it never detaches again
    out = capsys.readouterr().out
    assert f"runs detached (pid {os.getpid()}" in out and "framegate watch demo" in out


def test_a_child_that_ends_first_gives_its_output_and_code(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    orch = fake_orch(tmp_path)
    (orch.fdir("demo") / detached.RUN_LOG).write_bytes(b"an older run\n")
    spawn(monkeypatch, orch, 10, b"waiting at the tests gate\n")
    assert detached.run(orch, "demo", wait=5, poll=0.01) == 10
    assert capsys.readouterr().out == "waiting at the tests gate\n"


def test_an_active_run_is_not_detached(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = fake_orch(tmp_path)
    calls = spawn(monkeypatch, orch, None)
    procs.write_lock(orch.cfg.out_dir, "other")
    assert detached.run(orch, "demo") is None and not calls


def test_cli_run_detaches_with_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    from framegate import orchestrator

    def run(orch: Any, f: str) -> int:
        raise AssertionError("ran in the caller")

    monkeypatch.setattr(detached, "run", lambda orch, f: 0)
    monkeypatch.setattr(orchestrator.Orchestrator, "run", run)
    monkeypatch.setattr("framegate.config.find_root", lambda: pathlib.Path.cwd())
    monkeypatch.setattr("framegate.config.load_config", lambda root: types.SimpleNamespace())
    monkeypatch.setattr(orchestrator.Orchestrator, "__init__", lambda self, cfg: None)
    monkeypatch.setenv(detached.ENV, "1")
    assert cli.main(["run", "demo"]) == 0


def test_the_replay_gives_the_chat_the_env_var(tmp_path: pathlib.Path) -> None:
    from framegate.replay import run as replay
    from framegate.replay.sandbox import Layout, write_json

    lay = Layout(tmp_path, "c1")
    lay.run.mkdir(parents=True)
    write_json(lay.run / "run.json", {"python": str(tmp_path / "bin" / "python")})
    assert replay.run_env(lay)[detached.ENV] == "1"
