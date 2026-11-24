"""Before implementation, every test of the feature must FAIL against the stubs.

A test that passes against a stub asserts nothing useful. Errors (import/collection/fixture
problems) and skips are also reported. The exception are fixture precondition tests
(`test_fixture_*` / `Fixture_*`): they prove that pinned data has the situation a test needs,
with code that already exists, so they must PASS (a failing one means the data was picked wrong;
seen live: seeds pinned for a situation the data never produced, found only at the final gate).

The second exception is the spec's own list: requirements.md, `## Green against stubs`, names
the tests that cannot fail before the implementation, by design, one bullet each with the
reason (`- <test name or node ID>: <reason>`): a scan ("no analytic name appears in the
code"), a negative check ("X is not imported"), a structure test. Exactly those may pass
("passes by design"); a listed test that FAILS against the stubs is a failure, and so is a spec
ID that only such tests prove (nothing would fail if it were not built). The list is general:
a port carries such tests over from its reference (seen live, da-argos: a carried-over scan
failed this check in three rounds, and the test writer may not change a carried test), and a
normal feature can have a structure test of its own.

A test must fail for the right reason: on the stub, on an assertion about the new behaviour,
or inside the code under test. One that stops on another error before it gets there (the
adapter's stub_failure_problem) is wrong and fails the check. And a red test stops at its first
failing line: the assertions after it have never run. not_reached lists them per test, a note
for the test reviewer, who walks each by hand.

The verdict covers the tests the feature added or changed. A test file it did not touch
that cannot load (the run reports every file it collects) is a note when it was broken
before the task: listed in [gates] unrunnable_tests, recorded as failing then (regression
baseline), or it cannot be loaded at the spec-approved commit either (asked once per set
of files, in a scratch worktree; out/<feature>/red_before.json). A file that loads there
stays an error: the task broke it. Seen live: an old test file without its framework
failed this check in every round, and the test writer could do nothing about it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from framegate import task_base, unrunnable
from framegate.adapters.base import (
    CheckResult,
    LanguageAdapter,
    TestAssertions,
    TestCase,
    parse_junit,
)
from framegate.config import Config
from framegate.report import short_name
from framegate.specs import feature_ids
from framegate.state import GitError, git

FIXTURE_NAME = re.compile(r"^(?:test_)?fixture_", re.I)
GREEN_SECTION = "Green against stubs"
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_NOTHING = re.compile(r"^\W*(none|n/?a|nothing)\b[\W]*$", re.I)
#: `- <test>: <reason>`; the test is one word (a name or a node ID), backticks optional
_GREEN = re.compile(r"^`?([\w./\\:\[\]-]*\w\]?)`?\s*(?::|[—–]|\s-)\s+(\S.*)$")
_PARTS = re.compile(r"[.:/\\]+")
#: name parts that say nothing about which test it is
_FILLER = {"py", "cs", "tests", "test"}


@dataclass(frozen=True)
class GreenTest:
    """One line of the spec's `## Green against stubs`."""

    name: str
    reason: str
    #: a place of the tag scan (`file::name`; a tagged class: every test in it)
    tagged: bool = False

    def matches(self, case: str) -> bool:
        """Is the test case (a name from the test report, or `file::name` from the tag scan)
        this test? The function name must be the same, and every other part the spec names
        (file, class) must be in the case's name."""
        mine, theirs = _parts(self.name), _parts(case)
        if not mine or not theirs or (mine[-1] != theirs[-1] and not self.tagged):
            return False
        if self.tagged:
            return all(p in theirs for p in mine if p not in _FILLER)
        return all(p in theirs for p in mine[:-1] if p not in _FILLER)


def _parts(name: str) -> list[str]:
    return [p for p in _PARTS.split(re.split(r"[(\[]", name, maxsplit=1)[0]) if p]


def _green_bullets(text: str) -> list[str]:
    m = re.search(rf"^##\s+{re.escape(GREEN_SECTION)}\b.*$", _COMMENT.sub("", text), re.M | re.I)
    if not m:
        return []
    rest = _COMMENT.sub("", text)[m.end() :]
    end = re.search(r"^##\s", rest, re.M)
    lines = (rest[: end.start()] if end else rest).splitlines()
    found = [b.group(1) for x in lines if (b := _BULLET.match(x))]
    return [x for x in found if not _NOTHING.match(x) and not re.fullmatch(r"<.*>", x)]


def parse_green(text: str) -> list[GreenTest]:
    """The tests a requirements.md lists as green against the stubs by design ([] when the
    section is missing, empty or "None."; a bullet of the wrong shape is left out: see
    green_problems)."""
    return [
        GreenTest(m.group(1), m.group(2)) for x in _green_bullets(text) if (m := _GREEN.match(x))
    ]


def green_problems(text: str) -> list[str]:
    """Spec check: bullets of `## Green against stubs` that are not `- <test>: <reason>`."""
    return [
        f"requirements.md, '{GREEN_SECTION}': \"{x[:60]}\" is not `- <test name or node ID>: "
        "<why it cannot fail before the implementation>` (one test per bullet, the reason "
        "after a colon)"
        for x in _green_bullets(text)
        if not _GREEN.match(x)
    ]


