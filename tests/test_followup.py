"""Deploy and CI files are never a feature's to change: `[protected] extra` stays locked in the
spec phase and refuses the approval when dirty; what a feature needs there is a pipeline
follow-up (the spec section, the agents' notes, the final brief, kept after DONE). The
per-machine `.claude/settings.local.json` never blocks."""

from __future__ import annotations

import pathlib

import pytest

from conftest import REQUIREMENTS, DemoRepo, git, marker
from framegate import followup
from framegate.adapters import get_adapter
from framegate.checks import frame_diff
from framegate.config import load_config, parse_config
from framegate.orchestrator import EXIT_GATE
from framegate.scaffold import package_text, spec_brief

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
EXTRA = '["ci/", "platform/deploy/", "*_pb2.py", "pytest.ini", "requirements.txt"]'
SPEC_ITEM = "platform/deploy/Dockerfile: install the new dependency (the image lacks it)"
STEP_ITEM = "deploy/Dockerfile: COPY the new demo/ folder (the image does not contain it)"
REVIEW_ITEM = "ci/tests.yml: add tests/test_demo.py to the unit selection (CI does not run it)"
OUTSIDE_ITEM = "OUTSIDE [CI variable group demo-dev]: add DEMO_TOKEN (the new job reads it)"
OUTSIDE_SHOWN = "CI variable group demo-dev: add DEMO_TOKEN (the new job reads it) (from the spec)"


def write(r: DemoRepo, rel: str, text: str) -> None:
    path = r.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def to_final_gate(r: DemoRepo) -> None:
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE


# ---------- [protected] extra is not setup ----------
def test_setup_paths_per_adapter(tmp_path: pathlib.Path) -> None:
    """The setup subset is the adapter's own packaging / test / tool-config list; what a
    project adds under [protected] extra never joins it."""
    py = get_adapter(parse_config(tmp_path, {"protected": {"extra": ["ci/", "deploy/"]}}))
    assert {"pyproject.toml", "pytest.ini", "ruff.toml", ".ruff.toml", "requirements"} <= set(
        py.setup_paths()
    )
    assert not {"ci/", "deploy/"} & set(py.setup_paths())
    assert frame_diff.setup_entries(py)[0] == "framegate.toml"
    assert "ci/" not in frame_diff.setup_entries(py)
    net = get_adapter(
        parse_config(
            tmp_path, {"project": {"language": "dotnet"}, "protected": {"extra": ["k8s/"]}}
        )
    )
    assert "*.csproj" in net.setup_paths() and "k8s/" not in net.setup_paths()
    assert frame_diff.is_setup_file(net, "src/App/App.csproj")
    assert not frame_diff.is_setup_file(net, "k8s/app.yml")
    assert frame_diff.extra_locked(net, "k8s/app.yml")
    generic = get_adapter(
        parse_config(
            tmp_path,
            {
                "project": {"language": "generic"},
                "generic": {
                    "source_suffixes": [".ts"],
                    "test_files": ["**/*.test.ts"],
                    "tag_pattern": "req\\(([^)]*)\\)",
                    "test_all": ["npx", "vitest"],
                    "test_ids": ["npx", "vitest", "{ids_regex}"],
                    "protected": ["package.json"],
                },
                "protected": {"extra": ["Dockerfile"]},
            },
        )
    )
    assert frame_diff.is_setup_file(generic, "package.json")
    assert not frame_diff.is_setup_file(generic, "Dockerfile")


