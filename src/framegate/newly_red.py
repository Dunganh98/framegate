"""Existing tests that are red at the final gate.

The final regression run records them (out/<feature>/red_tests.json): `new` passed when the
spec was approved (the regression baseline) and fail now, `before` failed then already.
A new one is a final-gate item without a default. One from before is not the task's: a
line in the final summary and an entry of the deferred-work list.
"""

from __future__ import annotations

import json
from typing import Any

from framegate import defaults
from framegate.config import Config
from framegate.deferred import Item

FILE = "red_tests.json"
#: the label of the final repair's parked item (Orchestrator.repair_final_src)
REPAIR_LABEL = "final gate fixes"
HEAD = "existing test(s) that passed before the task fail now"
SHOWN = 10


def clear(cfg: Config, feature: str) -> None:
    (cfg.feature_out(feature) / FILE).unlink(missing_ok=True)


def record(cfg: Config, feature: str, new: list[str], before: list[str]) -> None:
    path = cfg.feature_out(feature) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"new": sorted(new), "before": sorted(before)}
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def read(cfg: Config, feature: str) -> tuple[list[str], list[str]]:
    """(newly red, red before the task) of the last final regression run."""
    try:
        data = json.loads((cfg.feature_out(feature) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    return [str(x) for x in data.get("new") or []], [str(x) for x in data.get("before") or []]


def _listed(names: list[str]) -> str:
    more = f" and {len(names) - SHOWN} more" if len(names) > SHOWN else ""
    return ", ".join(names[:SHOWN]) + more


def parked_ask(names: list[str]) -> str:
    """The end of the final repair's parked item while a new red test is left."""
    return (
        f" {len(names)} {HEAD}: {_listed(names)}. A: leave it parked: the task ends with "
        "the test(s) red (no change) / B: redo it with your answer (the words after the "
        f"letter; 'approved' applies what it proposes). {defaults.NO_DEFAULT}"
    )


def question(names: list[str]) -> str:
    """The final-gate item when no parked item stands for the red tests."""
    return (
        f"{len(names)} {HEAD}: {_listed(names)}. A: finish with the test(s) red (no change) "
        "/ B: one more repair run by the implementer, then the final checks again. "
        f"{defaults.NO_DEFAULT}"
    )


def is_question(item: str) -> bool:
    return HEAD in item and defaults.NO_DEFAULT in item


def section(before: list[str]) -> list[str]:
    """The generated section of final_summary.md and final_facts.md ([] = none)."""
    if not before:
        return []
    return [
        f"## Tests red before the task: {len(before)}",
        "",
        *(f"- {n}" for n in before),
        "",
        "They failed when the spec was approved and fail now: not held against this task. "
        "Suggested follow-up: one bug-fix task for them (on the deferred-work list).",
    ]


def deferred_items(before: list[str]) -> list[Item]:
    if not before:
        return []
    files = sorted({f for n in before if "/" in (f := n.split("::")[0])})
    text = f"tests red before the task, still red: {', '.join(before)}"
    return [Item("bugfix", text, files, "red before the task")]


def left(events: list[dict[str, Any]]) -> list[str]:
    """The newly red tests of the final checks the final gate was last approved with."""
    red: list[str] = []
    last: dict[str, Any] = {}
    for e in events:
        if e.get("kind") == "final_gates":
            last = e
        elif e.get("kind") == "human_gate" and e.get("gate") == "final" and e.get("approved"):
            red = [str(x) for x in last.get("newly_red") or []]
    return red
