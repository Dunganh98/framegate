"""The pipeline task (`framegate pipeline <name>`, pipeline.py): the separate step that changes
deploy / CI files. Here without `[git] state_branch` (framegate's files travel with the code):
the guard phase, the mechanical check, the approval and the branch the work is done on. With a
state branch, and how the change is published: tests/test_publish.py."""

from __future__ import annotations

import json
import pathlib

import pytest

from conftest import FAKE_CLAUDE, GITIGNORE, PYPROJECT, REQUIREMENTS, git, init_git
from framegate import cli, followup, pipeline
from framegate.config import load_config, parse_config
from framegate.hooks import protect_paths

ROOT = pathlib.Path(__file__).resolve().parents[1]
CI = "jobs:\n  unit:\n    steps:\n      - run: pytest tests/test_a.py\n"
LIST = """\
# Pipeline follow-up: {name}

- [ ] ci/tests.yml: add the new test file (CI does not select it)
- [ ] `deploy/Dockerfile`: COPY the new folder (the image lacks it) (from the spec)
"""


def write(root: pathlib.Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A set-up repository on `main`: source, a test, a CI file and a deploy file; `ci/` and
    `deploy/` are in [protected] extra."""
    root = tmp_path / "repo"
    root.mkdir()
    init_git(root)
    write(root, "pyproject.toml", PYPROJECT)
    write(root, ".gitignore", GITIGNORE)
    write(root, "src/demo/__init__.py", '"""Demo."""\n')
    write(root, "tests/test_a.py", "def test_a() -> None:\n    assert True\n")
    write(root, "ci/tests.yml", CI)
    write(root, "deploy/Dockerfile", "FROM python:3.12\nCOPY src/demo /app/demo\n")
    monkeypatch.chdir(root)
    monkeypatch.setenv("FRAMEGATE_CLAUDE", str(FAKE_CLAUDE))
    assert cli.main(["init"]) == 0
    toml = root / "framegate.toml"
    text = toml.read_text(encoding="utf-8")
    assert "[protected]\n" in text
    text = text.replace("[protected]\n", '[protected]\nextra = ["ci/", "deploy/", "pytest.ini"]\n')
    toml.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def af(*args: str) -> int:
    return cli.main(list(args))


def guard(root: pathlib.Path, rel: str) -> tuple[int, str]:
    hook = {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": str(root / rel)}}
    return protect_paths.evaluate(json.dumps(hook))


def branch(root: pathlib.Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


# ---------- the kept list ----------
def test_the_list_is_read_ticked_and_left_open(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    write(tmp_path, "specs/x/pipeline_followup.md", LIST.format(name="x"))
    one, two = followup.read_list(cfg, "x")
    assert (one.file, one.ticked, one.reason) == ("ci/tests.yml", False, "")
    assert two.file == "deploy/Dockerfile" and two.text.endswith("(from the spec)")
    assert one.covers("ci/tests.yml") and not one.covers("ci/tests.yml.bak")
    assert followup.open_count(cfg, "x") == 2
    path = followup.list_path(cfg, "x")
    text = path.read_text(encoding="utf-8")
    text += "- [ ] ci/: every job gets a timeout (left open: waits for the new agents)\n"
    text += "- [ ] the image needs more memory\n"
    path.write_text(text, encoding="utf-8")
    assert followup.tick(cfg, "x", {"ci/tests.yml", "ci/other.yml"}) == 1
    items = followup.read_list(cfg, "x")
    assert [it.ticked for it in items] == [True, False, False, False]
    assert items[2].reason == "waits for the new agents" and items[2].covers("ci/other.yml")
    assert items[3].file == ""  # no file named: it can only be left open
    assert followup.open_count(cfg, "x") == 3
    assert "- [x] ci/tests.yml: add the new test file" in path.read_text(encoding="utf-8")
    assert followup.read_list(cfg, "none") == [] and followup.open_count(cfg, "none") == 0


# ---------- a task of its own: the guard, the check, the approval ----------
def test_a_pipeline_task_of_its_own(repo: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert af("pipeline", "infra") == 0
    out = capsys.readouterr().out
    assert "Created the pipeline branch pipeline/infra from main" in out
    assert "Pipeline task infra (on its own: no task of this name), branch pipeline/infra." in out
    assert "deploy / CI files (.github/, .gitlab-ci.yml, azure-pipelines.yml, ci/, deploy/" in out
    assert "No list yet: write it to specs/infra/pipeline_followup.md" in out
    assert (repo / ".phase").read_text(encoding="utf-8") == "PIPELINE infra"
    assert af("status") == 0  # a phase of the chat's own: no stale-lock warning
    assert "stale" not in capsys.readouterr().out

    # --- the guard: deploy / CI files on the list, the list and the brief; nothing else ---
    code, msg = guard(repo, "ci/tests.yml")
    assert code == 2 and "not on the list (specs/infra/pipeline_followup.md)" in msg
    assert guard(repo, "specs/infra/pipeline_followup.md")[0] == 0
    write(repo, "specs/infra/pipeline_followup.md", LIST.format(name="infra"))
    allowed = ("ci/tests.yml", "deploy/Dockerfile", "out/infra/pipeline_brief.md")
    assert [guard(repo, rel)[0] for rel in allowed] == [0, 0, 0]
    locked = (
        *("src/demo/__init__.py", "tests/test_a.py", "pyproject.toml", "pytest.ini"),
        *("framegate.toml", "specs/infra/requirements.md", "specs/other/pipeline_followup.md"),
        *("CLAUDE.md", ".framegate/project.md", "README.md", "out/infra/answers.md"),
    )
    for rel in locked:
        code, msg = guard(repo, rel)
        assert code == 2 and "a pipeline task changes deploy / CI files only" in msg, rel
    for rel in ("ci/other.yml", ".github/workflows/ci.yml", "deploy/k8s/app.yml"):
        code, msg = guard(repo, rel)
        assert code == 2 and "but not on the list" in msg, rel

    # --- the check: nothing done yet ---
    assert af("pipeline-check", "infra") == 1
    out = capsys.readouterr().out
    assert "Pipeline check infra: 2 problem(s); 0 deploy / CI file(s) changed" in out
    assert "- OPEN ITEM 1: its file did not change" in out and "- OPEN ITEM 2:" in out

    # --- the work, with what must not be in it ---
    write(repo, "ci/tests.yml", CI + "      - run: pytest tests/test_new.py src/demo\n")
    write(repo, "ci/other.yml", "a: 1\n")
    write(repo, "src/demo/__init__.py", '"""Demo."""\nX = 1\n')
    write(repo, "notes.md", "the user's own file: not the task's business\n")
    assert af("pipeline-check", "infra") == 1
    out = capsys.readouterr().out
    assert "- NOT A DEPLOY / CI FILE: src/demo/__init__.py changed" in out
    assert "- NOT ON THE LIST: ci/other.yml changed" in out
    assert "1. done (ci/tests.yml): ci/tests.yml: add the new test file" in out
    assert "NOTE: ci/tests.yml names path(s) that do not exist here: tests/test_new.py" in out
    assert "notes.md" not in out and "src/demo (" not in out
    assert af("pipeline-approve", "infra") == 1  # refused while a problem is open
    assert "Not approved" in capsys.readouterr().out
    assert (repo / ".phase").is_file()

    git(repo, "checkout", "--", "src/demo/__init__.py")
    (repo / "ci/other.yml").unlink()
    listed = repo / "specs/infra/pipeline_followup.md"
    text = listed.read_text(encoding="utf-8").replace(
        "(from the spec)", "(from the spec) (left open: the image is rebuilt next sprint)"
    )
    listed.write_text(text + "- [ ] ci/config.json: add the job\n", encoding="utf-8")
    write(repo, "ci/config.json", '{"jobs": [}\n')
    write(repo, "ci/tests.yml", CI.replace("    steps", "\tsteps"))
    assert af("pipeline-check", "infra") == 1
    out = capsys.readouterr().out
    assert "- DOES NOT PARSE: ci/config.json" in out and "- DOES NOT PARSE: ci/tests.yml" in out
    assert "2. left open (the image is rebuilt next sprint): `deploy/Dockerfile`" in out
    assert "NOTE: yml files: no YAML parser is installed" in out or pipeline.yaml_parser()
    write(repo, "ci/config.json", '{"jobs": []}\n')
    write(repo, "ci/tests.yml", CI + "      - run: pytest tests/test_a.py -q\n")
    write(repo, "out/infra/pipeline_brief.md", "**What was asked**\n")
    assert af("pipeline-check", "infra") == 0
    assert "Pipeline check infra: OK; 2 deploy / CI file(s) changed" in capsys.readouterr().out

    # --- the user's OK ---
    assert af("pipeline-approve", "infra") == 0
    out = capsys.readouterr().out
    assert "Pipeline task infra approved: 2 item(s) ticked, 1 left open; 2 file(s) committed" in out
    assert "Next: push pipeline/infra and open its own pull request into main" in out
    assert not (repo / ".phase").exists()
    committed = set(git(repo, "show", "--name-only", "--format=", "HEAD").split())
    assert committed == {"ci/config.json", "ci/tests.yml", "specs/infra/pipeline_followup.md"}
    assert git(repo, "log", "-1", "--format=%s").strip() == "infra: deploy / CI changes"
    assert git(repo, "status", "--porcelain").split() == ["??", "notes.md"]
    assert [it.ticked for it in followup.read_list(load_config(repo), "infra")] == [
        *(True, False, True)
    ]
    assert guard(repo, "src/demo/__init__.py")[0] == 0  # the phase is over
    assert af("pipeline-check", "infra") == 1
    assert "No pipeline task 'infra' is open here" in capsys.readouterr().err
    # what is left open stays visible
    assert af("status") == 0
    line = "- infra: pipeline follow-up: 1 open item(s) -> /framegate:pipeline infra"
    assert line in capsys.readouterr().out
    assert af("status", "infra", "--brief") == 0
    out = capsys.readouterr().out
    assert "infra: a pipeline task of its own" in out
    assert "Pipeline follow-up: 1 open item(s) -> /framegate:pipeline infra" in out
    assert af("cleanup", "infra") == 0  # only the brief is there: nothing of a run to keep
    assert not (repo / "out/infra").exists() and (repo / "specs/infra").is_dir()


# ---------- after a task: its branch while it is not merged, else a branch of its own ----------
def test_the_branch_follows_the_parent_task(
    repo: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(repo, "checkout", "-q", "-b", "feat/demo")
    write(repo, "specs/demo/requirements.md", REQUIREMENTS)
    write(repo, "src/demo/__init__.py", '"""Demo."""\nX = 1\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "demo: step 1")
    git(repo, "checkout", "-q", "main")
    assert af("pipeline", "demo") == 1  # the list is written at DONE
    assert "demo is a task in progress (not DONE)" in capsys.readouterr().err
    assert branch(repo) == "main" and not (repo / ".phase").exists()  # back where it started
    git(repo, "checkout", "-q", "feat/demo")
    write(repo, "specs/demo/pipeline_followup.md", LIST.format(name="demo"))
    write(repo, "metrics/workflow_metrics.csv", "date,feature\n2026-01-01,demo\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "demo: workflow metrics")
    git(repo, "checkout", "-q", "main")

    # not merged: the task's own branch, one more commit on it
    assert af("pipeline", "demo") == 0
    out = capsys.readouterr().out
    assert "Pipeline task demo (after the task demo), branch feat/demo." in out
    assert "List (specs/demo/pipeline_followup.md): 2 open of 2 item(s)" in out
    assert af("spec", "other") == 1  # no second phase in this checkout meanwhile
    assert af("pipeline", "other") == 1
    assert "Another phase is open in this checkout (PIPELINE demo)" in capsys.readouterr().err
    write(repo, "ci/tests.yml", CI + "      - run: pytest tests -q\n")
    listed = repo / "specs/demo/pipeline_followup.md"
    text = listed.read_text(encoding="utf-8").rstrip("\n")
    listed.write_text(text + " (left open: next sprint)\n", encoding="utf-8")
    assert af("pipeline", "demo") == 0  # the chat came back: continued, same start
    assert ", continued." in capsys.readouterr().out
    assert af("pipeline-approve", "demo") == 0
    out = capsys.readouterr().out
    assert "1 file(s) committed on feat/demo" in out and "one more commit on that branch" in out

    # merged (a squash): the rest of the list gets a branch of its own from the base
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--squash", "feat/demo")
    git(repo, "commit", "-q", "-m", "demo (#1)")
    assert af("pipeline", "demo") == 0
    out = capsys.readouterr().out
    assert "Created the pipeline branch pipeline/demo from main" in out
    assert "List (specs/demo/pipeline_followup.md): 1 open of 2 item(s)" in out
    assert "1. ci/tests.yml: add the new test file (CI does not select it)  [done earlier]" in out
    assert "[left open: next sprint]" in out
    assert git(repo, "merge-base", "HEAD", "main").strip() == git(repo, "rev-parse", "main").strip()
    assert af("unlock") == 0  # given up: the phase is cleared like any other
    assert not (repo / ".phase").exists()


# ---------- the other flows lead here ----------
def test_every_mode_keeps_the_list_and_offers_the_pipeline_task() -> None:
    for name in ("feature", "bugfix", "refactor"):
        skill = (ROOT / f"plugin/skills/{name}/SKILL.md").read_text(encoding="utf-8")
        assert 'under "Pipeline follow-up" in requirements.md' in skill, name
        assert "**Pipeline follow-up** — deploy / CI changes this task makes necessary" in skill
    for folder in ("_template", "_template_bugfix", "_template_refactor"):
        path = ROOT / "src/framegate/templates/specs" / folder / "requirements.md"
        assert "## Pipeline follow-up" in path.read_text(encoding="utf-8"), folder
    protocol = (ROOT / "src/framegate/templates/chat_protocol.md").read_text(encoding="utf-8")
    assert "Do the pipeline follow-up now? (`/framegate:pipeline <f>`)" in protocol
    status = (ROOT / "plugin/skills/status/SKILL.md").read_text(encoding="utf-8")
    assert "/framegate:pipeline <f>" in status
    skill = (ROOT / "plugin/skills/pipeline/SKILL.md").read_text(encoding="utf-8")
    for command in ("framegate pipeline $ARGUMENTS", "framegate pipeline-check $ARGUMENTS"):
        assert command in skill
    assert "framegate pipeline-approve $ARGUMENTS" in skill
    assert "the pull request's CI run is the proof" in skill
