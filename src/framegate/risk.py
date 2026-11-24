"""How risky is it to accept a finished feature without a human look?

The engine answers from its own records, never from an agent's opinion: the run (gates, parks,
questions, answers, cap hits, assumptions), the size and shape of the change (git), paths the
project marked as never automatic, and the repository's track record (finished features in the
metrics file). A feature is `low` only when every test holds; each test that does not hold is a
reason, shown on the final brief and logged (`risk` event) next to the human's decision, so the
rating can be compared with what the human did before `[gates] final = "auto"` is switched on.

Limits: `[risk]` in framegate.toml (config.Risk). A project without a measured diff coverage or
mutation score is never low risk: there the frame proves less (but see mutation off below).

A configured check that measured nothing (NOT MEASURED: the tool cannot run here, or gave no
result) is a reason for `elevated`, named with its fix; `[gates] final = "auto"` never passes
it. Mutation testing switched off on purpose (`[gates] mutation_tool = "none"`, a decision of
the setup) is no reason.
"""

from __future__ import annotations

import collections
import csv
import datetime
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from framegate import task_base
from framegate.adapters.base import NOT_MEASURED, LanguageAdapter
from framegate.checks.existing_members import base_ref
from framegate.checks.frame_diff import path_matches
from framegate.config import Config, drop_machine_files
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


#: [risk] never_auto when the project sets none, by kind of path: schema and migration
#: files, then documentation (the deploy / CI paths come from the configuration)
SCHEMA_PATHS = ("**/*.sql", "**/*.prisma", "*migrations/*", "*alembic/*")
DOC_PATHS = ("docs/", "doc/", "**/README*", "**/CHANGELOG*")


def never_auto(cfg: Config) -> list[str]:
    """The paths whose change is never low risk: `[risk] never_auto`, else the general
    defaults (the built-in deploy / CI paths, `[protected] extra`, SCHEMA_PATHS, DOC_PATHS)."""
    if cfg.risk.never_auto is not None:
        return list(cfg.risk.never_auto)
    return [*cfg.deploy_ci_paths(), *cfg.protected_extra, *SCHEMA_PATHS, *DOC_PATHS]


def never_auto_hits(cfg: Config, paths: Sequence[str]) -> list[str]:
    """The changed paths that match never_auto. With the defaults framegate's own files
    (the code map under docs/, the specs) do not count: every task changes them."""
    from framegate import publish

    entries = never_auto(cfg)
    own = cfg.risk.never_auto is None
    return sorted(
        p for p in paths if path_matches(p, entries) and not (own and publish.is_workflow(cfg, p))
    )


def never_auto_words(cfg: Config) -> str:
    """The list in one line, for the setup table."""
    if cfg.risk.never_auto is not None:
        return ", ".join(cfg.risk.never_auto) or "none"
    extra = f", {', '.join(cfg.protected_extra)}" if cfg.protected_extra else ""
    return (
        f"deploy / CI files (built in{extra}), schema / migration files "
        f"({', '.join(SCHEMA_PATHS)}), docs ({', '.join(DOC_PATHS)})"
    )


def mark_unattended(
    feature: str, files: Sequence[pathlib.Path], facts: pathlib.Path
) -> list[pathlib.Path]:
    """An automatically approved final gate: each of `files` (the final brief, the final
    summary) gets a first line that says no person approved; a missing one is written with
    a pointer to `facts`. Returns the files."""
    today = datetime.date.today().isoformat()
    note = (
        f"> APPROVED AUTOMATICALLY on {today}: no person approved this final gate. framegate "
        'rated the task low risk ([gates] final = "auto").'
    )
    for path in files:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if not text.strip():
            text = f"# {feature}\n\nNo text was written; the facts: {facts.name}.\n"
        if not text.startswith("> APPROVED AUTOMATICALLY"):
            path.write_text(f"{note}\n\n{text}", encoding="utf-8")
    return list(files)


