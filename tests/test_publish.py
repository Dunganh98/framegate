"""Product branch and state branch (`[git] state_branch`): a shared repository whose base
branch holds no framegate file. Real git state is checked: trees, what the bare `origin`
received, what stayed local. No agent runs: the feature's work is committed by hand the way
the workflow commits it. Nothing calls gh or az."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

import pytest

from conftest import DESIGN, FAKE_CLAUDE, GITIGNORE, PLAN, PYPROJECT, REQUIREMENTS, STUB, git
from framegate import cli, doctor, hosts, publish
from framegate.adapters import get_adapter
from framegate.checks import frame_diff
from framegate.config import Config, ConfigError, load_config, parse_config
from framegate.orchestrator import Orchestrator
from framegate.state import commit, save_progress, set_marker, tag_exists

IMPL = STUB.replace(
    "    raise NotImplementedError\n",
    "    if not isinstance(a, int) or not isinstance(b, int):\n"
    '        raise TypeError("ints only")\n'
    "    return a + b\n",
)
TEST = """\
import pytest

from demo import add


@pytest.mark.req("DEMO-R-001")
def test_add() -> None:
    assert add(2, 3) == 5


@pytest.mark.req("DEMO-E-001")
def test_add_refuses_other_types() -> None:
    with pytest.raises(TypeError, match="ints only"):
        add("2", 3)  # type: ignore[arg-type]
"""
STATE_ON = '[git]\nbase = "dev"\nbranch_prefix = "feature/"\nstate_branch = "framegate/{base}"\n'
WORKFLOW_TOP = {"framegate.toml", "specs", "metrics", ".framegate"}


def af(root: pathlib.Path, *args: str) -> int:
    """`framegate <args>` run in `root`."""
    old = os.getcwd()
    os.chdir(root)
    try:
        return cli.main(list(args))
    finally:
        os.chdir(old)


def identify(root: pathlib.Path) -> None:
    for key, value in (
        ("user.email", "test@example.com"),
        ("user.name", "Test"),
        ("core.autocrlf", "false"),
        ("commit.gpgsign", "false"),
    ):
        git(root, "config", key, value)


def ok(root: pathlib.Path, *args: str) -> bool:
    """True when the git command succeeds."""
    return subprocess.run(["git", *args], cwd=root, capture_output=True).returncode == 0


def files(root: pathlib.Path, rev: str) -> set[str]:
    return set(git(root, "ls-tree", "-r", "--name-only", rev).split())


def top_level(root: pathlib.Path, rev: str) -> set[str]:
    return {f.split("/")[0] for f in files(root, rev)}


def sha(root: pathlib.Path, rev: str) -> str:
    return git(root, "rev-parse", rev).strip()


def refs(root: pathlib.Path) -> dict[str, str]:
    out = git(root, "for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split() for line in out.splitlines())


def show(root: pathlib.Path, rev: str, path: str) -> str:
    return git(root, "show", f"{rev}:{path}")


def write_spec(root: pathlib.Path, feature: str = "demo") -> None:
    spec = root / "specs" / feature
    (spec / "requirements.md").write_text(REQUIREMENTS, encoding="utf-8")
    (spec / "design.md").write_text(DESIGN, encoding="utf-8")
    (spec / "plan.md").write_text(PLAN, encoding="utf-8")


def finish(root: pathlib.Path, feature: str = "demo") -> Config:
    """What a run leaves at DONE, committed the way the workflow commits it: the tests
    (marker tests-approved), the code, the metrics row, the final summary in out/."""
    cfg = load_config(root)
    (root / "tests" / f"test_{feature}.py").write_text(TEST, encoding="utf-8")
    assert commit(cfg, f"{feature}: tests approved")
    set_marker(cfg, f"tests-approved/{feature}")
    (root / "src" / "demo" / "__init__.py").write_text(IMPL, encoding="utf-8")
    assert commit(cfg, f"{feature}: step 1")
    Orchestrator(cfg, echo=lambda s: None).write_metrics(feature)
    out = cfg.feature_out(feature)
    out.mkdir(parents=True, exist_ok=True)
    (out / "final_summary.md").write_text(f"# {feature}\n\nadd() adds.\n", encoding="utf-8")
    save_progress(cfg, feature, {"phase": "DONE"})
    return cfg


def build_base(top: pathlib.Path) -> pathlib.Path:
    """A product repository on `dev` with a bare `origin` next to it; no framegate file."""
    origin = top / "origin.git"
    git(top, "init", "-q", "--bare", "-b", "dev", str(origin))
    root = top / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "dev")
    identify(root)
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (root / "src" / "demo").mkdir(parents=True)
    (root / "src" / "demo" / "__init__.py").write_text('"""Demo feature."""\n', encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_base.py").write_text(
        "def test_base() -> None:\n    assert True\n", encoding="utf-8"
    )
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "product")
    git(root, "remote", "add", "origin", "../origin.git")
    git(root, "push", "-q", "origin", "dev")
    return root


def configure(root: pathlib.Path, git_section: str) -> None:
    toml = root / "framegate.toml"
    text = toml.read_text(encoding="utf-8")
    assert "[git]\n" in text
    text = text.replace("[git]\n", git_section, 1).replace(
        "[gates]\n", '[gates]\nmutation_runner = "native"\n', 1
    )
    toml.write_text(text, encoding="utf-8")


def build_team(top: pathlib.Path) -> None:
    """The set-up repository with the feature `demo` DONE on its work branch, unpublished."""
    root = build_base(top)
    assert af(root, "init") == 0
    git(root, "checkout", "--", ".gitignore")  # a product file: the run files go to info/exclude
    configure(root, STATE_ON)
    assert af(root, "state", "init") == 0
    assert af(root, "spec", "demo") == 0
    write_spec(root)
    (root / "src" / "demo" / "__init__.py").write_text(STUB, encoding="utf-8")
    assert af(root, "approve-spec", "demo") == 0
    finish(root)


@dataclass
class Team:
    top: pathlib.Path

    @property
    def root(self) -> pathlib.Path:
        return self.top / "repo"

    @property
    def origin(self) -> pathlib.Path:
        return self.top / "origin.git"

    def af(self, *args: str) -> int:
        return af(self.root, *args)

    def clone(self, name: str = "second") -> pathlib.Path:
        git(self.top, "clone", "-q", str(self.origin), name)
        identify(self.top / name)
        return self.top / name


@pytest.fixture(scope="session")
def team_template(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    top = tmp_path_factory.mktemp("team-template")
    old = os.environ.get("FRAMEGATE_CLAUDE")
    os.environ["FRAMEGATE_CLAUDE"] = str(FAKE_CLAUDE)
    try:
        build_team(top)
    finally:
        if old is None:
            os.environ.pop("FRAMEGATE_CLAUDE", None)
        else:
            os.environ["FRAMEGATE_CLAUDE"] = old
    return top


@pytest.fixture
def team(
    team_template: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Team:
    """A fresh copy of the template: `demo` is DONE on framegate-work/demo, nothing is published."""
    monkeypatch.setenv("FRAMEGATE_CLAUDE", str(FAKE_CLAUDE))
    shutil.copytree(team_template, tmp_path / "team")
    return Team(tmp_path / "team")


@pytest.fixture(autouse=True)
def no_pr_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches gh or az, whatever this machine has installed."""
    monkeypatch.setattr(publish, "which", lambda name: None)


