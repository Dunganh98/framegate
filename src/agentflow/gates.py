"""Gate runner: run the frame checks for a feature.

scope step   fast checks after each implementation step (tests of current + earlier steps)
scope final  everything, including coverage on the diff, traceability and mutation testing
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from agentflow.adapters.base import Check, CheckResult, LanguageAdapter
from agentflow.checks import frame_diff, mutation, test_smells, traceability
from agentflow.state import read_reqs


def checks(adapter: LanguageAdapter, feature: str, scope: str, skip_mutation: bool) -> list[Check]:
    tag = frame_diff.approved_tag(feature)
    c = list(adapter.lint_checks(tag))
    c.append(Check("frame diff", lambda: frame_diff.check(adapter, feature)))
    if scope == "step":
        reqs = read_reqs(adapter.cfg)

        def step_tests() -> CheckResult:
            run = adapter.run_tests(req_ids=reqs)
            return CheckResult("PASS" if run.status == "passed" else "FAIL", run.output)

        c.append(Check("tests (current + earlier steps)", step_tests))
    else:
        c += adapter.coverage_checks(tag)
        c.append(Check("traceability", lambda: traceability.check(adapter, feature)))
        c.append(Check("test smells", lambda: test_smells.check(adapter, feature)))
        if not skip_mutation:
            c.append(Check("mutation score", lambda: mutation.check(adapter, feature)))
    return c


@dataclass
class GateReport:
    """Result of a gate run: overall verdict, the report text, and per-check details."""

    ok: bool
    report: str
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: failing check name -> its output (last 30 lines)
    outputs: dict[str, str] = field(default_factory=dict)

    def excerpt(self, lines_per_check: int = 12, limit: int = 2000) -> str:
        """A trimmed excerpt of the failing checks' output (for events and the retro)."""
        parts = []
        for name in self.failed:
            tail = self.outputs.get(name, "").splitlines()[-lines_per_check:]
            parts.append(f"[FAIL] {name}" + ("\n" + "\n".join(tail) if tail else ""))
        text = "\n".join(parts)
        return text if len(text) <= limit else "..." + text[-limit:]


def run_gates(
    adapter: LanguageAdapter,
    feature: str,
    scope: str = "final",
    skip_mutation: bool = False,
    echo: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Run all checks of the scope. Returns (all passed, report text)."""
    rep = gate_report(adapter, feature, scope, skip_mutation, echo)
    return rep.ok, rep.report


def gate_report(
    adapter: LanguageAdapter,
    feature: str,
    scope: str = "final",
    skip_mutation: bool = False,
    echo: Callable[[str], None] | None = None,
) -> GateReport:
    """Run all checks of the scope and return the detailed result."""
    adapter.cfg.out_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def emit(text: str) -> None:
        lines.append(text)
        if echo:
            echo(text)

    failed: list[str] = []
    skipped: list[str] = []
    outputs: dict[str, str] = {}
    for check in checks(adapter, feature, scope, skip_mutation):
        try:
            res = check.run()
        except Exception as e:  # a crashing check must fail the gate, not the runner
            res = CheckResult("FAIL", f"check crashed: {type(e).__name__}: {e}")
        emit(f"[{res.status}] {check.name}")
        out = res.output.strip()
        if res.status == "FAIL":
            failed.append(check.name)
            outputs[check.name] = "\n".join(out.splitlines()[-30:])
            if out:
                emit(outputs[check.name])
        elif res.status == "SKIPPED":
            skipped.append(check.name)
            emit(f"  {out.splitlines()[-1] if out else 'SKIPPED'}")
        elif res.summary:
            emit(f"  {res.summary}")
        elif "SKIPPED" in out:
            emit("  " + next(line for line in out.splitlines() if "SKIPPED" in line).strip())
    summary = "ALL GATES PASSED" if not failed else "FAILED: " + ", ".join(failed)
    if skipped:
        summary += f" (SKIPPED: {', '.join(skipped)})"
    emit(f"\n{summary}")
    return GateReport(not failed, "\n".join(lines), failed, skipped, outputs)
