"""State files survive a killed run: progress.json is replaced atomically, a cut-short line of
events.jsonl is skipped, and agent_run events carry what cost analysis needs."""

from __future__ import annotations

import json

import pytest

from conftest import DemoRepo
from framegate.config import load_config
from framegate.orchestrator import token_usage
from framegate.state import (
    StateError,
    load_progress,
    log_event,
    read_events,
    save_progress,
)


def test_progress_is_replaced_not_rewritten(demo_repo: DemoRepo) -> None:
    cfg = load_config(demo_repo.root)
    save_progress(cfg, "demo", {"phase": "IMPLEMENT", "attempt": 2})
    d = cfg.feature_out("demo")
    assert json.loads((d / "progress.json").read_text("utf-8"))["attempt"] == 2
    assert not (d / "progress.json.tmp").exists()
    assert load_progress(cfg, "demo")["phase"] == "IMPLEMENT"
    (d / "progress.json").write_text('{"phase": "IMPL', encoding="utf-8")
    with pytest.raises(StateError, match="not valid JSON"):
        load_progress(cfg, "demo")


def test_a_cut_short_event_line_is_skipped(demo_repo: DemoRepo) -> None:
    cfg = load_config(demo_repo.root)
    before = len(read_events(cfg, "demo"))  # `framegate spec` logged its own
    log_event(cfg, "demo", "gate", passed=True)
    with open(cfg.out_dir / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-11-11T10:00:00", "feature": "demo", "kind": "ga')  # killed here
    log_event(cfg, "demo", "waiting")  # starts its own line after the broken one
    kinds = [e["kind"] for e in read_events(cfg, "demo")[before:]]
    assert kinds == ["gate", "waiting"]


def test_token_usage_of_a_result() -> None:
    res = {
        "usage": {
            "input_tokens": 12,
            "output_tokens": 340,
            "cache_read_input_tokens": 5000,
            "cache_creation_input_tokens": 800,
            "server_tool_use": {"web_search_requests": 0},
        }
    }
    assert token_usage(res) == {
        "tokens_in": 12,
        "tokens_out": 340,
        "cache_read": 5000,
        "cache_write": 800,
    }
    assert token_usage({}) == {} and token_usage({"usage": "n/a"}) == {}


def test_progress_move_is_tried_again_when_the_file_is_held(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows refuses the move while a reader has progress.json open (WinError 5)."""
    import framegate.state as state

    cfg = load_config(demo_repo.root)
    real, calls = state.os.replace, []

    def held_twice(src: object, dst: object) -> None:
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError(13, "Access is denied")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(state.os, "replace", held_twice)
    monkeypatch.setattr(state.time, "sleep", lambda s: None)
    save_progress(cfg, "demo", {"phase": "REVIEW"})
    assert len(calls) == 3
    assert load_progress(cfg, "demo")["phase"] == "REVIEW"


def test_progress_move_gives_up_with_the_error(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    import framegate.state as state

    cfg = load_config(demo_repo.root)

    def held(src: object, dst: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(state.os, "replace", held)
    monkeypatch.setattr(state.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        save_progress(cfg, "demo", {"phase": "REVIEW"})
