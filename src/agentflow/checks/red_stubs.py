"""Before implementation, every test of the feature must FAIL against the stubs.

A test that passes against a stub asserts nothing useful. Errors (import/collection/fixture
problems) and skips are also reported. The exception are fixture precondition tests
(`test_fixture_*` / `Fixture_*`): they prove that pinned data has the situation a test needs,
with code that already exists, so they must PASS (a failing one means the data was picked wrong;
seen live: seeds pinned for a situation the data never produced, found only at the final gate).
"""

from __future__ import annotations

import re

from agentflow.adapters.base import CheckResult, LanguageAdapter
from agentflow.report import short_name
from agentflow.specs import feature_ids

FIXTURE_NAME = re.compile(r"^(?:test_)?fixture_", re.I)


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    cfg = adapter.cfg
    ids = feature_ids(cfg, feature)
    if not ids:
        return CheckResult("FAIL", f"No spec IDs found in {cfg.paths.specs}/{feature}/")
    report = cfg.feature_out(feature) / "red_stubs.xml"
    run = adapter.red_stub_run(sorted(ids), report)
    if run.cases is None:
        tail = "\n".join(run.output.splitlines()[-20:])
        return CheckResult("FAIL", f"Test run produced no report (collection crashed?)\n{tail}")
    fixtures = [c for c in run.cases if is_fixture_check(c.name)]
    cases = [c for c in run.cases if not is_fixture_check(c.name)]
    passed = [c.name for c in cases if c.outcome == "passed"]
    errored = [c.name for c in run.cases if c.outcome == "error"]
    skipped = [c.name for c in run.cases if c.outcome == "skipped"]
    other = [
        f"{c.name} — {problem}"
        for c in cases
        if c.outcome == "failed" and (problem := adapter.stub_failure_problem(c))
    ]
    missing = [
        f"{c.name} — {(c.message.strip().splitlines() or ['no message'])[0][:200]}"
        for c in fixtures
        if c.outcome == "failed"
    ]
    total = len(run.cases)
    lines = [
        f"{total} tests for feature {feature}"
        + (f" ({len(fixtures)} fixture precondition checks, which must pass)." if fixtures else ".")
    ]
    lines += [f"PASSES AGAINST STUB (weak or wrong test): {n}" for n in passed]
    lines += [f"ERROR (import/fixture/collection problem): {n}" for n in errored]
    lines += [f"SKIPPED: {n}" for n in skipped]
    lines += [f"FAILS FOR ANOTHER REASON THAN THE STUB: {n}" for n in other]
    lines += [
        f"FIXTURE PRECONDITION FAILS (the pinned data lacks the situation; pick other data): {n}"
        for n in missing
    ]
    failed = bool(passed or errored or skipped or other or missing or total == 0)
    return CheckResult("FAIL" if failed else "PASS", "\n".join(lines))


def is_fixture_check(name: str) -> bool:
    """A fixture precondition test (`test_fixture_*` / `Fixture_*`): it checks, with code that
    exists before the feature, that pinned data (a seed, a recording) has the situation the
    feature's tests need. It must PASS against the stubs."""
    return bool(FIXTURE_NAME.match(short_name(name)))
