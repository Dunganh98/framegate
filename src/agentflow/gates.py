"""Gate runner: run the frame checks for a feature.

scope step   fast checks after each implementation step (tests of current + earlier steps)
scope final  everything, including coverage on the diff, traceability and mutation testing,
             then the structure report (sizes of the changed files, longest methods) and the
             project's own [gates.extra] checks (both reported, never blocking)

Both scopes first format the changed source files ([gates] format_fix).

Work that waits for the human's answer at the final gate has tests that fail by design: a parked
step's tests (by spec ID) and a provisional step's contested tests (by name). The step gates
leave the contested tests out; the final gate counts both as expected red, fails only on other
failures, and skips coverage, diff coverage and mutation until the redo.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agentflow.adapters.base import (
    Check,
    CheckResult,
    LanguageAdapter,
    TestCase,
    TestRun,
    TestScope,
    incomplete_result,
    run_command,
)
from agentflow.checks import frame_diff, mutation, structure, test_smells, traceability
from agentflow.config import Config
from agentflow.failure_filter import filter_failure_output
from agentflow.report import short_name
from agentflow.specs import feature_ids, plan_steps
from agentflow.state import load_progress, read_contested, read_reqs

#: most lines of a failing check's output in the gate report (and so in the fix prompt)
OUTPUT_LINES = 40


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


@dataclass
class ExpectedRed:
    """Tests a gate expects to fail while work waits for the human's answer at the final gate:
    the tests of parked plan steps (by spec ID) and the contested tests of provisional steps (by
    name; their code is kept, the proposed test fixes are approved at the final gate)."""

    parked_ids: list[str] = field(default_factory=list)
    parked: list[str] = field(default_factory=list)
    contested: list[str] = field(default_factory=list)
    provisional: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.parked_ids or self.contested)

    def reason(self) -> str:
        parts = []
        if self.parked:
            parts.append("parked steps: " + ", ".join(self.parked))
        if self.provisional:
            parts.append("provisional steps, tests contested: " + ", ".join(self.provisional))
        return "; ".join(parts)


def expected_red(cfg: Config, feature: str) -> ExpectedRed:
    """From the feature's progress.json: what the final gate expects red (empty: nothing)."""
    if not feature or not (cfg.feature_out(feature) / "progress.json").is_file():
        return ExpectedRed()
    prog = load_progress(cfg, feature)
    parked = [r for r in prog.get("parked") or [] if r.get("kind") == "step"]
    nums = {n for r in parked for n in (r.get("steps") or [r.get("step")])}
    ids = [i for s in plan_steps(cfg, feature) if s.num in nums for i in s.reqs]
    prov = prog.get("provisional") or []
    return ExpectedRed(
        ids,
        [str(r.get("label")) for r in parked],
        [str(t) for r in prov for t in r.get("contested") or []],
        [str(r.get("label")) for r in prov],
    )


