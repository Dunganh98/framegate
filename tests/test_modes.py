"""Modes: a bug fix starts with tests that fail on today's code, a refactor with tests that pass
on it (design doc 9.5). The feature flow is the default and unchanged."""

from __future__ import annotations

import shutil

import pytest

from agentflow.adapters import get_adapter
from agentflow.checks import baseline
from agentflow.config import load_config
from agentflow.gates import GateReport
from agentflow.orchestrator import EXIT_GATE, IDLE_STEP, Orchestrator
from agentflow.scaffold import spec_brief
from agentflow.specs import feature_mode
from conftest import DESIGN, PLAN, REQUIREMENTS, DemoRepo, git
from fake_claude import LOOSE, WRONG

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"

BUG_REPORT = (
    REQUIREMENTS.replace("Feature code: DEMO\n", "Feature code: DEMO\nMode: bugfix\n")
    + """
## Must not change
| ID | Behavior to keep |
|---|---|
| DEMO-I-001 | add(0, 0) is 0 |
"""
)
BUG_PLAN = PLAN.replace("DEMO-R-001, DEMO-E-001", "DEMO-R-001, DEMO-E-001, DEMO-I-001")
REFACTOR = (
    REQUIREMENTS.replace("Feature code: DEMO\n", "Feature code: DEMO\nMode: refactor\n")
    + """
## Scope
| File / type | What happens to it |
|---|---|
| `src/demo/__init__.py` | tidied |
"""
)


def start(r: DemoRepo, mode: str, requirements: str, plan: str, code: str) -> None:
    """The demo spec again, as a `mode` task on code that already exists."""
    shutil.rmtree(r.root / "specs" / "demo")
    assert r.af("spec", "demo", "--mode", mode) == 0
    spec = r.root / "specs" / "demo"
    assert f"\nMode: {mode}\n" in (spec / "requirements.md").read_text(encoding="utf-8")
    (spec / "requirements.md").write_text(requirements, encoding="utf-8")
    (spec / "design.md").write_text(DESIGN, encoding="utf-8")
    (spec / "plan.md").write_text(plan, encoding="utf-8")
    (r.root / "src/demo/__init__.py").write_text(code, encoding="utf-8")
    r.add_toml(FAST)
    r.defer_tests_gate()


def read(r: DemoRepo, rel: str) -> str:
    return (r.root / rel).read_text(encoding="utf-8")


def prompts(r: DemoRepo, phase: str) -> list[str]:
    return [c["prompt"] for c in r.calls() if c["phase"] == phase]


