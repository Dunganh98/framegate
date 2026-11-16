"""The pipeline task (`framegate pipeline <name>`, pipeline.py): the separate step that changes
deploy / CI files. Here without `[git] state_branch` (framegate's files travel with the code):
the guard phase, the mechanical check, the approval and the branch the work is done on. With a
state branch, and how the change is published: tests/test_publish.py."""

from __future__ import annotations

import json
import pathlib

import pytest

from conftest import FAKE_CLAUDE, GITIGNORE, PYPROJECT, REQUIREMENTS, git, init_git
from framegate import cli, followup, pipeline, publish
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
    assert followup.tick(cfg, "x", {"ci/tests.yml", "ci/other.yml"}) == (1, 0)
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
    assert (
        "deploy / CI files (the built-in names: Dockerfile, compose files, Jenkinsfile, CI yml "
        "and folders; [protected] extra: ci/, deploy/, pytest.ini)" in out
    )
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
    assert "- DOES NOT PARSE: ci/config.json" in out and "- DOES NOT PARSE: ci/tests.yml:" in out
    assert "2. left open (the image is rebuilt next sprint): `deploy/Dockerfile`" in out
    assert "no YAML parser" not in out  # PyYAML is a dependency: the yml check always parses
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
    own_text = "specs/infra/pipeline_pr_description.md"  # the brief, kept for its pull request
    assert committed == {
        *("ci/config.json", "ci/tests.yml", "specs/infra/pipeline_followup.md", own_text)
    }
    assert f"(description: {own_text})" in out
    assert (repo / own_text).read_text(encoding="utf-8").startswith("**What was asked**\n")
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


