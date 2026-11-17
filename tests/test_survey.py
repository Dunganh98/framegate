"""framegate survey: the agent's layout must account for every code file."""

from __future__ import annotations

import json
import pathlib

from conftest import init_git
from framegate import probe, survey
from framegate.adapters import get_adapter
from framegate.config import parse_config

LAYOUT = {
    "src": [{"path": "data", "evidence": "pyproject.toml"}],
    "tests": {"path": "tests", "evidence": "pytest.ini"},
    "excluded": [{"path": "deploy", "reason": "deploy scripts"}],
    "notes": [],
}


def test_every_code_file_must_be_in_a_folder_of_the_layout() -> None:
    files = ["data/a.py", "tests/test_a.py", "deploy/run.py", "platform/ingest/b.py", "main.py"]
    assert survey.uncovered(files, LAYOUT) == ["platform/ingest/b.py", "main.py"]
    assert survey.uncovered(files, {**LAYOUT, "src": [{"path": "."}]}) == []
    assert survey.folders(files) == [
        "data (1)",
        "deploy (1)",
        "main.py (1)",
        "platform/ingest (1)",
        "tests (1)",
    ]


def test_the_answer_is_the_last_json_block() -> None:
    text = 'I looked.\n```json\n{"src": []}\n```\nFinal:\n```json\n' + json.dumps(LAYOUT) + "\n```"
    assert survey.parse(text) == LAYOUT
    assert survey.parse("no json here") is None


def test_the_survey_runs_again_on_files_it_left_out_and_the_probe_reads_it(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    root = tmp_path / "repo"
    for f in ("data/a.py", "platform/ingest/b.py", "tests/test_a.py", "pyproject.toml"):
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("", encoding="utf-8")
    init_git(root)
    asked: list[str] = []

    def ask(cfg, adapter, prompt, resume=""):
        asked.append(prompt)
        layout = (
            LAYOUT
            if not resume
            else {**LAYOUT, "src": [*LAYOUT["src"], {"path": "platform/ingest"}]}
        )
        answer = f"```json\n{json.dumps(layout)}\n```"
        return {"result": answer, "session_id": "s1", "total_cost_usd": 0.2}, ""

    monkeypatch.setattr(survey, "ask", ask)
    cfg = parse_config(root, {"paths": {"src": ["data"]}})
    data = survey.survey(cfg, get_adapter(cfg), ["Language: python"])

    assert "platform/ingest (1)" in asked[0] and asked[1].endswith("platform/ingest/b.py")
    assert data["uncovered"] == [] and data["cost_usd"] == 0.4
    seen = probe.collect(root)
    assert [s["path"] for s in seen["src"]] == ["data", "platform/ingest"]
    assert seen["layout_source"] == "the survey, out/survey.json"