# ---------- what is framegate's ----------
def test_workflow_paths_follow_the_config(tmp_path: pathlib.Path) -> None:
    cfg = parse_config(tmp_path, {})
    assert publish.workflow_paths(cfg) == [
        *("framegate.toml", ".framegate", "specs", "metrics", "docs/code-map.md")
    ]
    for path in ("framegate.toml", "specs/x/plan.md", ".framegate/project.md", "metrics/a.csv"):
        assert publish.is_workflow(cfg, path), path
    for path in ("src/demo/x.py", "tests/review/test_x.py", "pyproject.toml", "specs.md"):
        assert not publish.is_workflow(cfg, path), path
    assert not publish.enabled(cfg)  # off by default
    custom = parse_config(
        tmp_path,
        {
            "project": {"knowledge": "docs/agents/facts.md"},
            "paths": {"specs": "docs/specs", "metrics": ".framegate/metrics"},
            "roles": {"extra_dir": "tools/roles/"},
            "git": {"state_branch": "framegate/{base}", "publish_code_map": True},
        },
    )
    assert publish.enabled(custom)
    assert publish.workflow_paths(custom) == [
        *("framegate.toml", ".framegate", "docs/specs", "docs/agents/facts.md", "tools/roles")
    ]
    assert not publish.is_workflow(custom, "docs/code-map.md")  # published with the product
    # the local work branch never has the pushed product branch's name
    assert (custom.work_branch("x"), publish.product_branch(custom, "x")) == (
        *("framegate-work/x", "feat/x"),
    )
    assert cfg.work_branch("x") == "feat/x"  # off: one branch, as ever
    with pytest.raises(ConfigError, match="work_prefix"):
        parse_config(
            tmp_path, {"git": {"state_branch": "framegate/{base}", "work_prefix": "feat/"}}
        )
    for bad in ("framegate/{branch}", "framegate/{base", "frame gate"):
        with pytest.raises(ConfigError, match="state_branch"):
            parse_config(tmp_path, {"git": {"state_branch": bad}})


def test_names_drop_the_remote_from_the_base(team: Team) -> None:
    cfg = load_config(team.root)
    assert publish.names(cfg) == publish.Names("origin", "dev", "framegate/dev")
    cfg.git_base = "origin/dev"  # da-argos: features start from the remote's branch
    assert publish.names(cfg) == publish.Names("origin", "dev", "framegate/dev")
    cfg.git_base = "release/1.x"  # a slash that is no remote
    assert publish.names(cfg).state == "framegate/release/1.x"
    assert publish.product_branch(cfg, "demo") == "feature/demo"


# ---------- start of a feature ----------
def test_a_feature_starts_from_the_base_with_the_state_branch_merged_in(team: Team) -> None:
    r = team.root
    assert git(r, "rev-parse", "--abbrev-ref", "HEAD").strip() == "framegate-work/demo"
    assert not ok(r, "rev-parse", "--verify", "-q", "refs/heads/feature/demo")  # the team's name
    # the base: untouched, no framegate file; the state branch: nothing else, no shared history
    assert sha(r, "dev") == sha(team.origin, "dev")
    assert not top_level(r, "dev") & WORKFLOW_TOP
    assert top_level(r, "framegate/dev") <= WORKFLOW_TOP
    assert "framegate.toml" in files(r, "framegate/dev")
    assert not ok(r, "merge-base", "framegate/dev", "dev")
    # the work branch: the base plus the state branch (both kinds of files in one tree)
    assert git(r, "merge-base", "HEAD", "dev").strip() == sha(r, "dev")
    assert {"framegate.toml", "pyproject.toml"} <= top_level(r, "HEAD")
    assert "specs/_template/plan.md" in files(r, "HEAD")
    # run files are ignored through this clone, the project's .gitignore is as it was
    exclude = (r / ".git" / "info" / "exclude").read_text(encoding="utf-8").split()
    assert {"out/", ".phase", ".reqs", "mutants/"} <= set(exclude)
    assert (r / ".gitignore").read_text(encoding="utf-8") == GITIGNORE
    assert not git(r, "status", "--porcelain").strip()
    assert not ok(r, "config", "--get", "branch.framegate-work/demo.remote")  # tracks nothing


