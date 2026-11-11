"""`agentflow doctor`: checks the setup without agents."""

from __future__ import annotations

import pytest

from agentflow import doctor
from agentflow.config import load_config
from conftest import DemoRepo, git


def run(r: DemoRepo, full: bool = False, brief: bool = False) -> tuple[int, str]:
    lines: list[str] = []
    code = doctor.run(load_config(r.root), echo=lines.append, full=full, only_brief=brief)
    return code, "\n".join(lines)


def test_doctor_on_a_working_setup(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml("[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n")
    code, out = run(r, full=True)
    assert code == 0, out
    for line in (
        "[OK] config: language python (PythonAdapter)",
        "[OK] claude CLI",
        "[OK] knowledge file .agentflow/project.md",
        "[OK] spec templates in specs/_template/",
        "[OK] .gitignore",
        "[OK] test selection by spec ID",
        "[OK] test scan: spec-ID tags",
        "[WARN] whole test suite (the final gate's regression run)",
        "OK: 0 failure(s)",
        "[WARN] code map docs/code-map.md",
    ):
        assert line in out, line
    assert "--req DOCTOR-R-000" in out
    # the brief at the end: the verdict, then only what needs a decision
    tail = out[out.index("Verdict:") :].splitlines()
    assert tail[0].startswith("Verdict: ready ("), tail
    assert tail[1].startswith("- WARN code map docs/code-map.md: missing: /agentflow:setup")
    assert any(x.startswith("- WARN whole test suite") for x in tail)
    assert not any("[OK]" in x or "- OK" in x for x in tail)
    assert tail[-1] == "Next: a feature, or a smoke run (see /agentflow:setup)."
    (r.root / "docs").mkdir()
    (r.root / "docs/code-map.md").write_text("# Code map\n", encoding="utf-8")
    code, out = run(r, brief=True)
    assert code == 0 and out.startswith("Verdict: ready") and "[OK]" not in out
    assert "code map" not in out


def test_doctor_reports_what_is_broken(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = demo_repo
    r.add_toml(
        "[gates.commands]\nlint = ['no-such-linter-xyz', '{src}']\nformat = []\ntypecheck = []\n"
    )
    r.set_toml("git", "base", '"nope"')
    (r.root / ".agentflow/project.md").unlink()
    git(r.root, "commit", "-qam", "no knowledge")
    monkeypatch.setenv("AGENTFLOW_CLAUDE", "no-such-claude-xyz")
    code, out = run(r)
    assert code == 1
    assert "[FAIL] claude CLI" in out and "[FAIL] gate command: lint" in out
    assert "[WARN] git: base branch 'nope' not found" in out
    assert "[WARN] knowledge file .agentflow/project.md" in out
    assert "[SKIP] whole test suite" in out and "FAILED: 2 failure(s)" in out
    code, out = run(r, brief=True)
    lines = out.splitlines()
    assert lines[0] == "Verdict: NOT READY (2 failure(s) to fix)"
    assert lines[1].startswith("- FAIL claude CLI") and lines[2].startswith("- FAIL gate command")
    assert "- WARN git: base branch 'nope' not found: set [git] base to the branch" in out


def test_doctor_warns_about_an_outdated_template_copy(demo_repo: DemoRepo) -> None:
    """The project's specs/_template/ copy (written by init) wins over agentflow's templates,
    so sections added by later retros never reach the project's specs unless it is updated."""
    r = demo_repo
    tpl = r.root / "specs" / "_template" / "requirements.md"
    text = tpl.read_text(encoding="utf-8")
    tpl.write_text(
        text.replace("## Existing data this spec reads", "## Something else"), encoding="utf-8"
    )
    stale = doctor.outdated_templates(load_config(r.root))
    assert stale == {"requirements.md": ["Existing data this spec reads"]}
    _, out = run(r)
    assert "[WARN] spec templates in specs/_template/" in out
    assert "requirements.md lacks: Existing data this spec reads" in out
    assert "delete the copy to use agentflow's" in out
    # a column a later retro added to a table (the sections are all there)
    tpl.write_text(
        text.replace(" Measured with |", "").replace("|---|---|---|---|---|", "|---|---|---|---|"),
        encoding="utf-8",
    )
    assert doctor.outdated_templates(load_config(r.root)) == {
        "requirements.md": ['column "Measured with"']
    }


def test_doctor_checks_the_chat_side(demo_repo: DemoRepo) -> None:
    """The allow rules the chat skills need, and agentflow.toml on the base branch."""
    r = demo_repo
    r.add_toml("[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n")
    code, out = run(r)
    assert "[OK] chat: agentflow commands allowed" in out
    assert "is not on 'main'" not in out  # the fixture committed agentflow.toml on main

    settings = r.root / ".claude" / "settings.local.json"
    settings.write_text('{"permissions": {"allow": ["Bash(agentflow:*)"]}}', encoding="utf-8")
    code, out = run(r)
    assert code == 0
    assert "[WARN] chat: agentflow commands allowed" in out
    assert 'add "PowerShell(agentflow *)" to permissions.allow' in out  # Bash: older spelling
    settings.unlink()
    _, out = run(r, brief=True)
    assert '- WARN chat: agentflow commands allowed: add "Bash(agentflow *)", ' in out

    # a base branch without the config: feature branches from it would start without it
    git(r.root, "checkout", "-q", "--orphan", "empty")
    git(r.root, "rm", "-r", "-q", "--cached", ".")
    git(r.root, "commit", "-q", "--allow-empty", "-m", "empty")
    git(r.root, "checkout", "-q", "-f", "feat/demo")
    r.set_toml("git", "base", '"empty"')
    _, out = run(r)
    assert "[WARN] git: agentflow.toml is not on 'empty'" in out
