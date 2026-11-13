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
    assert local not in git(r.root, "show", "--name-only", "--format=", "HEAD")
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


def test_every_template_has_the_section_and_the_agents_are_told() -> None:
    for name in ("_template", "_template_bugfix", "_template_refactor"):
        text = package_text("templates", f"specs/{name}/requirements.md")
        assert "## Pipeline follow-up" in text, name
        assert followup.parse_spec(text) == [], name  # "None." by default
    for role in ("implement", "review"):
        assert "PIPELINE FOLLOW-UP [<file>]" in package_text("roles", f"{role}.md"), role
    assert "never a question, a decision" in package_text("roles", "pr.md")


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
        REQUIREMENTS + f"\n## Pipeline follow-up\n- {SPEC_ITEM}\n",
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
    assert [it.line() for it in followup.items(cfg, "demo")] == wanted
    run_file = (r.root / "out/demo/pipeline_followup.md").read_text(encoding="utf-8")
    assert all(f"- [ ] {x}" in run_file for x in wanted)
    facts = (r.root / "out/demo/final_facts.md").read_text(encoding="utf-8")
    head = "## Pipeline follow-up (a separate step after this feature; not part of it): 3"
    assert head in facts and all(f"- {x}" in facts for x in wanted)
    assert "Not a gate item" in facts
    assert "## Gate items (numbered as the final gate shows them): 0" in facts
    summary = (r.root / "out/demo/final_summary.md").read_text(encoding="utf-8")
    assert (
        "## Pipeline follow-up (a separate step after this feature; not part of it) "
        "(generated by framegate): 3" in summary
    )
    assert all(f"- {x}" in summary for x in wanted)

    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    brief = capsys.readouterr().out
    assert (
        "Pipeline follow-up (a separate step after this feature; not part of it): 3; nothing "
        "to answer\n" + "".join(f"  - {x}\n" for x in wanted)
    ) in brief
    assert 'Decision: "ok" approves' in brief  # no question came of it
    assert r.progress()["waiting"]["gate"] == "final" and not r.progress().get("parked")

    assert r.af("finish", "demo", "--no-retro") == 0
    kept = r.root / "specs/demo/pipeline_followup.md"
    assert all(f"- [ ] {x}" in kept.read_text(encoding="utf-8") for x in wanted)
    assert "specs/demo/pipeline_followup.md" in git(
        r.root, "show", "--name-only", "--format=%s", "HEAD"
    )
    (ev,) = [e for e in r.events() if e["kind"] == "pipeline_followup"]
    assert ev["items"] == wanted
    assert r.af("cleanup", "demo") == 0
    assert not (r.root / "out/demo").exists() and kept.is_file()


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
