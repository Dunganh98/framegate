"""Gate runner: run the frame checks for a feature.

scope step   fast checks after each implementation step (tests of current + earlier steps)
scope final  everything, including coverage on the diff, traceability and mutation testing,
             then the project's own [gates.extra] checks (reported, never blocking)

Both scopes first format the changed source files ([gates] format_fix).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agentflow.adapters.base import Check, CheckResult, LanguageAdapter, TestScope, run_command
from agentflow.checks import frame_diff, mutation, test_smells, traceability
from agentflow.specs import feature_ids
from agentflow.state import read_reqs


def feature_scope(adapter: LanguageAdapter, feature: str) -> TestScope:
    """The feature's tests: its spec IDs and the review tests (if the folder exists)."""
    cfg = adapter.cfg
    paths = [cfg.paths.review_tests] if (cfg.root / cfg.paths.review_tests).exists() else []
    return TestScope(sorted(feature_ids(cfg, feature)), paths)


def final_scopes(
    adapter: LanguageAdapter, feature: str
) -> tuple[TestScope | None, TestScope | None]:
    """(coverage scope, mutation scope); None = the whole suite."""
    scope = feature_scope(adapter, feature)
    if scope.whole_suite:
        return None, None
    cfg = adapter.cfg
    return (
        scope if cfg.coverage_scope == "feature" else None,
        scope if cfg.mutation_scope == "feature" else None,
    )


def scope_lines(adapter: LanguageAdapter, feature: str, skip_mutation: bool) -> list[str]:
    """The header of a final gate report: which tests each check used."""
    cov, mut = final_scopes(adapter, feature)
    whole = TestScope().describe()
    lines = ["Test scope of the final checks:"]
    if cov is not None:
        lines.append(f"- regression: {whole}, once, without coverage")
        lines.append(f"- coverage + diff coverage: {cov.describe()}")
    else:
        lines.append(f"- regression + coverage + diff coverage: {whole}, with coverage")
    if not skip_mutation:
        lines.append(f"- mutation: {(mut or TestScope()).describe()}")
    if cov is not None or mut is not None:
        ids = ", ".join((cov or mut or TestScope()).req_ids)
        if ids:
            lines.append(f"  feature spec IDs: {ids}")
    return lines


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
        cov, mut = final_scopes(adapter, feature)
        if cov is not None:
            c.append(adapter.regression_check())
        c += adapter.coverage_checks(tag, cov)
        c.append(Check("traceability", lambda: traceability.check(adapter, feature)))
        c.append(Check("test smells", lambda: test_smells.check(adapter, feature)))
        if not skip_mutation:
            name = "mutation score" + (" (feature tests)" if mut is not None else "")
            c.append(Check(name, lambda: mutation.check(adapter, feature, mut)))
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
    #: check name -> wall-clock seconds
    durations: dict[str, float] = field(default_factory=dict)
    #: [gates.extra] check name -> exit code (reported at the final gate, never blocking)
    extra: dict[str, int] = field(default_factory=dict)

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
    durations: dict[str, float] = {}
    if scope == "final":
        for line in scope_lines(adapter, feature, skip_mutation):
            emit(line)
        emit("")
    try:
        note = adapter.format_fix(frame_diff.approved_tag(feature))
    except Exception as e:  # the checks below still report the formatting
        note = f"skipped ({type(e).__name__}: {e})"
    if note:
        emit(f"Format fix: {note}")
    for check in checks(adapter, feature, scope, skip_mutation):
        t0 = time.monotonic()
        try:
            res = check.run()
        except Exception as e:  # a crashing check must fail the gate, not the runner
            res = CheckResult("FAIL", f"check crashed: {type(e).__name__}: {e}")
        durations[check.name] = round(time.monotonic() - t0, 1)
        emit(f"[{res.status}] {check.name}")
        out = res.output.strip()
        if res.status == "FAIL":
            failed.append(check.name)
            if res.summary:
                emit(f"  {res.summary}")
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
    extra = run_extra_checks(adapter, feature, emit, durations) if scope == "final" else {}
    return GateReport(not failed, "\n".join(lines), failed, skipped, outputs, durations, extra)


def run_extra_checks(
    adapter: LanguageAdapter,
    feature: str,
    emit: Callable[[str], None],
    durations: dict[str, float],
) -> dict[str, int]:
    """[gates.extra]: the project's own checks (e.g. a balance measurement), after the gates.
    Reported with their exit code and output tail, full output in {out}/{f}/extra_<name>.log;
    never part of the verdict."""
    cfg = adapter.cfg
    if not cfg.extra_checks:
        return {}
    emit("\nProject checks ([gates.extra], not blocking):")
    codes: dict[str, int] = {}
    for name, cmd in cfg.extra_checks.items():
        t0 = time.monotonic()
        code, out = run_command(
            adapter.resolve_command(cmd),
            cfg.root,
            env=adapter.tool_env(),
            timeout=cfg.extra_timeout,
        )
        durations[f"extra: {name}"] = round(time.monotonic() - t0, 1)
        codes[name] = code
        log = cfg.feature_out(feature) / f"extra_{name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(out, encoding="utf-8")
        status = "OK" if code == 0 else f"EXIT {code}"
        emit(f"[{status}] {name} (full output: {cfg.paths.out}/{feature}/{log.name})")
        tail = out.strip().splitlines()[-30:]
        if tail:
            emit("\n".join(tail))
    return codes