def test_extra_paths_refuse_the_approval(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """A CI file, a deploy file and a generated file inside a source folder, all only in
    [protected] extra: not committed with the spec, the approval refuses and says where such a
    change belongs. pytest.ini and requirements.txt are listed in extra too and stay setup."""
    r = demo_repo
    r.set_toml("protected", "extra", EXTRA)
    write(r, "ci/build.yml", "steps: []\n")
    write(r, "platform/deploy/Dockerfile", "FROM python\n")
    write(r, "src/demo/wsic_pb2.py", "X = 1\n")
    write(r, "pytest.ini", "[pytest]\n")
    write(r, "requirements.txt", "requests\n")
    adapter = get_adapter(load_config(r.root))
    stray = ["ci/build.yml", "platform/deploy/Dockerfile", "src/demo/wsic_pb2.py"]
    assert frame_diff.setup_state(adapter) == (["pytest.ini", "requirements.txt"], stray)
    assert r.af("spec-check", "demo") == 1
    out = capsys.readouterr().out
    assert "3 protected file(s) have uncommitted changes" in out
    assert "Setup files changed (committed with the spec): pytest.ini, requirements.txt" in out
    assert r.af("approve-spec", "demo", "--force") == 1
    err = capsys.readouterr().err
    assert ", ".join(stray) in err and "`## Pipeline follow-up`" in err
    assert not marker(r.root, "spec-approved/demo")

    brief = spec_brief(load_config(r.root), "demo")
    line = next(x for x in brief.splitlines() if x.startswith("- Setup files"))
    assert "pytest.ini" in line and "ci/" not in line and "platform/deploy/" not in line
    follow = next(x for x in brief.splitlines() if x.startswith("- Pipeline follow-up"))
    assert "here also [protected] extra: ci/, platform/deploy/, *_pb2.py" in follow
    assert "never changed by a feature, not even now" in follow

    for rel in stray:
        (r.root / rel).unlink()
    assert r.af("approve-spec", "demo") == 0
    committed = git(r.root, "show", "--name-only", "--format=", "HEAD").split()
    assert {"pytest.ini", "requirements.txt"} <= set(committed)


# ---------- .claude/settings.local.json ----------
def test_the_per_machine_settings_file_never_blocks(demo_repo: DemoRepo) -> None:
    """Untracked and not ignored (as Claude Code leaves it, and as framegate's own docs tell the
    user to fill it): no stray file at the approval, no frame violation when it changes during
    the run. The shared settings.json next to it stays protected."""
    r = demo_repo
    r.add_toml(FAST)
    local = ".claude/settings.local.json"
    # untracked and not ignored, whatever this machine's global ignore file says (Claude
    # Code adds the file there on some machines; the fixture tracks it where it does not)
    git(r.root, "config", "core.excludesFile", str(r.root.parent / "no-global-ignore"))
    git(r.root, "rm", "-q", "--cached", "--ignore-unmatch", local)
    git(r.root, "commit", "-q", "--allow-empty", "-m", "untrack the per-machine settings")
    assert f"?? {local}" in git(r.root, "status", "--porcelain", "-uall")
    adapter = get_adapter(load_config(r.root))
    assert frame_diff.setup_state(adapter) == ([], [])
    assert r.af("approve-spec", "demo") == 0
    # not committed with the spec (HEAD may still be the commit above that untracked it)
    assert local not in git(r.root, "log", "-1", "--name-only", "--diff-filter=AM", "--format=")
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    write(r, local, '{"permissions": {"allow": ["Bash(framegate *)", "Bash(git log *)"]}}\n')
    assert frame_diff.check(adapter, "demo").status == "PASS"
    assert r.af("run", "demo") == EXIT_GATE
    assert not r.progress().get("parked")

    write(r, ".claude/settings.json", "{}\n")
    res = frame_diff.check(adapter, "demo")
    assert res.status == "FAIL"
    assert res.output == f"{frame_diff.PROTECTED_CHANGED}.claude/settings.json"
    assert frame_diff.setup_state(adapter) == ([], [".claude/settings.json"])


# ---------- pipeline follow-up ----------
def test_parse_the_spec_section_and_the_run_lines() -> None:
    assert followup.parse_spec(REQUIREMENTS) == []
    for empty in ("None.", "none", "<!-- hint\n- a: b -->\nNone.\n", "", "N/A", "-"):
        assert followup.parse_spec(f"## Pipeline follow-up\n{empty}\n\n## Next\n- x\n") == []
    text = (
        "## Pipeline follow-up\n<!-- - not: this -->\n- `Dockerfile`: COPY runner/ (new "
        "package)\n* ci/unit.yml: add the folder\n\n## Open questions\n- other\n"
    )
    assert followup.parse_spec(text) == [
        "`Dockerfile`: COPY runner/ (new package)",
        "ci/unit.yml: add the folder",
    ]
    assert followup.parse_spec("## Pipeline follow-up\nThe image needs libpq.\n") == [
        "The image needs libpq."
    ]
    notes = (
        "## Step 1\nBuilt it.\n- ASSUMPTION [X-R-001]: fine\n"
        "- PIPELINE FOLLOW-UP [`deploy/Dockerfile`]: COPY runner/ (missing)\n"
        "* pipeline followup [ci/unit.yml] add the folder\n- PIPELINE [none]: rebuild the image\n"
    )
    assert followup.parse_run(notes) == [
        "deploy/Dockerfile: COPY runner/ (missing)",
        "ci/unit.yml: add the folder",
        "rebuild the image",
    ]
    assert followup.brief_lines([]) == [] and followup.section_lines([]) == []


def test_items_outside_the_repository_have_one_form() -> None:
    """`- OUTSIDE [<where>]: <what> (<why>)` in the spec section and in an agent's notes; the
    same text in the kept list. Shown apart, never mixed with the repository's items."""
    spec = (
        "## Pipeline follow-up\n- ci/unit.yml: add the folder\n"
        "- OUTSIDE [vault kv/argos]: add the queue password (the runner logs in with it)\n"
        "- `outside follow-up` [prod database] run migration 0042\n\n## Next\n"
    )
    assert followup.parse_spec(spec) == [
        "ci/unit.yml: add the folder",
        "OUTSIDE [vault kv/argos]: add the queue password (the runner logs in with it)",
        "OUTSIDE [prod database]: run migration 0042",
    ]
    notes = (
        "- PIPELINE FOLLOW-UP [Dockerfile]: COPY runner/\n"
        "* OUTSIDE [`CI variable group`]: set RUNNER_TOKEN (the job reads it)\n"
        "- outside the repository nothing changes\n- OUTSIDE: no place named\n"
    )
    assert followup.parse_run(notes) == [
        "Dockerfile: COPY runner/",
        "OUTSIDE [CI variable group]: set RUNNER_TOKEN (the job reads it)",
    ]
    # only the pipeline list has them
    assert followup.parse_run(notes, followup.REFERENCE) == []
    found = [
        followup.Item("Dockerfile: COPY runner/", "implementer"),
        followup.Item("OUTSIDE [vault]: add the password", "spec"),
    ]
    assert [it.outside for it in found] == [False, True]
    assert found[1].line() == "OUTSIDE [vault]: add the password (from the spec)"
    assert found[1].shown() == "vault: add the password (from the spec)"
    assert followup.brief_lines(found) == [
        "Pipeline follow-up (a separate step after this feature; not part of it): 1; nothing "
        "to answer",
        "  - Dockerfile: COPY runner/ (implementer)",
        "Pipeline follow-up, outside the repository: by hand: 1; nothing to answer (a "
        "checklist in the pull request)",
        "  - vault: add the password (from the spec)",
    ]
    by_hand = "## Outside the repository, by hand (framegate does not do them): 1"
    assert followup.sections(found) == [
        [
            "## Pipeline follow-up (a separate step after this feature; not part of it): 1",
            "",
            "- Dockerfile: COPY runner/ (implementer)",
        ],
        [by_hand, "", "- [ ] vault: add the password (from the spec)"],
    ]
    assert followup.sections(found[1:]) == [
        [by_hand, "", "- [ ] vault: add the password (from the spec)"]
    ]
    assert "- [ ] OUTSIDE [vault]: add the password (from the spec)" in followup.markdown(
        "demo", found
    )
    assert followup.with_checklist("# Summary\n\ntext\n", []) == "# Summary\n\ntext\n"
    assert followup.with_checklist("", ["vault: x"]) == f"{by_hand}\n\n- [ ] vault: x\n"


# ---------- the built-in deploy / CI names ----------
def test_builtin_names_are_deploy_files_everywhere(demo_repo: DemoRepo) -> None:
    """No [protected] extra entry: a Dockerfile, a compose file and a CI yml are deploy / CI
    files by their name, at any depth; a folder name that can mean something else is not.
    The frame, the stray-file check and the spec brief agree."""
    r = demo_repo
    adapter = get_adapter(load_config(r.root))
    yes = (
        *("Dockerfile", "src/demo/Dockerfile", "a/b/dockerfile.prod", "img/api.Dockerfile"),
        *(".dockerignore", "docker-compose.yml", "x/docker-compose.override.yaml"),
        *("compose.yaml", "x/compose.dev.yml", "Jenkinsfile", ".circleci/config.yml"),
        *("bitbucket-pipelines.yml", ".azure-pipelines/jobs/build.yml", "azure-pipelines.yml"),
        *("ci/azure-pipelines-pr.yml", ".gitlab-ci.yml", ".gitlab-ci.deploy.yml", ".travis.yml"),
        ".github/workflows/ci.yml",
    )
    no = (
        *(
            "deploy/job.yml",
            "ci/build.sh",
            "k8s/app.yml",
            "helm/values.yaml",
            "charts/x/Chart.yaml",
        ),
        *("src/demo/compose.py", "src/demo/dockerfile.py", "docs/Dockerfile.md/x.txt"),
        *("tests/data/Dockerfile", "specs/demo/compose.yml", "out/demo/compose.yml"),
        *("pyproject.toml", "framegate.toml", "sub/.github/workflows/ci.yml"),
    )
    protected = frame_diff.protected_paths(adapter)
    for rel in yes:
        assert frame_diff.is_pipeline_file(adapter, rel), rel
        assert frame_diff.path_matches(rel, protected), rel
    for rel in no:
        assert not frame_diff.is_pipeline_file(adapter, rel), rel
    assert frame_diff.path_matches("Dockerfile", frame_diff.spec_locked(adapter.cfg))
    # a source file that a built-in name happens to match is source: for the frame as well
    assert not frame_diff.is_protected(adapter, "src/demo/dockerfile.py", protected)
    assert frame_diff.is_protected(adapter, "src/demo/Dockerfile.dev", protected)
    assert frame_diff.is_protected(adapter, "tests/dockerfile.py", protected)

    write(r, "src/demo/Dockerfile", "FROM python\n")
    write(r, "docker-compose.yml", "services: {}\n")
    write(r, "deploy/job.yml", "x: 1\n")  # not built in, not in extra: not the frame's business
    assert frame_diff.setup_state(adapter) == ([], ["docker-compose.yml", "src/demo/Dockerfile"])
    follow = next(
        x for x in spec_brief(adapter.cfg, "demo").splitlines() if x.startswith("- Pipeline")
    )
    assert "by name, at any depth: Dockerfile*" in follow and "[protected] extra" not in follow
    assert "`- OUTSIDE [<where>]: <what must be done> (<why>)`" in follow
    assert "'outside the repository: by hand'" in follow

    r.set_toml("protected", "builtin_off", '["Dockerfile", "docker-compose*.yml"]')
    adapter = get_adapter(load_config(r.root))
    assert frame_diff.setup_state(adapter) == ([], [])
    assert not frame_diff.is_pipeline_file(adapter, "src/demo/Dockerfile")
    assert frame_diff.is_pipeline_file(adapter, "src/demo/Dockerfile.dev")
    assert not frame_diff.path_matches("Dockerfile", frame_diff.protected_paths(adapter))
    follow = next(
        x for x in spec_brief(adapter.cfg, "demo").splitlines() if x.startswith("- Pipeline")
    )
    assert "not here ([protected] builtin_off): Dockerfile, docker-compose*.yml" in follow


def test_every_template_has_the_section_and_the_agents_are_told() -> None:
    for name in ("_template", "_template_bugfix", "_template_refactor"):
        text = package_text("templates", f"specs/{name}/requirements.md")
        assert "## Pipeline follow-up" in text, name
        assert followup.parse_spec(text) == [], name  # "None." by default
    for role in ("implement", "review"):
        assert "PIPELINE FOLLOW-UP [<file>]" in package_text("roles", f"{role}.md"), role
    assert "never a question, a decision" in package_text("roles", "summary.md")


def test_pipeline_followup_reaches_the_final_brief_and_survives_cleanup(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """One item from the spec, one from the implementer's step note, one from the reviewer:
    framegate lists all three at the final gate itself (facts, summary, brief), as a separate
    step and not as a gate item, and keeps the list in the spec folder after DONE."""
    r = demo_repo
    r.add_toml(FAST)
    write(
        r,
        "specs/demo/requirements.md",
        REQUIREMENTS + f"\n## Pipeline follow-up\n- {SPEC_ITEM}\n- {OUTSIDE_ITEM}\n",
    )
    r.scenario(followup=True)
    to_final_gate(r)
    task = next(c for c in r.calls() if c["phase"] == "IMPLEMENT")
    assert "`- PIPELINE FOLLOW-UP [<file>]: <what must change and why>`" in task["prompt"]
    assert "do not attempt it" in task["prompt"]

    wanted = [
        f"{SPEC_ITEM} (from the spec)",
        f"{STEP_ITEM} (implementer)",
        f"{REVIEW_ITEM} (reviewer)",
    ]
    cfg = load_config(r.root)
    # the item outside the repository is on the list, and apart from the three everywhere
    outside = f"{OUTSIDE_ITEM} (from the spec)"
    listed = [wanted[0], outside, *wanted[1:]]
    assert [it.line() for it in followup.items(cfg, "demo")] == listed
    assert "`- OUTSIDE [<where>]: <what must be done> (<why>)`" in task["prompt"]
    run_file = (r.root / "out/demo/pipeline_followup.md").read_text(encoding="utf-8")
    assert all(f"- [ ] {x}" in run_file for x in listed)
    assert "A line that starts with OUTSIDE is no file of this repository" in run_file
    facts = (r.root / "out/demo/final_facts.md").read_text(encoding="utf-8")
    head = "## Pipeline follow-up (a separate step after this feature; not part of it): 3"
    assert head in facts and all(f"- {x}" in facts for x in wanted)
    by_hand = "## Outside the repository, by hand (framegate does not do them)"
    assert f"{by_hand}: 1\n\n- [ ] {OUTSIDE_SHOWN}\n" in facts
    assert facts.count("Not a gate item") == 2
    assert "## Gate items (numbered as the final gate shows them): 0" in facts
    summary = (r.root / "out/demo/final_summary.md").read_text(encoding="utf-8")
    assert (
        "## Pipeline follow-up (a separate step after this feature; not part of it) "
        "(generated by framegate): 3" in summary
    )
    assert all(f"- {x}" in summary for x in wanted)
    # an unticked checklist in the summary, and on top of a pull request's description
    assert f"{by_hand} (generated by framegate): 1\n\n- [ ] {OUTSIDE_SHOWN}\n" in summary
    assert followup.pr_checklist(cfg, "demo") == [OUTSIDE_SHOWN]
    text = followup.with_checklist(summary, [OUTSIDE_SHOWN])
    assert text.startswith(f"{by_hand}: 1\n\n- [ ] {OUTSIDE_SHOWN}\n\n")
    assert text.count(by_hand) == 1 and "## Cost, time, turns per phase" in text

    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    brief = capsys.readouterr().out
    assert (
        "Pipeline follow-up (a separate step after this feature; not part of it): 3; nothing "
        "to answer\n"
        + "".join(f"  - {x}\n" for x in wanted)
        + "  after this task: /framegate:pipeline demo does them\n"
        + "Pipeline follow-up, outside the repository: by hand: 1; nothing to answer (a "
        + "checklist in the pull request)\n"
        + f"  - {OUTSIDE_SHOWN}\n"
    ) in brief
    assert 'Decision: "ok" approves' in brief  # no question came of it
    assert r.progress()["waiting"]["gate"] == "final" and not r.progress().get("parked")

    assert r.af("finish", "demo", "--no-retro") == 0
    kept = r.root / "specs/demo/pipeline_followup.md"
    assert all(f"- [ ] {x}" in kept.read_text(encoding="utf-8") for x in listed)
    assert "specs/demo/pipeline_followup.md" in git(
        r.root, "show", "--name-only", "--format=%s", "HEAD"
    )
    (ev,) = [e for e in r.events() if e["kind"] == "pipeline_followup"]
    assert ev["items"] == listed
    # the pipeline task has three items to do; the fourth is counted apart
    assert (followup.open_count(cfg, "demo"), followup.outside_count(cfg, "demo")) == (3, 1)
    capsys.readouterr()
    assert r.af("status", "demo", "--brief") == 0
    assert (
        "Pipeline follow-up: 3 open item(s) -> /framegate:pipeline demo; outside the "
        "repository: 1 item(s) to do by hand (specs/demo/pipeline_followup.md)"
    ) in capsys.readouterr().out
    assert r.af("cleanup", "demo") == 0
    assert not (r.root / "out/demo").exists() and kept.is_file()
    # ticked by the user: no longer counted, no longer in a pull request
    kept.write_text(kept.read_text("utf-8").replace("- [ ] OUTSIDE", "- [x] OUTSIDE"), "utf-8")
    assert followup.outside_count(cfg, "demo") == 0 and followup.pr_checklist(cfg, "demo") == []


def test_no_followup_no_lines(demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]) -> None:
    r = demo_repo
    r.add_toml(FAST)
    to_final_gate(r)
    assert not (r.root / "out/demo/pipeline_followup.md").exists()
    assert "Pipeline follow-up" not in (r.root / "out/demo/final_facts.md").read_text("utf-8")
    assert "Pipeline follow-up" not in (r.root / "out/demo/final_summary.md").read_text("utf-8")
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    assert "Pipeline follow-up" not in capsys.readouterr().out
    assert r.af("finish", "demo", "--no-retro") == 0
    assert not (r.root / "specs/demo/pipeline_followup.md").exists()
    assert not [e for e in r.events() if e["kind"] == "pipeline_followup"]
