"""The engine's risk rating next to what the human did at the same final gate
(`framegate stats --risk`), from the events: read-only, no agent.

Each stop at a final gate has a `risk` event (the rating) and ends with one decision:
approved as it stood (`approve`, `finish`, or answers that only took defaults), answered
with changes (an `answer` that started rework), or approved by the engine
(`[gates] final = "auto"`: no person). "low" agreed with the human when the gate was
approved as it stood; a "low" gate answered with changes is the case that speaks against
switching the automatic approval on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from framegate.config import Config
from framegate.state import read_events

AS_IT_STOOD = "approved as it stood"
CHANGES = "answered with changes"
ENGINE = "approved by the engine (no person)"


@dataclass
class Decision:
    feature: str
    ts: str
    level: str
    reasons: list[str]
    decision: str


def decisions(events: list[dict[str, Any]]) -> list[Decision]:
    """One row per decided final gate, in the order of the events (all features)."""
    rating: dict[str, dict[str, Any]] = {}
    out: list[Decision] = []
    pending: dict[str, tuple[str, Decision]] = {}  # an answer, until its approval shows

    def decide(feature: str, e: dict[str, Any], what: str) -> Decision | None:
        r = rating.pop(feature, None)
        if r is None:
            return None  # a second event of the same stop
        d = Decision(feature, str(e.get("ts") or ""), str(r.get("level") or "?"), [], what)
        d.reasons = [str(x) for x in r.get("reasons") or []]
        out.append(d)
        return d

    for e in events:
        f, kind = str(e.get("feature") or ""), e.get("kind")
        if kind == "risk":
            rating[f] = e
            pending.pop(f, None)
        elif kind == "answer" and e.get("gate") == "final":
            d = decide(f, e, CHANGES)
            if d:
                pending[f] = (str(e.get("ref") or ""), d)
        elif kind == "human_gate" and e.get("gate") == "final" and e.get("approved"):
            ref, answered = pending.pop(f, ("", None))
            if answered is not None and e.get("from_answers") == ref:
                answered.decision = AS_IT_STOOD  # the answers only took defaults
            else:
                decide(f, e, ENGINE if e.get("auto") else AS_IT_STOOD)
    return out


def counts(rows: list[Decision]) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for d in rows:
        out[(d.level, d.decision)] = out.get((d.level, d.decision), 0) + 1
    return out


def summary(rows: list[Decision]) -> str:
    """One line: how often "low" agreed with the human ("" = no decided final gate)."""
    by_person = [d for d in rows if d.decision != ENGINE]
    if not by_person:
        return ""
    low = [d for d in by_person if d.level == "low"]
    agreed = sum(1 for d in low if d.decision == AS_IT_STOOD)
    high = [d for d in by_person if d.level != "low"]
    waved = sum(1 for d in high if d.decision == AS_IT_STOOD)
    return (
        f"Risk rating against your decision at {len(by_person)} final gate(s): low "
        f"{len(low)} ({agreed} approved as it stood, {len(low) - agreed} answered with "
        f"changes); elevated {len(high)} ({waved} approved as it stood, "
        f"{len(high) - waved} answered with changes)."
    )


def outcome_lines(cfg: Config, rows: list[Decision]) -> list[str]:
    """The outcome behind the ratings: escaped defects (defects.py) per rating of the task
    they were logged against ([] = none logged)."""
    from framegate import defects

    found = defects.per_task(cfg)
    if not found:
        return []
    last = {d.feature: d.level for d in rows}
    out = []
    for level in ("low", "elevated"):
        tasks = sorted(t for t in found if last.get(t) == level)
        n = sum(len(found[t]) for t in tasks)
        out.append(
            f"Escaped defects on tasks rated {level}: {n}"
            + (f" ({', '.join(tasks)})" if tasks else "")
        )
    return out


def lines(cfg: Config, feature: str | None = None) -> list[str]:
    rows = decisions(read_events(cfg))
    if feature:
        rows = [d for d in rows if d.feature == feature]
    if not rows:
        return [f"No decided final gate yet in {cfg.paths.out}/events.jsonl."]
    out = [summary(rows) or "No final gate was decided by a person yet."]
    engine = sum(1 for d in rows if d.decision == ENGINE)
    if engine:
        out.append(f"Approved by the engine, no person: {engine} gate(s).")
    wrong = [d for d in rows if d.level == "low" and d.decision == CHANGES]
    out.append(
        f'"low" disagreed with you {len(wrong)} time(s)'
        + (": " + ", ".join(d.feature for d in wrong) if wrong else "")
        + '. [gates] final = "auto" passes only gates rated low.'
    )
    out += outcome_lines(cfg, rows)
    out.append("")
    for d in rows:
        why = f" ({'; '.join(d.reasons[:2])}" + (", ..." if len(d.reasons) > 2 else "")
        why = why + ")" if d.reasons else ""
        out.append(f"  {d.ts[:16]}  {d.feature}: {d.level}{why} -> {d.decision}")
    return out
