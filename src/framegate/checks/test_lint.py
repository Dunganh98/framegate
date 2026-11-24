"""Lint on the lines the test writer wrote, before the tests gate: the implementer may not edit
tests, so a lint finding there parked its step (seen live: `UTC = UTC` in a new test file)."""

from __future__ import annotations

from framegate import red_tree
from framegate.adapters.base import GATE_NAMES, CheckResult, LanguageAdapter


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    from framegate import gates

    cfg = adapter.cfg
    changed = adapter.changed_since(f"spec-approved/{feature}")
    if changed is None:
        return CheckResult("PASS", "Not checked: the spec approval marker is missing.")
    folder = cfg.paths.tests.rstrip("/") + "/"
    tests = {f for f in changed if f.startswith(folder)}
    if not tests:
        return CheckResult("PASS", "No test file changed.")
    lint = next(
        (c for c in gates.lint_checks(adapter, feature) if c.name == GATE_NAMES["lint"]), None
    )
    if lint is None:
        return CheckResult("PASS", "Not checked: no lint command.")
    return red_tree.in_changed(adapter, tests, lint.run(), gates.task_lines(adapter, feature))
