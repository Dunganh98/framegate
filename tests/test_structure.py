"""Readability at the structure level: the final gate's structure report (file sizes, longest
methods), the reviewer's structure findings (restructured by the implementer before the final
gates), file-size questions at the final gate and their answers, the code map check."""

from __future__ import annotations

import pytest

from conftest import DemoRepo, git
from framegate.checks import structure
from framegate.orchestrator import EXIT_GATE

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"

CSHARP = """\
namespace Demo;

/// <summary>Kick model.</summary>
public sealed class SetPieceShotModel
{
    public double Power(int a)
    {
        if (a > 0)
        {
            return a * 2.0;
        }
        return 0;
    }

    private static int Short() => 1;

    public interface IHidden { }
}

public record struct Kick(int A);
"""


def to_final_gate(r: DemoRepo) -> None:
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE
    assert r.af("approve", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE


def test_method_lengths() -> None:
    py = "def a():\n    x = 1\n    return x\n\n\nclass B:\n    def c(self):\n        pass\n"
    assert dict(structure.python_methods(py)) == {"a": 3, "c": 2}
    assert dict(structure.brace_methods(CSHARP)) == {"Power": 8}


def test_new_public_type_patterns() -> None:
    lines = ["+" + x for x in CSHARP.splitlines()]
    found = [
        m.group(1) for x in lines if (m := structure.PUBLIC_TYPE["dotnet"].match(x)) is not None
    ]
    assert found == ["SetPieceShotModel", "IHidden", "Kick"]
    assert structure.PUBLIC_TYPE["python"].match("+class Model:")
    assert not structure.PUBLIC_TYPE["python"].match("+class _Private:")
    assert structure.GENERIC_TYPE.match("+export class Shot {").group(1) == "Shot"  # type: ignore[union-attr]
    assert structure.GENERIC_TYPE.match("+pub struct Kick {").group(1) == "Kick"  # type: ignore[union-attr]


def test_only_a_file_the_feature_made_longer_counts() -> None:
    """Seen live: the final gate asked about a file the feature had left at 869 lines and
    about two it had made shorter; and two steps added to files already over the limit
    without any step gate saying so."""
    shape = structure.Structure(
        [
            structure.FileSize("src/same.cs", 869, before=869),
            structure.FileSize("src/shorter.cs", 774, before=835),
            structure.FileSize("src/longer.cs", 1114, before=1093),
            structure.FileSize("src/new.cs", 450),
            structure.FileSize("src/small.cs", 80, before=20),
        ],
        limit=400,
    )
    assert [f.path for f in shape.grown] == ["src/longer.cs", "src/new.cs"]
    assert shape.as_dict()["grown"] == ["src/longer.cs", "src/new.cs"]
    assert len(shape.as_dict()["over"]) == 4  # type: ignore[arg-type]
    assert shape.step_lines() == [
        "Structure (not blocking; changed files over 400 lines):",
        "  src/longer.cs: 1093 -> 1114  GREW: put new code in its own file",
        "  src/same.cs: 869 -> 869",
        "  src/shorter.cs: 835 -> 774",
        "  src/new.cs: 450 (new)  GREW: put new code in its own file",
    ]
    final = "\n".join(shape.lines())
    assert "src/same.cs: 869 lines (was 869)  OVER 400 (not longer than before" in final
    assert "src/longer.cs: 1114 lines (was 1093)  OVER 400\n" in final
    assert structure.Structure([structure.FileSize("a.py", 3)], limit=400).step_lines() == []


def test_the_implementer_is_told_which_files_are_too_long(demo_repo: DemoRepo) -> None:
    r = demo_repo
    git(r.root, "checkout", "-q", "main")  # code from before the feature branch
    (r.root / "src/demo/old.py").write_text("X = 1\n" * 9, encoding="utf-8")
    (r.root / "src/demo/fine.py").write_text("X = 1\n" * 8, encoding="utf-8")
    git(r.root, "add", "src/demo/old.py", "src/demo/fine.py")
    git(r.root, "commit", "-q", "-m", "old code")
    git(r.root, "checkout", "-q", "feat/demo")
    git(r.root, "merge", "-q", "main")
    r.add_toml(FAST)
    r.set_toml("gates", "max_file_lines", "8")
    from framegate.adapters import get_adapter
    from framegate.config import load_config
    from framegate.scaffold import spec_brief

    cfg = load_config(r.root)
    assert structure.oversized(get_adapter(cfg)) == ([("src/demo/old.py", 9)], 1)
    assert "- Source files over 8 lines ([gates] max_file_lines): src/demo/old.py (9). A plan" in (
        spec_brief(cfg, "demo")
    )
    r.scenario()
    to_final_gate(r)
    tasks = [c["prompt"] for c in r.calls() if "implement plan step 1" in c["prompt"]]
    assert tasks and "Source files already over 8 lines: src/demo/old.py (9). Do not" in tasks[0]
    # the feature did not touch old.py: no question about it at the final gate
    oq = r.root / "out/demo/open_questions.md"
    assert not oq.exists() or "old.py" not in oq.read_text(encoding="utf-8")
    gates = [e for e in r.events() if e["kind"] == "gate"]
    assert gates and all(e["grown"] == [] for e in gates)


def test_final_gates_report_the_changed_files(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario()
    to_final_gate(r)
    final = (r.root / "out/demo/final_gates.md").read_text(encoding="utf-8")
    assert "Structure (not blocking; files the feature changed):" in final
    assert "  src/demo/__init__.py: " in final and "lines (new)" in final
    assert "longest methods: add (" in final
    (ev,) = [e for e in r.events() if e["kind"] == "final_gates"]
    assert ev["structure"]["limit"] == 400 and ev["structure"]["over"] == []
    assert ev["results"]["traceability"].startswith("PASS")
    # no file over the limit: no question; no code map: a warning at the gate
    assert not (r.root / "out/demo/open_questions.md").exists()
    assert r.progress()["code_map"] == {"exists": False, "updated": False, "missing": []}


def test_structure_findings_are_applied_before_the_final_gates(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(structure=True)
    to_final_gate(r)
    prompts = [c["prompt"] for c in r.calls()]
    (task,) = [p for p in prompts if "restructure the code without changing its behavior" in p]
    assert "- src/demo/__init__.py: add() and neg() are two concepts -> one file each" in task
    assert "## Conventions" in task  # the implementer gets the conventions brief
    kinds = [e["kind"] for e in r.events()]
    assert kinds.index("structure_fixed") < kinds.index("final_gates")
    prog = r.progress()
    assert "structure_findings" not in prog and not prog.get("parked")
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert "[structure fixes] 1 finding(s) to apply" in log


def test_a_file_over_the_limit_is_asked_at_the_final_gate(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.set_toml("docs", "code_map", '"docs/map.md"')
    r.set_toml("gates", "max_file_lines", "5")
    r.scenario()
    to_final_gate(r)
    oq = (r.root / "out/demo/open_questions.md").read_text(encoding="utf-8")
    assert "- [structure report] src/demo/__init__.py has " in oq
    assert "Split it into one file per concept? A: leave it for a later cleanup (the " in oq
    assert "(default) / B: split it now by concept" in oq
    facts = (r.root / "out/demo/final_facts.md").read_text(encoding="utf-8")
    assert "## Gate items (numbered as the final gate shows them): 1" in facts
    assert "1. [structure report] src/demo/__init__.py has" in facts
    assert "docs/map.md does not exist yet" in facts
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    out = capsys.readouterr().out
    assert "\n  1. [structure report] src/demo/__init__.py has " in out
    assert "(WARN: no code map at docs/map.md)" in out
    # the answer goes to the structure run (here: split it), and is not asked again
    (r.root / "out/demo/answers.md").write_text(
        "1. Q: split __init__.py?\n   A: B: split it now by concept\n", encoding="utf-8"
    )
    assert r.af("answer", "demo") == 0
    prog = r.progress()
    assert prog["size_answered"] == ["src/demo/__init__.py"]
    assert prog["structure_findings"][0].endswith("The human's answer: B: split it now by concept")
    n = len(r.calls())
    assert r.af("run", "demo") == EXIT_GATE
    later = [c["prompt"] for c in r.calls()[n:]]
    assert any("restructure the code" in p and "B: split it now" in p for p in later)
    assert not (r.root / "out/demo/open_questions.md").exists()
    assert len([e for e in r.events() if e["kind"] == "final_gates"]) == 2


def test_answers_that_only_take_the_defaults_approve_without_a_run(demo_repo: DemoRepo) -> None:
    """Seen live: "leave the split for later" started a spec update, a test amend, a
    structure run and every final check again, then a second stop at the same gate."""
    r = demo_repo
    r.add_toml(FAST)
    r.set_toml("gates", "max_file_lines", "5")
    r.scenario()
    to_final_gate(r)
    (r.root / "out/demo/answers.md").write_text(
        "1. Q: split __init__.py?\n   A: A: leave the split for later\n", encoding="utf-8"
    )
    n = len(r.calls())
    assert r.af("answer", "demo") == 0
    prog = r.progress()
    assert prog["phase"] == "RETRO" and not prog.get("structure_findings")
    assert prog["size_answered"] == ["src/demo/__init__.py"]
    assert r.af("run", "demo") == 0  # the retro, then DONE
    assert [c["phase"] for c in r.calls()[n:]] == ["RETRO"]
    events = r.events()
    assert len([e for e in events if e["kind"] == "final_gates"]) == 1
    (gate,) = [e for e in events if e["kind"] == "human_gate" and e["gate"] == "final"]
    assert gate["approved"] and gate["from_answers"] == "ANS-1"
    assert [e for e in events if e["kind"] == "answer"][-1]["size_left"] == ["src/demo/__init__.py"]
    assert r.progress()["phase"] == "DONE"
    # the split left for later is on the deferred list, committed with the task
    entry = "- D1 [refactor] src/demo/__init__.py: has "
    assert entry in (r.root / ".framegate/deferred.md").read_text(encoding="utf-8")
    assert entry in git(r.root, "show", "HEAD:.framegate/deferred.md")


def test_code_map_lists_missing_public_types(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(code_map=True)
    # a new public type in the feature's code (the stubs are part of the spec commit)
    (r.root / "src/demo/shot.py").write_text('class Shot:\n    """A shot."""\n', "utf-8")
    to_final_gate(r)
    assert r.progress()["code_map"] == {"exists": True, "updated": True, "missing": ["Shot"]}
    assert "demo: code map" in git(r.root, "log", "--format=%s", "-5")


def test_a_split_of_a_file_that_was_too_big_before_is_asked_not_done(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.set_toml("gates", "max_file_lines", "2")
    r.scenario(structure=True)
    to_final_gate(r)
    prompts = [c["prompt"] for c in r.calls()]
    assert not [p for p in prompts if "restructure the code without changing its behavior" in p]
    oq = (r.root / "out/demo/open_questions.md").read_text(encoding="utf-8")
    assert "- [structure report] src/demo/__init__.py had " in oq
    assert "lines before this feature (limit 2); the reviewer proposes: " in oq
    assert "A: leave it for a later cleanup (the deferred list) (default) / B: split it now." in oq


def test_an_answer_that_names_no_option_is_asked_once(
    demo_repo: DemoRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    """Seen live: free text that meant the default started a spec update and a rework."""
    r = demo_repo
    r.add_toml(FAST)
    r.set_toml("gates", "max_file_lines", "5")
    r.scenario()
    to_final_gate(r)
    answers = r.root / "out/demo/answers.md"
    answers.write_text("1. Q: split?\n   A: No restructuring in this task.\n", encoding="utf-8")
    capsys.readouterr()
    assert r.af("answer", "demo") == 1
    err = capsys.readouterr().err
    assert "1 answer(s) in out/demo/answers.md name no option of their question" in err
    assert "- item 1 (src/demo/__init__.py has " in err and "names none of A / B" in err
    assert r.progress()["waiting"]["gate"] == "final"
    assert [e["kind"] for e in r.events()][-1] == "answer_unclear"
    # the owner chose: the letter is the option, the words a note; no run
    answers.write_text("1. Q: split?\n   A: A: no restructuring in this task.\n", "utf-8")
    assert r.af("answer", "demo") == 0
    assert r.progress()["phase"] == "RETRO"
    (ev,) = [e for e in r.events() if e["kind"] == "answer"]
    assert ev["items"][0]["option"] == "A" and ev["items"][0]["note"].startswith("no restruct")
