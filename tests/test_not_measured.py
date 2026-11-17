"""A check whose tool cannot run on this machine is NOT MEASURED: not a fail, not a silent
pass, and no reason for an elevated risk rating. Seen live: on a Windows machine whose default
WSL distribution was Docker Desktop's (no python3), mutmut never ran and every run was rated
"elevated risk". None of these tests needs WSL: wsl.exe is replaced by a table of answers."""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from conftest import DemoRepo
from framegate import chat, description, gates, risk, stats
from framegate.adapters import wsl
from framegate.adapters.base import (
    NOT_MEASURED,
    CheckResult,
    LanguageAdapter,
    TestScope,
    not_measured,
)
from framegate.adapters.python import PythonAdapter
from framegate.config import load_config, parse_config
from framegate.orchestrator import EXIT_GATE, Orchestrator

FAST = "[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n"
REASON = "the default WSL distribution (docker-desktop) has no python3"
FIX = 'set [env] wsl_distro = "Ubuntu"'
# `wsl.exe -l -v` on the machine of the finding (the header is localized on others)
LIST = "  NAME              STATE           VERSION\n* docker-desktop    Stopped         2\n"
UBUNTU = "  Ubuntu            Stopped         2\n"


def fake_wsl(monkeypatch: pytest.MonkeyPatch, listing: str, python_in: set[str | None]) -> list:
    """wsl.exe that lists `listing` and has python3 in the distributions of `python_in`
    (None = the default one). Returns the commands it was asked to run."""
    calls: list[list[str]] = []

    def run(cmd: list[str], cwd: pathlib.Path, **kw: Any) -> tuple[int, str]:
        calls.append(list(cmd[1:]))
        if cmd[1:] == ["-l", "-v"]:
            return 0, "\x00".join(listing)  # wsl.exe writes UTF-16
        distro = cmd[2] if cmd[1] == "-d" else None
        if distro == "Gone":
            return 1, "There is no distribution\nwith the supplied name."
        if "wslpath" in cmd:
            return 0, "/mnt/d/repo\n"
        return (0, "Python 3.12.3") if distro in python_in else (127, "not found")

    monkeypatch.setattr(wsl, "wsl_exe", lambda: "wsl.exe")
    monkeypatch.setattr(wsl, "run_command", run)
    return calls


# ---------- the probe: which distribution, or why none ----------
def test_distribution_list_is_read_by_position() -> None:
    assert wsl.parse_distros(LIST + UBUNTU) == [("docker-desktop", True), ("Ubuntu", False)]
    german = "  NAME      STATUS          VERSION\n* Ubuntu    Wird ausgeführt 2\n"
    assert wsl.parse_distros(german) == [("Ubuntu", True)]
    assert wsl.parse_distros("WSL has no installed distributions.\nInstall one: ...\n") == []


