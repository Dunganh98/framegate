"""`agentflow doctor`: checks the setup without agents."""

from __future__ import annotations

import pytest

from agentflow import doctor
from agentflow.config import load_config
from conftest import DemoRepo, git


def run(r: DemoRepo, full: bool = False) -> tuple[int, str]:
    lines: list[str] = []
    code = doctor.run(load_config(r.root), echo=lines.append, full=full)
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
    ):
        assert line in out, line
    assert "--req DOCTOR-R-000" in out


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
