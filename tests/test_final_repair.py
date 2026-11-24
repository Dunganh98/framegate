"""Final gates: mutation is skipped when the build or the tests are red, and a failed final
gate gets one repair run per owner (implementer / test writer) before the human sees it."""

from __future__ import annotations

import pytest

from conftest import DemoRepo, git, marker
from framegate import gates
from framegate.adapters import get_adapter
from framegate.adapters.base import Check, CheckResult, LanguageAdapter, TestScope
from framegate.checks import mutation
from framegate.config import load_config
from framegate.defaults import takes_default
from framegate.gates import red_blockers
from framegate.orchestrator import EXIT_GATE, Orchestrator

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
#: an older test the step gate does not find (it does not name the function it calls)
LEGACY = """\
import pytest

import demo


def test_legacy_bool_rejected() -> None:
    plus = getattr(demo, "a" + "dd")
    with pytest.raises(TypeError):
        plus(True, 1)
"""
NAMED = """\
import pytest

from demo import add


def test_named_bool_rejected() -> None:
    with pytest.raises(TypeError):
        add(True, 1)
"""


def to_final_gate(r: DemoRepo) -> None:
    r.add_toml(FAST)
    r.defer_tests_gate()
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.progress()["waiting"]["gate"] == "final"


def final_gates(r: DemoRepo) -> list[dict]:
    return [e for e in r.events() if e["kind"] == "final_gates"]


def repairs(r: DemoRepo) -> list[dict]:
    return [e for e in r.events() if e["kind"] == "final_repair"]


def test_red_blockers_name_build_and_test_failures() -> None:
    failed = ["lint", "typecheck", "regression: whole suite, no coverage", "format"]
    assert red_blockers(failed) == ["typecheck", "regression: whole suite, no coverage"]
    assert red_blockers(["lint", "diff coverage >= 90% (feature tests)"]) == []
    assert Check("x", lambda: CheckResult("PASS", "")).needs_green is False


def test_weak_tests_are_strengthened_before_the_human_sees_the_gate(demo_repo: DemoRepo) -> None:
    """Diff coverage fails: the test writer adds a test (reviewed, committed, tagged), the final
    gates run again and pass; the human gets a green final gate."""
    r = demo_repo
    r.scenario(uncovered=True)
    to_final_gate(r)
    runs = final_gates(r)
    assert [e["passed"] for e in runs] == [False, True]
    assert any(n.startswith("diff coverage") for n in runs[0]["failed"])
    rep = repairs(r)
    assert [(e["owner"], e["changed"]) for e in rep] == [("tests", True)]
    phases = [c["phase"] for c in r.calls()]
    assert phases == [
        "TESTS",
        "TEST_REVIEW",
        "IMPLEMENT",
        "REVIEW",
        "TESTS",
        "TEST_REVIEW",
        "SUMMARY",
    ]
    strengthen = r.calls()[4]["prompt"]
    assert "found the tests too weak" in strengthen and "diff coverage" in strengthen
    assert "The failing final checks:" in strengthen
    assert "test_add_special_value" in (r.root / "tests/test_demo.py").read_text("utf-8")
    assert marker(r.root, "tests-update/demo")
    assert "tests strengthened (final gates)" in git(r.root, "log", "--format=%s")
    assert "Final gates PASSED" in r.progress()["waiting"]["message"]


def test_a_test_writer_that_changes_nothing_leaves_the_failure_to_the_human(
    demo_repo: DemoRepo,
) -> None:
    r = demo_repo
    r.scenario(uncovered="stubborn")
    to_final_gate(r)
    assert [e["passed"] for e in final_gates(r)] == [False]  # no second run for nothing
    assert [(e["owner"], e["changed"]) for e in repairs(r)] == [("tests", False)]
    assert "Final gates FAILED" in r.progress()["waiting"]["message"]


