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

from framegate.config import SCHEMA, Config, tomllib

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


def _known(key: str) -> bool:
    """Is `section.key` a key framegate.toml can hold (config.SCHEMA)?"""
    node: Any = SCHEMA
    for part in key.split("."):
        if node == {} or node == (dict,):  # free names below: [phases.X], [gates.commands]
            return True
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return not isinstance(node, dict)


SECTION_RE = re.compile(r"\[([a-z_]+(?:\.\w+)*)\]")
PROPOSAL_RE = re.compile(
    r"Proposed fix\W*:(.*?)(?=^\W*(?:Evidence|Contested tests)\W*:|\Z)", re.S | re.M
)


def proposed_weakening(diagnosis: str, current: dict[str, Any]) -> list[str]:
    """The changes a diagnosis proposes for framegate.toml that weaken a check, as `shown`
    gives them: read when the diagnosis is, so a fix the engine would refuse is never
    offered for approval. A proposal is every TOML line (`key = value`, in backticks or on
    a line of its own) under every `[section]` its `Proposed fix` names; what cannot be
    read that way is not judged here (apply_config_update still is the last word)."""
    m = PROPOSAL_RE.search(diagnosis)
    text = m.group(1) if m else diagnosis
    sections = SECTION_RE.findall(text)
    have = _flat(current)
    found: dict[str, Any] = {}
    for piece in [*re.findall(r"`([^`\n]+)`", text), *text.splitlines()]:
        try:
            pairs = _flat(tomllib.loads(piece.strip()))
        except tomllib.TOMLDecodeError:
            continue
        for key, value in pairs.items():
            for full in (key, *(f"{s}.{key}" for s in sections)):
                if "." in full and _known(full):
                    found[full] = value
    changed = [(k, have.get(k), v) for k, v in sorted(found.items()) if have.get(k) != v]
    return [shown(c) for c in changed if weakens(*c)]
