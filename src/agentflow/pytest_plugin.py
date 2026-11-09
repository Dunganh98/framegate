"""pytest plugin (auto-registered via the `pytest11` entry point): `req` marker + `--req`.

Usage in tests:

    @pytest.mark.req("HI2-R-001", "HI2-E-003")
    def test_health_index_empty_window_raises(): ...

`pytest --req HI2-R-001,HI2-E-003` runs only tests tagged with at least one of the IDs.
`--req-paths tests/review` also keeps the tests under these paths (relative to the rootdir),
tagged or not (the final gates' feature scope: the feature's IDs + the review tests).
"""

from __future__ import annotations

import pathlib
from typing import Any


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--req",
        default="",
        help="Comma-separated spec IDs; only run tests tagged with at least one of them.",
    )
    parser.addoption(
        "--req-paths",
        default="",
        help="Comma-separated paths (relative to the rootdir) whose tests are kept as well.",
    )


def pytest_configure(config: Any) -> None:
    config.addinivalue_line(
        "markers", "req(*ids): spec IDs (requirements/edge cases/invariants) this test covers"
    )


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    wanted = {x.strip() for x in config.getoption("--req").split(",") if x.strip()}
    paths = [
        x.strip().replace("\\", "/").strip("/") + "/"
        for x in config.getoption("--req-paths").split(",")
        if x.strip()
    ]
    if not wanted and not paths:
        return
    root = pathlib.Path(str(config.rootpath))
    selected: list[Any] = []
    deselected: list[Any] = []
    for item in items:
        ids = {a for m in item.iter_markers("req") for a in m.args}
        keep = bool(ids & wanted)
        if not keep and paths:
            try:
                rel = pathlib.Path(str(item.path)).relative_to(root).as_posix()
            except ValueError:
                rel = ""
            keep = any(rel.startswith(p) for p in paths)
        (selected if keep else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected
