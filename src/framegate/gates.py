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

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from framegate.adapters.base import (
    Check,
    CheckResult,
    LanguageAdapter,
    TestCase,
    TestRun,
    TestScope,
    incomplete_result,
    run_command,
)
from framegate.checks import (
    frame_diff,
    mutation,
    structure,
    test_smells,
    traceability,
    unreached,
)
from framegate.config import Config
from framegate.failure_filter import filter_failure_output
from framegate.report import short_name
from framegate.specs import feature_ids, plan_steps
from framegate.state import load_progress, read_contested, read_reqs

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


#: out/<feature>/<this>: the tests that already failed when the spec was approved
BASELINE_FILE = "regression_baseline.json"


def record_regression_baseline(adapter: LanguageAdapter, feature: str) -> tuple[int, str]:
    """[gates] regression_baseline: run the whole suite once (as the final regression check
    does) and record the tests that fail today. Returns (number recorded, problem); with a
    problem ("" = fine) nothing is recorded and the final check stays strict."""
    run = adapter.regression_run()
    if run.incomplete:
        return 0, f"the suite run was cut short ({run.incomplete})"
    if run.status == "passed":
        failing: list[str] = []
    elif run.status == "failed" and run.cases is not None:
        failing = sorted({c.name for c in run.cases if c.outcome in ("failed", "error")})
    else:
        return 0, f"the suite run gave no per-test results ({run.status})"
    path = adapter.cfg.feature_out(feature) / BASELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"failing": failing}, indent=2), encoding="utf-8")
    return len(failing), ""


def regression_baseline(cfg: Config, feature: str) -> set[str]:
    """Names of the tests that already failed when the spec was approved (empty: none
    recorded, or [gates] regression_baseline is off)."""
    if not cfg.regression_baseline or not feature:
        return set()
    path = cfg.feature_out(feature) / BASELINE_FILE
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(n) for n in data.get("failing") or []} if isinstance(data, dict) else set()


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


