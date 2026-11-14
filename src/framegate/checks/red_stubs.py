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
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from framegate.adapters.base import CheckResult, LanguageAdapter
from framegate.config import Config
from framegate.report import short_name
from framegate.specs import feature_ids

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

    def matches(self, case: str) -> bool:
        """Is the test case (a name from the test report, or `file::name` from the tag scan)
        this test? The function name must be the same, and every other part the spec names
        (file, class) must be in the case's name."""
        mine, theirs = _parts(self.name), _parts(case)
        if not mine or not theirs or mine[-1] != theirs[-1]:
            return False
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
    green = feature_green(cfg, feature)

    def by_design(name: str) -> GreenTest | None:
        return next((g for g in green if g.matches(name)), None)

    fixtures = [c for c in run.cases if is_fixture_check(c.name)]
    listed = [c for c in run.cases if not is_fixture_check(c.name) and by_design(c.name)]
    cases = [c for c in run.cases if c not in fixtures and c not in listed]
    passed = [c.name for c in cases if c.outcome == "passed"]
    designed = [f"{c.name} — {g.reason}" for c in listed if (g := by_design(c.name))]
    red_listed = [c.name for c in listed if c.outcome == "failed"]
    unseen = [g.name for g in green if not any(g.matches(c.name) for c in run.cases)]
    alone = only_green_ids(adapter, ids, green)
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
    lines += [f"PASSES AGAINST STUB (weak or wrong test): {n}" for n in passed]
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
    lines += [f"SKIPPED: {n}" for n in skipped]
    lines += [f"FAILS FOR ANOTHER REASON THAN THE STUB: {n}" for n in other]
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