def test_default_distribution_with_python_is_used_without_a_look_at_the_others(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fake_wsl(monkeypatch, LIST + UBUNTU, {None})
    found = wsl.probe(tmp_path, None)
    assert (found.root, found.distro, found.note) == ("/mnt/d/repo", None, "")
    assert ["-l", "-v"] not in calls


def test_another_distribution_with_python_stands_in_for_the_default(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finding's machine: the default is docker-desktop, Ubuntu has python3."""
    fake_wsl(monkeypatch, LIST + UBUNTU, {"Ubuntu"})
    found = wsl.probe(tmp_path, None)
    assert (found.root, found.distro) == ("/mnt/d/repo", "Ubuntu")
    assert found.note == REASON


def test_no_distribution_with_python_names_the_one_command(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_wsl(monkeypatch, LIST, set())
    found = wsl.probe(tmp_path, None)
    assert found.root is None
    assert found.problem == REASON + ", and no other distribution is installed"
    assert found.fix == "install a distribution with Python: wsl --install -d Ubuntu"
    # docker-desktop's own distributions are never tried; a real one without python3 is named
    calls = fake_wsl(monkeypatch, LIST + "  docker-desktop-data Stopped 2\n" + UBUNTU, set())
    found = wsl.probe(tmp_path, None)
    assert "tried: Ubuntu)" in found.problem and "wsl -d Ubuntu -e sudo apt" in found.fix
    assert not [c for c in calls if c[:2] == ["-d", "docker-desktop-data"]]


def test_a_configured_distribution_is_the_only_one_tried(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fake_wsl(monkeypatch, LIST + UBUNTU, {"Ubuntu"})
    found = wsl.probe(tmp_path, "docker-desktop")
    assert found.root is None and "docker-desktop ([env] wsl_distro) has no python3" in (
        found.problem
    )
    assert "install python3 in docker-desktop" in found.fix
    assert not [c for c in calls if c[:2] == ["-d", "Ubuntu"]]
    found = wsl.probe(tmp_path, "Gone")
    assert "could not start (exit 1): There is no distribution with the supplied" in found.problem
    assert found.fix.startswith("name an installed distribution in [env] wsl_distro")


# ---------- the adapter: the check result and doctor's line ----------
def wsl_adapter(root: pathlib.Path) -> PythonAdapter:
    return PythonAdapter(parse_config(root, {"gates": {"mutation_runner": "wsl"}}))


def test_mutation_check_without_a_usable_wsl_is_not_measured(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_wsl(monkeypatch, LIST, set())
    res = wsl_adapter(tmp_path).mutation_check("f", "t", ["src/demo/__init__.py"])
    assert res.status == NOT_MEASURED and not res.tool_failure
    assert res.summary == (
        f"mutation testing via WSL is unavailable: {REASON}, and no other distribution is "
        "installed (mutmut does not run on native Windows). Fix: install a distribution with "
        "Python: wsl --install -d Ubuntu"
    )


def test_mutation_check_runs_in_the_distribution_the_probe_found(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_wsl(monkeypatch, LIST + UBUNTU, {"Ubuntu"})
    a = wsl_adapter(tmp_path)
    seen: dict[str, Any] = {}

    def finish(feature: str, modules: list[str], cmd: list[str], where: str) -> CheckResult:
        seen.update(cmd=cmd, where=where)
        return CheckResult("PASS", "")

    monkeypatch.setattr(a, "_mutmut_finish", finish)
    assert a.mutation_check("f", "t", ["src/demo/__init__.py"]).status == "PASS"
    assert seen["where"] == "WSL Ubuntu"
    assert seen["cmd"][1:5] == ["-d", "Ubuntu", "-e", "python3"]


def test_doctor_reports_what_the_mutation_check_will_do(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def line(a: PythonAdapter) -> tuple[bool, str]:
        return next((ok, d) for name, ok, d in a.tool_report() if name == "mutmut (mutation)")

    monkeypatch.setattr(PythonAdapter, "_has_module", lambda self, m: True)
    monkeypatch.setattr(PythonAdapter, "_agent_python_report", lambda self: ("x", True, ""))
    monkeypatch.setattr(PythonAdapter, "tool_path", lambda self, *a: "x")
    fake_wsl(monkeypatch, LIST, set())
    ok, detail = line(wsl_adapter(tmp_path))
    assert not ok and detail.startswith(f"mutation testing cannot run here: {REASON}")
    assert detail.endswith("Fix: install a distribution with Python: wsl --install -d Ubuntu")
    fake_wsl(monkeypatch, LIST + UBUNTU, {"Ubuntu"})
    assert line(wsl_adapter(tmp_path)) == (True, f"runs in WSL Ubuntu ({REASON})")
    fake_wsl(monkeypatch, LIST, {None})
    assert line(wsl_adapter(tmp_path)) == (True, "runs in WSL")


# ---------- risk: no reason, no proof ----------
def gates_event(mutation: str) -> dict[str, Any]:
    return {
        "kind": "final_gates",
        "passed": True,
        "failed": [],
        "results": {
            "diff coverage >= 90% (feature tests)": "PASS: 100%",
            "mutation score (feature tests)": mutation,
        },
    }


@pytest.fixture
def rate(demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch) -> Any:
    small = {"changed_lines": 20, "changed_files": 1, "removed_lines": 0, "paths": ["src/a.py"]}
    monkeypatch.setattr(risk, "change_stats", lambda a, f: dict(small))
    monkeypatch.setattr(risk, "finished_features", lambda a, f: 3)
    adapter = Orchestrator(load_config(demo_repo.root)).adapter

    def run(mutation: str, **prog: Any) -> risk.Assessment:
        return risk.assess(adapter, "demo", prog, [gates_event(mutation)], 0, 0)

    return run


def test_not_measured_does_not_raise_the_risk_and_is_no_proof(rate: Any) -> None:
    a = rate(f"{NOT_MEASURED}: {REASON}. Fix: {FIX}")
    assert a.low and a.reasons == []
    assert a.not_measured == {"mutation score": f"{REASON}. Fix: {FIX}"}
    assert not a.auto  # [gates] final = "auto" still stops
    assert a.as_dict()["not_measured"] == a.not_measured
    for auto in (True, False):
        assert a.line(auto) == (
            "Risk: low for what was measured (clean run, small change); mutation score "
            f"NOT MEASURED: {REASON}. Fix: {FIX}. This gate stops for it also with [gates] "
            'final = "auto"'
        )


def test_a_low_score_or_a_switched_off_check_still_raises_the_risk(rate: Any) -> None:
    low = rate("FAIL: Mutation score 40.0% (2/5 killed), threshold 60%")
    off = rate("SKIPPED: mutation testing disabled ([gates] mutation_tool)")
    for a in (low, off):
        assert not a.low and a.reasons == ["mutation score was not measured"]
        assert a.not_measured == {} and "not_measured" not in a.as_dict()
    measured = rate("PASS: Mutation score 90.0%")
    assert (
        measured.low
        and measured.auto
        and "coverage and mutation measured" in measured.line(auto=True)
    )


def test_other_reasons_stand_and_the_line_keeps_them_apart(rate: Any) -> None:
    a = rate(f"{NOT_MEASURED}: {REASON}. Fix: {FIX}", parked=[{"label": "step 2"}])
    assert not a.low and a.reasons == ["1 parked item(s)"]
    assert a.line(auto=False) == (
        "Risk: elevated: 1 parked item(s). Not a reason: mutation score NOT MEASURED: "
        f"{REASON}. Fix: {FIX}"
    )


# ---------- the facts line, the description, the statistics ----------
def test_scores_name_it_with_the_fix_for_the_brief_and_without_for_the_description() -> None:
    last = gates_event(f"{NOT_MEASURED}: {REASON}. Fix: {FIX}")
    name = "mutation score (feature tests)"
    assert description.final_scores(last) == [
        "diff coverage >= 90% (feature tests) PASS: 100%",
        f"{name} NOT MEASURED: {REASON}. Fix: {FIX}",
    ]
    assert description.final_scores(last, fix=False)[1] == f"{name} NOT MEASURED: {REASON}"
    skipped = gates_event("SKIPPED: mutation testing disabled")
    assert len(description.final_scores(skipped)) == 1  # left out by the config: no line


def test_statistics_say_which_run_passed_without_a_check() -> None:
    event = dict(gates_event("x"), not_measured=["mutation score (feature tests)"], seconds=5)
    st = stats.feature_stats("demo", [event, {"kind": "risk", "level": "low"}])
    assert st.final_runs[0].not_measured == ["mutation score (feature tests)"]
    assert st.as_dict()["final_runs"][0]["not_measured"] == ["mutation score (feature tests)"]
    assert "  1. PASS (not measured: mutation score (feature tests)), 5 s" in stats.detail(st)


# ---------- the whole way: gate report, events, brief, auto mode ----------
def unavailable(a: LanguageAdapter, feature: str, scope: TestScope | None = None) -> CheckResult:
    return not_measured(REASON, FIX, log="$ python3 -m venv x\nno ensurepip")


def test_final_gate_shows_not_measured_and_auto_mode_stops(
    demo_repo: DemoRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    r = demo_repo
    r.add_toml(FAST)
    r.defer_tests_gate()
    r.set_toml("gates", "final", '"auto"')
    monkeypatch.setattr(gates.mutation, "check", unavailable)
    monkeypatch.setattr(risk, "finished_features", lambda a, f: 3)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE  # low risk, but not passed without a stop

    report = (r.root / "out/demo/final_gates.md").read_text(encoding="utf-8")
    assert f"[NOT MEASURED] mutation score (feature tests)\n  {REASON}. Fix: {FIX}\n" in report
    assert "  no ensurepip\n" in report  # the tool's own log is kept under it
    assert "ALL GATES PASSED (NOT MEASURED, the tool cannot run here: mutation score" in report

    run = [e for e in r.events() if e["kind"] == "final_gates"][-1]
    name = "mutation score (feature tests)"
    assert run["passed"] is True and run["failed"] == [] and run["tool_failures"] == []
    assert run["not_measured"] == [name] and name not in run["skipped"]
    assert run["results"][name] == f"NOT MEASURED: {REASON}. Fix: {FIX}"
    assert not [e for e in r.events() if e["kind"] == "final_repair"]  # nothing to repair

    rated = [e for e in r.events() if e["kind"] == "risk"][-1]
    assert rated["level"] == "low" and rated["reasons"] == []
    assert rated["not_measured"] == {"mutation score": f"{REASON}. Fix: {FIX}"}
    waiting = r.progress()["waiting"]
    assert waiting["gate"] == "final"
    assert (
        "risk low; not measured (the tool cannot run here): mutation score." in (waiting["message"])
    )
    log = (r.root / "out/demo/progress.log").read_text(encoding="utf-8")
    assert f"final gates: PASSED; not measured: {name}" in log
    assert "approved automatically" not in log

    capsys.readouterr()
    assert chat.gate_summary(Orchestrator(load_config(r.root)), "demo") == 0
    out = capsys.readouterr().out
    assert f"{name} NOT MEASURED: {REASON}. Fix: {FIX}" in out  # the facts under "Outcomes"
    assert (
        "Risk: low for what was measured (clean run, small change); mutation score "
        f"NOT MEASURED: {REASON}. Fix: {FIX}. This gate stops"
    ) in out
