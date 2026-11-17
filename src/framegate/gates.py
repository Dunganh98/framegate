"""Gate runner: run the frame checks for a feature.

scope step   fast checks after each implementation step (tests of current + earlier steps;
             with [gates] step_tests = "suite" the whole everyday suite too)
scope fix    the same checks for a run after the review (review fixes, structure fixes, final
             repair) with the feature's tests and the review tests only: the final gates run
             the whole suite right after
scope idle   a plan step whose run changed nothing since the last green step gate (same
             commit, clean tree): only the step's tests; the rest is that gate's result
             (the orchestrator picks it, never a refactor: an idle refactor step fails)
A port's step and fix gates also show the ported files the run touched against the
reference (not blocking; references.step_fidelity_lines).
scope final  everything, including coverage on the diff, traceability and mutation testing,
             then the structure report (sizes of the changed files, longest methods), for a
             port the fidelity report (each target file against its reference file) and the
             project's own [gates.extra] checks (all reported, never blocking)

Both scopes first format the changed source files ([gates] format_fix).

Work that waits for the human's answer at the final gate has tests that fail by design: a parked
step's tests (by spec ID) and a provisional step's contested tests (by name). The step gates
leave the contested tests out; the final gate counts both as expected red, fails only on other
failures, and skips coverage, diff coverage and mutation until the redo.
"""

from __future__ import annotations

import functools
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from framegate import red_tree, references, task_base, unrunnable
from framegate.adapters.base import (
    NOT_MEASURED,
    Check,
    CheckResult,
    LanguageAdapter,
    TestCase,
    TestRun,
    TestScope,
    changed_lines,
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
from framegate.specs import baseline_on, feature_ids, plan_steps
from framegate.state import load_progress, log_event, read_contested, read_reqs, tag_exists

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
    # a test this machine cannot run is left out by its own list, in every mode
    run, _ = unrunnable.leave_out(adapter.cfg, adapter.regression_run())
    if run.incomplete:
        return 0, f"the suite run was cut short ({run.incomplete})"
    if run.status == "passed":
        failing: list[str] = []
    elif run.status == "failed" and run.cases is not None:
        failing = sorted({c.name for c in run.cases if c.outcome in ("failed", "error")})
        # never the feature's own tests (a record taken after they were written: they are
        # red by design then, and the regression check would skip one that breaks later)
        own = later_step_tests(adapter, feature, []) if failing else []
        failing = [n for n in failing if not is_test_at(n, own)]
    else:
        return 0, f"the suite run gave no per-test results ({run.status})"
    path = adapter.cfg.feature_out(feature) / BASELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"failing": failing}, indent=2), encoding="utf-8")
    return len(failing), ""


def baseline_recorded(cfg: Config, feature: str) -> bool:
    """Is there a record of the tests that failed when the spec was approved?"""
    return bool(
        feature
        and baseline_on(cfg, feature)
        and (cfg.feature_out(feature) / BASELINE_FILE).is_file()
    )


