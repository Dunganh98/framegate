"""Listed tests ([gates] unrunnable_tests) that failed on this machine. Later whole-suite runs
here skip them; the record is in the out folder, so another machine still runs them.
`framegate doctor --tree` runs them all and writes the record anew."""

from __future__ import annotations

import json
import pathlib

from framegate import unrunnable
from framegate.adapters.base import TestRun
from framegate.config import Config

FILE = "known_red.json"


def _path(cfg: Config) -> pathlib.Path:
    return cfg.out_dir / FILE


def record(cfg: Config, run: TestRun) -> list[str]:
    """From a run of every test: the listed entries all of whose tests failed. A run without
    per-test results records nothing."""
    if run.cases is None or run.incomplete:
        return []
    red = []
    for entry in cfg.unrunnable_tests:
        hits = [c for c in run.cases if unrunnable.matches(cfg, c.name, entry)]
        if hits and all(c.outcome in ("failed", "error") for c in hits):
            red.append(entry)
    path = _path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": red}, indent=1), encoding="utf-8")
    return red


def entries(cfg: Config) -> list[str]:
    """The recorded entries that are still listed."""
    try:
        data = json.loads(_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = data.get("entries") if isinstance(data, dict) else None
    return [e for e in found or [] if isinstance(e, str) and e in cfg.unrunnable_tests]
