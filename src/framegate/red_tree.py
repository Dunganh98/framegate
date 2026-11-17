"""What is already red on the untouched tree, for the lint / format / typecheck / imports
checks (the tests have their own record: gates.record_regression_baseline).

Seen live (three replay runs on three repositories): a format check that wanted to reformat a
file from before the feature, 353 lint findings in a repository that never configured the
linter. Every step gate blamed the feature, the steps parked, and the run reached the final
gate with nothing built.

`approve-spec` runs each of these checks once ([gates] regression_baseline: on for a bug
fix, and wherever framegate.toml sets it; specs.baseline_on)
and records, per failing check, how many output lines name each file. A gate then counts only
what is new: a failing check passes while no file is named more often than at the approval.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from framegate.adapters.base import Check, CheckResult, LanguageAdapter
from framegate.config import Config
from framegate.specs import baseline_on

#: out/<feature>/<this>: check name -> {"files": {path: lines naming it}}, failing checks only
CHECKS_FILE = "lint_baseline.json"
#: most files named in a one-line result
SHOWN_FILES = 8


def _path_regex(adapter: LanguageAdapter) -> re.Pattern[str]:
    sfx = "|".join(re.escape(s.lstrip(".")) for s in adapter.source_suffixes) or r"\w+"
    return re.compile(rf"(?:[A-Za-z]:)?[\w.\-/\\]+\.(?:{sfx})\b")


def line_files(adapter: LanguageAdapter, line: str) -> set[str]:
    """Repository files (posix, relative to the root) one output line names."""
    root = adapter.cfg.root.resolve()
    found = set()
    for m in _path_regex(adapter).finditer(line):
        path = pathlib.Path(m.group(0).replace("\\", "/"))
        try:
            rel = (path if path.is_absolute() else root / path).resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        if (root / rel).is_file():
            found.add(rel.as_posix())
    return found


def file_counts(adapter: LanguageAdapter, text: str) -> dict[str, int]:
    """File -> the number of output lines that name it. A tool prints the same number of
    such lines per finding from run to run, so a higher number means more findings."""
    counts: Counter[str] = Counter()
    for line in text.splitlines():
        counts.update(line_files(adapter, line))
    return dict(counts)


def path_of(cfg: Config, feature: str) -> pathlib.Path:
    return cfg.feature_out(feature) / CHECKS_FILE


def record(adapter: LanguageAdapter, feature: str) -> dict[str, dict[str, int]]:
    """Run the lint / format / typecheck / imports checks once on the tree as it is and
    record the failing ones. Returns check name -> {file: lines naming it}."""
    red: dict[str, dict[str, int]] = {}
    for check in adapter.lint_checks(None):
        try:
            res = check.run()
        except Exception:  # a crashing tool is no finding of the tree
            continue
        if res.status == "FAIL" and not res.tool_failure:
            red[check.name] = file_counts(adapter, res.output)
    path = path_of(adapter.cfg, feature)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {name: {"files": files} for name, files in red.items()}
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return red


def load(cfg: Config, feature: str) -> dict[str, dict[str, int]]:
    """Check name -> {file: lines naming it} as recorded at the spec approval ({} = none
    recorded, or [gates] regression_baseline is off)."""
    if not feature or not baseline_on(cfg, feature):
        return {}
    try:
        data = json.loads(path_of(cfg, feature).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, int]] = {}
    for name, rec in data.items():
        files = rec.get("files") if isinstance(rec, dict) else None
        if isinstance(files, dict):
            out[str(name)] = {str(f): int(n) for f, n in files.items()}
    return out


def _shown(names: Iterable[str]) -> str:
    listed = sorted(names)
    more = f", and {len(listed) - SHOWN_FILES} more" if len(listed) > SHOWN_FILES else ""
    return ", ".join(listed[:SHOWN_FILES]) + more


def judge(adapter: LanguageAdapter, then: dict[str, int], res: CheckResult) -> CheckResult:
    """A failing check against its record from the spec approval: PASS while no file is named
    more often than then. A FAIL keeps only what is new: the lines that name a left-out file
    are dropped, so the implementer (and the "can it fix this at all" question) sees the
    files that count."""
    if res.status != "FAIL" or res.tool_failure:
        return res
    now = file_counts(adapter, res.output)
    if not now:
        if then:
            return res  # it named files then and names none now: another failure
        return CheckResult(
            "PASS",
            res.output,
            "failed the same way when the spec was approved, names no file (left out)",
        )
    worse = {f: n for f, n in now.items() if n > then.get(f, 0)}
    old = {f: n for f, n in now.items() if f not in worse}
    left_out = (
        f"already there when the spec was approved (left out): {sum(old.values())} line(s) "
        f"in {len(old)} file(s)"
    )
    if not worse:
        return CheckResult("PASS", res.output, left_out)
    kept = [
        line
        for line in res.output.splitlines()
        if not (files := line_files(adapter, line)) or files & set(worse)
    ]
    parts = [f"{f} (now {n}, then {then.get(f, 0)})" for f, n in sorted(worse.items())]
    note = (
        "Only these files count: they have more findings than when the spec was approved "
        "(lines naming a file, now against then): "
        + "; ".join(parts[:SHOWN_FILES])
        + (f"; and {len(parts) - SHOWN_FILES} more" if len(parts) > SHOWN_FILES else "")
        + ". Findings in other files were there before the feature and are left out."
    )
    summary = f"new in {_shown(worse)}" + (f"; {left_out}" if old else "")
    return CheckResult("FAIL", "\n".join(kept) + "\n\n" + note, summary)


def against_baseline(adapter: LanguageAdapter, feature: str, check: Check) -> Check:
    """`check` judged against its record from the spec approval (unchanged when there is
    none: the tree was clean for this check, or nothing was recorded)."""
    then = load(adapter.cfg, feature).get(check.name)
    if then is None:
        return check
    return Check(check.name, lambda: judge(adapter, then, check.run()), check.needs_green)


def summary_lines(tests: int | None, checks: dict[str, dict[str, int]]) -> list[str]:
    """What `approve-spec` says about a tree that is not green (no line = it is)."""
    parts: list[str] = []
    if tests:
        parts.append(f"{tests} test(s)")
    for name, files in checks.items():
        parts.append(
            f"{name}: {sum(files.values())} line(s) in {len(files)} file(s)"
            if files
            else f"{name}: fails without naming a file"
        )
    if not parts:
        return []
    return [
        "Already red on the untouched tree: " + "; ".join(parts) + ".",
        "Recorded: the gates of this task count only what is new against it. A new finding "
        "in a file that already had some still counts; an old failure that changes its cause "
        "does not show.",
    ]


def event_fields(checks: dict[str, dict[str, int]]) -> dict[str, Any]:
    return {
        name: {"files": len(files), "lines": sum(files.values())} for name, files in checks.items()
    }