# ---------- the yml check ----------
def test_changes_from_before_the_task_are_not_held_against_it(
    repo: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Seen live: a tool's config file and an edit of the user, both uncommitted before the
    task started, blocked the check. They stay out while they look as they did."""
    git(repo, "checkout", "-q", "-b", "pipeline/infra")
    write(repo, "src/demo/__init__.py", '"""Demo."""\nMINE = 1\n')
    write(repo, "framegate.toml", (repo / "framegate.toml").read_text("utf-8") + "\n# mine\n")
    assert af("pipeline", "infra") == 0
    out = capsys.readouterr().out
    assert "Changed before this task started (uncommitted; not part of it" in out
    assert "framegate.toml, src/demo/__init__.py. Ask the user once" in out
    write(repo, "specs/infra/pipeline_followup.md", "- [ ] ci/tests.yml: add a test (new)\n")
    write(repo, "ci/tests.yml", CI + "      - run: pytest tests/test_a.py -q\n")
    assert af("pipeline-check", "infra") == 0
    assert "Pipeline check infra: OK; 1 deploy / CI file(s) changed" in capsys.readouterr().out
    # a further change to one of them is the task's doing again
    write(repo, "src/demo/__init__.py", '"""Demo."""\nMINE = 2\n')
    assert af("pipeline-check", "infra") == 1
    out = capsys.readouterr().out
    assert "- NOT A DEPLOY / CI FILE: src/demo/__init__.py changed" in out
    assert "framegate.toml changed" not in out
    write(repo, "src/demo/__init__.py", '"""Demo."""\nMINE = 1\n')
    assert af("pipeline-approve", "infra") == 0
    capsys.readouterr()
    committed = set(git(repo, "show", "--name-only", "--format=", "HEAD").split())
    assert committed == {
        *("ci/tests.yml", "specs/infra/pipeline_followup.md"),
        "specs/infra/pipeline_pr_description.md",
    }
    # the user's changes are still there, uncommitted; the snapshot is gone
    assert git(repo, "status", "--porcelain", "-uno").split() == [
        *("M", "framegate.toml", "M", "src/demo/__init__.py")
    ]
    assert not (repo / ".git/framegate/pipeline-before/infra.json").exists()


def test_machine_files_are_never_the_pipeline_tasks_business(
    repo: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Seen live: a tool rewrites its own tracked config (`.serena/project.yml`). Uncommitted
    before the task, changed again during it, untracked tool folders next to it: the start
    switches the branch all the same, nothing lists or asks about them, the check and the
    approval leave them out, and the task may not write them."""
    write(repo, ".serena/project.yml", "name: demo\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tool config")
    write(repo, ".serena/project.yml", "name: demo\nlanguages: [python]\n")
    write(repo, ".vscode/settings.json", "{}\n")
    capsys.readouterr()
    assert af("pipeline", "infra") == 0  # a branch switch: main -> pipeline/infra
    assert branch(repo) == "pipeline/infra"
    out = capsys.readouterr().out
    assert "Changed before this task started" not in out and ".serena" not in out
    assert not (repo / ".git/framegate/pipeline-before/infra.json").exists()
    write(repo, "specs/infra/pipeline_followup.md", "- [ ] ci/tests.yml: add a test (new)\n")
    write(repo, "ci/tests.yml", CI + "      - run: pytest tests/test_a.py -q\n")
    write(repo, ".serena/project.yml", "name: demo\nlanguages: [python, yaml]\n")  # again
    write(repo, ".venv/Lib/pkg/Dockerfile", "FROM python\n")
    assert af("pipeline-check", "infra") == 0
    out = capsys.readouterr().out
    assert "Pipeline check infra: OK; 1 deploy / CI file(s) changed" in out
    assert ".serena" not in out and ".venv" not in out and ".vscode" not in out
    assert pipeline.changed_files(load_config(repo), "infra") == ["ci/tests.yml"]
    code, msg = guard(repo, ".serena/project.yml")
    assert code == 2 and "[PIPELINE] Blocked write to .serena/project.yml: it is a machine" in msg
    assert "machine file" in pipeline.write_problem(load_config(repo), "infra", ".venv/x.cfg")
    assert af("pipeline-approve", "infra") == 0
    assert ".serena" not in capsys.readouterr().out
    committed = set(git(repo, "show", "--name-only", "--format=", "HEAD").split())
    assert not [f for f in committed if f.startswith(".")]
    assert git(repo, "status", "--porcelain", "-uno").split() == ["M", ".serena/project.yml"]
    # back on main with the file still changed: the next start switches again
    git(repo, "checkout", "-q", "main")
    assert af("pipeline", "other") == 0 and branch(repo) == "pipeline/other"
    # a project file changed the same way still refuses, with the three ways out
    assert af("unlock") == 0
    git(repo, "checkout", "-q", "main")
    write(repo, "src/demo/__init__.py", '"""Demo."""\nMINE = 1\n')
    capsys.readouterr()
    assert af("pipeline", "third") == 1
    err = capsys.readouterr().err
    assert "Uncommitted changes to tracked files on main" in err and "src/demo/__init__.py" in err
    assert "commit it, restore it (git restore <file>), or, when it is tool state" in err
    assert "[git] machine_files" in err and ".serena" not in err


def test_yml_is_always_parsed(tmp_path: pathlib.Path) -> None:
    """PyYAML is a dependency: every document of a file is parsed, tags of the file's own
    dialect pass, a template that is no plain YAML is a note and never a problem."""
    cfg = parse_config(tmp_path, {})
    cases = {
        "ok.yml": "a: 1\nb: [x, y]\n",
        "multi.yaml": "kind: A\n---\nkind: B\n---\nkind: C\n",
        "tags.yml": "job:\n  script: !reference [.setup, script]\nref: !Ref B\nmap: !x {a: 1}\n",
        "bad.yml": "a: 1\n  b: 2\n",
        "bad-second.yml": "kind: A\n---\nkind: [unclosed\n",
        "tab.yml": "a:\n\tb: 1\n",
        "helm.yaml": "{{- if .Values.on }}\napiVersion: v1\nkind: {{ .Values.kind }}\n{{- end }}\n",
        "jinja.yml": "{% for x in items %}\n- name: {{ x }}\n{% endfor %}\n",
        "ok.json": '{"a": 1}\n',
        "bad.toml": "a = \n",
    }
    for name, text in cases.items():
        write(tmp_path, f"ci/{name}", text)
    result = {name: pipeline.parse_problem(cfg, f"ci/{name}") for name in cases}
    for name in ("ok.yml", "multi.yaml", "tags.yml", "ok.json"):
        assert result[name] == ("", ""), name
    assert result["bad.yml"][0].startswith("DOES NOT PARSE: ci/bad.yml:2: ")
    assert result["bad-second.yml"][0].startswith("DOES NOT PARSE: ci/bad-second.yml:")
    assert result["tab.yml"][0].startswith("DOES NOT PARSE: ci/tab.yml:2: ")
    assert result["bad.toml"][0].startswith("DOES NOT PARSE: ci/bad.toml: ")
    for name in ("helm.yaml", "jinja.yml"):
        problem, note = result[name]
        assert problem == "" and note.startswith(f"ci/{name} is a template, not plain YAML"), name
    assert pipeline.parse_problem(cfg, "ci/gone.yml") == ("", "")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"pyyaml>=6.0",' in pyproject.split("[project.optional-dependencies]")[0]


# ---------- built-in deploy / CI names, and items outside the repository ----------
def test_builtin_names_and_outside_items(
    repo: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Dockerfile and a compose file count by their name (no [protected] extra entry), also
    inside a source folder; publish puts them into the deploy / CI commit. An `OUTSIDE` line
    of the list is nobody's work here: shown apart, never open, never a problem."""
    write(repo, "src/demo/Dockerfile", "FROM python:3.12\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "an image of the package")
    start = git(repo, "rev-parse", "HEAD").strip()
    listed = (
        "# Pipeline follow-up: img\n\n"
        "- [ ] src/demo/Dockerfile: install libpq (the new driver needs it)\n"
        "- [ ] docker-compose.dev.yml: add the queue service (the runner needs it)\n"
        "- [ ] OUTSIDE [CI variable group argos-dev]: add QUEUE_URL (the job reads it)\n"
        "- [x] outside [vault]: rotate the token (it was in a log) (from the spec)\n"
    )
    write(repo, "specs/img/pipeline_followup.md", listed)
    cfg = load_config(repo)
    one, two, three, four = followup.read_list(cfg, "img")
    assert (one.outside, three.outside, four.outside) == (False, True, True)
    assert three.file == "" and three.text.startswith("OUTSIDE [CI variable group argos-dev]: ")
    assert three.shown() == "CI variable group argos-dev: add QUEUE_URL (the job reads it)"
    assert (followup.open_count(cfg, "img"), followup.outside_count(cfg, "img")) == (2, 1)

    assert af("pipeline", "img") == 0
    out = capsys.readouterr().out
    assert "List (specs/img/pipeline_followup.md): 2 open of 2 item(s)" in out
    assert "Outside the repository: by hand, not this task: 1 to do, 1 ticked" in out
    assert "  - CI variable group argos-dev: add QUEUE_URL (the job reads it)" in out
    assert "  - [x] vault: rotate the token" in out
    for rel in ("src/demo/Dockerfile", "docker-compose.dev.yml"):
        assert guard(repo, rel)[0] == 0, rel
    code, msg = guard(repo, "src/demo/__init__.py")
    assert code == 2 and "`- OUTSIDE [<where>]: <what must be done> (<why>)`" in msg
    code, msg = guard(repo, "Jenkinsfile")
    assert code == 2 and "but not on the list" in msg

    write(repo, "src/demo/Dockerfile", "FROM python:3.12\nRUN apt-get install -y libpq5\n")
    write(repo, "docker-compose.dev.yml", "services:\n  queue:\n    image: redis\n")
    assert af("pipeline-check", "img") == 0
    out = capsys.readouterr().out
    assert "Pipeline check img: OK; 2 deploy / CI file(s) changed" in out
    assert "Outside the repository: by hand, not this task: 1 to do, 1 ticked" in out
    assert "OPEN" not in out
    assert af("pipeline-approve", "img") == 0
    out = capsys.readouterr().out
    assert "approved: 2 item(s) ticked, 0 left open; 2 file(s) committed" in out
    assert "Outside the repository: by hand, not this task: 1 to do" in out
    text = (repo / "specs/img/pipeline_followup.md").read_text(encoding="utf-8")
    assert "- [ ] OUTSIDE [CI variable group argos-dev]" in text  # never ticked by the task
    assert text.count("- [x] ") == 3

    # the publish split is by the same definition
    assert publish.pipeline_changed(load_config(repo), start) == [
        "docker-compose.dev.yml",
        "src/demo/Dockerfile",
    ]
    # status: no open work for the pipeline task; the outside item counted apart
    assert af("status") == 0
    out = capsys.readouterr().out
    line = "- img: outside the repository: 1 item(s) to do by hand (specs/img/pipeline_followup.md)"
    assert line in out and "/framegate:pipeline img" not in out

    # a list with nothing but outside items: nothing to do, nothing fails
    write(repo, "specs/env/pipeline_followup.md", "- [ ] OUTSIDE [prod database]: migrate (v2)\n")
    assert af("pipeline", "env") == 0
    assert af("pipeline-check", "env") == 0
    out = capsys.readouterr().out
    assert "Pipeline check env: OK; 0 deploy / CI file(s) changed" in out
    assert af("pipeline-approve", "env") == 0
    assert "Pipeline task env approved: 0 item(s) ticked, 0 left open" in capsys.readouterr().out
    assert not (repo / ".phase").exists()


def test_an_item_that_is_not_needed_is_closed(
    repo: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Already in place, or impossible as written: `(not needed: <reason>)` closes the item.
    `(left open: ...)` would keep it open in `framegate status` for good (seen live)."""
    outside = "- [ ] OUTSIDE [vault]: add a secret (the job reads it)\n"
    write(repo, "specs/x/pipeline_followup.md", LIST.format(name="x") + outside)
    assert af("pipeline", "x") == 0
    write(repo, "ci/tests.yml", CI + "      - run: pytest tests/test_a.py -q\n")
    capsys.readouterr()
    assert af("pipeline-check", "x") == 1
    out = capsys.readouterr().out
    assert "- OPEN ITEM 2: its file did not change" in out and "`(not needed: <reason>)`" in out
    listed = repo / "specs/x/pipeline_followup.md"
    text = listed.read_text(encoding="utf-8")
    text = text.replace("(from the spec)", "(from the spec) (not needed: the image copies src/)")
    listed.write_text(text.replace("reads it)", "reads it) (Not needed: no job reads it)"), "utf-8")
    cfg = load_config(repo)
    one, two, three = followup.read_list(cfg, "x")
    assert (one.open, two.open, three.open) == (True, False, False)
    assert two.not_needed == "the image copies src/" and not two.ticked and not two.reason
    assert two.text.endswith("(from the spec)") and three.outside
    assert (followup.open_count(cfg, "x"), followup.outside_count(cfg, "x")) == (1, 0)
    assert followup.pr_checklist(cfg, "x") == []
    assert af("pipeline", "x") == 0  # the list again
    out = capsys.readouterr().out
    assert "1 open of 2 item(s)" in out
    assert "(the image lacks it) (from the spec)  [not needed: the image copies src/]" in out
    assert "Outside the repository: by hand, not this task: 0 to do, 1 not needed" in out
    assert "  - [not needed: no job reads it] vault: add a secret (the job reads it)" in out
    assert af("pipeline-check", "x") == 0
    out = capsys.readouterr().out
    assert "Pipeline check x: OK; 1 deploy / CI file(s) changed" in out
    assert "2. not needed (the image copies src/): `deploy/Dockerfile`: COPY the new folder" in out
    assert af("pipeline-approve", "x") == 0
    out = capsys.readouterr().out
    assert "approved: 1 item(s) ticked, 2 closed as not needed, 0 left open; 1 file(s)" in out
    # closed in the file too, with its reason; never open again, in no status
    text = listed.read_text(encoding="utf-8")
    assert text.count("- [x] ") == 3 and "(not needed: the image copies src/)" in text
    assert followup.open_count(cfg, "x") == 0
    assert af("status") == 0
    assert "- x:" not in capsys.readouterr().out
    described = (repo / "specs/x/pipeline_pr_description.md").read_text(encoding="utf-8")
    assert "- ci/tests.yml: add the new test file (CI does not select it)\n" in described
    # closed without a change: no news for a reviewer (seen live: it was noise on the PR)
    assert "not needed" not in described and "deploy/Dockerfile" not in described
    assert "To do by hand" not in described and not git(repo, "status", "--porcelain").strip()
    skill = (ROOT / "plugin/skills/pipeline/SKILL.md").read_text(encoding="utf-8")
    assert '"<item>: not needed: <reason>? (default: yes)"' in skill


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
    # items outside the repository: one form, told to every writer of the list
    form = "OUTSIDE [<where>]: <what must be done> (<why>)"
    assert followup.OUTSIDE_FORMAT == f"- {form}" and f"- [ ] {form}" in skill
    assert "**Outside the repository: by hand**" in skill and "tabs" not in skill
    for name in ("feature", "bugfix", "refactor"):
        text = (ROOT / f"plugin/skills/{name}/SKILL.md").read_text(encoding="utf-8")
        assert f"`- {form}`" in text and '"outside the repository: by hand: ' in text, name
    for folder in ("_template", "_template_bugfix", "_template_refactor"):
        path = ROOT / "src/framegate/templates/specs" / folder / "requirements.md"
        assert f"`- {form}`" in path.read_text(encoding="utf-8"), folder
    for role in ("implement", "review"):
        text = (ROOT / f"src/framegate/roles/{role}.md").read_text(encoding="utf-8")
        assert f"`- {form}`" in text, role
    assert "`- OUTSIDE [<where>]: <what> (<why>)`" in protocol
    assert "Outside the repository: N item(s) to do by hand" in protocol
