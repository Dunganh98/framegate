"""How risky is it to accept a finished feature without a human look?

The engine answers from its own records, never from an agent's opinion: the run (gates, parks,
questions, answers, cap hits, assumptions), the size and shape of the change (git), paths the
project marked as never automatic, and the repository's track record (finished features in the
metrics file). A feature is `low` only when every test holds; each test that does not hold is a
reason, shown on the final brief and logged (`risk` event) next to the human's decision, so the
rating can be compared with what the human did before `[gates] final = "auto"` is switched on.

Limits: `[risk]` in framegate.toml (config.Risk). A project without a measured diff coverage or
mutation score is never low risk: there the frame proves less.
"""

from __future__ import annotations

import collections
import csv
from dataclasses import dataclass, field
from typing import Any

from framegate.adapters.base import LanguageAdapter
from framegate.checks.existing_members import base_ref
from framegate.checks.frame_diff import path_matches
from framegate.specs import feature_mode
from framegate.state import GitError, git

LOW = "low"
ELEVATED = "elevated"
#: final checks that must have run and passed (by the start of the check's name)
MEASURED = {"diff coverage": "diff coverage", "mutation score": "mutation score"}
#: event kinds of a run that needed the human or hit a limit, with their wording
RUN_EVENTS = {
    "answer": "the human answered questions in this run",
    "parked": "work was parked during the run",
    "provisional": "a step was kept as provisional (its tests were contested)",
    "escalation": "an agent stopped at its turn or budget cap twice",
}


@dataclass
class Assessment:
    level: str
    #: why the feature is not low risk (empty when it is)
    reasons: list[str] = field(default_factory=list)
    #: the numbers behind it (changed lines, files, removed lines, assumptions, features)
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def low(self) -> bool:
        return self.level == LOW

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "reasons": self.reasons, "facts": self.facts}

    def line(self, auto: bool) -> str:
        """The final brief's risk line."""
        if self.low:
            return "Risk: low (clean run, small change, coverage and mutation measured)" + (
                "" if auto else '; with [gates] final = "auto" this gate would not have stopped'
            )
        return "Risk: elevated: " + "; ".join(self.reasons)


def moved_lines(diff: str) -> tuple[int, int]:
    """A `git diff -U0` -> (moved lines, moved lines that left a file that still exists).
    A line is moved when it is removed in one place and added, the same but for its
    indent, in another: each removed line pairs with one added line at most."""
    added: collections.Counter[str] = collections.Counter()
    removed: collections.Counter[str] = collections.Counter()
    kept: collections.Counter[str] = collections.Counter()  # removed, the file stays
    stays = header = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            header = True
        elif line.startswith("@@"):
            header = False
        elif header:
            if line.startswith("+++ "):
                stays = not line.startswith("+++ /dev/null")
        elif line.startswith("+"):
            added[line[1:].strip()] += 1
        elif line.startswith("-"):
            removed[line[1:].strip()] += 1
            if stays:
                kept[line[1:].strip()] += 1
    return sum((added & removed).values()), sum((added & kept).values())


def change_stats(adapter: LanguageAdapter, feature: str) -> dict[str, Any] | None:
    """Source changes of the feature against its base: changed lines and files, removed lines
    in files that existed before, and every changed path of the repository. None = unknown.
    In a refactor, moved lines (moved_lines) are not counted as changed or removed: moving
    code is the task, and `moved_lines` in the result says how many they were."""
    cfg = adapter.cfg
    ref = base_ref(adapter, feature)
    if ref is None:
        return None
    try:
        numstat = git(cfg, "diff", "--numstat", ref, "--", *cfg.src_roots)
        modified = git(cfg, "diff", "--numstat", "--diff-filter=M", ref, "--", *cfg.src_roots)
        names = git(cfg, "diff", "--name-only", ref).split()
    except GitError:
        return None

    def rows(text: str) -> list[tuple[int, int]]:
        out = []
        for line in text.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:  # binary files count as "-"
                added = int(parts[0]) if parts[0].isdigit() else 0
                removed = int(parts[1]) if parts[1].isdigit() else 0
                out.append((added, removed))
        return out

    all_rows = rows(numstat)
    stats: dict[str, Any] = {
        "changed_lines": sum(a + r for a, r in all_rows),
        "changed_files": len(all_rows),
        "removed_lines": sum(r for _, r in rows(modified)),
        "paths": names,
    }
    if feature_mode(cfg, feature) == "refactor":
        try:
            diff = git(cfg, "diff", "-U0", "--no-renames", ref, "--", *cfg.src_roots)
        except GitError:
            return stats
        moved, from_kept = moved_lines(diff)
        stats["moved_lines"] = moved
        stats["changed_lines"] -= 2 * moved  # the removed and the added side
        stats["removed_lines"] = max(0, stats["removed_lines"] - from_kept)
    return stats