def split_failures(
    cases: list[TestCase], names: set[str], contested: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """The failed / errored cases as (in `names`, contested, other). Contested tests match by
    method name (the implementer names them as it sees them, e.g. `Class.Method`)."""
    keys = {short_name(n) for n in contested}
    named: list[str] = []
    cont: list[str] = []
    other: list[str] = []
    for c in cases:
        if c.outcome not in ("failed", "error"):
            continue
        if c.name in names:
            named.append(c.name)
        elif short_name(c.name) in keys:
            cont.append(c.name)
        else:
            other.append(c.name)
    return named, cont, other


def _listed(names: list[str], limit: int = 30) -> str:
    more = f"\n  ... {len(names) - limit} more" if len(names) > limit else ""
    return "\n".join(f"  {n}" for n in names[:limit]) + more


def tests_expecting(run: TestRun, exp: ExpectedRed, parked_names: set[str]) -> CheckResult:
    """A test run judged with the expected red tests left out: FAIL only on other failures."""
    if run.incomplete:
        return incomplete_result(run.incomplete, run.output)
    if run.status == "passed":
        return CheckResult("PASS", run.output)
    if run.status != "failed" or run.cases is None:
        return CheckResult(
            "FAIL",
            run.output + "\n(no per-test results: the expected red tests cannot be told apart "
            "from other failures)",
        )
    parked, contested, other = split_failures(run.cases, parked_names, exp.contested)
    parts = []
    if exp.parked_ids:
        parts.append(f"expected red (parked): {len(parked)}")
    if exp.contested:
        parts.append(f"contested (provisional, left out): {len(contested)}")
    if other:
        return CheckResult(
            "FAIL",
            run.output + "\n\nUNEXPECTED failures (neither a parked step's test nor a contested "
            f"test): {len(other)}\n{_listed(other)}",
            "; ".join(parts),
        )
    return CheckResult("PASS", run.output, "; ".join([*parts, "no other failure"]))


def parked_test_names(adapter: LanguageAdapter, exp: ExpectedRed) -> tuple[set[str], str]:
    """Names of the tests tagged with the parked steps' spec IDs (one run of just those), and a
    problem note ("" if fine)."""
    if not exp.parked_ids:
        return set(), ""
    junit = adapter.cfg.out_dir / "parked_tests.xml"
    run = adapter.run_tests(req_ids=exp.parked_ids, junit=junit)
    if run.cases is None:
        return set(), f"the parked steps' tests produced no per-test report ({run.status})"
    return {c.name for c in run.cases}, ""


def regression_expecting(adapter: LanguageAdapter, exp: ExpectedRed) -> CheckResult:
    """The whole suite with parked steps / contested tests: their failures are expected
    (counted, not failing); any other failure fails the check."""
    names, problem = parked_test_names(adapter, exp)
    res = tests_expecting(adapter.regression_run(), exp, names)
    if problem and res.status == "FAIL":
        res.output += f"\n({problem})"
    return res


def _skipped(reason: str) -> Callable[[], CheckResult]:
    return lambda: CheckResult("SKIPPED", reason)


def checks(
    adapter: LanguageAdapter,
    feature: str,
    scope: str,
    skip_mutation: bool,
    exp: ExpectedRed | None = None,
) -> list[Check]:
    tag = frame_diff.approved_tag(feature)
    c = list(adapter.lint_checks(tag))
    c.append(Check("frame diff", lambda: frame_diff.check(adapter, feature)))
    if scope == "step":
        reqs = read_reqs(adapter.cfg)
        contested = read_contested(adapter.cfg)

        def step_tests() -> CheckResult:
            if not contested:
                run = adapter.run_tests(req_ids=reqs)
                return CheckResult("PASS" if run.status == "passed" else "FAIL", run.output)
            run = adapter.run_tests(req_ids=reqs, junit=adapter.cfg.out_dir / "step_tests.xml")
            return tests_expecting(run, ExpectedRed(contested=contested), set())

        c.append(Check("tests (current + earlier steps)", step_tests))
    else:
        cov, mut = final_scopes(adapter, feature)
        waiting = exp or ExpectedRed()
        reason = f"SKIPPED ({waiting.reason()}): runs again after the redo"
        if waiting:
            c.append(
                Check(
                    "regression: whole suite, no coverage",
                    lambda: regression_expecting(adapter, waiting),
                )
            )
            c += [Check(k.name, _skipped(reason)) for k in adapter.coverage_checks(tag, cov)]
        else:
            if cov is not None:
                c.append(adapter.regression_check())
            c += adapter.coverage_checks(tag, cov)
        c.append(Check("traceability", lambda: traceability.check(adapter, feature)))
        c.append(Check("test smells", lambda: test_smells.check(adapter, feature)))
        if not skip_mutation:
            name = "mutation score" + (" (feature tests)" if mut is not None else "")
            run = _skipped(reason) if waiting else (lambda: mutation.check(adapter, feature, mut))
            # a switched-off tool says so itself; only a real run is worth waiting for green
            off = (adapter.cfg.mutation_tool or "").lower() == "none"
            c.append(Check(name, run, needs_green=not off))
    return c


#: final checks whose failure means the build or the tests are red (by the start of their name)
RED_CHECKS = ("typecheck", "regression", "coverage run", "full test suite")


def red_blockers(failed: list[str]) -> list[str]:
    """The failed checks that make a `needs_green` check pointless (its result would not count
    and, for mutation testing, costs minutes)."""
    return [n for n in failed if n.startswith(RED_CHECKS)]


@dataclass
class GateReport:
    """Result of a gate run: overall verdict, the report text, and per-check details."""

    ok: bool
    report: str
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: failing check name -> its output, filtered to the failure lines (failure_filter)
    outputs: dict[str, str] = field(default_factory=dict)
    #: failing check name -> its whole output (for decisions, e.g. which files a lint names)
    full_outputs: dict[str, str] = field(default_factory=dict)
    #: check name -> wall-clock seconds
    durations: dict[str, float] = field(default_factory=dict)
    #: [gates.extra] check name -> exit code (reported at the final gate, never blocking)
    extra: dict[str, int] = field(default_factory=dict)
    #: check name -> "PASS" / "FAIL" / "SKIPPED" plus its one-line result (score, coverage)
    results: dict[str, str] = field(default_factory=dict)
    #: final scope: the structure report (file sizes, longest methods; structure.Structure)
    structure: dict[str, object] = field(default_factory=dict)

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
    full_outputs: dict[str, str] = {}
    durations: dict[str, float] = {}
    results: dict[str, str] = {}
    exp = expected_red(adapter.cfg, feature) if scope == "final" else ExpectedRed()
    if scope == "final":
        for line in scope_lines(adapter, feature, skip_mutation):
            emit(line)
        if exp:
            emit(f"Waiting for the human's answer ({exp.reason()}):")
            if exp.parked_ids:
                emit(f"- expected red: the tests of {', '.join(exp.parked_ids)}")
            if exp.contested:
                emit(f"- contested tests, left out: {', '.join(exp.contested)}")
            emit("- coverage, diff coverage and mutation: skipped until the redo")
        emit("")
    try:
        note = adapter.format_fix(frame_diff.approved_tag(feature))
    except Exception as e:  # the checks below still report the formatting
        note = f"skipped ({type(e).__name__}: {e})"
    if note:
        emit(f"Format fix: {note}")
    for check in checks(adapter, feature, scope, skip_mutation, exp):
        t0 = time.monotonic()
        blockers = red_blockers(failed) if check.needs_green else []
        try:
            res = (
                CheckResult(
                    "SKIPPED",
                    f"SKIPPED: not run, {', '.join(blockers)} failed first (it needs a green "
                    "build and green tests; it runs once they pass)",
                )
                if blockers
                else check.run()
            )
        except Exception as e:  # a crashing check must fail the gate, not the runner
            res = CheckResult("FAIL", f"check crashed: {type(e).__name__}: {e}")
        durations[check.name] = round(time.monotonic() - t0, 1)
        results[check.name] = res.status + (f": {res.summary}" if res.summary else "")
        emit(f"[{res.status}] {check.name}")
        out = res.output.strip()
        if res.status == "FAIL":
            failed.append(check.name)
            if res.summary:
                emit(f"  {res.summary}")
            full_outputs[check.name] = out
            outputs[check.name] = filter_failure_output(out, max_lines=OUTPUT_LINES)
            if len(out.splitlines()) > OUTPUT_LINES:
                log = save_full_output(adapter, feature, check.name, out)
                if log:
                    outputs[check.name] += f"\n(filtered to the failure lines; full output: {log})"
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
    shape: dict[str, object] = {}
    if scope == "final":
        try:
            report = structure.report(adapter, feature)
        except Exception as e:  # a report, never a reason to fail the gate
            report = structure.Structure(note=f"report failed: {type(e).__name__}: {e}")
        emit("")
        for line in report.lines():
            emit(line)
        shape = report.as_dict()
    extra = run_extra_checks(adapter, feature, emit, durations) if scope == "final" else {}
    return GateReport(
        not failed,
        "\n".join(lines),
        failed,
        skipped,
        outputs,
        full_outputs,
        durations,
        extra,
        results,
        shape,
    )


def save_full_output(adapter: LanguageAdapter, feature: str, check: str, text: str) -> str:
    """Write a failing check's whole output to {out}/{feature}/check_logs/<check>.log (the gate
    report shows only the failure lines). Returns the repo-relative path, "" if not written."""
    cfg = adapter.cfg
    slug = re.sub(r"[^\w-]+", "_", check).strip("_") or "check"
    path = cfg.feature_out(feature) / "check_logs" / f"{slug}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        return path.relative_to(cfg.root).as_posix()
    except (OSError, ValueError):
        return ""


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
