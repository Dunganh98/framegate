"""The metrics row (out/metrics/workflow_metrics.csv): what each count column counts."""

from __future__ import annotations

from agentflow.orchestrator import metric_counts


def test_metric_counts_questions_parks_and_final_gate_failures() -> None:
    """M5-freekick-review's row said questions 1 and gate_failures 0: one questions event holds
    four questions, two parks asked the human, and two final-gate runs failed."""
    events = [
        {"kind": "questions", "questions": "- Q1?\n- Q2?\n- Q3?\n- Q4?\n"},
        {"kind": "parked", "label": "step 1", "question": "# Park\nTwo tests are wrong."},
        {"kind": "parked", "label": "step 2", "question": "", "reason": "waits for step 1"},
        {"kind": "parked", "label": "step 3", "question": "", "reason": "waits for step 2"},
        {"kind": "provisional", "label": "step 4", "question": "Test X is wrong."},
        {"kind": "final_gates", "passed": False},
        {"kind": "gate", "passed": True},
        {"kind": "parked", "label": "steps 2, 3", "question": "Is Goal the goal event?"},
        {"kind": "final_gates", "passed": False},
        {"kind": "gate", "passed": False},
        {"kind": "final_gates", "passed": True},
        {"kind": "human_gate", "approved": True},
        {"kind": "agent_run", "cost": 1.234},
        {"kind": "agent_run", "cost": 2.0},
    ]
    rejections, escalations, questions, gate_failures, cost, parked = metric_counts(events)
    assert (rejections, escalations) == (0, 0)
    assert questions == 4 + 2 + 1
    assert gate_failures == 2 + 1
    assert cost == 3.23 and parked == 4
