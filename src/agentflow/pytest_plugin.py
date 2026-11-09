"""pytest plugin (auto-registered via the `pytest11` entry point): `req` marker + `--req`.

Usage in tests:

    @pytest.mark.req("HI2-R-001", "HI2-E-003")
    def test_health_index_empty_window_raises(): ...

`pytest --req HI2-R-001,HI2-E-003` runs only tests tagged with at least one of the IDs.
"""

from __future__ import annotations

from typing import Any


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--req",
        default="",
        help="Comma-separated spec IDs; only run tests tagged with at least one of them.",
    )


def pytest_configure(config: Any) -> None:
    config.addinivalue_line(
        "markers", "req(*ids): spec IDs (requirements/edge cases/invariants) this test covers"
    )


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    wanted = {x.strip() for x in config.getoption("--req").split(",") if x.strip()}
    if not wanted:
        return
    selected: list[Any] = []
    deselected: list[Any] = []
    for item in items:
        ids = {a for m in item.iter_markers("req") for a in m.args}
        (selected if ids & wanted else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected
