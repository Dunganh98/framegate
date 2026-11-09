"""Fail if a spec ID of the feature has no test, or a test references an ID that is in no spec."""

from __future__ import annotations

from agentflow.adapters.base import CheckResult, LanguageAdapter
from agentflow.specs import all_spec_ids, feature_ids


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    cfg = adapter.cfg
    fids = feature_ids(cfg, feature)
    all_ids = all_spec_ids(cfg)
    tested = adapter.collect_req_tags()
    missing = sorted(fids - tested.keys())
    unknown = sorted(set(tested) - all_ids)
    lines = [f"{len(fids)} spec IDs, {len(fids & tested.keys())} covered by tests."]
    lines += [f"MISSING test for {i}" for i in missing]
    lines += [f"UNKNOWN ID {i} used in {tested[i]}" for i in unknown]
    if not fids:
        lines.append(f"No spec IDs found in {cfg.paths.specs}/{feature}/")
    failed = bool(missing or unknown or not fids)
    return CheckResult("FAIL" if failed else "PASS", "\n".join(lines))
