"""Python adapter mutation testing: mutmut result parsing (real mutmut 3.8 output), survivors,
the WSL venv requirements, runner selection and SKIPPED paths, and the standalone runner script
driven by a fake `mutmut` module (no fork needed, so it runs on Windows too). The real mutmut
through WSL is in test_python_mutation_e2e.py (slow)."""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

from framegate.adapters import mutmut_runner, wsl
from framegate.adapters.base import TestScope
from framegate.adapters.python import (
    PythonAdapter,
    collect_requirements,
    tally_mutmut,
    translate_drive_paths,
    write_survivors,
)
from framegate.config import ConfigError, parse_config

# captured from `python -m mutmut results --all true` (mutmut 3.8.1, WSL Ubuntu, demo fixture)
REAL_RESULTS = """\
    demo.x_add__mutmut_1: killed
    demo.x_add__mutmut_2: killed
    demo.x_add__mutmut_3: killed
    demo.x_add__mutmut_4: killed
    demo.x_add__mutmut_5: survived
    demo.x_add__mutmut_6: killed
    demo.x_add__mutmut_7: killed
"""
# captured from `python -m mutmut show demo.x_add__mutmut_5`
REAL_SHOW = """\
# demo.x_add__mutmut_5: survived
--- src/demo/__init__.py
+++ src/demo/__init__.py
@@ -1,5 +1,5 @@
 def add(a: int, b: int) -> int:
     \"\"\"Add two integers.\"\"\"
     if not isinstance(a, int) or not isinstance(b, int):
-        raise TypeError("ints only")
+        raise TypeError("XXints onlyXX")
     return a + b
"""


def adapter(root: pathlib.Path, data: dict | None = None) -> PythonAdapter:
    return PythonAdapter(parse_config(root, data or {}))


# ---------- parsing ----------
def test_tally_real_output() -> None:
    t = tally_mutmut(REAL_RESULTS, ["demo"])
    assert (t.detected, t.total) == (6, 7) and round(t.score, 1) == 85.7
    assert t.survivors == [("demo.x_add__mutmut_5", "survived")]


def test_tally_filters_modules_and_statuses() -> None:
    results = (
        REAL_RESULTS
        + "    demo.sub.x_f__mutmut_1: survived\n"  # another module
        + "    other.x_g__mutmut_1: survived\n"
        + "    demo.xǁCalcǁadd__mutmut_1: timeout\n"  # method, timeout = detected
        + "    demo.xǁCalcǁadd__mutmut_2: no tests\n"
        + "    demo.x_h__mutmut_1: not checked\n"
        + "    demo.x_h__mutmut_2: skipped\n"
        + "garbage line\n"
    )
    t = tally_mutmut(results, ["demo"])
    assert (t.detected, t.total, t.unchecked) == (7, 9, 2)
    assert ("demo.xǁCalcǁadd__mutmut_2", "no tests") in t.survivors
    assert all(not n.startswith(("demo.sub.", "other.")) for n, _ in t.survivors)
    assert tally_mutmut(results, ["demo.sub"]).survivors == [("demo.sub.x_f__mutmut_1", "survived")]
    assert tally_mutmut("", ["demo"]).score == 100.0