@dataclass
class Assessment:
    level: str
    #: why the feature is not low risk (empty when it is)
    reasons: list[str] = field(default_factory=list)
    #: the numbers behind it (changed lines, files, removed lines, assumptions, features)
    facts: dict[str, Any] = field(default_factory=dict)
    #: check -> reason and fix, for configured checks that measured nothing (also a reason)
    not_measured: dict[str, str] = field(default_factory=dict)
    #: why no gate of this project passes without a person yet (track_blockers): the
    #: project's track record, which never changes the rating itself
    blocked: list[str] = field(default_factory=list)

    @property
    def low(self) -> bool:
        return self.level == LOW

    @property
    def auto(self) -> bool:
        """May `[gates] final = "auto"` pass the final gate without a stop? Only a low-risk
        feature with every check measured, in a project whose track record allows it."""
        return self.low and not self.not_measured and not self.blocked

    def as_dict(self) -> dict[str, Any]:
        out = {"level": self.level, "reasons": self.reasons, "facts": self.facts}
        if self.blocked:
            out["blocked"] = self.blocked
        return {**out, "not_measured": self.not_measured} if self.not_measured else out

    def line(self, auto: bool) -> str:
        """The final brief's risk line."""
        if not self.low:  # an unmeasured check is one of the reasons
            return "Risk: elevated: " + "; ".join(self.reasons)
        proven = (
            "coverage measured, mutation off by config"
            if self.facts.get("mutation_off")
            else "coverage and mutation measured"
        )
        text = f"Risk: low (clean run, small change, {proven})"
        if self.blocked:
            return text + "; never automatic yet: " + "; ".join(self.blocked)
        return text + (
            "" if auto else '; with [gates] final = "auto" this gate would not have stopped'
        )


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
        names = drop_machine_files(cfg, git(cfg, "diff", "--name-only", ref).split())
        # the lines: against the start formatted as the gates format (a rewrap is no change)
        ref = task_base.formatted(adapter, ref, f"start/{feature}") if feature else ref
        numstat = git(cfg, "diff", "--numstat", ref, "--", *cfg.src_roots)
        modified = git(cfg, "diff", "--numstat", "--diff-filter=M", ref, "--", *cfg.src_roots)
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
    not_measured: dict[str, str] = {}
    facts: dict[str, Any] = {"assumptions": assumptions, "open_items": items}

    runs = [e for e in events if e.get("kind") == "final_gates"]
    last = runs[-1] if runs else {}
    results = {str(k): str(v) for k, v in (last.get("results") or {}).items()}
    if not last:
        reasons.append("the final gates did not run")
    elif not last.get("passed"):
        reasons.append("final gates failed: " + ", ".join(last.get("failed") or []))
    mutation_off = (cfg.mutation_tool or "").lower() == "none"
    if mutation_off:
        facts["mutation_off"] = True
    for prefix, name in MEASURED.items():
        if mutation_off and name == "mutation score":
            continue  # the project's decision (setup), not a gap of this feature
        found = [res for check, res in results.items() if check.startswith(prefix)]
        if not last or any(res.startswith("PASS") for res in found):
            continue
        blocked = [res for res in found if res.startswith(NOT_MEASURED)]
        if blocked:  # configured, but the tool measured nothing: no proof
            not_measured[name] = blocked[0].removeprefix(NOT_MEASURED).lstrip(": ")
            reasons.append(f"{name} {NOT_MEASURED}: {not_measured[name]}")
        else:  # failed, or left out by the config or by a red check before it
            reasons.append(f"{name} was not measured")

    if prog.get("parked"):
        reasons.append(f"{len(prog['parked'])} parked item(s)")
    if prog.get("provisional"):
        reasons.append("provisional steps wait for an answer")
    if items:
        reasons.append(f"{items} open item(s) to decide")
    kinds = {e.get("kind") for e in events}
    reasons += [text for kind, text in RUN_EVENTS.items() if kind in kinds]
    reasons += strict_reasons(cfg, feature, events, results)
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
        marked = never_auto_hits(cfg, paths)
        if marked:
            more = f" (+{len(marked) - 3} more)" if len(marked) > 3 else ""
            reasons.append("touches paths marked never automatic: " + ", ".join(marked[:3]) + more)

    shape = last.get("structure") or {}
    sizes = shape.get("files") or {}
    big = sorted(p for p, n in sizes.items() if cfg.max_file_lines and n > cfg.max_file_lines)
    if "grown" in shape:  # only a file this feature made longer counts against it
        big = [p for p in big if p in shape["grown"]]
    if big:
        reasons.append(f"file(s) over {cfg.max_file_lines} lines: " + ", ".join(big[:3]))

    done = finished_features(adapter, feature)
    facts["finished_features"] = done
    if limits.min_features and done < limits.min_features:
        reasons.append(
            f"{done} finished feature(s) in this repository (low risk needs {limits.min_features})"
        )
    blocked = [] if reasons else track_blockers(cfg, feature)
    return Assessment(ELEVATED if reasons else LOW, reasons, facts, not_measured, blocked)


