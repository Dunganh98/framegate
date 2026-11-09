"""Before implementation, every test of the feature must FAIL against the stubs.

A test that passes against a stub asserts nothing useful. Errors (import/collection/fixture
problems) and skips are also reported.
"""

from __future__ import annotations

from agentflow.adapters.base import CheckResult, LanguageAdapter
from agentflow.specs import feature_ids


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
    passed = [c.name for c in run.cases if c.outcome == "passed"]
    errored = [c.name for c in run.cases if c.outcome == "error"]
    skipped = [c.name for c in run.cases if c.outcome == "skipped"]
    total = len(run.cases)
    lines = [f"{total} tests for feature {feature}."]
    lines += [f"PASSES AGAINST STUB (weak or wrong test): {n}" for n in passed]
    lines += [f"ERROR (import/fixture/collection problem): {n}" for n in errored]
    lines += [f"SKIPPED: {n}" for n in skipped]
    failed = bool(passed or errored or skipped or total == 0)
    return CheckResult("FAIL" if failed else "PASS", "\n".join(lines))
