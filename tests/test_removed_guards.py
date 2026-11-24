"""The repair guard: removed suppressions and catch-alls, and the reviewer's look at a
final repair that changed source files."""

from __future__ import annotations

import pytest

from conftest import DemoRepo, git
from framegate.adapters import get_adapter
from framegate.checks import removed_guards
from framegate.config import load_config
from test_final_repair import LEGACY, to_final_gate

GUARDED = """\


def load(path: str) -> int:
    try:
        return int(open(path).read())  # type: ignore[arg-type]
    except Exception:
        return 0  # pragma: no cover
"""


def test_guards_count_by_their_text() -> None:
    patterns = {"# noqa": r"#\s*noqa\b(?::\s*\w+)?", "bare except": r"^\s*except\s*:"}
    before = "x = f(a)  # noqa: E501\ntry:\n    y()\nexcept:\n    pass\n"
    moved = "x = f(\n    a\n)  # noqa:  E501\ntry:\n    y()\nexcept:\n    pass\n"
    assert removed_guards.removed(before, moved, patterns) == []
    assert removed_guards.removed(before, "x = f(a)\ny()\n", patterns) == [
        ("# noqa", "# noqa: E501"),
        ("bare except", "except:"),
    ]
    ids = {"DEMO-R-001"}
    line = "- Removed the noqa in src/a.py: DEMO-R-001 shortens the line"
    assert removed_guards.named(line, "src/a.py", "# noqa", "# noqa: E501", ids)
    assert not removed_guards.named(line, "src/b.py", "# noqa", "# noqa: E501", ids)
    assert not removed_guards.named(line, "src/a.py", "bare except", "except:", ids)
    assert not removed_guards.named(line.replace("DEMO-R-001", "it"), "src/a.py", "# noqa", "", ids)


def test_a_removed_guard_fails_until_steps_md_names_it(demo_repo: DemoRepo) -> None:
    r = demo_repo
    rel = "src/demo/__init__.py"
    src = r.root / rel
    src.write_text(src.read_text(encoding="utf-8") + GUARDED, encoding="utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-qm", "guards")
    git(r.root, "tag", "tests-approved/demo")
    adapter = get_adapter(load_config(r.root))
    assert removed_guards.check(adapter, "demo").status == "PASS"

    text = src.read_text(encoding="utf-8")
    src.write_text(
        text.replace("  # type: ignore[arg-type]", "").replace(
            "except Exception:", "except OSError:"
        ),
        encoding="utf-8",
    )
    res = removed_guards.check(adapter, "demo")
    assert res.status == "FAIL" and res.output.startswith("2 removed without a spec ID")
    assert f"REMOVED, NOT NAMED: {rel}: # type: ignore (`# type: ignore[arg-type]`)" in res.output
    assert f"REMOVED, NOT NAMED: {rel}: except Exception (`except Exception`)" in res.output
    assert "pragma" not in res.output

    steps = adapter.cfg.feature_out("demo") / "steps.md"
    steps.parent.mkdir(parents=True, exist_ok=True)
    steps.write_text(
        "- Removed the type: ignore in src/demo/__init__.py: DEMO-R-001, the file is closed now\n"
        "- Removed except Exception in __init__.py: DEMO-E-001 names OSError only\n",
        encoding="utf-8",
    )
    res = removed_guards.check(adapter, "demo")
    assert res.status == "PASS" and res.output.count("removed, named in steps.md") == 2