def test_spec_templates_per_mode(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    assert feature_mode(cfg, "demo") == "feature"
    # the demo spec is a feature spec: it cannot be continued as something else
    assert r.af("spec", "demo", "--mode", "bugfix") == 1
    assert "is a feature spec" in capsys.readouterr().err
    shutil.rmtree(r.root / "specs" / "demo")
    assert r.af("spec", "demo", "--mode", "refactor") == 0
    assert feature_mode(cfg, "demo") == "refactor"
    text = read(r, "specs/demo/requirements.md")
    assert "## Behavior to preserve" in text and "## Structure targets" in text
    assert "## Existing tests of the code in scope" in text
    assert "every step is checked\n     with ALL" in read(r, "specs/demo/plan.md")
    assert r.af("spec", "demo") == 0  # no --mode: what the spec says
    brief = spec_brief(cfg, "demo")
    assert "- Mode: refactor." in brief and "Interface stubs go in" not in brief
    assert "templates in specs/_template_refactor/" in brief
    started = [e for e in r.events() if e["kind"] == "spec_started"]
    assert started[-1]["mode"] == "refactor"
    # an unknown mode word is refused at approval, not silently run as a feature
    (r.root / "specs/demo/requirements.md").write_text(
        REQUIREMENTS.replace("Feature code: DEMO\n", "Feature code: DEMO\nMode: hotfix\n"),
        encoding="utf-8",
    )
    assert r.af("approve-spec", "demo") == 1
    assert "known modes: feature, bugfix, refactor" in capsys.readouterr().err


def test_bugfix_tests_must_fail_on_todays_code(demo_repo: DemoRepo) -> None:
    """The first test run does not show the bug (its reproduction test passes today): the
    check fails and the tests go back. The second shows it; the fix makes it pass; the final
    facts name the cause."""
    r = demo_repo
    start(r, "bugfix", BUG_REPORT, BUG_PLAN, WRONG)
    r.scenario(mode="bugfix", bug_not_shown=True)
    assert r.af("approve-spec", "demo") == 0
    assert [e for e in r.events() if e["kind"] == "spec_approved"][-1]["mode"] == "bugfix"
    assert r.af("run", "demo") == EXIT_GATE
    assert r.progress()["waiting"]["gate"] == "final"

    reviews = [e for e in r.events() if e["kind"] == "test_review"]
    assert reviews[0]["failed_checks"] == ["Bug reproduced on today's code"]
    assert reviews[0]["sent_back"] is True and reviews[-1]["failed_checks"] == []
    checks = read(r, "out/demo/test_checks.md")
    assert "### Bug reproduced on today's code: PASS" in checks
    assert "2 reproduction test(s) for DEMO-E-001, DEMO-R-001." in checks
    assert "1 must-not-change test(s) for DEMO-I-001." in checks
    assert "fails today: " in checks and "test_add_returns_sum" in checks
    assert "Existing members asserted (note, not blocking): 0" in checks

    tests = prompts(r, "TESTS")
    assert tests[0].startswith("Bug fix 'demo': the code exists, there are no stubs.")
    assert "interface stubs in" not in tests[0]
    assert "This is a bug fix: the tests ran against today's code" in prompts(r, "TEST_REVIEW")[0]
    assert "- ROOT CAUSE: <what, where (file: method)>" in prompts(r, "IMPLEMENT")[0]
    assert "This is a bug fix: check that the cause is fixed" in prompts(r, "REVIEW")[0]

    final = [e for e in r.events() if e["kind"] == "final_gates"][-1]
    assert final["passed"] is True
    facts = read(r, "out/demo/final_facts.md")
    assert "## Mode: bug fix" in facts
    assert "- ROOT CAUSE: add() subtracted its arguments (src/demo/__init__.py: add)" in facts
    assert "- RULED OUT: swapped arguments: add(3, 2) was wrong too" in facts


def test_bugfix_check_names_what_is_wrong(demo_repo: DemoRepo) -> None:
    r = demo_repo
    start(r, "bugfix", BUG_REPORT, BUG_PLAN, WRONG)
    adapter = get_adapter(load_config(r.root))
    (r.root / "tests/test_demo.py").write_text(
        "import pytest\n\nfrom demo import add\n\n\n"
        '@pytest.mark.req("DEMO-R-001")\ndef test_passes_today() -> None:\n'
        "    assert add(0, 0) == 0\n\n\n"
        '@pytest.mark.req("DEMO-E-001")\ndef test_errors() -> None:\n'
        "    assert undefined_name == 1  # noqa: F821\n\n\n"
        '@pytest.mark.req("DEMO-I-001")\ndef test_keep_fails_today() -> None:\n'
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    res = baseline.check(adapter, "demo")
    assert res.status == "FAIL"
    assert "PASSES TODAY (does not show the bug): " in res.output
    assert "FAILS TODAY (a must-not-change test has to pass before the fix): " in res.output
    # a NameError inside the test body is a failure in pytest's report: listed with its reason
    assert "fails today: " in res.output and "undefined_name" in res.output


def test_refactor_tests_stay_green_and_a_step_must_change_code(demo_repo: DemoRepo) -> None:
    r = demo_repo
    start(r, "refactor", REFACTOR, PLAN, LOOSE)
    r.scenario(mode="refactor", refactor_idle=1)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.progress()["waiting"]["gate"] == "final"

    checks = read(r, "out/demo/test_checks.md")
    assert "### Green on today's code: PASS" in checks
    assert "0 existing test(s) tagged with a spec ID of this refactor" in checks
    # before any code moves: which lines of the file in scope the tagged tests run
    assert "### Scope coverage (note, not blocking): 0 line(s) not run" in checks
    assert "src/demo/__init__.py: 100% (" in checks
    assert "'Scope coverage', lists the lines" in prompts(r, "TEST_REVIEW")[0]
    tests = prompts(r, "TESTS")[0]
    assert tests.startswith("Refactor 'demo': the code exists")
    assert "Existing tests first: search tests/ for the tests that already" in tests
    assert "Existing tests come first" in prompts(r, "TEST_REVIEW")[0]
    implement = prompts(r, "IMPLEMENT")
    assert len(implement) == 2 and "This is a refactor: move, split and rename" in implement[0]
    gates = [e for e in r.events() if e["kind"] == "gate"]
    # the first run changed nothing: green tests prove nothing, the attempt fails
    assert [(e["passed"], e["failed"]) for e in gates] == [(False, [IDLE_STEP]), (True, [])]
    assert "The step changed no source file." in implement[1]
    assert "# refactored: same behavior" in read(r, "src/demo/__init__.py")
    assert [e for e in r.events() if e["kind"] == "final_gates"][-1]["passed"] is True
    assert "## Mode: refactor" in read(r, "out/demo/final_facts.md")
    assert git(r.root, "log", "-1", "--format=%s", "--", "src").strip() == "demo: step 1 - add"


def test_refactor_has_no_test_repair(demo_repo: DemoRepo) -> None:
    """Tests written after the refactor would pin the changed code: weak tests go to the human."""
    r = demo_repo
    start(r, "refactor", REFACTOR, PLAN, LOOSE)
    cfg = load_config(r.root)
    lines: list[str] = []
    orch = Orchestrator(cfg, get_adapter(cfg), echo=lines.append)
    weak = "diff coverage >= 90% (feature tests)"
    rep = GateReport(False, "", [weak], outputs={weak: "Missing lines"})
    prog: dict = {"phase": "REVIEW"}
    assert orch.repair_final("demo", prog, rep) is False
    assert not r.calls() and "final_repair" not in prog
    assert any("no test repair in a refactor" in x for x in lines)


def test_feature_mode_is_unchanged(demo_repo: DemoRepo) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    assert baseline.name(feature_mode(cfg, "demo")) == "Red against stubs"
    orch = Orchestrator(cfg, get_adapter(cfg), echo=lambda s: None)
    task = orch.task_text("TESTS", "demo")
    assert task.startswith("Feature 'demo': write the tests from specs/demo/ and the interface")
    assert "This is a" not in orch.task_text("REVIEW", "demo")


def test_refactor_structure_fix_is_checked_like_a_step(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live (FCSim): a helper extraction after the review ran the whole suite in the agent, in
    its gate and again in the final gates. A refactor's structure fix uses the feature's tests;
    the whole suite runs once, at the end."""
    r = demo_repo
    cfg = load_config(r.root)
    seen: list[object] = []

    def loop(
        self: Orchestrator, feature: str, prog: dict, task: str, reqs: object, item: dict
    ) -> bool:
        seen.append(reqs)
        return True

    monkeypatch.setattr(Orchestrator, "implement_loop", loop)
    orch = Orchestrator(cfg, get_adapter(cfg), echo=lambda s: None)
    orch.fix_structure("demo", {"phase": "REVIEW", "structure_findings": ["split x"]})
    assert seen == [None]  # a feature: the whole suite, as before
    start(r, "refactor", REFACTOR, PLAN, LOOSE)
    orch.fix_structure("demo", {"phase": "REVIEW", "structure_findings": ["split x"]})
    assert seen[1] == ["DEMO-E-001", "DEMO-R-001"]
