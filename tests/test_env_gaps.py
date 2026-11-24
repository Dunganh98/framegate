"""Frameworks the project declares and the environment lacks: a fact for the spec dialogue."""

from __future__ import annotations

import pathlib

from framegate import env_gaps
from framegate.adapters import get_adapter
from framegate.adapters.python_probe import declared
from framegate.config import parse_config

PYPROJECT = """\
[project]
name = "demo"
dependencies = [
  "pytest>=7",
  "framework-nobody-has>=1 ; python_version >= '3.0'",
  "only-on-python-one ; python_version < '2.0'",
]

[project.optional-dependencies]
spark = ["sparkish_not_there[sql]==3.5"]
"""
TEST = 'import pytest\n\nnp = pytest.importorskip("module_nobody_has.sub")\n'


def test_declared_frameworks_the_environment_lacks(tmp_path: pathlib.Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text(
        "# tools\n-r base.txt\nlinter-nobody-has\n", "utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text(TEST, encoding="utf-8")
    assert declared(tmp_path)["sparkish_not_there[sql]==3.5"] == "pyproject.toml extra 'spark'"
    cfg = parse_config(tmp_path, {})
    adapter = get_adapter(cfg)
    assert adapter.missing_frameworks() == {
        "framework-nobody-has": "pyproject.toml dependencies",
        "sparkish-not-there": "pyproject.toml extra 'spark'",
        "linter-nobody-has": "requirements-dev.txt",
        "module_nobody_has.sub": "importorskip in tests/test_x.py",
    }
    lines = env_gaps.lines(adapter)
    assert lines[1] == (
        "declared but not installed: sparkish-not-there (pyproject.toml extra 'spark') — tests "
        "needing it cannot run here: ask the user to drop those tests/requirements, allow "
        "installing it, or another way"
    )
    block = env_gaps.write(cfg, adapter, "demo")
    path = cfg.feature_out("demo") / env_gaps.FILE
    assert block[0] == "## Declared but not installed: 4"
    assert path.read_text(encoding="utf-8").count("\n- declared but not installed: ") == 4

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "requirements-dev.txt").unlink()
    (tmp_path / "tests/test_x.py").write_text("import pytest\n", encoding="utf-8")
    assert env_gaps.write(cfg, adapter, "demo") == [] and not path.exists()