def feature_green(cfg: Config, feature: str) -> list[GreenTest]:
    path = cfg.spec_dir(feature) / "requirements.md"
    return parse_green(path.read_text(encoding="utf-8")) if path.is_file() else []


def tagged_green(
    adapter: LanguageAdapter, green: list[GreenTest]
) -> tuple[list[GreenTest], set[str]]:
    """A listed spec ID stands for the tests tagged with it (the tag scan): (the list with
    such a line as its tests, the IDs listed so). An ID without a tagged test stays."""
    ids = re.compile(adapter.cfg.id_pattern)
    whole = {g.name for g in green if ids.fullmatch(g.name)}
    try:
        tags = adapter.collect_req_tags() if whole else {}
    except Exception:  # a broken test file: the run reports it
        tags = {}
    out: list[GreenTest] = []
    for g in green:
        places = tags.get(g.name) or [] if g.name in whole else []
        out += [GreenTest(p, g.reason, tagged=True) for p in places] or [g]
    return out, {i for i in whole if tags.get(i)}


def only_green_ids(
    adapter: LanguageAdapter, ids: set[str], green: list[GreenTest]
) -> dict[str, list[str]]:
    """Spec IDs whose every tagged test is green by design -> those tests (from the static
    tag scan; a tag on a class counts as another test). {} when the scan fails."""
    if not green:
        return {}
    try:
        tags = adapter.collect_req_tags()
    except Exception:  # a broken test file: the run above reports it
        return {}
    return {
        i: places
        for i in sorted(ids)
        if (places := tags.get(i) or []) and all(any(g.matches(p) for g in green) for p in places)
    }


#: under a test that passes before the code: what the test writer can do about it
PASSES_REMEDY = (
    "For a new test that passes: make it fail first (assert the accepted case next to the "
    "refused one), or ask to list it as green against stubs"
)


#: under a test that is red for another reason than the stub or an assertion
OTHER_REMEDY = (
    "A red test fails on the stub or on an assertion about the new behaviour. Assert what is "
    "missing (the key, the attribute, the value) so that the assertion fails, or fix the "
    "test's setup. If the stubs lack what the spec names, ask; do not write around it"
)
REPORT = "red_stubs.xml"
#: the heading of the note in test_checks.md
NOT_REACHED = "Asserts not reached at red"


def not_reached(adapter: LanguageAdapter, feature: str) -> list[str]:
    """One line per red test of the last red run (its report): where it stopped and the
    assertions after that line, which no run has executed yet. [] when the adapter cannot
    tell where a test stopped."""
    cfg = adapter.cfg
    report = cfg.feature_out(feature) / REPORT
    try:
        cases = parse_junit(report) if report.is_file() else []
    except Exception:  # a cut-off report: nothing to say
        return []
    tests = cfg.paths.tests.rstrip("/") + "/"
    known: dict[str, list[TestAssertions]] = {}
    out: dict[str, None] = {}
    for c in cases:
        if c.outcome != "failed":
            continue
        name = short_name(c.name).split("[")[0]
        for rel, line in reversed(adapter.failure_frames(c)):
            if not rel.startswith(tests):
                continue
            if rel not in known:
                known[rel] = adapter.test_assertions(rel)
            test = next(
                (t for t in known[rel] if t.name == name and t.first <= line <= t.last), None
            )
            if test is None:
                continue
            later = [n for n in test.lines if n > line]
            if later:
                where = ", ".join(f":{n}" for n in later)
                out[f"{rel}::{name}: red at :{line}; asserts at {where} never ran"] = None
            break
    return list(out)


UNTOUCHED_HINT = (
    "(an error in a test file the feature did not touch, which loads at the spec-approved "
    "commit: the stubs or a test file of this task broke it)"
)
BEFORE_FILE = "red_before.json"


def broken_before(adapter: LanguageAdapter, feature: str, names: list[str]) -> set[str]:
    """Of the load errors `names`, those that cannot load at the spec-approved commit
    either (LanguageAdapter.broken_at). Kept in out/<feature>/red_before.json per commit
    and set of names. Empty when it cannot be told."""
    cfg = adapter.cfg
    if not names:
        return set()
    path = cfg.feature_out(feature) / BEFORE_FILE
    try:
        commit = git(cfg, "rev-parse", f"spec-approved/{feature}^{{commit}}").strip()
        key = [commit, sorted(names)]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict) and data.get("key") == key:
            return {str(n) for n in data.get("broken") or []}
        with task_base.scratch(cfg, commit) as tree:
            found = adapter.broken_at(tree, key[1])
        if found is None:
            return set()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"key": key, "broken": sorted(found)}), encoding="utf-8")
        return found
    except (GitError, OSError):
        return set()


