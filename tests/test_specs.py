from __future__ import annotations

import pathlib

from agentflow.config import parse_config
from agentflow.specs import PlanStep, all_spec_ids, feature_ids, parse_plan, plan_steps


def test_parse_plan() -> None:
    text = """# X — Implementation plan

## Step 1: parse input
Covers: HI2-R-001, HI2-E-001
Build the parser.

## Step 2:   window stats
Covers: HI2-R-002,HI2-I-001 ,
More text. Covers: not-a-line-start

## Step 10: no covers line
Nothing.
"""
    assert parse_plan(text) == [
        PlanStep(1, "parse input", ["HI2-R-001", "HI2-E-001"]),
        PlanStep(2, "window stats", ["HI2-R-002", "HI2-I-001"]),
        PlanStep(10, "no covers line", []),
    ]


def test_parse_plan_without_steps() -> None:
    assert parse_plan("# Plan\n\n### Step 1: wrong level\nCovers: A-R-001\n") == []


def test_feature_and_all_ids(tmp_path: pathlib.Path) -> None:
    specs = tmp_path / "specs"
    (specs / "_template").mkdir(parents=True)
    (specs / "_template" / "requirements.md").write_text("XXX-R-001", encoding="utf-8")
    (specs / "hi2").mkdir()
    (specs / "hi2" / "requirements.md").write_text(
        "| HI2-R-001 | x |\n| HI2-E-002 | y |\n| HI2-I-003 | z |\nnot an id: HI2-X-001, H-R-001",
        encoding="utf-8",
    )
    (specs / "other").mkdir()
    (specs / "other" / "requirements.md").write_text("OT-R-001", encoding="utf-8")
    cfg = parse_config(tmp_path, {})
    assert feature_ids(cfg, "hi2") == {"HI2-R-001", "HI2-E-002", "HI2-I-003"}
    assert all_spec_ids(cfg) == {"HI2-R-001", "HI2-E-002", "HI2-I-003", "OT-R-001"}
    assert feature_ids(cfg, "missing") == set()
    assert plan_steps(cfg, "hi2") == []