def tests_expecting(
    run: TestRun,
    exp: ExpectedRed,
    parked_names: set[str],
    known: set[str] | frozenset[str] = frozenset(),
) -> CheckResult:
    """A test run judged with the expected red tests left out: FAIL only on other failures.
    `known`: tests that already failed when the spec was approved (regression baseline)."""
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
    old = [n for n in other if n in known]
    other = [n for n in other if n not in known]
    parts = []
    if old:
        parts.append(f"already failing at spec approval (left out): {len(old)}")
    if exp.parked_ids:
        parts.append(f"expected red (parked): {len(parked)}")
    if exp.contested:
        parts.append(f"contested (provisional, left out): {len(contested)}")
    if other:
        kinds = "neither a parked step's test nor a contested test"
        if known:
            kinds += ", and green when the spec was approved"
        return CheckResult(
            "FAIL",
            run.output + f"\n\nUNEXPECTED failures ({kinds}): {len(other)}\n{_listed(other)}",
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


def regression_expecting(
    adapter: LanguageAdapter, exp: ExpectedRed, known: set[str] | frozenset[str] = frozenset()
) -> CheckResult:
    """The whole suite with parked steps / contested tests / tests that already failed when
    the spec was approved: their failures are expected (counted, not failing); any other
    failure fails the check."""
    names, problem = parked_test_names(adapter, exp)
    res = tests_expecting(adapter.regression_run(), exp, names, known)
    if problem and res.status == "FAIL":
        res.output += f"\n({problem})"
    return res


def _skipped(reason: str) -> Callable[[], CheckResult]:
    return lambda: CheckResult("SKIPPED", reason)


#: step scope: the whole suite beside the step's own tests ([gates] step_tests = "suite")
STEP_SUITE = "other tests (whole suite; this feature's later steps left out)"


def _name_parts(name: str) -> list[str]:
    """`tests.test_x.TestA::test_b[1]` / `Ns.Class.Method(x: 1)` -> their name parts."""
    head = re.split(r"[(\[]", name, maxsplit=1)[0]
    return [p for p in re.split(r"[.:/\\]+", head) if p]


def later_step_tests(adapter: LanguageAdapter, feature: str, covered: list[str]) -> list[str]:
    """Where the feature's tests are that carry none of the `covered` IDs (`file::name`, as
    collect_req_tags gives them): tests of later or parked steps, red by design at a step."""
    ids = feature_ids(adapter.cfg, feature)
    by_test: dict[str, set[str]] = {}
    for req, places in adapter.collect_req_tags().items():
        for where in places:
            by_test.setdefault(where, set()).add(req)
    done = set(covered)
    return sorted(w for w, reqs in by_test.items() if reqs & ids and not reqs & done)


def is_test_at(case_name: str, places: list[str]) -> bool:
    """Is the test case (a name from the test report) one of `places` (`file::Class.name`)?
    The name must match, and so must its file or its class: a test of another feature with
    the same method name is not left out."""
    parts = _name_parts(case_name)
    for where in places:
        file, _, tail = where.rpartition("::")
        names = _name_parts(tail)
        if not names or names[-1] not in parts:
            continue
        stem = file.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem in parts or (len(names) > 1 and names[-2] in parts):
            return True
    return False


def step_suite(adapter: LanguageAdapter, feature: str, covered: list[str]) -> CheckResult:
    """The whole suite (the adapter's everyday selection) at a step gate. Seen live: a step
    made helpers public and broke another feature's frozen public-surface test; both step
    gates passed (they ran only this feature's tests) and the break showed in a later fix
    run. Failures of this feature's later steps, of contested tests and of tests that
    already failed when the spec was approved are left out."""
    cfg = adapter.cfg
    later = later_step_tests(adapter, feature, covered)
    run = adapter.run_tests(junit=cfg.out_dir / "step_suite.xml")
    if run.status == "passed":
        return CheckResult("PASS", run.output)
    if run.status != "failed" or run.cases is None:
        if run.status == "failed" and later:
            return CheckResult(
                "SKIPPED",
                "SKIPPED: the suite fails, but the run gave no per-test results, so the tests "
                "of this feature's later steps cannot be told apart from other failures",
            )
        return CheckResult("FAIL", run.output)
    known = regression_baseline(cfg, feature)
    contested = {short_name(n) for n in read_contested(cfg)}
    left_out = 0
    other = []
    for c in run.cases:
        if c.outcome not in ("failed", "error"):
            continue
        if c.name in known or short_name(c.name) in contested or is_test_at(c.name, later):
            left_out += 1
        else:
            other.append(c.name)
    note = f"left out (later steps, contested, failing before the feature): {left_out}"
    if other:
        return CheckResult(
            "FAIL",
            run.output
            + f"\n\nTests outside this step fail ({len(other)}); they passed before it. Your "
            f"change broke them: fix the code, not the tests.\n{_listed(other)}",
            note,
        )
    return CheckResult("PASS", run.output, note + "; no other failure")


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
        if reqs and feature and adapter.cfg.step_tests == "suite":
            # without IDs the check above already ran everything
            c.append(
                Check(STEP_SUITE, lambda: step_suite(adapter, feature, reqs), needs_green=True)
            )
    else:
        cov, mut = final_scopes(adapter, feature)
        waiting = exp or ExpectedRed()
        known = regression_baseline(adapter.cfg, feature)
        reason = f"SKIPPED ({waiting.reason()}): runs again after the redo"
        # the coverage run also records the feature's own test files: which assertions ran
        watch = adapter.cfg.unreached_assertions and bool(feature)
        cover_tests = unreached.feature_test_files(adapter, feature) if watch else []
        if waiting:
            c.append(
                Check(
                    "regression: whole suite, no coverage",
                    lambda: regression_expecting(adapter, waiting, known),
                )
            )
            c += [
                Check(k.name, _skipped(reason))
                for k in adapter.coverage_checks(tag, cov, cover_tests)
            ]
        else:
            if cov is not None and known:
                c.append(
                    Check(
                        "regression: whole suite, no coverage",
                        lambda: regression_expecting(adapter, ExpectedRed(), known),
                    )
                )
            elif cov is not None:
                c.append(adapter.regression_check())
            c += adapter.coverage_checks(tag, cov, cover_tests)
        c.append(Check("traceability", lambda: traceability.check(adapter, feature)))
        c.append(Check("test smells", lambda: test_smells.check(adapter, feature)))
        if cover_tests:
            run_unreached = (
                _skipped(reason) if waiting else (lambda: unreached.check(adapter, feature))
            )
            c.append(Check(unreached.NAME, run_unreached))
        if not skip_mutation:
            name = "mutation score" + (" (feature tests)" if mut is not None else "")
            run = _skipped(reason) if waiting else (lambda: mutation.check(adapter, feature, mut))
            # a switched-off tool says so itself; only a real run is worth waiting for green
            off = (adapter.cfg.mutation_tool or "").lower() == "none"
            c.append(Check(name, run, needs_green=not off))
    return c


#: under a failed check whose tool gave no result
TOOL_FAILURE_NOTE = (
    "TOOL FAILURE: the tool gave no result; this says nothing about the code or the tests"
)

#: final checks whose failure means the build or the tests are red (by the start of their name)
RED_CHECKS = ("typecheck", "regression", "coverage run", "full test suite", "tests (current")


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
    #: failed checks whose tool gave no result (CheckResult.tool_failure): no agent can fix them
    tool_failures: list[str] = field(default_factory=list)

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
    tool_failures: list[str] = []
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
            if res.tool_failure:
                tool_failures.append(check.name)
                results[check.name] = "FAIL: tool failure" + (
                    f": {res.summary}" if res.summary else ""
                )
                emit(f"  {TOOL_FAILURE_NOTE}")
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
    if scope == "final" or (feature and adapter.cfg.max_file_lines):
        try:
            report = structure.report(adapter, feature)
        except Exception as e:  # a report, never a reason to fail the gate
            report = structure.Structure(note=f"report failed: {type(e).__name__}: {e}")
        shown = report.lines() if scope == "final" else report.step_lines()
        if shown:
            emit("")
        for line in shown:
            emit(line)
        if scope == "final" or shown:
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
        tool_failures,
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
