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
    assert "lines (limit 5). Split it into one file per concept? Default: split it now" in oq
    facts = (r.root / "out/demo/final_facts.md").read_text(encoding="utf-8")
    assert "## Gate items (numbered as the final gate shows them): 1" in facts
    assert "1. [structure report] src/demo/__init__.py has" in facts
    assert "docs/map.md does not exist yet" in facts
    capsys.readouterr()
    assert r.af("gate-summary", "demo") == 0
    out = capsys.readouterr().out
    assert "\n  1. [structure report] src/demo/__init__.py has " in out
    assert "(WARN: no code map at docs/map.md)" in out
    # the answer goes to the structure run (here: leave it), and is not asked again
    (r.root / "out/demo/answers.md").write_text(
        "1. Q: split __init__.py?\n   A: leave it as it is\n", encoding="utf-8"
    )
    assert r.af("answer", "demo") == 0
    prog = r.progress()
    assert prog["size_answered"] == ["src/demo/__init__.py"]
    assert prog["structure_findings"][0].endswith("The human's answer: leave it as it is")
    n = len(r.calls())
    assert r.af("run", "demo") == EXIT_GATE
    later = [c["prompt"] for c in r.calls()[n:]]
    assert any(
        "restructure the code" in p and "The human's answer: leave it as it is" in p for p in later
    )
    assert not (r.root / "out/demo/open_questions.md").exists()


def test_code_map_lists_missing_public_types(demo_repo: DemoRepo) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.scenario(code_map=True)
    # a new public type in the feature's code (the stubs are part of the spec commit)
    (r.root / "src/demo/shot.py").write_text('class Shot:\n    """A shot."""\n', "utf-8")
    to_final_gate(r)
    assert r.progress()["code_map"] == {"exists": True, "updated": True, "missing": ["Shot"]}
    assert "demo: code map" in git(r.root, "log", "--format=%s", "-5")
