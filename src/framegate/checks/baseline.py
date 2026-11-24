"""The first proof of the tests, before any implementation. What it is depends on the mode:

- feature: every test FAILS against the stubs (checks/red_stubs.py).
- bugfix: the reproduction tests (R and E IDs) FAIL on today's code, each listed with why it
  fails so the test reviewer can tell "fails for the reported reason" from "fails for another
  one"; the must-not-change tests (I IDs) PASS on today's code.
- refactor: every characterization test PASSES on today's code.

Fixture precondition tests (`test_fixture_*` / `Fixture_*`) must pass in every mode.
"""

from __future__ import annotations

import re

from framegate.adapters.base import CheckResult, LanguageAdapter, TestCase
from framegate.checks import red_stubs
from framegate.specs import feature_ids, feature_mode
from framegate.state import GitError, git, tag_exists

#: the check's name in test_checks.md and progress.json, per mode
NAMES = {
    "feature": "Red against stubs",
    "bugfix": "Bug reproduced on today's code",
    "refactor": "Green on today's code",
}
_KIND = re.compile(r"-([A-Z])-\d+$")


def name(mode: str) -> str:
    return NAMES.get(mode, NAMES["feature"])


def _first_line(case: TestCase) -> str:
    return (case.message.strip().splitlines() or ["no message"])[0][:200]


def _run(
    adapter: LanguageAdapter, feature: str, ids: list[str], report: str
) -> tuple[list[TestCase] | None, str, list[str]]:
    """(the cases the verdict covers, the output's tail, notes: red_stubs.own_cases)."""
    run = adapter.red_stub_run(ids, adapter.cfg.feature_out(feature) / report)
    tail = "\n".join(run.output.splitlines()[-20:])
    if run.cases is None:
        return None, tail, []
    kept, notes = red_stubs.own_cases(adapter, feature, run.cases)
    return kept, tail, notes


def _must_pass(cases: list[TestCase], why: str) -> list[str]:
    lines = [
        f"FAILS TODAY ({why}): {c.name} — {_first_line(c)}" for c in cases if c.outcome == "failed"
    ]
    lines += [
        f"ERROR (import/fixture/collection problem): {c.name}"
        for c in cases
        if c.outcome == "error"
    ]
    lines += [f"SKIPPED: {c.name}" for c in cases if c.outcome == "skipped"]
    return lines


def bugfix(adapter: LanguageAdapter, feature: str, ids: list[str]) -> CheckResult:
    red_ids = [i for i in ids if (m := _KIND.search(i)) and m.group(1) != "I"]
    keep_ids = [i for i in ids if i not in red_ids]
    if not red_ids:
        return CheckResult(
            "FAIL", "The bug report has no reproduction ID (an R row): nothing shows the bug."
        )
    cases, tail, notes = _run(adapter, feature, red_ids, "red_stubs.xml")
    if cases is None:
        return CheckResult("FAIL", f"Test run produced no report (collection crashed?)\n{tail}")
    fixtures = [c for c in cases if red_stubs.is_fixture_check(c.name)]
    repro = [c for c in cases if not red_stubs.is_fixture_check(c.name)]
    lines = [f"{len(repro)} reproduction test(s) for {', '.join(red_ids)}.", *notes]
    problems = [
        f"PASSES TODAY (does not show the bug): {c.name}" for c in repro if c.outcome == "passed"
    ]
    problems += [
        f"ERROR (the test does not reach its assertion): {c.name} — {_first_line(c)}"
        for c in repro
        if c.outcome == "error"
    ]
    problems += [f"SKIPPED: {c.name}" for c in repro if c.outcome == "skipped"]
    problems += _must_pass(fixtures, "a fixture precondition must pass; pick other data")
    shown = [f"fails today: {c.name} — {_first_line(c)}" for c in repro if c.outcome == "failed"]
    if not repro:
        problems.append("No reproduction test ran.")
    kept: list[TestCase] = []
    if keep_ids:
        kept_cases, keep_tail, _ = _run(adapter, feature, keep_ids, "keep_tests.xml")
        if kept_cases is None:
            problems.append(f"The must-not-change tests produced no report\n{keep_tail}")
        else:
            kept = kept_cases
            problems += _must_pass(kept, "a must-not-change test has to pass before the fix")
        lines.append(f"{len(kept)} must-not-change test(s) for {', '.join(keep_ids)}.")
    if shown:
        lines += [
            "Why each reproduction test fails (the test reviewer checks: is it the "
            "reported reason?):",
            *shown,
        ]
    return CheckResult("FAIL" if problems else "PASS", "\n".join([*lines, *problems]))