def finished_features(adapter: LanguageAdapter, feature: str) -> int:
    """Rows of the metrics file for other features (the repository's track record)."""
    cfg = adapter.cfg
    path = cfg.root / cfg.paths.metrics / "workflow_metrics.csv"
    if not path.is_file():
        return 0
    with open(path, encoding="utf-8", newline="") as fh:
        names = {row.get("feature") for row in csv.DictReader(fh)}
    return len({n for n in names if n and n != feature})


def assess(
    adapter: LanguageAdapter,
    feature: str,
    prog: dict[str, Any],
    events: list[dict[str, Any]],
    items: int,
    assumptions: int,
) -> Assessment:
    """Rate the feature at its final gate. `events`: the feature's events of the current spec
    version; `items`: the numbered items the gate would ask; `assumptions`: logged ones."""
    cfg, limits = adapter.cfg, adapter.cfg.risk
    reasons: list[str] = []
    facts: dict[str, Any] = {"assumptions": assumptions, "open_items": items}

    runs = [e for e in events if e.get("kind") == "final_gates"]
    last = runs[-1] if runs else {}
    results = {str(k): str(v) for k, v in (last.get("results") or {}).items()}
    if not last:
        reasons.append("the final gates did not run")
    elif not last.get("passed"):
        reasons.append("final gates failed: " + ", ".join(last.get("failed") or []))
    for prefix, name in MEASURED.items():
        found = [res for check, res in results.items() if check.startswith(prefix)]
        if last and not any(res.startswith("PASS") for res in found):
            reasons.append(f"{name} was not measured")

    if prog.get("parked"):
        reasons.append(f"{len(prog['parked'])} parked item(s)")
    if prog.get("provisional"):
        reasons.append("provisional steps wait for an answer")
    if items:
        reasons.append(f"{items} open item(s) to decide")
    kinds = {e.get("kind") for e in events}
    reasons += [text for kind, text in RUN_EVENTS.items() if kind in kinds]
    if any(e.get("kind") == "human_gate" and not e.get("approved") for e in events):
        reasons.append("the human rejected a gate in this run")
    if limits.max_assumptions and assumptions > limits.max_assumptions:
        reasons.append(
            f"{assumptions} assumptions (more than {limits.max_assumptions}): the spec had gaps"
        )

    stats = change_stats(adapter, feature)
    if stats is None:
        reasons.append("the size of the change could not be measured")
    else:
        paths = stats.pop("paths")
        facts.update(stats)
        moved = (
            f", {stats['moved_lines']} moved lines not counted" if "moved_lines" in stats else ""
        )
        for key, what in (
            ("changed_lines", "changed source lines"),
            ("changed_files", "changed source files"),
            ("removed_lines", "removed lines in existing code"),
        ):
            limit = getattr(limits, f"max_{key}")
            if limit and stats[key] > limit:
                reasons.append(f"{stats[key]} {what} (more than {limit}{moved})")
        marked = sorted(p for p in paths if path_matches(p, limits.never_auto))
        if marked:
            more = f" (+{len(marked) - 3} more)" if len(marked) > 3 else ""
            reasons.append("touches paths marked never automatic: " + ", ".join(marked[:3]) + more)

    sizes = (last.get("structure") or {}).get("files") or {}
    big = sorted(p for p, n in sizes.items() if cfg.max_file_lines and n > cfg.max_file_lines)
    if big:
        reasons.append(f"file(s) over {cfg.max_file_lines} lines: " + ", ".join(big[:3]))

    done = finished_features(adapter, feature)
    facts["finished_features"] = done
    if limits.min_features and done < limits.min_features:
        reasons.append(
            f"{done} finished feature(s) in this repository (low risk needs {limits.min_features})"
        )
    return Assessment(ELEVATED if reasons else LOW, reasons, facts)
