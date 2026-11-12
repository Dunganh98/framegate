"""`[paths] src` as a list: a project whose sources live in several folders (a Godot game's
core/, autoload/, scenes/; a data project's src/, notebooks/, sql/)."""

from __future__ import annotations

import pytest

from conftest import DemoRepo, git
from framegate.adapters import get_adapter
from framegate.checks import frame_diff
from framegate.config import ConfigError, load_config, parse_config
from framegate.orchestrator import Orchestrator


def test_one_folder_or_a_list(demo_repo: DemoRepo) -> None:
    root = demo_repo.root
    one = parse_config(root, {"paths": {"src": "app/"}})
    assert one.src_roots == ["app"] and one.paths.src == "app"
    many = parse_config(root, {"paths": {"src": ["core/", "./autoload", "scenes"]}})
    assert many.src_roots == ["core", "autoload", "scenes"]
    assert many.paths.src == "core"  # the first one, for texts and single-folder tools
    assert many.in_src("autoload/game.gd") and not many.in_src("corelib/x.gd")
    assert many.write_paths("IMPLEMENT") == ["core/", "autoload/", "scenes/", "out/"]
    assert many.write_paths("SPEC") == ["specs/", "core/", "autoload/", "scenes/"]
    assert parse_config(root, {}).src_roots == ["src"]
    with pytest.raises(ConfigError, match="at least one folder"):
        parse_config(root, {"paths": {"src": []}})
    with pytest.raises(ConfigError, match="list of strings"):
        parse_config(root, {"paths": {"src": ["core", 3]}})


def test_commands_and_texts_name_every_folder(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.set_toml("paths", "src", '["src", "lib"]')
    cfg = load_config(r.root)
    adapter = get_adapter(cfg)
    assert adapter.resolve_command(["tool", "{src}", "--out={out}", "x/{src}"]) == [
        "tool",
        "src",
        "lib",
        "--out=out",
        "x/src",
    ]
    lint = adapter.default_gate_commands()["lint"]
    assert lint[-3:] == ["src", "lib", "tests"]
    orch = Orchestrator(cfg, adapter, echo=lambda s: None)
    assert "stubs in src/, lib/." in orch.render("the stubs in {src}/.", "demo")


def test_frame_and_park_cover_every_folder(demo_repo: DemoRepo) -> None:
    """Changed sources, forbidden patterns and the revert of parked work see the second folder."""
    r = demo_repo
    (r.root / "lib").mkdir()
    (r.root / "lib" / "util.py").write_text("X = 1\n", encoding="utf-8")
    r.set_toml("paths", "src", '["src", "lib"]')  # commits everything
    git(r.root, "tag", "tests-approved/demo")
    cfg = load_config(r.root)
    adapter = get_adapter(cfg)
    (r.root / "lib" / "util.py").write_text("X = 2  # noqa\n", encoding="utf-8")
    (r.root / "lib" / "new.py").write_text("Y = 1  # type: ignore\n", encoding="utf-8")
    assert frame_diff.changed_src_files(adapter, "tests-approved/demo") == [
        "lib/new.py",
        "lib/util.py",
    ]
    res = frame_diff.check(adapter, "demo")
    assert res.status == "FAIL"
    assert "noqa" in res.output and "type: ignore" in res.output

    orch = Orchestrator(cfg, adapter, echo=lambda s: None)
    diff = orch.revert_src("demo", "step 1")
    assert diff.endswith("step-1.diff")
    assert (r.root / "lib" / "util.py").read_text(encoding="utf-8") == "X = 1\n"
    assert not (r.root / "lib" / "new.py").exists()
    assert "lib/util.py" in (r.root / diff).read_text(encoding="utf-8")