def existing_tests(adapter: LanguageAdapter, feature: str, ids: list[str]) -> list[str]:
    """Lines for the refactor check: how many of the tests tagged with the refactor's IDs
    existed before it (the test writer tagged them: they already pin the behavior) and how
    many are new; the IDs only existing tests pin; existing test files with removed lines
    (a tag is an added line: anything else the test reviewer looks at). Notes, not blocking."""
    cfg = adapter.cfg
    tag = f"spec-approved/{feature}"
    try:
        if not tag_exists(cfg, tag):
            return []
        old = set(git(cfg, "ls-tree", "-r", "--name-only", tag).splitlines())
        tags = adapter.collect_req_tags()
    except Exception:  # a scan problem must not fail the check
        return []
    by_id = {i: {w.replace("\\", "/") for w in tags.get(i, [])} for i in ids}
    tests = set().union(*by_id.values()) if by_id else set()
    kept = {w for w in tests if w.split("::")[0] in old}
    files = sorted({w.split("::")[0] for w in kept})
    lines = [
        f"{len(kept)} existing test(s) tagged with a spec ID of this refactor (they already "
        f"pin the behavior), {len(tests) - len(kept)} new test(s)."
    ]
    if files:
        lines.append("Existing test files with tagged tests: " + ", ".join(files))
    only = sorted(i for i, ws in by_id.items() if ws and ws <= kept)
    if only:
        lines.append("Pinned by existing tests only: " + ", ".join(only))
    if not kept:
        lines.append(
            "NOTE: no existing test was tagged. If existing tests cover the code in scope, "
            "tag them instead of writing the same test again (test_summary.md says which "
            "were found)."
        )
    for f in files:
        try:
            stat = git(cfg, "diff", "--numstat", tag, "--", f).split()
        except GitError:
            continue
        if len(stat) >= 2 and stat[1].isdigit() and int(stat[1]) > 0:
            lines.append(
                f"NOTE: {f}: {stat[1]} line(s) of this existing test file were removed or "
                "changed. Tagging adds lines only: the test reviewer checks that no "
                "existing test was changed."
            )
    return lines


def refactor(adapter: LanguageAdapter, feature: str, ids: list[str]) -> CheckResult:
    cases, tail, left_out = _run(adapter, feature, ids, "red_stubs.xml")
    if cases is None:
        return CheckResult("FAIL", f"Test run produced no report (collection crashed?)\n{tail}")
    problems = _must_pass(cases, "a characterization test must describe what the code does now")
    if not cases:
        problems.append("No characterization test ran.")
    head = (
        f"{len(cases)} characterization test(s) for feature {feature}; all must pass on "
        "today's code."
    )
    notes = [*left_out, *existing_tests(adapter, feature, ids)]
    return CheckResult("FAIL" if problems else "PASS", "\n".join([head, *notes, *problems]))


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    cfg = adapter.cfg
    mode = feature_mode(cfg, feature)
    if mode == "feature":
        return red_stubs.check(adapter, feature)
    ids = sorted(feature_ids(cfg, feature))
    if not ids:
        return CheckResult("FAIL", f"No spec IDs found in {cfg.paths.specs}/{feature}/")
    return bugfix(adapter, feature, ids) if mode == "bugfix" else refactor(adapter, feature, ids)


#: a test that passes before the code: green by design, or a weak test (only the owner can say)
_PASSES = re.compile(r"^(?:PASSES AGAINST STUB|PASSES TODAY)\b[^:]*:\s+(\S+)")
#: any other problem line of the check (ERROR, SKIPPED, FAILS ..., LISTED ..., no report)
_OTHER = re.compile(r"^(?!NOTE\b)[A-Z]{4,}\b|produced no report|^No \w+ test ran")


def green_only(output: str) -> list[str]:
    """The function names of the tests this check failed for passing before the code, when
    nothing else failed it; [] otherwise."""
    names: list[str] = []
    for line in output.splitlines():
        if m := _PASSES.match(line):
            names.append(red_stubs._parts(m.group(1))[-1])
        elif _OTHER.search(line):
            return []
    return names