def test_write_survivors(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "f" / "mutation_survivors.md"
    t = tally_mutmut(REAL_RESULTS + "    demo.x_h__mutmut_1: not checked\n", ["demo"])
    write_survivors(out, t, {"demo.x_add__mutmut_5": REAL_SHOW})
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Mutation survivors")
    assert "## `demo.x_add__mutmut_5`: survived" in text
    assert '+        raise TypeError("XXints onlyXX")' in text and "```diff" in text
    assert "# demo.x_add__mutmut_5: survived" not in text  # mutmut's header line dropped
    assert "1 mutant(s) skipped or not checked" in text
    write_survivors(out, tally_mutmut("", ["demo"]), {})
    assert "None." in out.read_text(encoding="utf-8")


# ---------- WSL venv requirements ----------
def test_collect_requirements(tmp_path: pathlib.Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "My_Pkg"
dependencies = ["attrs>=23"]
[project.optional-dependencies]
fast = ["orjson"]
[dependency-groups]
dev = ["pytest>=8", "hypothesis", {include-group = "lint"}, "my-pkg[fast]"]
lint = ["ruff"]
[tool.uv]
dev-dependencies = ["attrs>=23", "mypy"]
""",
        encoding="utf-8",
    )
    (tmp_path / "requirements-dev.txt").write_text("coverage\n", encoding="utf-8")
    reqs, files = collect_requirements(tmp_path)
    assert reqs == ["attrs>=23", "orjson", "pytest>=8", "hypothesis", "ruff", "mypy"]
    assert [f.name for f in files] == ["requirements-dev.txt"]
    assert collect_requirements(tmp_path / "nothing") == ([], [])


def test_translate_drive_paths() -> None:
    def w(p: pathlib.Path) -> str:
        return wsl.fallback_path(p)

    assert translate_drive_paths("lib @ file:///D:/x/lib", w) == "lib @ file:///mnt/d/x/lib"
    assert translate_drive_paths("C:\\work\\pkg", w) == "/mnt/c/work/pkg"
    assert translate_drive_paths("pytest>=8; python_version < '4'", w) == (
        "pytest>=8; python_version < '4'"
    )
    assert wsl.fallback_path("D:\\Me\\Projects") == "/mnt/d/Me/Projects"
    assert wsl.fallback_path("/already/posix") == "/already/posix"


def test_wsl_requirements_and_stamp(tmp_path: pathlib.Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["framegate==0.1.0", "attrs"]\n', encoding="utf-8"
    )
    a = adapter(tmp_path)
    reqs, files, stamp = a._wsl_requirements(lambda p: wsl.fallback_path(p))
    assert "attrs" in reqs and "mutmut>=3" in reqs and "pytest" in reqs
    assert "framegate==0.1.0" not in reqs  # replaced by this source tree (tests run from one)
    checkout = pathlib.Path(__file__).resolve().parents[1].name  # whatever the clone is called
    assert any(r.startswith("/mnt/") and r.endswith("/" + checkout) for r in reqs)
    assert files == []
    _, _, same = a._wsl_requirements(lambda p: wsl.fallback_path(p))
    assert same == stamp
    (tmp_path / "uv.lock").write_text("lock", encoding="utf-8")
    assert a._wsl_requirements(lambda p: wsl.fallback_path(p))[2] != stamp  # lock change


# ---------- runner selection / SKIPPED ----------
def test_mutation_runner_config(tmp_path: pathlib.Path) -> None:
    assert adapter(tmp_path).mutation_runner() == ("wsl" if os.name == "nt" else "native")
    for value in ("native", "wsl"):
        assert adapter(tmp_path, {"gates": {"mutation_runner": value}}).mutation_runner() == value
    with pytest.raises(ConfigError, match="mutation_runner"):
        parse_config(tmp_path, {"gates": {"mutation_runner": "docker"}})
    cfg = parse_config(tmp_path, {"env": {"wsl_distro": "Ubuntu"}})
    assert cfg.wsl_distro == "Ubuntu"
    assert "mutants/" in adapter(tmp_path).gitignore_entries()


def test_wsl_unavailable_is_skipped(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wsl, "wsl_exe", lambda: None)
    a = adapter(tmp_path, {"gates": {"mutation_runner": "wsl"}})
    res = a.mutation_check("f", "t", ["src/demo/__init__.py"])
    assert res.status == "SKIPPED"
    assert "via WSL is unavailable: wsl.exe not found" in res.output
    assert a.mutation_check("f", "t", ["README.md"]).status == "PASS"  # nothing to mutate


@pytest.mark.skipif(os.name != "nt", reason="native Windows only")
def test_native_windows_is_skipped(tmp_path: pathlib.Path) -> None:
    a = adapter(tmp_path, {"gates": {"mutation_runner": "native"}})
    res = a.mutation_check("f", "t", ["src/demo/__init__.py"])
    assert res.status == "SKIPPED" and 'mutation_runner = "wsl"' in res.output


# ---------- runner script with a fake mutmut ----------
FAKE_MUTMUT = """\
import os, sys
cmd = sys.argv[1]
if cmd == "run":
    os.makedirs("mutants/src", exist_ok=True)
    with open("run_args.txt", "w") as fh:
        fh.write(" ".join(sys.argv[2:]))
    with open("addopts.txt", "w") as fh:
        fh.write(os.environ.get("PYTEST_ADDOPTS", ""))
    print("\\r⠋ 0/7\\r⠼ 7/7  🎉 6 🙁 1\\n108.40 mutations/second")
    sys.exit(int(os.environ.get("FAKE_MUTMUT_RC", "0")))
elif cmd == "results":
    print(os.environ["FAKE_MUTMUT_RESULTS"])
elif cmd == "show":
    print("# " + sys.argv[2] + ": survived\\n--- a.py\\n+++ a.py\\n-x\\n+y")
"""


@pytest.fixture
def fake_mutmut(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    pkg = tmp_path / "fakepath" / "mutmut"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text(FAKE_MUTMUT, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fakepath"))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("FAKE_MUTMUT_RESULTS", REAL_RESULTS)
    root = tmp_path / "repo"
    root.mkdir()
    return root


def run_fake(
    a: PythonAdapter, modules: list[str], scope: TestScope | None = None
) -> tuple[str, str, str]:
    work = a._mutmut_dir()
    spec = a._mutmut_spec(modules, str(a.root), str(work / "result.json"), scope)
    (work / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    res = a._mutmut_finish(
        "f", modules, [sys.executable, str(work / "runner.py"), str(work / "spec.json")], "native"
    )
    return res.status, res.output, res.summary


def test_runner_script_flow(fake_mutmut: pathlib.Path) -> None:
    a = adapter(fake_mutmut, {"gates": {"mutation_score": 80}})
    status, output, summary = run_fake(a, ["demo"])
    assert status == "PASS", output
    assert output.startswith("Mutation score 85.7% (6/7 killed), threshold 80% (native,")
    assert summary == output
    assert (fake_mutmut / "run_args.txt").read_text(encoding="utf-8") == "demo.x*"
    assert not (fake_mutmut / "mutants").exists()  # cleaned after the run
    survivors = (fake_mutmut / "out/f/mutation_survivors.md").read_text(encoding="utf-8")
    assert "demo.x_add__mutmut_5" in survivors and "+y" in survivors
    result = json.loads((fake_mutmut / "out/.mutmut/result.json").read_text(encoding="utf-8"))
    assert "7/7" in result["run_output"] and "\r" not in result["run_output"]
    assert list(result["shows"]) == ["demo.x_add__mutmut_5"]

    a90 = adapter(fake_mutmut, {"gates": {"mutation_score": 90}})
    assert run_fake(a90, ["demo"])[0] == "FAIL"
    assert run_fake(a, ["other"])[1] == "No mutants generated for the changed files."


def test_runner_script_failures(fake_mutmut: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = adapter(fake_mutmut)
    monkeypatch.setenv("FAKE_MUTMUT_RC", "1")
    status, output, _ = run_fake(a, ["demo"])
    assert status == "FAIL" and "mutmut run failed (native)" in output
    # the runner can't even start
    res = a._mutmut_finish("f", ["demo"], [sys.executable, "-c", "raise SystemExit(3)"], "WSL")
    assert res.status == "FAIL" and "mutmut runner (WSL) failed (exit 3)" in res.output


def test_runner_setup_failure_is_skipped(fake_mutmut: pathlib.Path) -> None:
    a = adapter(fake_mutmut)
    result = fake_mutmut / "out/.mutmut/result.json"
    data = {
        "ok": False,
        "error": "could not create the mutmut venv",
        "hint": "HINT",
        "setup_log": "x",
    }
    code = f"open({str(result)!r}, 'w').write({json.dumps(json.dumps(data))})"
    a._mutmut_dir()
    res = a._mutmut_finish("f", ["demo"], [sys.executable, "-c", code], "WSL")
    assert res.status == "SKIPPED"
    assert "could not create the mutmut venv (WSL); HINT" in res.output


def test_runner_reuses_stamped_venv(tmp_path: pathlib.Path) -> None:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")
    (venv / mutmut_runner.STAMP_FILE).write_text("abc", encoding="utf-8")
    ok, reused, log = mutmut_runner.ensure_venv({"venv": str(venv), "stamp": "abc"})
    assert ok and reused and "reusing" in log
    assert "uv" in mutmut_runner.INSTALL_HINT
