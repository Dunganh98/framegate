"""`framegate uses <symbol>`: the source lines that read a function or a variable."""

from __future__ import annotations

import pathlib

import pytest

from framegate import cli, uses
from framegate.adapters import get_adapter
from framegate.config import parse_config

CORE = """\
LIMIT: int = 3
limits = [LIMIT]


def scale(value: int) -> int:
    # scale() is used by the report
    return value * LIMIT


class Box:
    def __init__(self) -> None:
        self.limit = LIMIT
"""
REPORT = "from pkg.core import scale\n\n\ndef total(xs: list[int]) -> int:\n"
REPORT += "    return sum(scale(x) for x in xs)\n"


def project(root: pathlib.Path) -> None:
    files = {
        "src/pkg/core.py": CORE,
        "src/pkg/report.py": REPORT,
        "tests/test_core.py": "from pkg.core import scale\n\nassert scale(1) == 3\n",
        "src/pkg/tests/test_in_src.py": "from pkg.core import scale\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def test_uses_lists_reads_in_the_source_only(tmp_path: pathlib.Path) -> None:
    project(tmp_path)
    cfg = parse_config(tmp_path, {"paths": {"src": "src", "tests": "src/pkg/tests"}})
    adapter = get_adapter(cfg)
    found = uses.find(cfg, adapter, "pkg.core.scale()")
    assert found.read == [
        "src/pkg/report.py:1: from pkg.core import scale",
        "src/pkg/report.py:5: return sum(scale(x) for x in xs)",
    ]
    assert found.defined == ["src/pkg/core.py:5: def scale(value: int) -> int:"]
    limit = uses.find(cfg, adapter, "LIMIT")
    assert limit.defined == ["src/pkg/core.py:1: LIMIT: int = 3"]
    assert [x.split(":")[1] for x in limit.read] == ["2", "7", "12"]  # `limit` is another name
    assert uses.find(cfg, adapter, "scale", "src/pkg/core.py").read == []
    assert uses.find(cfg, adapter, "not a name").lines()[0].startswith("0 use(s)")


def test_the_uses_command(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project(tmp_path)
    (tmp_path / "framegate.toml").write_text(
        '[project]\nlanguage = "python"\n[paths]\nsrc = "src"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    assert cli.main(["uses", "scale", "--file", "src/pkg/report.py"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "2 use(s) of `scale` in the source (tests left out):"
    assert out[2] == "src/pkg/report.py:5: return sum(scale(x) for x in xs)"


def test_the_test_files_that_mention_a_symbol(tmp_path: pathlib.Path) -> None:
    project(tmp_path)
    (tmp_path / "tests/test_box.py").write_text("# scale is not used here\nBox()\n", "utf-8")
    (tmp_path / "tests/review").mkdir()
    (tmp_path / "tests/review/test_probe.py").write_text("scale(2)\n", encoding="utf-8")
    cfg = parse_config(tmp_path, {"paths": {"src": "src"}})
    found = uses.test_files(cfg, get_adapter(cfg), ["scale", "Box", "rescale", "a.b"])
    assert found == {"tests/test_box.py": ["Box"], "tests/test_core.py": ["scale"]}
    assert uses.test_files(cfg, get_adapter(cfg), []) == {}