def regression_baseline(cfg: Config, feature: str) -> set[str]:
    """Names of the tests that already failed when the spec was approved (empty: none
    recorded, or [gates] regression_baseline is off)."""
    if not feature or not baseline_on(cfg, feature):
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
    the spec was approved / tests this machine cannot run ([gates] unrunnable_tests): their
    failures are expected (counted, not failing); any other failure fails the check."""
    names, problem = parked_test_names(adapter, exp)
    run, off = unrunnable.leave_out(adapter.cfg, adapter.regression_run())
    res = unrunnable.noted(tests_expecting(run, exp, names, known), off)
    if problem and res.status == "FAIL":
        res.output += f"\n({problem})"
    return res


def _skipped(reason: str) -> Callable[[], CheckResult]:
    return lambda: CheckResult("SKIPPED", reason)


#: step scope, only with [gates] step_tests = "suite": the whole suite beside the step's tests
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
    """The whole suite (the adapter's everyday selection) at a step gate, only with [gates]
    step_tests = "suite". Off by default: a gate judges what belongs to the task, and seen
    live this check failed every step on tests that were red before the task. Failures of
    this feature's later steps, of contested tests, of tests that already failed when the
    spec was approved and of listed tests ([gates] unrunnable_tests) are left out."""
    cfg = adapter.cfg
    later = later_step_tests(adapter, feature, covered)
    run, off = unrunnable.leave_out(cfg, adapter.run_tests(junit=cfg.out_dir / "step_suite.xml"))
    if run.status == "passed":
        return unrunnable.noted(CheckResult("PASS", run.output), off)
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
    if off:
        note = f"{unrunnable.LEFT_OUT}: {len(off)}; {note}"
    if other:
        blame = (
            "they passed when the spec was approved. Your change broke them: fix the code, "
            "not the tests."
            if baseline_recorded(cfg, feature)
            else "the tests that failed before this feature were not recorded ([gates] "
            "regression_baseline is off, or its run gave no result), so they cannot be told "
            "apart from tests your change broke. Fix what your change broke; a test that "
            "fails for another reason is not yours to fix: say so in your step note."
        )
        return CheckResult(
            "FAIL",
            run.output
            + f"\n\nTests outside this step fail ({len(other)}); {blame}\n{_listed(other)}",
            note,
        )
    return CheckResult("PASS", run.output, note + "; no other failure")


#: fix scope (the gates of the runs after the review: review fixes, structure fixes, final
#: repair): the feature's tests and the review tests, not the whole suite. Seen live
#: (da-argos): the suite took 4.5 min per gate, twice for two small fix runs, directly before
#: the final gates ran it again. What such a run breaks elsewhere, the final regression finds
#: (and its repair run then checks with the whole suite).
FIX_TESTS = "tests (this feature)"
REVIEW_TESTS = "review tests"


#: idle scope (a plan step whose run changed nothing since the last green step gate: same
#: commit, clean tree): only the step's tests run. Seen live (da-argos, a port): step 1
#: ported two whole files, step 4 "needed no code change", and its gate ran the whole suite
#: again (4.5 min) on the code the gate before it had just checked.
IDLE_NOTE = (
    "Nothing changed since the last green step gate (same commit, clean tree): lint and the "
    "frame check are that gate's results; only this step's tests ran."
)


def review_tests_checked(adapter: LanguageAdapter, feature: str) -> bool:
    """Does a fix gate run the review tests folder on top of the feature's IDs (a review test
    without a spec-ID tag)? Not while a step is parked: findings on its IDs wait for the redo,
    their review tests are red by design (the tagged ones of the done steps still run)."""
    cfg = adapter.cfg
    if not (cfg.root / cfg.paths.review_tests).exists():
        return False
    return not expected_red(cfg, feature).parked_ids


def review_tests(adapter: LanguageAdapter, feature: str, contested: list[str]) -> CheckResult:
    """Every test in the review tests folder (contested tests, tests that already failed
    when the spec was approved and tests this machine cannot run are left out, as in the
    suite)."""
    cfg = adapter.cfg
    run = adapter.run_tests(
        paths=[cfg.paths.review_tests], junit=cfg.out_dir / "step_review_tests.xml"
    )
    if run.status == "no_tests":
        return CheckResult("PASS", run.output, "no review tests")
    known = regression_baseline(cfg, feature)
    run, off = unrunnable.leave_out(cfg, run)
    return unrunnable.noted(
        tests_expecting(run, ExpectedRed(contested=contested), set(), known), off
    )


def task_files(adapter: LanguageAdapter, feature: str) -> set[str] | None:
    """The files the task changed: since the tests approval, and since the spec approval
    (the tests it wrote before that tag). None before both: nothing is known."""
    tags = (frame_diff.approved_tag(feature), f"spec-approved/{feature}") if feature else ()
    found = [s for tag in tags if (s := adapter.changed_since(tag)) is not None]
    return set().union(*found) if found else None


def task_lines(adapter: LanguageAdapter, feature: str) -> Callable[[str], set[int] | None]:
    """file -> the lines the task wrote in it (None = all of it): changed since the spec
    approval (else the tests approval), old lines the formatter rewrote left out
    (task_base.py)."""
    cfg = adapter.cfg
    tags = (f"spec-approved/{feature}", frame_diff.approved_tag(feature)) if feature else ()
    found: dict[str, set[int] | None] = {}
    base: list[str | None] = []

    def lines(rel: str) -> set[int] | None:
        if not base:
            marker = next((t for t in tags if tag_exists(cfg, t)), None)
            base.append(task_base.task_base(adapter, marker))
        if rel not in found:
            found[rel] = changed_lines(cfg, base[0], rel) if base[0] else None
        return found[rel]

    return lines


def lint_checks(adapter: LanguageAdapter, feature: str) -> list[Check]:
    """The lint / format / typecheck / imports checks. With `file_scoped_lint` they run on the
    whole tree (mypy needs it) and a finding counts only on a line the task wrote."""
    found = adapter.lint_checks(frame_diff.approved_tag(feature))
    if not adapter.file_scoped_lint:
        return found

    def scoped(check: Check) -> CheckResult:
        res = check.run()
        changed = task_files(adapter, feature)
        if changed is None:
            return res
        return red_tree.in_changed(adapter, changed, res, task_lines(adapter, feature))

    return [Check(k.name, functools.partial(scoped, k), k.needs_green) for k in found]


def checks(
    adapter: LanguageAdapter,
    feature: str,
    scope: str,
    skip_mutation: bool,
    exp: ExpectedRed | None = None,
) -> list[Check]:
    tag = frame_diff.approved_tag(feature)
    # an idle step (scope "idle"): the code is the one the last green gate checked, so only
    # the step's own tests are new; lint and the frame check are its results
    idle = scope == "idle"
    c = [] if idle else lint_checks(adapter, feature)
    if not idle:
        c.append(Check(frame_diff.CHECK_NAME, lambda: frame_diff.check(adapter, feature)))
    if scope in ("step", "fix", "idle"):
        reqs = read_reqs(adapter.cfg)
        contested = read_contested(adapter.cfg)

        def step_tests() -> CheckResult:
            cfg = adapter.cfg
            if not contested and not cfg.unrunnable_tests:
                run = adapter.run_tests(req_ids=reqs)
                return CheckResult("PASS" if run.status == "passed" else "FAIL", run.output)
            run = adapter.run_tests(req_ids=reqs, junit=cfg.out_dir / "step_tests.xml")
            run, off = unrunnable.leave_out(cfg, run)
            res = tests_expecting(run, ExpectedRed(contested=contested), set())
            return unrunnable.noted(res, off)

        fix = scope == "fix" and bool(reqs)  # without IDs the check runs everything
        c.append(Check(FIX_TESTS if fix else "tests (current + earlier steps)", step_tests))
        if fix:
            if review_tests_checked(adapter, feature):
                c.append(Check(REVIEW_TESTS, lambda: review_tests(adapter, feature, contested)))
        elif scope == "step" and reqs and feature and adapter.cfg.step_tests == "suite":
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
            if cov is not None and (known or adapter.cfg.unrunnable_tests):
                c.append(
                    Check(
                        "regression: whole suite, no coverage",
                        lambda: regression_expecting(adapter, ExpectedRed(), known),
                    )
                )
            elif cov is not None:
                c.append(adapter.regression_check())
            # diff coverage counts the task's own lines, not the ones the formatter rewrote
            c += adapter.coverage_checks(task_base.task_base(adapter, tag) or tag, cov, cover_tests)
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


#: under a check whose tool gave no result (it crashed, was cut short): NOT MEASURED, not FAIL
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
    #: check name -> "PASS" / "FAIL" / "SKIPPED" / "NOT MEASURED" plus its one-line result
    #: (score, coverage; for NOT MEASURED the reason and the fix)
    results: dict[str, str] = field(default_factory=dict)
    #: final scope: the structure report (file sizes, longest methods; structure.Structure)
    structure: dict[str, object] = field(default_factory=dict)
    #: checks whose tool gave no result (CheckResult.tool_failure): shown as NOT MEASURED (also
    #: in `not_measured`), never in `failed`; the final gate asks whether to run them again
    tool_failures: list[str] = field(default_factory=list)
    #: final scope, a port: each target file against its reference file (references.fidelity)
    fidelity: dict[str, object] = field(default_factory=dict)
    #: checks whose tool cannot run on this machine (base.not_measured): passed over, never
    #: counted as passed; `results` has the reason and the fix
    not_measured: list[str] = field(default_factory=list)

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
    not_measured: list[str] = []
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
        if adapter.cfg.unrunnable_tests:
            # said at every final gate: a green result says nothing about these tests
            emit(
                "Listed tests ([gates] unrunnable_tests: this machine cannot run them, or "
                "they were red before any task; a failure of one is left out):"
            )
            for line in unrunnable.listed_lines(adapter.cfg):
                emit(line)
        emit("")
    if scope == "idle":
        emit(IDLE_NOTE)
    try:
        note = "" if scope == "idle" else adapter.format_fix(frame_diff.approved_tag(feature))
    except Exception as e:  # the checks below still report the formatting
        note = f"skipped ({type(e).__name__}: {e})"
    if note:
        emit(f"Format fix: {note}")
    if scope != "idle" and getattr(adapter, "format_beyond", None):
        log_event(adapter.cfg, feature, "format_debt", files=adapter.format_beyond)
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
        if res.status == "FAIL" and res.tool_failure:
            # no result is no verdict: NOT MEASURED, and the final gate asks about it
            tool_failures.append(check.name)
            text = res.output.strip()
            long = len(text.splitlines()) > OUTPUT_LINES
            log = save_full_output(adapter, feature, check.name, text) if long else ""
            parts = (TOOL_FAILURE_NOTE, res.summary, f"full output: {log}" if log else "")
            res = CheckResult(NOT_MEASURED, res.output, "; ".join(x for x in parts if x))
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
        elif res.status == NOT_MEASURED:
            not_measured.append(check.name)
            emit(f"  {res.summary}")
            for line in out.splitlines()[-OUTPUT_LINES:]:  # e.g. the tool's setup log
                emit(f"  {line}")
        elif res.summary:
            emit(f"  {res.summary}")
        elif "SKIPPED" in out:
            emit("  " + next(line for line in out.splitlines() if "SKIPPED" in line).strip())
    summary = "ALL GATES PASSED" if not failed else "FAILED: " + ", ".join(failed)
    if skipped:
        summary += f" (SKIPPED: {', '.join(skipped)})"
    if not_measured:
        summary += f" (NOT MEASURED, the tool cannot run here: {', '.join(not_measured)})"
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
    fidelity = (
        port_fidelity(adapter.cfg, feature, emit)
        if scope == "final"
        else step_fidelity(adapter.cfg, feature, emit)
        if feature
        else {}
    )
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
        fidelity,
        not_measured,
    )


def port_fidelity(cfg: Config, feature: str, emit: Callable[[str], None]) -> dict[str, object]:
    """A port's fidelity report (references.py): reported, never part of the verdict."""
    try:
        pins = load_progress(cfg, feature).get("references")
        rep: dict[str, object] = references.fidelity(cfg, feature, pins)
    except Exception as e:  # a report, never a reason to fail the gate
        rep = {"reference": "?", "skipped": f"report failed: {type(e).__name__}: {e}"}
    lines = references.fidelity_lines(rep)
    if lines:
        emit("")
    for line in lines:
        emit(line)
    return rep


def step_fidelity(cfg: Config, feature: str, emit: Callable[[str], None]) -> dict[str, object]:
    """A step or fix gate of a port: the target files the run touched (uncommitted changes)
    against their reference files, with the same numbers as the final report. Reported, never
    part of the verdict; the lines are part of the report, so a failed attempt's feedback
    holds them. Seen live: a ported file's wording differed from the reference from step 1
    on and only the review said so (three structure findings, one more run and gate)."""
    try:
        touched = references.touched_targets(cfg, feature)
        if not touched:
            return {}
        pins = load_progress(cfg, feature).get("references")
        rep: dict[str, object] = references.fidelity(cfg, feature, pins, only=touched)
        lines = references.step_fidelity_lines(rep)
    except Exception:  # a report, never a reason to fail the gate
        return {}
    if lines:
        emit("")
    for line in lines:
        emit(line)
    return rep if lines else {}


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
