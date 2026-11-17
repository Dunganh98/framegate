"""What a framegate.toml change during a task changes, and whether it weakens a check.

A task may change how a check runs (a timeout, a tool path, a command, agent settings). What
a check demands or leaves out is setup work (/framegate:setup): redo.apply_config_update
refuses such a change. A key that is on no list below counts as weakening.
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase
from typing import Any

from framegate.config import Config

#: keys (section.key) that never change what a check demands
SAFE = (
    "agents.claude",
    "agents.max_attempts",
    "agents.max_turns",
    "agents.max_budget_usd",
    "agents.model",
    "agents.effort",
    "agents.step_batch_ids",
    "agents.diagnose_after",
    "agents.limit_*",
    "phases.*.max_turns",
    "phases.*.max_budget_usd",
    "phases.*.model",
    "phases.*.effort",
    "env.*",
    "*.*_timeout",
    "gates.mutation_runner",
    "dotnet.executable",
    "notify.toast",
    "update.check",
)
#: commands: fine while not emptied and no added argument leaves something out
COMMANDS = (
    "gates.commands.*",
    "gates.extra.*",
    "python.pytest_args",
    "generic.test_all",
    "generic.test_ids",
    "generic.test_paths",
    "generic.coverage",
    "generic.coverage_ids",
    "generic.mutation",
    "generic.format_fix",
)
LEAVES_OUT = re.compile(r"exclude|ignore|skip|deselect|disable|^-k", re.I)
#: thresholds: raising one is fine. key -> the value when the file has none
THRESHOLDS = {
    "gates.diff_coverage": Config.diff_coverage,
    "gates.mutation_score": Config.mutation_score,
}

Change = tuple[str, Any, Any]


def _flat(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{key}."))
        else:
            out[prefix + key] = value
    return out


def changes(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """(key, old value, new value) per key that differs; None = the file does not set it."""
    a, b = _flat(old), _flat(new)
    return [(k, a.get(k), b.get(k)) for k in sorted(a.keys() | b.keys()) if a.get(k) != b.get(k)]


def weakens(key: str, old: Any, new: Any) -> bool:
    if any(fnmatchcase(key, p) for p in SAFE):
        return False
    if any(fnmatchcase(key, p) for p in COMMANDS):
        before = old if isinstance(old, list) else []
        return not new or any(LEAVES_OUT.search(str(a)) for a in new if a not in before)
    if key in THRESHOLDS:
        default = THRESHOLDS[key]
        return bool((default if new is None else new) < (default if old is None else old))
    if key == "gates.unrunnable_tests":  # taking a test off the list is fine
        listed = {e.get("test") for e in old or []}
        return any(e.get("test") not in listed for e in new or [])
    return True


def shown(change: Change) -> str:
    key, old, new = change
    a, b = ("not set" if v is None else json.dumps(v) for v in (old, new))
    return f"{key}: {a} -> {b}"
