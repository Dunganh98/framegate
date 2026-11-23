"""Frameworks the project declares that this environment does not have.

Seen live: the spec asked for PySpark tests, the project names pyspark in an optional
extra, the task's environment did not have it, and the tests phase found out. So the
setup (`framegate probe`) and the spec start (`framegate spec`) compare what the project
declares (dependencies, optional extras, modules its tests guard with importorskip) with
what the environment has, and the spec dialogue gets one fact line per gap
(out/<feature>/env_facts.md) to ask the user about before a requirement needs it.
"""

from __future__ import annotations

from framegate.adapters.base import LanguageAdapter
from framegate.config import Config

FILE = "env_facts.md"
HEAD = "Declared but not installed"
ASK = (
    "tests needing it cannot run here: ask the user to drop those tests/requirements, "
    "allow installing it, or another way"
)


def lines(adapter: LanguageAdapter) -> list[str]:
    """One fact line per declared framework the environment lacks ([] = none, or the
    adapter cannot tell)."""
    try:
        gaps = adapter.missing_frameworks() or {}
    except Exception:  # a fact for the dialogue, never a reason to stop
        return []
    return [f"declared but not installed: {n} ({where}) — {ASK}" for n, where in gaps.items()]


def write(cfg: Config, adapter: LanguageAdapter, feature: str) -> list[str]:
    """The fact block of a feature's spec dialogue: written to out/<feature>/env_facts.md
    (removed when there is no gap). Returns its lines."""
    found = lines(adapter)
    path = cfg.feature_out(feature) / FILE
    if not found:
        path.unlink(missing_ok=True)
        return []
    block = [f"## {HEAD}: {len(found)}", "", *(f"- {x}" for x in found)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(block) + "\n", encoding="utf-8")
    return block
