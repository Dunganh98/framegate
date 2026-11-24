"""The simplify pass: one agent run between the last plan step and the review; kept as its
own commit when the checks stay green, dropped without a stop otherwise."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import DemoRepo, git
from framegate import simplify, stats
from framegate.config import ConfigError, load_config, parse_config
from framegate.orchestrator import EXIT_GATE, Orchestrator, package_text
from framegate.state import load_progress

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
IMPL = "src/demo/__init__.py"


def to_final_gate(r: DemoRepo, toml: str = "[simplify]\nmin_lines = 0\n", **scen: Any) -> None:
    r.add_toml(FAST + toml)
    r.defer_tests_gate()
    r.scenario(**scen)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.progress()["waiting"]["gate"] == "final"


def passes(r: DemoRepo) -> list[dict[str, Any]]:
    return [e for e in r.events() if e["kind"] == "simplify"]


def phases(r: DemoRepo) -> list[str]:
    return [c["phase"] for c in r.calls()]


def test_a_smaller_diff_with_green_checks_is_its_own_commit(demo_repo: DemoRepo) -> None:
    r = demo_repo
    to_final_gate(r, uncovered=True, simplify="smaller")
    assert phases(r) == ["TESTS", "TEST_REVIEW", "IMPLEMENT", "SIMPLIFY", "REVIEW", "SUMMARY"]
    (e,) = passes(r)
    assert e["status"] == "kept" and e["after"] < e["before"] and e["files"] == [IMPL]
    assert "12345" not in (r.root / IMPL).read_text("utf-8")
    log = git(r.root, "log", "--format=%s").splitlines()
    assert log.index("demo: simplify") < log.index("demo: step 1 - add")
    # the branch no test reached is gone: the final checks pass without a repair
    assert [x["passed"] for x in r.events() if x["kind"] == "final_gates"] == [True]
    facts = (r.root / "out/demo/final_facts.md").read_text("utf-8")
    assert f"(added + removed): {e['before']} before, {e['after']} after; its own commit." in facts

    call = r.calls()[3]
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--max-turns") + 1] == "40"
    assert argv[argv.index("--max-budget-usd") + 1] == "1"
    assert call["role"].startswith("You are the SIMPLIFIER")
    assert f"git diff tests-approved/demo -- {IMPL}" in call["prompt"]
    assert f"You may change only these files: {IMPL}\n" in call["prompt"]
    assert "## Conventions" in call["prompt"]

    lines: list[str] = []
    cfg = load_config(r.root)
    assert stats.run(cfg, "demo", echo=lines.append) == 0
    assert any(x.startswith("Simplify pass: kept: the task's source diff went") for x in lines)
    assert [x.split()[0] for x in lines if x.startswith("  ")][:4] == [
        "phase",
        "TESTS",
        "TEST_REVIEW",
        "IMPLEMENT",
    ]


def test_a_pass_that_breaks_a_check_is_dropped_and_the_run_goes_on(demo_repo: DemoRepo) -> None:
    r = demo_repo
    to_final_gate(r, simplify="break")
    (e,) = passes(r)
    assert e["status"] == "dropped" and any(
        n.startswith("tests (this feature)") for n in e["failed"]
    )
    assert "return a + b" in (r.root / IMPL).read_text("utf-8")
    assert "demo: simplify" not in git(r.root, "log", "--format=%s")
    assert git(r.root, "status", "--porcelain", "--", "src", "tests") == ""
    assert phases(r)[3:5] == ["SIMPLIFY", "REVIEW"]  # one run, no retry, then the review
    assert "Final gates PASSED" in r.progress()["waiting"]["message"]
    facts = (r.root / "out/demo/final_facts.md").read_text("utf-8")
    assert "## Simplify pass" in facts and "- Dropped: " in facts


def test_a_change_outside_the_tasks_source_files_drops_the_pass(demo_repo: DemoRepo) -> None:
    r = demo_repo
    to_final_gate(r, uncovered=True, simplify="outside")
    (e,) = passes(r)
    assert e["status"] == "dropped" and e["outside"] == ["tests/test_demo.py"]
    assert "# simplified" not in (r.root / "tests/test_demo.py").read_text("utf-8")
    assert "12345" in (r.root / IMPL).read_text("utf-8")  # the code as it was


def test_the_pass_never_asks_and_never_parks(demo_repo: DemoRepo) -> None:
    r = demo_repo
    to_final_gate(r, simplify="ask")
    (e,) = passes(r)
    assert e["status"] == "unchanged"
    assert not (r.root / "out/demo/park.md").exists()
    assert not (r.root / "out/demo/questions.md").exists()
    prog = r.progress()
    assert not prog["parked"] and "open question" not in prog["waiting"]["message"]
    assert not [x for x in r.events() if x["kind"] in ("questions", "parked")]

    # an interrupted pass: its half-done edits go, the code is the last commit's
    orch = Orchestrator(load_config(r.root), echo=lambda s: None)
    path = r.root / IMPL
    before = path.read_text("utf-8")
    path.write_text(before + "\n# half done\n", encoding="utf-8")
    state = load_progress(orch.cfg, "demo")
    state["simplify"] = {"status": "running", "dirty": []}
    simplify.run(orch, "demo", state)
    assert path.read_text("utf-8") == before and state["simplify"]["status"] == "dropped"
    assert len(r.calls()) == len(phases(r)) and phases(r).count("SIMPLIFY") == 1


def test_a_tiny_diff_gets_no_pass_by_default(demo_repo: DemoRepo) -> None:
    r = demo_repo
    to_final_gate(r, toml="", simplify="smaller")
    assert "SIMPLIFY" not in phases(r)
    (e,) = passes(r)
    assert e["status"] == "skipped" and "(fewer than 10)" in e["text"]
    assert "## Simplify pass" not in (r.root / "out/demo/final_facts.md").read_text("utf-8")


def test_the_switch_and_the_modes_without_a_pass(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = demo_repo
    cfg = load_config(r.root)
    assert cfg.simplify_enabled and cfg.simplify_min_lines == 10
    assert cfg.agent_model("SIMPLIFY") == "sonnet" and cfg.write_paths("SIMPLIFY") == ["src/"]
    with pytest.raises(ConfigError, match=r"\[simplify\] min_lines must be >= 0"):
        parse_config(r.root, {"simplify": {"min_lines": -1}})

    off = Orchestrator(parse_config(r.root, {"simplify": {"enabled": False}}), echo=lambda s: None)
    prog: dict[str, Any] = {}
    simplify.run(off, "demo", prog)
    assert prog == {}  # no event, no state

    orch = Orchestrator(cfg, echo=lambda s: None)
    assert "work waits for an answer" in simplify.skip_reason(orch, "demo", {"parked": [{}]})
    monkeypatch.setattr(simplify, "feature_mode", lambda c, f: "refactor")
    assert simplify.skip_reason(orch, "demo", {}).startswith("a refactor")


def test_role_and_facts_are_general() -> None:
    role = package_text("roles", "simplify.md")
    for needle in ("only the source files your task lists", "Do not ask, do not park"):
        assert needle in role
    assert simplify.facts_lines([{"kind": "simplify", "status": "skipped"}]) == []
    assert simplify.facts_lines([]) == []
    dropped = [{"kind": "simplify", "status": "dropped", "text": "dropped: lint failed after it"}]
    assert simplify.facts_lines(dropped)[-1] == "- Dropped: lint failed after it."
