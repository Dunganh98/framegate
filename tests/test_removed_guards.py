"""The repair guard: removed suppressions and catch-alls, and the reviewer's look at a
final repair that changed source files."""

from __future__ import annotations

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
    questions = (r.root / "out/demo/open_questions.md").read_text(encoding="utf-8")
    assert "- [repair review] src/demo/__init__.py: bools were ints before" in questions
    gate = [e for e in r.events() if e["kind"] == "final_gates"][-1]
    assert gate["results"][removed_guards.NAME].startswith("PASS")