def own_cases(
    adapter: LanguageAdapter, feature: str, cases: list[TestCase]
) -> tuple[list[TestCase], list[str]]:
    """(the cases the verdict covers, a note per case left out): an error in a test file
    the feature did not touch is left out when it was broken before the task (see the
    module text). Without the spec approval marker nothing is left out."""
    from framegate import gates

    cfg = adapter.cfg
    touched = adapter.changed_since(f"spec-approved/{feature}")
    if touched is None:
        return cases, []
    known = gates.regression_baseline(cfg, feature)
    kept: list[TestCase] = []
    notes: list[str] = []
    unsure: list[TestCase] = []
    for c in cases:
        if c.outcome != "error" or any(unrunnable.matches(cfg, c.name, f) for f in touched):
            kept.append(c)
        elif entry := unrunnable.entry_of(cfg, c.name):
            why = cfg.unrunnable_tests[entry]
            notes.append(f"NOTE: cannot load, listed in unrunnable_tests ({why}): {c.name}")
        elif c.name in known:
            notes.append(f"NOTE: could not load before the task either: {c.name}")
        else:
            unsure.append(c)
    before = broken_before(adapter, feature, [c.name for c in unsure])
    notes += [f"NOTE: could not load before the task: {c.name}" for c in unsure if c.name in before]
    return [*kept, *(c for c in unsure if c.name not in before)], notes


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    cfg = adapter.cfg
    ids = feature_ids(cfg, feature)
    if not ids:
        return CheckResult("FAIL", f"No spec IDs found in {cfg.paths.specs}/{feature}/")
    report = cfg.feature_out(feature) / REPORT
    run = adapter.red_stub_run(sorted(ids), report)
    if run.cases is None:
        tail = "\n".join(run.output.splitlines()[-20:])
        return CheckResult("FAIL", f"Test run produced no report (collection crashed?)\n{tail}")
    green, whole = tagged_green(adapter, feature_green(cfg, feature))
    counted, notes = own_cases(adapter, feature, run.cases)
    run.cases = counted

    def by_design(name: str) -> GreenTest | None:
        return next((g for g in green if g.matches(name)), None)

    fixtures = [c for c in run.cases if is_fixture_check(c.name)]
    listed = [c for c in run.cases if not is_fixture_check(c.name) and by_design(c.name)]
    cases = [c for c in run.cases if c not in fixtures and c not in listed]
    passed = [c.name for c in cases if c.outcome == "passed"]
    designed = [f"{c.name} — {g.reason}" for c in listed if (g := by_design(c.name))]
    red_listed = [c.name for c in listed if c.outcome == "failed"]
    unseen = [g.name for g in green if not any(g.matches(c.name) for c in run.cases)]
    # an ID the spec lists itself is green by design as a whole
    alone = {i: p for i, p in only_green_ids(adapter, ids, green).items() if i not in whole}
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
    lines += [
        f"passes by design (listed in the spec's '{GREEN_SECTION}'): {n}"
        for c, n in zip(listed, designed, strict=True)
        if c.outcome == "passed"
    ]
    lines += [
        f"NOTE: the spec's '{GREEN_SECTION}' lists {n}, which is not among the feature's "
        "tests (not written, renamed, or without a spec-ID tag)"
        for n in unseen
    ]
    lines += notes
    lines += [f"PASSES AGAINST STUB (weak or wrong test): {n}" for n in passed]
    lines += [PASSES_REMEDY] if passed else []
    lines += [
        f"LISTED AS GREEN BY DESIGN BUT RED (the spec's '{GREEN_SECTION}' says it cannot "
        f"fail before the implementation; the test or the stubs are not what the spec "
        f"describes): {n}"
        for n in red_listed
    ]
    lines += [
        f"ONLY TESTS THAT PASS BY DESIGN PROVE {i} ({', '.join(places)}): add a test for it "
        "that fails against the stubs"
        for i, places in alone.items()
    ]
    lines += [f"ERROR (import/fixture/collection problem): {n}" for n in errored]
    touched = adapter.changed_since(f"spec-approved/{feature}") if errored else None
    if touched is not None and not all(
        any(unrunnable.matches(cfg, n, f) for f in touched) for n in errored
    ):
        lines.append(UNTOUCHED_HINT)
    lines += [f"SKIPPED: {n}" for n in skipped]
    lines += [f"FAILS FOR ANOTHER REASON THAN THE STUB: {n}" for n in other]
    lines += [OTHER_REMEDY] if other else []
    lines += [
        f"FIXTURE PRECONDITION FAILS (the pinned data lacks the situation; pick other data): {n}"
        for n in missing
    ]
    failed = bool(
        passed or errored or skipped or other or missing or red_listed or alone or total == 0
    )
    return CheckResult("FAIL" if failed else "PASS", "\n".join(lines))


def is_fixture_check(name: str) -> bool:
    """A fixture precondition test (`test_fixture_*` / `Fixture_*`): it checks, with code that
    exists before the feature, that pinned data (a seed, a recording) has the situation the
    feature's tests need. It must PASS against the stubs."""
    return bool(FIXTURE_NAME.match(short_name(name)))