def strict_reasons(
    cfg: Config, feature: str, events: list[dict[str, Any]], results: dict[str, str]
) -> list[str]:
    """The reasons a project can allow one by one (`[risk] allow_*`): a skipped final
    check, a final-gate repair, a review finding judged valid, a proposed doc update."""
    from framegate import doc_proposals

    limits, out = cfg.risk, []
    skipped = sorted(n for n, res in results.items() if res.startswith("SKIPPED"))
    if skipped and not limits.allow_skipped_checks:
        out.append("final check(s) skipped: " + ", ".join(skipped[:3]))
    if not limits.allow_repair and any(e.get("kind") == "final_repair" for e in events):
        out.append("the final gates needed a repair run")
    valid = [
        f
        for e in events
        if e.get("kind") == "triage"
        for f in e.get("findings") or []
        if str(f.get("verdict") or "").upper() == "VALID"
    ]
    if valid and not limits.allow_valid_findings:
        out.append(f"{len(valid)} review finding(s) judged valid")
    if not limits.allow_doc_proposals:
        try:
            docs = len(doc_proposals.proposals(cfg, feature))
        except OSError:
            docs = 0
        if docs:
            out.append(f"{docs} proposed doc update(s)")
    return out


def track_blockers(cfg: Config, feature: str) -> list[str]:
    """Why `[gates] final = "auto"` passes no gate of this project yet, from the compared
    past decisions (risk_review) and the escaped defects (defects.py) of other tasks: too
    few gates rated low that a person approved as they stood, a gate rated low that a
    person answered with changes, an escaped defect on a task rated low."""
    from framegate import defects, risk_review
    from framegate.state import read_events

    limits = cfg.risk
    rows = [d for d in risk_review.decisions(read_events(cfg)) if d.feature != feature]
    low = [d for d in rows if d.level == LOW and d.decision != risk_review.ENGINE]
    agreed = sum(1 for d in low if d.decision == risk_review.AS_IT_STOOD)
    out = []
    if limits.min_agreed and agreed < limits.min_agreed:
        out.append(
            f"{agreed} past final gate(s) rated low that you approved as they stood "
            f"(needs {limits.min_agreed})"
        )
    overruled = len(low) - agreed
    if limits.max_low_overruled >= 0 and overruled > limits.max_low_overruled:
        out.append(f"{overruled} past gate(s) rated low that you answered with changes")
    rated_low = {d.feature for d in rows if d.level == LOW}
    escaped = sum(len(v) for task, v in defects.per_task(cfg).items() if task in rated_low)
    if limits.max_low_defects >= 0 and escaped > limits.max_low_defects:
        out.append(f"{escaped} escaped defect(s) on task(s) rated low")
    return out