def test_final_repair_can_be_switched_off(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.set_toml("gates", "final_repair", "false")
    r.scenario(uncovered=True)
    to_final_gate(r)
    assert [e["passed"] for e in final_gates(r)] == [False]
    assert repairs(r) == []


def test_a_broken_existing_test_goes_to_the_implementer(demo_repo: DemoRepo) -> None:
    """The feature's own tests pass, but the whole-suite regression finds an older test the
    new code breaks: the implementer fixes the code, the gates run again and pass."""
    r = demo_repo
    (r.root / "tests/test_legacy.py").write_text(LEGACY, encoding="utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "legacy test")
    to_final_gate(r)
    runs = final_gates(r)
    assert [e["passed"] for e in runs] == [False, True]
    assert any(n.startswith("regression") for n in runs[0]["failed"])
    assert [(e["owner"], e["changed"]) for e in repairs(r)] == [("src", True)]
    fix = [c for c in r.calls() if "the final gates failed these checks" in c["prompt"]]
    assert len(fix) == 1 and fix[0]["phase"] == "IMPLEMENT"
    assert "test_legacy_bool_rejected" in fix[0]["prompt"]
    assert "type(a) is not int" in (r.root / "src/demo/__init__.py").read_text("utf-8")
    runs_ev = [e for e in r.events() if e["kind"] == "agent_run" and e["phase"] == "IMPLEMENT"]
    assert runs_ev[-1]["step"] == "final gate fixes" and runs_ev[-1]["attempt"] == 1


def test_the_step_gate_runs_the_existing_tests_that_name_the_changed_code(
    demo_repo: DemoRepo,
) -> None:
    """Seen live: three existing tests of a changed function failed first at the final
    regression (its fix gate: the whole suite, 464 s)."""
    r = demo_repo
    (r.root / "tests/test_named.py").write_text(NAMED, encoding="utf-8")
    (r.root / "tests/test_other.py").write_text("def test_x() -> None:\n    assert 1\n", "utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "older tests")
    git(r.root, "branch", "-f", "main", "HEAD")  # they were there before the task
    to_final_gate(r)
    steps = [e for e in r.events() if e["kind"] == "gate"]
    assert [(e["passed"], e["failed"]) for e in steps] == [
        (False, [gates.EXISTING_TESTS]),
        (True, []),
    ]
    again = [c["prompt"] for c in r.calls() if c["phase"] == "IMPLEMENT"][1]
    assert (
        "1 existing test file(s) mention what the task changed (add): tests/test_named.py" in again
    )
    assert "test_named_bool_rejected" in again and "Fix the code, not the tests" in again
    assert [e["passed"] for e in final_gates(r)] == [True] and repairs(r) == []


def test_at_most_ten_existing_test_files_run_and_the_rest_is_named(demo_repo: DemoRepo) -> None:
    r = demo_repo
    for i in range(12):
        body = "from demo import add\n\n\ndef test_x() -> None:\n    assert add\n"
        (r.root / f"tests/test_old_{i:02}.py").write_text(body + "# add\n" * i, "utf-8")
    git(r.root, "add", "tests")
    git(r.root, "commit", "-q", "-m", "older tests")
    git(r.root, "branch", "-f", "main", "HEAD")  # they were there before the task
    assert r.af("approve-spec", "demo") == 0
    (r.root / "tests/test_demo.py").write_text("from demo import add\n", encoding="utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "tests")
    git(r.root, "tag", "tests-approved/demo")
    src = r.root / "src/demo/__init__.py"
    src.write_text(src.read_text("utf-8").replace("raise NotImplementedError", "return a + b"))
    adapter = get_adapter(load_config(r.root))
    files, left, names = gates.existing_test_files(adapter, "demo")
    assert names == ["add"] and "tests/test_demo.py" not in files + left  # the task's own file
    assert len(files) == gates.EXISTING_FILES
    assert left == ["tests/test_old_10.py", "tests/test_old_11.py"]
    res = gates.existing_tests(adapter, "demo", (files, left, names), [])
    assert res.status == "PASS", res.output
    assert "Left out (2 more file(s), at most 10 run here; the final regression runs" in res.output


def test_a_fail_without_a_score_is_a_tool_failure(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = get_adapter(load_config(demo_repo.root))
    git(demo_repo.root, "tag", "tests-approved/demo")
    results = {
        "crash": CheckResult("FAIL", "Stryker failed twice for Core.csproj (exit 1)"),
        "cut": CheckResult("FAIL", "x", summary="ERROR: mutation run incomplete (timed out)"),
        "weak": CheckResult(
            "FAIL", "x", summary="Mutation score 40.0% (2/5 killed), threshold 60%"
        ),
        "fine": CheckResult(
            "PASS", "x", summary="Mutation score 80.0% (4/5 killed), threshold 60%"
        ),
        "off": CheckResult("SKIPPED", "SKIPPED: mutmut is not installed"),
    }
    for name, res in results.items():
        monkeypatch.setattr(type(adapter), "mutation_check", lambda *a, _r=res, **k: _r)
        assert mutation.check(adapter, "demo").tool_failure is (name in ("crash", "cut")), name


def test_a_tool_failure_is_asked_not_repaired(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seen live: Stryker could not build its own copy once. That says nothing about the tests,
    so no test writer run: the final gate asks whether to run the checks again."""
    r = demo_repo
    broken = {"now": True}

    def fake(adapter: LanguageAdapter, feature: str, scope: TestScope | None = None) -> CheckResult:
        if broken["now"]:
            return CheckResult("FAIL", "Stryker failed twice (exit 1)", tool_failure=True)
        return CheckResult("PASS", "", summary="Mutation score 100.0% (2/2 killed)")

    monkeypatch.setattr(gates.mutation, "check", fake)
    to_final_gate(r)
    run = final_gates(r)[-1]
    # no result is no verdict: NOT MEASURED, never FAIL
    assert run["passed"] is True and run["failed"] == []
    (name,) = run["tool_failures"]
    assert name.startswith("mutation score") and run["not_measured"] == [name]
    assert run["results"][name].startswith("NOT MEASURED: TOOL FAILURE: the tool gave no result")
    assert repairs(r) == []
    assert [c["phase"] for c in r.calls()].count("TESTS") == 1  # no strengthening run
    report = (r.root / "out/demo/final_gates.md").read_text(encoding="utf-8")
    assert f"[NOT MEASURED] {name}\n  TOOL FAILURE: the tool gave no result" in report
    assert "Stryker failed twice (exit 1)" in report
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert f"final gates: PASSED; not measured: {name} [tool failure]" in log

    cfg = load_config(r.root)
    orch = Orchestrator(cfg, get_adapter(cfg), echo=lambda s: None)
    items = orch.final_items("demo", r.progress())
    asked = [i for i, x in enumerate(items, 1) if x.startswith(f"[final gates] {name}: the tool")]
    first = items[asked[0] - 1]
    assert len(asked) == 1 and "A: finish without this check (approve as is) (default)" in first
    assert takes_default(first, "ok") and not takes_default(first, "Yes, run them again.")
    assert orch.must_ask("demo", r.progress()) == set(asked)  # the brief cannot leave it out

    def run_again() -> list[str]:
        (r.root / "out/demo/answers.md").write_text(
            f"{asked[0]}. B: yes, run them again.\n", encoding="utf-8"
        )
        assert r.af("answer", "demo") == 0
        assert r.af("run", "demo") == EXIT_GATE
        return orch.final_items("demo", r.progress())

    # seen live: "run again" a second time, with the same result: the tool needs the setup
    items = run_again()
    asked = [i for i, x in enumerate(items, 1) if f"{name}: the tool gave no result again" in x]
    assert (
        len(asked) == 1
        and "finish without this check (approve as is) (default)" in items[asked[0] - 1]
    )
    assert "/framegate:setup" in items[asked[0] - 1]
    again = items[asked[0] - 1]
    assert takes_default(again, "Default") and takes_default(again, "finish without the check")
    assert not takes_default(again, "B")

    broken["now"] = False
    run_again()
    assert final_gates(r)[-1]["passed"] is True and final_gates(r)[-1]["tool_failures"] == []
    assert not orch.open_questions("demo")