def test_spec_refuses_without_a_state_branch_and_with_changed_copies(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_base(tmp_path)
    assert af(root, "init") == 0
    git(root, "checkout", "--", ".gitignore")
    configure(root, STATE_ON)
    assert af(root, "spec", "demo") == 1
    err = capsys.readouterr().err
    assert "The state branch framegate/dev does not exist yet" in err
    assert "framegate state init" in err
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "dev"
    assert not git(root, "branch", "--list", "framegate-work/demo").strip()

    assert af(root, "state", "init") == 0
    out = capsys.readouterr().out
    assert "Created the state branch framegate/dev" in out and "Not pushed yet" in out
    assert af(root, "state", "init") == 1  # once per repository
    assert "exists already" in capsys.readouterr().err
    # an untracked copy that no longer equals the state branch's file: nothing is thrown away
    (root / ".framegate" / "project.md").write_text("# my other facts\n", encoding="utf-8")
    assert af(root, "spec", "demo") == 1
    err = capsys.readouterr().err
    assert "untracked files differ from the state branch" in err and "project.md" in err
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "dev"
    assert not git(root, "branch", "--list", "framegate-work/demo").strip()
    assert (root / ".framegate" / "project.md").read_text(encoding="utf-8").startswith("# my")


# ---------- publish ----------
def test_dry_run_changes_nothing(team: Team, capsys: pytest.CaptureFixture[str]) -> None:
    r = team.root
    before, there, head = refs(r), refs(team.origin), sha(r, "HEAD")
    capsys.readouterr()
    assert team.af("publish", "demo", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Publish demo (dry run: nothing is changed or pushed)" in out
    assert "State branch framegate/dev: " in out and "A specs/demo/requirements.md" in out
    assert 'Product branch feature/demo: 2 file(s), one commit "demo" on dev' in out
    assert "M src/demo/__init__.py" in out and "A tests/test_demo.py" in out
    assert "Would push to origin: framegate/dev, feature/demo" in out
    assert "framegate.toml" not in out.split("Product branch")[1]
    assert refs(r) == before and refs(team.origin) == there and sha(r, "HEAD") == head


def test_publish_sends_the_product_and_the_state_apart(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r, o = team.root, team.origin
    dev = sha(o, "dev")
    assert team.af("publish", "demo", "--title", "demo: add two ints") == 0
    out = capsys.readouterr().out
    assert "Pushed the state branch framegate/dev to origin." in out
    assert "Pushed feature/demo to origin" in out
    # nothing but branches on the remote: the base untouched, the state, the product
    assert set(refs(o)) == {
        *("refs/heads/dev", "refs/heads/framegate/dev", "refs/heads/feature/demo")
    }
    assert sha(o, "dev") == dev

    # the product branch: one commit on the base, the feature's product files, no framegate file
    assert git(o, "rev-list", "--parents", "-1", "feature/demo").split()[1:] == [dev]
    assert git(o, "log", "-1", "--format=%s", "feature/demo").strip() == "demo: add two ints"
    product = files(o, "feature/demo")
    assert product == files(o, "dev") | {"tests/test_demo.py"}
    assert not top_level(o, "feature/demo") & WORKFLOW_TOP
    assert show(o, "feature/demo", "src/demo/__init__.py") == IMPL
    # equal to the work branch in every product file
    diff = git(r, "diff", "--name-only", sha(o, "feature/demo"), "HEAD").split()
    assert diff and all(f.split("/")[0] in WORKFLOW_TOP for f in diff)

    # the state branch: the feature's framegate files, nothing of the product
    state = files(o, "framegate/dev")
    assert {"framegate.toml", "specs/demo/requirements.md", ".framegate/project.md"} <= state
    assert top_level(o, "framegate/dev") <= WORKFLOW_TOP
    assert ",demo," in show(o, "framegate/dev", "metrics/workflow_metrics.csv")
    assert git(o, "log", "--format=%s", "framegate/dev").splitlines() == [
        "framegate: workflow files after demo",
        "framegate: workflow files (framegate/dev created)",
    ]

    # the pull request: not asked for; a local path is no known host
    assert "Pull request feature/demo -> dev: not created: no tool for this host" in out
    assert "out/demo/final_summary.md (copy it before the cleanup)" in out

    # again: nothing left to do, nothing moves
    there, head = refs(o), sha(r, "HEAD")
    assert team.af("publish", "demo", "--title", "demo: add two ints") == 0
    out = capsys.readouterr().out
    assert "State branch framegate/dev: in line" in out
    assert "feature/demo on origin is in line already" in out
    assert refs(o) == there and sha(r, "HEAD") == head


def test_markers_frame_check_and_cleanup_survive_the_publish(team: Team) -> None:
    r = team.root
    cfg = load_config(r)
    adapter = get_adapter(cfg)
    marks = {m: sha(r, m) for m in ("spec-approved/demo", "tests-approved/demo")}
    before = frame_diff.check(adapter, "demo")
    assert before.status == "PASS", before.output
    assert team.af("publish", "demo") == 0
    # the work branch is never rewritten: the private refs point where they did
    assert {m: sha(r, m) for m in marks} == marks
    assert all(ok(r, "merge-base", "--is-ancestor", m, "HEAD") for m in marks)
    after = frame_diff.check(adapter, "demo")
    assert (after.status, after.output) == (before.status, before.output)
    assert sha(r, publish.product_ref("demo")) == sha(team.origin, "feature/demo")
    assert team.af("cleanup", "demo", "--tags") == 0
    assert not tag_exists(cfg, "tests-approved/demo")
    assert publish.product_ref("demo") not in refs(r)
    assert not (r / "out" / "demo").exists() and (r / "specs" / "demo").is_dir()
    # the product ref is gone, the remote branch holds this result: still nothing to do
    assert team.af("publish", "demo") == 0
    assert sha(r, publish.product_ref("demo")) == sha(team.origin, "feature/demo")


def test_more_work_after_the_publish_is_a_fast_forward(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r, o = team.root, team.origin
    assert team.af("publish", "demo") == 0
    first, state1 = sha(o, "feature/demo"), sha(o, "framegate/dev")
    cfg = load_config(r)
    # review feedback on the pull request, and a fact the retro added
    (r / "src" / "demo" / "__init__.py").write_text(IMPL + "\n\nEXTRA = 1\n", encoding="utf-8")
    facts = r / ".framegate" / "project.md"
    facts.write_text(facts.read_text(encoding="utf-8") + "- add() takes ints.\n", encoding="utf-8")
    assert commit(cfg, "demo: review feedback")
    capsys.readouterr()
    assert team.af("publish", "demo") == 0
    assert "M .framegate/project.md" in capsys.readouterr().out
    second = sha(o, "feature/demo")
    assert git(o, "rev-list", "--parents", "-1", second).split()[1:] == [first]  # no rewrite
    assert "EXTRA = 1" in show(o, "feature/demo", "src/demo/__init__.py")
    assert not top_level(o, "feature/demo") & WORKFLOW_TOP
    assert git(o, "rev-list", "--parents", "-1", "framegate/dev").split()[1:] == [state1]
    assert "add() takes ints." in show(o, "framegate/dev", ".framegate/project.md")


def test_state_save_sends_a_config_change_without_a_feature(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r, o = team.root, team.origin
    toml = r / "framegate.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n# a note\n", encoding="utf-8")
    assert team.af("state", "save") == 1  # uncommitted
    assert "Uncommitted changes" in capsys.readouterr().err
    git(r, "commit", "-q", "-m", "config", "framegate.toml")
    assert team.af("state", "save", "--push") == 0
    assert "# a note" in show(o, "framegate/dev", "framegate.toml")
    assert "refs/heads/feature/demo" not in refs(o)  # the product is not published by it
    capsys.readouterr()
    assert team.af("state") == 0
    out = capsys.readouterr().out
    assert "State branch: framegate/dev (local " in out and ", origin " in out
    assert "framegate's files: framegate.toml, .framegate, specs, metrics" in out
    git(r, "checkout", "-q", "dev")
    assert af(r, "state", "save") == 1  # never from the base
    assert "runs on a feature branch (this is dev)" in capsys.readouterr().err


def test_without_a_remote_everything_stays_local(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r = team.root
    git(r, "remote", "remove", "origin")
    there = refs(team.origin)
    capsys.readouterr()
    assert team.af("publish", "demo") == 0
    out = capsys.readouterr().out
    assert "No remote: nothing pushed. The product commit: refs/framegate/product/demo" in out
    product = publish.product_ref("demo")
    assert git(r, "rev-list", "--parents", "-1", product).split()[1:] == [sha(r, "dev")]
    assert not top_level(r, product) & WORKFLOW_TOP
    assert "specs/demo/plan.md" in files(r, "framegate/dev")
    assert refs(team.origin) == there


# ---------- refusals ----------
def test_publish_refuses_what_is_not_ready(team: Team, capsys: pytest.CaptureFixture[str]) -> None:
    r, o = team.root, team.origin
    there = refs(o)

    def refused(*args: str) -> str:
        capsys.readouterr()
        assert team.af("publish", *args) == 1
        assert refs(o) == there  # nothing reached the remote
        return capsys.readouterr().err

    assert "git switch framegate-work/other" in refused("other")
    (r / "src" / "demo" / "__init__.py").write_text(IMPL + "# wip\n", encoding="utf-8")
    assert "Uncommitted changes" in refused("demo")
    git(r, "checkout", "--", "src/demo/__init__.py")
    (r / "tests" / "test_new.py").write_text("def test_n() -> None:\n    pass\n", encoding="utf-8")
    assert "?? tests/test_new.py" in refused("demo")  # it would silently stay out
    (r / "tests" / "test_new.py").unlink()
    (r / "notes.md").write_text("mine\n", encoding="utf-8")  # the user's own file: fine

    metrics = r / "metrics" / "workflow_metrics.csv"
    row = metrics.read_text(encoding="utf-8")
    metrics.write_text(row.splitlines()[0] + "\n", encoding="utf-8")
    git(r, "commit", "-q", "-m", "no row", "metrics")
    assert "demo is not DONE yet" in refused("demo")
    metrics.write_text(row, encoding="utf-8")
    git(r, "commit", "-q", "-m", "row", "metrics")

    git(r, "checkout", "-q", "dev")
    capsys.readouterr()
    assert af(r, "publish", "demo") == 1  # the base has no framegate.toml
    err = capsys.readouterr().err
    assert "framegate's files are on the state branch framegate/dev" in err
    git(r, "checkout", "-q", "framegate-work/demo")
    assert team.af("publish", "demo") == 0
    assert (r / "notes.md").is_file()


def test_publish_refuses_a_base_that_holds_framegate_files(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r = team.root
    cfg = load_config(r)
    git(r, "checkout", "-q", "dev")
    (r / "metrics").mkdir()
    (r / "metrics" / "model_scores.csv").write_text("auc\n0.9\n", encoding="utf-8")
    git(r, "add", "metrics")
    git(r, "commit", "-q", "-m", "the project's own metrics folder")
    git(r, "checkout", "-q", "framegate-work/demo")
    git(r, "merge", "-q", "--no-edit", "dev")
    capsys.readouterr()
    assert team.af("publish", "demo") == 1
    err = capsys.readouterr().err
    assert "holds files under framegate's paths: metrics/model_scores.csv" in err
    assert "[paths] metrics" in err
    assert "refs/heads/feature/demo" not in refs(team.origin)
    items = {i.name: i for i in doctor.state_branch_items(cfg, get_adapter(cfg))}
    assert items["git: 'dev' holds files under framegate's paths"].status == "WARN"


def test_a_remote_product_branch_with_other_content_is_not_overwritten(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    o = team.origin
    other = team.clone()
    git(other, "checkout", "-q", "-b", "feature/demo")
    (other / "theirs.txt").write_text("a colleague's commit\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "theirs")
    git(other, "push", "-q", "origin", "feature/demo")
    theirs = sha(o, "feature/demo")
    r = team.root
    mine = sha(r, "HEAD"), sha(r, "framegate/dev")
    capsys.readouterr()
    assert team.af("publish", "demo") == 1
    err = capsys.readouterr().err
    assert "origin already has a branch feature/demo with other content" in err
    assert "--force-with-lease" in err
    assert sha(o, "feature/demo") == theirs and "refs/heads/framegate/dev" not in refs(o)
    assert (sha(r, "HEAD"), sha(r, "framegate/dev")) == mine  # refused before any change
    assert team.af("publish", "demo", "--force-with-lease") == 0
    assert sha(o, "feature/demo") != theirs
    assert "theirs.txt" not in files(o, "feature/demo")
    assert git(o, "rev-list", "--parents", "-1", "feature/demo").split()[1:] == [sha(o, "dev")]


def test_a_diverged_state_branch_is_refused(team: Team, capsys: pytest.CaptureFixture[str]) -> None:
    r, o = team.root, team.origin
    assert team.af("state", "save", "--push") == 0
    pushed = sha(o, "framegate/dev")
    # another clone publishes a change of its own ...
    other = team.clone()
    git(other, "checkout", "-q", "framegate/dev")
    (other / "specs" / "theirs.md").write_text("theirs\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "theirs")
    git(other, "push", "-q", "origin", "framegate/dev")
    theirs = sha(o, "framegate/dev")
    # ... while this clone has a state commit that never got out
    mine = git(r, "commit-tree", "framegate/dev^{tree}", "-p", pushed, "-m", "mine").strip()
    git(r, "update-ref", "refs/heads/framegate/dev", mine)
    capsys.readouterr()
    assert team.af("publish", "demo") == 1
    err = capsys.readouterr().err
    assert "The state branch framegate/dev has diverged" in err
    assert "git branch -f framegate/dev origin/framegate/dev" in err
    assert sha(o, "framegate/dev") == theirs and sha(r, "framegate/dev") == mine
    assert "refs/heads/feature/demo" not in refs(o)


# ---------- a second machine, features side by side ----------
def test_a_second_machine_starts_from_a_clone_of_the_base(
    team: Team, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    o = team.origin
    assert team.af("publish", "demo") == 0
    second = team.clone()
    assert git(second, "rev-parse", "--abbrev-ref", "HEAD").strip() == "dev"
    assert not (second / "framegate.toml").exists()
    capsys.readouterr()
    assert af(second, "doctor") == 1  # nothing to read the config from, but a way on
    err = capsys.readouterr().err
    assert "no framegate.toml found" in err
    assert "framegate's files are on the state branch framegate/dev" in err
    assert "framegate spec <feature>" in err
    assert af(second, "status") == 0  # the status skill injects this: never a failure
    assert "No framegate.toml in this checkout. framegate's files are on" in capsys.readouterr().out
    assert af(second, "state") == 0
    assert "State branch: framegate/dev (not fetched here yet, origin " in capsys.readouterr().out

    # the one command: the state branch is fetched, the feature starts from the base
    assert af(second, "spec", "other") == 0
    out = capsys.readouterr().out
    assert "Fetched the state branch framegate/dev from origin." in out
    assert "Created the feature branch framegate-work/other from dev (local only;" in out
    assert "pushes the product as feature/other)." in out
    assert git(second, "rev-parse", "--abbrev-ref", "HEAD").strip() == "framegate-work/other"
    assert (second / "specs" / "demo" / "requirements.md").is_file()  # the first feature's spec
    assert (second / "specs" / "other" / "plan.md").is_file()
    assert (second / ".phase").read_text(encoding="utf-8") == "SPEC"
    assert not git(second, "status", "--porcelain").replace("?? specs/other/", "").strip()
    assert sha(o, "dev") == sha(second, "dev")


def test_two_features_side_by_side_meet_on_the_state_branch(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r, o = team.root, team.origin
    git(r, "push", "-q", "origin", "framegate/dev")  # as created: the second machine can start
    second = team.clone()
    assert af(second, "spec", "other") == 0
    write_spec(second, "other")
    (second / "src" / "demo" / "__init__.py").write_text(STUB, encoding="utf-8")
    (second / "src" / "demo" / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
    assert af(second, "approve-spec", "other") == 0
    cfg2 = load_config(second)
    Orchestrator(cfg2, echo=lambda s: None).write_metrics("other")
    # the first machine publishes first; the second then meets a newer state branch
    assert team.af("publish", "demo") == 0
    capsys.readouterr()
    assert af(second, "publish", "other") == 0
    assert "Merged the newer framegate/dev into framegate-work/other." in capsys.readouterr().out
    state = files(o, "framegate/dev")
    assert {"specs/demo/plan.md", "specs/other/plan.md"} <= state
    rows = show(o, "framegate/dev", "metrics/workflow_metrics.csv")
    assert ",demo," in rows and ",other," in rows  # both rows kept (merge=union)
    assert len(rows.splitlines()) == 3 and rows.count("date,feature") == 1  # one header
    # each product branch holds its own feature only
    assert "src/demo/other.py" in files(o, "feature/other")
    assert "tests/test_demo.py" not in files(o, "feature/other")
    assert not top_level(o, "feature/other") & WORKFLOW_TOP
    assert "src/demo/other.py" not in files(o, "feature/demo")
    # the first machine's next feature starts with both specs
    git(r, "checkout", "-q", "dev")
    assert af(r, "spec", "third") == 0
    assert (r / "specs" / "other" / "plan.md").is_file()


# ---------- the work branch's own name, the push guard ----------
def push(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "push", *args], cwd=root, capture_output=True, text=True)


def test_a_push_of_framegate_files_is_refused_by_the_guard(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r, o = team.root, team.origin
    there = refs(o)
    hook = r / ".git" / "hooks" / "pre-push"
    assert hook.read_bytes().startswith(b"#!/bin/sh\n# framegate pre-push guard v1")
    assert b"\r" not in hook.read_bytes()  # git's sh runs it, on Windows too
    # by hand, under its own name, under the team's name, by HEAD, with everything else
    for args in (
        ("origin", "framegate-work/demo"),
        ("origin", "framegate-work/demo:feature/demo"),
        ("origin", "HEAD:refs/heads/feature/demo"),
        ("-u", "origin", "framegate-work/demo"),
        ("origin", "--all"),
    ):
        res = push(r, *args)
        assert res.returncode != 0, args
        assert "framegate: push refused" in res.stderr and "framegate.toml" in res.stderr, args
        assert refs(o) == there, args
    # what may go out goes out: the state branch, a branch without framegate's files
    assert push(r, "origin", "framegate/dev").returncode == 0
    assert push(r, "origin", "dev:refs/heads/copy-of-dev").returncode == 0
    assert push(r, "origin", "--delete", "copy-of-dev").returncode == 0
    assert team.af("publish", "demo") == 0  # its own two pushes pass the guard
    assert not top_level(o, "feature/demo") & WORKFLOW_TOP

    # --no-verify skips every hook; publish is the second line: it refuses, then repairs
    forced = push(r, "--no-verify", "-f", "origin", "framegate-work/demo:feature/demo")
    assert forced.returncode == 0
    assert "framegate.toml" in files(o, "feature/demo")
    capsys.readouterr()
    assert team.af("publish", "demo") == 1
    assert "already has a branch feature/demo with other content" in capsys.readouterr().err
    assert team.af("publish", "demo", "--force-with-lease") == 0
    assert not top_level(o, "feature/demo") & WORKFLOW_TOP


def test_the_guard_keeps_a_hook_of_the_users_and_respects_hooks_path(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r, o = team.root, team.origin
    cfg = load_config(r)
    hooks = r / ".git" / "hooks"
    seen = r.parent / "user-hook-ran.txt"
    mine = '#!/bin/sh\necho "$1" >> "{}"\ncat > /dev/null\n[ ! -f "{}" ]\n'.format(
        seen.as_posix(), (r.parent / "block").as_posix()
    )
    (hooks / "pre-push").write_bytes(mine.encode())  # the user's own hook, in the guard's place
    capsys.readouterr()
    assert team.af("state", "guard") == 0
    out = capsys.readouterr().out
    assert "Your pre-push hook was kept as pre-push.user; the push guard runs it first." in out
    assert (hooks / "pre-push.user").read_bytes() == mine.encode()
    assert push(r, "origin", "framegate/dev").returncode == 0
    assert seen.read_text(encoding="utf-8").split() == ["origin"]  # it ran, with its arguments
    assert push(r, "origin", "framegate-work/demo").returncode != 0  # the guard after it
    (r.parent / "block").write_text("x", encoding="utf-8")  # the user's hook says no: no push
    assert push(r, "origin", "dev:refs/heads/copy").returncode != 0
    assert "refs/heads/copy" not in refs(o)
    assert team.af("state", "guard") == 0  # again: nothing to do, nothing moved
    assert (hooks / "pre-push.user").read_bytes() == mine.encode()

    # both names taken by hooks that are not framegate's: nothing is overwritten
    (hooks / "pre-push").write_bytes(b"#!/bin/sh\nexit 0\n")
    capsys.readouterr()
    assert team.af("state", "guard") == 1
    assert "Merge the two by hand" in capsys.readouterr().out
    assert (hooks / "pre-push").read_bytes() == b"#!/bin/sh\nexit 0\n"
    items = {i.name: i for i in doctor.state_branch_items(cfg, get_adapter(cfg))}
    guard = items["git: push guard (pre-push hook)"]
    assert guard.status == "WARN" and "another pre-push hook is in its place" in guard.detail

    # hooks the repository manages elsewhere (husky, a shared folder): never written into
    (hooks / "pre-push").unlink()
    shared = r.parent / "shared-hooks"
    shared.mkdir()
    git(r, "config", "core.hooksPath", str(shared))
    assert team.af("state", "guard") == 1
    assert "core.hooksPath is set" in capsys.readouterr().out
    assert not list(shared.iterdir()) and not (hooks / "pre-push").exists()
    items = {i.name: i for i in doctor.state_branch_items(cfg, get_adapter(cfg))}
    assert "core.hooksPath is set" in items["git: push guard (pre-push hook)"].detail
    assert team.af("publish", "demo") == 0  # publish itself does not depend on the guard
    git(r, "config", "--unset", "core.hooksPath")

    # a config change moves what counts as framegate's: the guard is out of date until rewritten
    cfg.paths.metrics = ".framegate/metrics"
    state = publish.guard_state(cfg, publish.names(cfg))
    assert state == (False, "not installed: framegate state guard")
    assert team.af("state", "guard") == 0
    assert publish.guard_state(cfg, publish.names(cfg))[1].startswith("out of date")


def test_a_branch_under_the_teams_name_that_holds_framegate_files_is_renamed(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    """A feature started before the work branch had its own name: `spec` (resume) and
    `publish` find it under the product branch's name and rename it; never when the remote
    has a branch of that name with other content."""
    r, o = team.root, team.origin
    git(r, "branch", "-m", "framegate-work/demo", "feature/demo")
    head = sha(r, "HEAD")
    capsys.readouterr()
    assert team.af("spec", "demo") == 0
    out = capsys.readouterr().out
    assert "Renamed the local branch feature/demo to framegate-work/demo" in out
    assert git(r, "rev-parse", "--abbrev-ref", "HEAD").strip() == "framegate-work/demo"
    assert sha(r, "HEAD") == head and not ok(r, "rev-parse", "-q", "--verify", "feature/demo")
    assert git(r, "config", "branch.framegate-work/demo.framegateFeature").strip() == "demo"

    git(r, "branch", "-m", "framegate-work/demo", "feature/demo")
    assert team.af("publish", "demo", "--dry-run") == 0  # reads it, renames nothing
    assert git(r, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/demo"
    other = team.clone()
    git(other, "checkout", "-q", "-b", "feature/demo")
    git(other, "commit", "-q", "--allow-empty", "-m", "a colleague's branch of that name")
    git(other, "push", "-q", "origin", "feature/demo")
    theirs = sha(o, "feature/demo")
    capsys.readouterr()
    assert team.af("publish", "demo") == 1
    err = capsys.readouterr().err
    assert "origin has a branch feature/demo with other content" in err and "not renamed" in err
    assert "git branch -m feature/demo framegate-work/demo" in err
    assert git(r, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/demo"
    assert sha(o, "feature/demo") == theirs
    assert team.af("spec", "demo") == 1  # the same refusal: no second branch is started
    assert not ok(r, "rev-parse", "-q", "--verify", "refs/heads/framegate-work/demo")


# ---------- a project set up the old way ----------
def test_migration_of_a_feature_branch_that_holds_framegate_files(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The feature was built with framegate's files committed on its branch (set up there,
    never pushed). Switch on, `state init`, `publish`: the team gets the product only."""
    monkeypatch.setenv("FRAMEGATE_CLAUDE", str(FAKE_CLAUDE))
    root = build_base(tmp_path)
    origin = tmp_path / "origin.git"
    git(root, "checkout", "-q", "-b", "feature/skel")
    assert af(root, "init") == 0
    # `branch = false` (the user made the branch): no effect once the state branch is on
    configure(root, '[git]\nbase = "origin/dev"\nbranch_prefix = "feature/"\nbranch = false\n')
    git(root, "add", "-A")  # everything, .gitignore included, as the setup said then
    git(root, "commit", "-q", "-m", "framegate setup")
    assert af(root, "spec", "skel") == 0
    write_spec(root, "skel")
    (root / "src" / "demo" / "__init__.py").write_text(STUB, encoding="utf-8")
    assert af(root, "approve-spec", "skel") == 0
    finish(root, "skel")
    old_head = sha(root, "HEAD")

    # 1. switch on (committed on the feature branch), 2. state init, 3. publish
    toml = root / "framegate.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(
        text.replace("[git]\n", '[git]\nstate_branch = "framegate/{base}"\n', 1), encoding="utf-8"
    )
    git(root, "commit", "-q", "-m", "framegate: state branch", "framegate.toml")
    capsys.readouterr()
    assert af(root, "state", "init") == 0
    out = capsys.readouterr().out
    assert "Created the state branch framegate/dev" in out
    # the branch held framegate's files under the name the team will see: renamed, local
    assert "Renamed the local branch feature/skel to framegate-work/skel" in out
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "framegate-work/skel"
    assert not ok(root, "rev-parse", "--verify", "-q", "refs/heads/feature/skel")
    assert "Push guard installed" in out
    assert ".gitignore: took out the lines framegate once added" in out
    assert "out/" in (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert top_level(root, "framegate/dev") <= WORKFLOW_TOP
    assert "specs/skel/requirements.md" in files(root, "framegate/dev")
    assert af(root, "publish", "skel", "--dry-run") == 0
    out = capsys.readouterr().out
    # the run-file lines the setup once put into .gitignore are gone from the product
    assert ".gitignore" not in out
    assert af(root, "publish", "skel") == 0

    assert set(refs(origin)) == {
        *("refs/heads/dev", "refs/heads/framegate/dev", "refs/heads/feature/skel")
    }
    dev = sha(origin, "dev")
    assert git(origin, "rev-list", "--parents", "-1", "feature/skel").split()[1:] == [dev]
    assert not top_level(origin, "feature/skel") & WORKFLOW_TOP
    assert files(origin, "feature/skel") == files(origin, "dev") | {"tests/test_skel.py"}
    assert ",skel," in show(origin, "framegate/dev", "metrics/workflow_metrics.csv")
    # the old work branch is all still there, under its new name only
    assert ok(root, "merge-base", "--is-ancestor", old_head, "HEAD")
    assert not ok(root, "rev-parse", "--verify", "-q", "refs/heads/feature/skel")

    # the next feature starts from the clean base and gets the files from the state branch
    git(root, "checkout", "-q", "dev")
    assert not (root / "framegate.toml").exists()
    assert af(root, "spec", "next") == 0
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "framegate-work/next"
    assert git(root, "merge-base", "HEAD", "origin/dev").strip() == dev
    assert (root / "specs" / "skel" / "requirements.md").is_file()
    assert "state_branch" in (root / "framegate.toml").read_text(encoding="utf-8")


# ---------- off: as before ----------
def test_off_by_default(demo_repo: Any, capsys: pytest.CaptureFixture[str]) -> None:
    r = demo_repo
    assert not publish.enabled(load_config(r.root))
    capsys.readouterr()
    assert r.af("publish", "demo", "--dry-run") == 0
    assert "State branch: off" in capsys.readouterr().out
    assert r.af("publish", "demo") == 1
    assert "The state branch is off" in capsys.readouterr().err
    assert r.af("state") == 0
    assert "State branch: off" in capsys.readouterr().out
    assert r.af("state", "init") == 1
    assert not git(r.root, "branch", "--list", "framegate/*").strip()
    assert not (r.root / ".git" / "info" / "attributes").exists()


# ---------- the pull request ----------
class Tool:
    """Stands in for gh / az: records the calls, answers like the tool."""

    def __init__(self, stdout: str = "", code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.stdout, self.code = stdout, code

    def __call__(self, cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        probe = "extension" in cmd
        out = "" if probe else self.stdout
        return subprocess.CompletedProcess(cmd, 0 if probe else self.code, out, "boom")


def test_pull_request_on_github(
    team: Team, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    r = team.root
    cfg = load_config(r)
    git(r, "remote", "set-url", "origin", "git@github.com:acme/widgets.git")
    n = publish.names(cfg)
    lines: list[str] = []
    page = "https://github.com/acme/widgets/compare/dev...feature/demo?expand=1"
    # not asked for, or no gh: where to open it and where its text is
    assert publish.pull_request(cfg, n, "demo", "demo: add", lines.append, create=True)
    assert lines[-1] == (
        "Pull request feature/demo -> dev: not created: `gh` is not installed; open it at "
        f"{page}; its description: out/demo/final_summary.md (copy it before the cleanup)."
    )
    tool = Tool("https://github.com/acme/widgets/pull/7\n")
    monkeypatch.setattr(publish, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(publish, "run_tool", tool)
    assert publish.pull_request(cfg, n, "demo", "demo: add", lines.append, create=False)
    assert "not created (publish with --pr creates it)" in lines[-1] and not tool.calls
    assert publish.pull_request(cfg, n, "demo", "demo: add", lines.append, create=True)
    assert tool.calls == [
        [
            *("/bin/gh", "pr", "create", "--repo", "acme/widgets", "--base", "dev"),
            *("--head", "feature/demo", "--title", "demo: add"),
            *("--body-file", str(r / "out" / "demo" / "final_summary.md")),
        ]
    ]
    assert lines[-1] == (
        "Pull request feature/demo -> dev created: https://github.com/acme/widgets/pull/7"
    )
    # the tool fails (no login, a pull request exists): said, with the way by hand
    monkeypatch.setattr(publish, "run_tool", Tool(code=1))
    assert not publish.pull_request(cfg, n, "demo", "demo: add", lines.append, create=True)
    assert "`gh pr create` failed (boom); open it at " + page in lines[-1]

    # what is left to do by hand outside the repository: an unticked checklist on top of the
    # description, for the task's pull request and for a pipeline-only one
    kept = r / "specs" / "demo" / "pipeline_followup.md"
    kept.write_text(
        "- [x] .github/workflows/ci.yml: run the new tests\n"
        "- [ ] OUTSIDE [CI variable group]: add DEMO_TOKEN (the job reads it)\n"
        "- [x] OUTSIDE [vault]: done already\n",
        encoding="utf-8",
    )
    described = r / "out" / "demo" / "pr_description.md"
    tool = Tool("https://github.com/acme/widgets/pull/9\n")
    monkeypatch.setattr(publish, "run_tool", tool)
    del lines[:]
    assert publish.pull_request(
        cfg, n, "demo", "demo: add", lines.append, create=False, write=False
    )  # a dry run: said, nothing written
    assert lines[0].startswith("Outside the repository: 1 item(s) to do by hand; an unticked")
    assert not described.exists()
    assert publish.pull_request(cfg, n, "demo", "demo: add", lines.append, create=True)
    (call,) = tool.calls
    assert call[call.index("--body-file") + 1] == str(described)
    text = described.read_text(encoding="utf-8")
    summary = (r / "out" / "demo" / "final_summary.md").read_text(encoding="utf-8")
    head = "## Outside the repository, by hand (framegate does not do them): 1\n\n"
    assert text.startswith(head + "- [ ] CI variable group: add DEMO_TOKEN (the job reads it)\n")
    assert "vault" not in text and summary.strip() in text
    brief = r / "out" / "demo" / "pipeline_brief.md"
    brief.write_text("**What was asked**\n1. run the new tests\n", encoding="utf-8")
    assert publish.pull_request(
        *(cfg, n, "demo", "demo: deploy / CI changes", lines.append),
        create=False,
        head="pipeline/demo",
        body="out/demo/pipeline_brief.md",
    )
    assert "its description: out/demo/pr_description.md (copy it before the cleanup)" in lines[-1]
    text = described.read_text(encoding="utf-8")
    assert text.startswith(head) and text.endswith("**What was asked**\n1. run the new tests\n")


def test_pull_request_on_azure_devops(
    team: Team, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    r = team.root
    cfg = load_config(r)
    url = "https://contoso@dev.azure.com/contoso/Data%20Platform/_git/da-argos"
    git(r, "remote", "set-url", "origin", url)
    n = publish.names(cfg)
    summary = r / "out" / "demo" / "final_summary.md"
    summary.write_text("line of the summary\n" * 400, encoding="utf-8")  # over the host's limit
    lines: list[str] = []
    tool = Tool("412\n")
    monkeypatch.setattr(publish, "which", lambda name: f"C:/tools/{name}.cmd")
    monkeypatch.setattr(publish, "run_tool", tool)
    assert publish.pull_request(cfg, n, "demo", 'demo: "a" & b', lines.append, create=True)
    probe, create = tool.calls
    assert probe == [
        *("C:/tools/az.cmd", "extension", "show", "--name", "azure-devops", "--output", "none")
    ]
    short = r / "out" / "demo" / "pr_description.md"
    assert create == [
        *("C:/tools/az.cmd", "repos", "pr", "create", "--detect", "false"),
        *("--organization", "https://dev.azure.com/contoso"),
        *("--project", "Data Platform", "--repository", "da-argos"),
        *("--source-branch", "feature/demo", "--target-branch", "dev"),
        *("--title", "demo: a  b"),  # a batch file's arguments pass through cmd.exe
        *("--description", f"@{short}"),
        *("--query", "pullRequestId", "--output", "tsv"),
    ]
    text = short.read_text(encoding="utf-8")
    assert len(text) <= hosts.AZURE_DESCRIPTION_MAX and "full text: final_summary.md" in text
    base = "https://dev.azure.com/contoso/Data%20Platform/_git/da-argos"
    assert lines[-1] == f"Pull request feature/demo -> dev created: {base}/pullrequest/412"

    # az without the devops extension: the page to open it by hand
    class NoExtension(Tool):
        def __call__(self, cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, "", "not installed")

    bare = NoExtension()
    monkeypatch.setattr(publish, "run_tool", bare)
    assert publish.pull_request(cfg, n, "demo", "demo", lines.append, create=True)
    assert len(bare.calls) == 1 and "`az` has no azure-devops extension" in lines[-1]
    assert (
        f"open it at {base}/pullrequestcreate?sourceRef=feature%2Fdemo&targetRef=dev" in lines[-1]
    )


# ---------- doctor, the chat's wrap-up ----------
def test_doctor_items_with_a_state_branch(team: Team) -> None:
    r = team.root
    cfg = load_config(r)
    adapter = get_adapter(cfg)
    items = {i.name: i for i in doctor.state_branch_items(cfg, adapter)}
    assert items["git: state branch 'framegate/dev'"].status == "OK"
    assert items["git: push guard (pre-push hook)"].status == "OK"
    assert not any("holds files under" in name for name in items)
    # the tests go to people without framegate: its marker must be the project's own
    marker = items["team: pytest without framegate knows the `req` marker"]
    assert marker.status == "WARN" and "req(*ids): spec IDs this test proves" in marker.detail
    # the whole doctor: these items are in it, the old "framegate.toml is not on the base"
    # warning is not (it is the point), and the run files count as ignored (info/exclude)
    lines: list[str] = []
    assert doctor.run(cfg, echo=lines.append) == 0
    out = "\n".join(lines)
    assert "[OK] git: state branch 'framegate/dev'" in out and "[OK] .gitignore" in out
    assert "is not on 'dev'" not in out
    assert "- WARN team: pytest without framegate knows the `req` marker" in out
    with open(r / "pyproject.toml", "a", encoding="utf-8") as fh:
        fh.write('markers = ["req(*ids): spec IDs this test proves"]\n')
    items = {i.name: i for i in doctor.state_branch_items(cfg, adapter)}
    assert items["team: pytest without framegate knows the `req` marker"].status == "OK"
    # a pull request's tests then run in an environment without the plugin, strict or not
    code = subprocess.run(
        [adapter.python, "-m", "pytest", "-q", "-p", "no:framegate", "--strict-markers", "tests"],
        cwd=r,
        capture_output=True,
    ).returncode
    assert code == 0
    git(r, "branch", "-q", "-D", "framegate/dev")
    items = {i.name: i for i in doctor.state_branch_items(cfg, adapter)}
    assert items["git: state branch 'framegate/dev'"].status == "WARN"


def test_the_chat_offers_the_publish_only_with_a_state_branch() -> None:
    from framegate.orchestrator import package_text

    text = package_text("templates", "chat_protocol.md")
    wrap_up = text[text.index("## D. DONE") :]
    assert "framegate publish <f> --dry-run" in wrap_up
    assert "framegate publish <f> --pr" in wrap_up
    assert "State branch: off" in wrap_up and "gh pr create" in wrap_up  # the old offer stays
    assert "Never push without that yes" in wrap_up
    # the pipeline follow-up is offered before the publish: both commits in the first push
    offer = wrap_up.index("Do the pipeline follow-up now? (`/framegate:pipeline <f>`)")
    assert offer < wrap_up.index("framegate publish <f> --dry-run")


# ---------- the pipeline task: deploy / CI files are a commit of their own ----------
CI_FILE = ".github/workflows/ci.yml"
CI_TEXT = "jobs:\n  unit:\n    steps:\n      - run: pytest tests/test_demo.py\n"
CI_ITEM = f"{CI_FILE}: run the new tests (CI does not select them)"


def pipeline_task(root: pathlib.Path, name: str = "demo", item: str = CI_ITEM) -> None:
    """A pipeline task as the chat does it: start, the list (kept at DONE, or written now),
    the edit, the check, the user's OK."""
    assert af(root, "pipeline", name) == 0
    listed = root / "specs" / name / "pipeline_followup.md"
    listed.parent.mkdir(parents=True, exist_ok=True)
    listed.write_text(f"# Pipeline follow-up: {name}\n\n- [ ] {item}\n", encoding="utf-8")
    (root / CI_FILE).parent.mkdir(parents=True, exist_ok=True)
    (root / CI_FILE).write_text(CI_TEXT, encoding="utf-8")
    assert af(root, "pipeline-check", name) == 0
    assert af(root, "pipeline-approve", name) == 0
    assert f"- [x] {item}" in listed.read_text(encoding="utf-8")
    assert not (root / ".phase").exists() and not git(root, "status", "--porcelain").strip()


def parents(root: pathlib.Path, rev: str) -> list[str]:
    return git(root, "rev-list", "--parents", "-1", rev).split()[1:]


def changed(root: pathlib.Path, rev: str) -> set[str]:
    return set(git(root, "diff", "--name-only", f"{rev}^", rev).split())


def tree(root: pathlib.Path, rev: str) -> str:
    return git(root, "rev-parse", f"{rev}^{{tree}}").strip()


def test_pipeline_before_the_publish_two_commits_in_the_first_push(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r, o = team.root, team.origin
    dev = sha(o, "dev")
    pipeline_task(r)
    capsys.readouterr()
    assert team.af("publish", "demo", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Product branch feature/demo: 3 file(s), 1 of them deploy / CI files in a commit" in out
    assert team.af("publish", "demo", "--title", "demo: add two ints") == 0
    # dev <- the product <- the deploy / CI files, on the one product branch
    second = sha(o, "feature/demo")
    (first,) = parents(o, second)
    assert parents(o, first) == [dev]
    assert git(o, "log", "--format=%s", f"{dev}..feature/demo").splitlines() == [
        "demo: deploy / CI changes",
        "demo: add two ints",
    ]
    assert changed(o, first) == {"src/demo/__init__.py", "tests/test_demo.py"}
    assert changed(o, second) == {CI_FILE}
    assert show(o, "feature/demo", CI_FILE) == CI_TEXT
    assert not top_level(o, "feature/demo") & WORKFLOW_TOP
    assert "refs/heads/pipeline/demo" not in refs(o)
    assert "- [x] " in show(o, "framegate/dev", "specs/demo/pipeline_followup.md")
    # again: nothing moves
    there = refs(o)
    assert team.af("publish", "demo", "--title", "demo: add two ints") == 0
    assert refs(o) == there
    # a remote branch that was replaced: with the lease both commits are built again
    assert push(r, "--no-verify", "-f", "origin", "dev:feature/demo").returncode == 0
    assert team.af("publish", "demo") == 1
    assert team.af("publish", "demo", "--force-with-lease") == 0
    rebuilt = sha(o, "feature/demo")
    assert changed(o, rebuilt) == {CI_FILE} and parents(o, parents(o, rebuilt)[0]) == [dev]
    assert tree(o, rebuilt) == tree(o, second)


def test_pipeline_after_the_publish_is_a_second_commit_on_the_same_branch(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r, o = team.root, team.origin
    assert team.af("publish", "demo") == 0
    first = sha(o, "feature/demo")
    # while the task is open nothing is published
    assert team.af("pipeline", "demo") == 0
    capsys.readouterr()
    assert team.af("publish", "demo") == 1
    assert "The pipeline task of demo is open" in capsys.readouterr().err
    pipeline_task(r)
    assert team.af("publish", "demo") == 0
    second = sha(o, "feature/demo")
    assert parents(o, second) == [first]  # added, never rewritten
    assert changed(o, second) == {CI_FILE}
    assert git(o, "log", "-1", "--format=%s", second).strip() == "demo: deploy / CI changes"
    assert sha(r, publish.product_ref("demo")) == second


def squash_merge(team: Team, branch: str = "feature/demo") -> None:
    """The pull request is merged on the host (a squash); this clone's `dev` does not know."""
    other = team.clone("merger")
    git(other, "merge", "-q", "--squash", f"origin/{branch}")
    git(other, "commit", "-q", "-m", f"{branch} (#1)")
    git(other, "push", "-q", "origin", "dev")


def test_pipeline_of_a_merged_task_gets_its_own_branch_and_pull_request(
    team: Team, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    r, o = team.root, team.origin
    start = sha(r, "dev")
    assert team.af("publish", "demo") == 0
    product = sha(o, "feature/demo")
    squash_merge(team)
    toml = r / "framegate.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text.replace("[git]\n", '[git]\npipeline_prefix = "infra/"\n', 1), "utf-8")
    git(r, "commit", "-q", "-m", "config", "framegate.toml")
    pipeline_task(r)
    brief = r / "out" / "demo" / "pipeline_brief.md"
    brief.write_text("**What was asked**\n1. run the new tests\n", encoding="utf-8")
    capsys.readouterr()
    assert team.af("publish", "demo") == 0
    out = capsys.readouterr().out
    assert "Pipeline branch infra/demo (the product is merged): 1 deploy / CI file(s)" in out
    assert "Pull request infra/demo -> dev: not created: no tool for this host" in out
    assert "its description: out/demo/pipeline_brief.md (copy it before the cleanup)" in out
    # its own branch: one commit on the commit the work started from, the CI file only
    tip = sha(o, "infra/demo")
    assert parents(o, tip) == [start] and changed(o, tip) == {CI_FILE}
    assert files(o, "infra/demo") == files(o, start) | {CI_FILE}
    assert git(o, "log", "-1", "--format=%s", tip).strip() == "demo: deploy / CI changes"
    assert sha(o, "feature/demo") == product  # the merged branch is left alone
    assert sha(r, publish.pipeline_ref("demo")) == tip
    there = refs(o)
    assert team.af("publish", "demo") == 0  # again: in line
    assert refs(o) == there
    # the pull request, when the host has a tool: this branch, the approved brief as its text
    cfg = load_config(r)
    git(r, "remote", "set-url", "origin", "git@github.com:acme/widgets.git")
    tool = Tool("https://github.com/acme/widgets/pull/8\n")
    monkeypatch.setattr(publish, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(publish, "run_tool", tool)
    assert publish.pull_request(
        *(cfg, publish.names(cfg), "demo", "demo: deploy / CI changes", lambda s: None),
        create=True,
        head="infra/demo",
        body="out/demo/pipeline_brief.md",
    )
    (call,) = tool.calls
    assert call[call.index("--head") + 1] == "infra/demo"
    assert call[call.index("--body-file") + 1] == str(brief)
    assert team.af("cleanup", "demo", "--tags") == 0
    assert publish.pipeline_ref("demo") not in refs(r)
    with pytest.raises(ConfigError, match="pipeline_prefix"):
        parse_config(r, {"git": {"pipeline_prefix": "feat/"}})


def test_a_pipeline_task_of_its_own_with_a_state_branch(
    team: Team, capsys: pytest.CaptureFixture[str]
) -> None:
    r, o = team.root, team.origin
    git(r, "push", "-q", "origin", "framegate/dev")
    git(r, "checkout", "-q", "dev")  # no framegate.toml here: read from the state branch
    capsys.readouterr()
    pipeline_task(r, "ci-cache", f"{CI_FILE}: cache the packages (slow builds)")
    out = capsys.readouterr().out
    assert "Pipeline task ci-cache (on its own: no task of this name)" in out
    assert "No list yet: write it to specs/ci-cache/pipeline_followup.md" in out
    assert git(r, "rev-parse", "--abbrev-ref", "HEAD").strip() == "framegate-work/ci-cache"
    assert team.af("publish", "ci-cache") == 0  # no spec, no metrics row: nothing to be DONE
    out = capsys.readouterr().out
    assert "Pipeline branch pipeline/ci-cache (this branch changed nothing else)" in out
    assert "its description: specs/ci-cache/pipeline_followup.md." in out
    tip = sha(o, "pipeline/ci-cache")
    assert parents(o, tip) == [sha(o, "dev")] and changed(o, tip) == {CI_FILE}
    assert not top_level(o, "pipeline/ci-cache") & WORKFLOW_TOP
    assert "refs/heads/feature/ci-cache" not in refs(o)
    assert "- [x] " in show(o, "framegate/dev", "specs/ci-cache/pipeline_followup.md")
    # the work branch stays local: the guard still refuses it
    assert push(r, "origin", "framegate-work/ci-cache").returncode != 0