def test_the_reviewer_looks_at_a_final_repair_of_the_code(demo_repo: DemoRepo) -> None:
    r = demo_repo
    (r.root / "tests/test_legacy.py").write_text(LEGACY, encoding="utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "legacy test")
    r.scenario(repair_objection="src/demo/__init__.py: bools were ints before (DEMO-R-001)")
    to_final_gate(r)
    look = [c for c in r.calls() if "follow-up review of a final repair" in c["prompt"]]
    assert len(look) == 1 and look[0]["phase"] == "REVIEW"
    assert "## The repair diff" in look[0]["prompt"] and "+++ b/src/demo" in look[0]["prompt"]
    ev = [e for e in r.events() if e["kind"] == "repair_review"]
    assert [(e["follow_up"], e["verdict"], e["files"]) for e in ev] == [
        (True, "OBJECTIONS", ["src/demo/__init__.py"])
    ]
    assert [(e["round"], e["sent_back"]) for e in ev] == [(1, True)]
    # sent back once; the implementer changed nothing, so the objection stands: no default
    again = [c for c in r.calls() if "The reviewer objects to the repair" in c["prompt"]]
    assert len(again) == 1 and again[0]["phase"] == "IMPLEMENT"
    assert "- bools were ints before" not in again[0]["prompt"]
    assert "- src/demo/__init__.py: bools were ints before (DEMO-R-001)" in again[0]["prompt"]
    questions = (r.root / "out/demo/open_questions.md").read_text(encoding="utf-8")
    assert "- [repair review] src/demo/__init__.py: bools were ints before" in questions
    assert "A: keep the repair as it is (no change) / B: the implementer changes" in questions
    assert "No default: choose." in questions
    gate = [e for e in r.events() if e["kind"] == "final_gates"][-1]
    assert gate["results"][removed_guards.NAME].startswith("PASS")


def legacy(r: DemoRepo, **scenario: object) -> None:
    (r.root / "tests/test_legacy.py").write_text(LEGACY, encoding="utf-8")
    git(r.root, "add", "-A")
    git(r.root, "commit", "-q", "-m", "legacy test")
    r.scenario(repair_objection="src/demo/__init__.py: bools were ints before", **scenario)
    to_final_gate(r)


def test_an_objection_to_a_repair_goes_back_to_the_implementer_once(demo_repo: DemoRepo) -> None:
    """Seen live: the objection became a gate question and shipped on its default."""
    r = demo_repo
    legacy(r, repair_met=True)
    ev = [e for e in r.events() if e["kind"] == "repair_review"]
    assert [(e["round"], e["verdict"], e["sent_back"]) for e in ev] == [
        (1, "OBJECTIONS", True),
        (2, "OK", False),
    ]
    assert "objection met" in (r.root / "src/demo/__init__.py").read_text(encoding="utf-8")
    assert not (r.root / "out/demo/open_questions.md").exists()
    gates = [e for e in r.events() if e["kind"] == "final_gates"]
    assert [e["passed"] for e in gates] == [False, True]  # the gates ran after the retry
    assert (r.root / "out/demo/repair_review_round1.md").is_file()
    assert r.af("approve", "demo") == 0 and r.af("run", "demo") == 0
    retro = (r.root / "out/demo/retro_input.md").read_text(encoding="utf-8")
    assert "## Repair review (the reviewer on the final repair)" in retro
    assert "repair_review_round1.md:" in retro and "bools were ints before" in retro


def test_an_objection_that_stands_has_no_default(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    legacy(r, repair_met="stays")
    ev = [e for e in r.events() if e["kind"] == "repair_review"]
    assert [(e["round"], e["verdict"]) for e in ev] == [(1, "OBJECTIONS"), (2, "OBJECTIONS")]
    answers = r.root / "out/demo/answers.md"
    for unclear in ("ok", "Default: keep it", "leave it"):
        answers.write_text(f"1. Q: the repair\n   A: {unclear}\n", encoding="utf-8")
        capsys.readouterr()
        assert r.af("answer", "demo") == 1
        assert "names none of A / B" in capsys.readouterr().err
    n = len(r.calls())
    answers.write_text("1. Q: the repair\n   A: B: keep bools as ints\n", encoding="utf-8")
    assert r.af("answer", "demo") == 0
    assert r.progress()["repair_objections"][0].endswith("The owner's words: keep bools as ints")
    assert r.af("run", "demo") == 10
    fix = [c for c in r.calls()[n:] if "The reviewer objects to the repair" in c["prompt"]]
    assert len(fix) == 1 and "The owner's words: keep bools as ints" in fix[0]["prompt"]
    assert "repair_objections" not in r.progress()
    assert not (r.root / "out/demo/open_questions.md").exists()
