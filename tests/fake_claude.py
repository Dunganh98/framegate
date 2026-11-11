"""Fake `claude -p` for the end-to-end tests (selected via AGENTFLOW_CLAUDE).

Reads the task from stdin, the phase from .phase, and a scenario from FAKE_CLAUDE_SCENARIO
(JSON). Writes canned files for feature `demo`, logs every call to calls.jsonl next to the
scenario file, and prints the result: `--output-format stream-json` -> one JSON message per line
(system init, one assistant message per API turn, the result last), else the single result object
of `--output-format json`.

Scenario keys:
  questions_in: list of phases that ask one question in their first run (TESTS, TEST_REVIEW,
    ...: questions.md, then the normal work; IMPLEMENT: questions.md only = a park request)
  test_review_objections: number of TEST_REVIEW runs that object (Verdict: OBJECTIONS)
  implement_fail: number of IMPLEMENT runs that write a wrong implementation
  park_steps: plan step numbers whose first IMPLEMENT run writes park.md (and half-done code)
  assume: true -> the first successful IMPLEMENT run logs one assumption in steps.md
  review_finding: true -> the reviewer writes one failing test (bool accepted as int); TRIAGE
    marks it VALID
  review_invalid: true -> the reviewer also writes an invalid finding (negative ints rejected);
    TRIAGE deletes it and marks it INVALID
  language: "python" (default) or "dotnet" (C# files for the .NET fixture, see test_dotnet_e2e)
  retro_omit: item IDs (e.g. ["F1"]) the RETRO run leaves out of retro_proposals.md (every
    other ID from out/demo/retro_input.md is listed under "Not acted on")
  retro_omit_runs: how many RETRO runs omit them (default 1: the re-run fixes it)
  denials: {phase: [{"tool_name": ..., "tool_input": {...}}]} reported as `permission_denials`
    in the result of that phase's first run
  max_turns: {phase: n} -> the first n runs of that phase end with subtype error_max_turns
    (files are still written); the stream then has `cap` assistant messages, num_turns = cap + 1
  max_budget: {phase: n} -> the first n runs of that phase end with subtype error_max_budget_usd
  hang: phase whose first run starts a child process and sleeps (pid files in the scenario dir)
  usage_limit: {phase: n} -> the first n runs of that phase are refused by the usage limit (no
    files; a rejected rate_limit_event resetting in usage_limit_reset_s seconds, default 120);
    they do not count as runs for the other keys
  contest_steps: plan step numbers whose first IMPLEMENT run finds the extra test
    test_add_refuses_bool_contested wrong: TESTS adds that test (bools must be rejected), the run
    writes the loose add() (bools pass) and a `Park kind: tests-wrong` park.md naming it
  contest_accept: true -> TESTS_AMEND deletes the contested test (the human approved the fix);
    false -> no test change (the fix was not approved), so the redo writes the strict add()
  spec_update: what SPEC_UPDATE does with specs/demo/requirements.md: "edit" (default: marks the
    DEMO-E-001 row with the answer reference), "new_id" (edit + a new row DEMO-E-002, added to
    plan.md Covers; TESTS then writes a test for it), "renumber" (DEMO-E-001 -> DEMO-E-009),
    "stray" (edit + writes specs/other/requirements.md and specs/demo/notes.md), "none"
"""

import json
import os
import pathlib
import re
import subprocess
import sys
import time

STUB_DOC = '"""Add two integers. Serves DEMO-R-001, DEMO-E-001."""'

TESTS = """\
import pytest

from demo import add


@pytest.mark.req("DEMO-R-001")
def test_add_returns_sum() -> None:
    assert add(2, 3) == 5


@pytest.mark.req("DEMO-E-001")
def test_add_rejects_non_int() -> None:
    with pytest.raises(TypeError, match="ints only"):
        add("1", 2)
"""

TESTS_E2 = """

@pytest.mark.req("DEMO-E-002")
def test_add_zeros() -> None:
    assert add(0, 0) == 0
"""

TESTS_STEP2 = """

@pytest.mark.req("DEMO-R-002")
def test_neg_negates() -> None:
    assert neg(4) == -4
"""

REVIEW_INVALID = """\
import pytest

from demo import add


# Finding (invalid: the spec allows negative ints): negative inputs should be rejected.
@pytest.mark.req("DEMO-R-001")
def test_add_rejects_negative() -> None:
    with pytest.raises(TypeError):
        add(-1, 1)
"""

REVIEW_TEST = """\
import pytest

from demo import add


# Finding: bool is a subclass of int, so add(True, 1) is accepted; the spec says ints only.
@pytest.mark.req("DEMO-E-001")
def test_add_rejects_bool() -> None:
    with pytest.raises(TypeError, match="ints only"):
        add(True, 1)
"""


TESTS_CONTESTED = """

@pytest.mark.req("DEMO-E-001")
def test_add_refuses_bool_contested() -> None:
    with pytest.raises(TypeError, match="ints only"):
        add(True, 1)
"""

NEG_HEAD = '\n\ndef neg(a: int) -> int:\n    """Negate an integer. Serves DEMO-R-002."""\n'
NEG_STUB = NEG_HEAD + "    raise NotImplementedError\n"
NEG_DONE = NEG_HEAD + "    return -a  # neg-done\n"
NEG_PARTIAL = NEG_HEAD + "    return 0  # partial\n"


def impl(body: str) -> str:
    return f'"""Demo feature."""\n\n\ndef add(a: int, b: int) -> int:\n    {STUB_DOC}\n{body}'


WRONG = impl("    return a - b\n")
LOOSE = impl(
    "    if not isinstance(a, int) or not isinstance(b, int):\n"
    '        raise TypeError("ints only")\n'
    "    return a + b\n"
)
STRICT = impl(
    "    if type(a) is not int or type(b) is not int:\n"
    '        raise TypeError("ints only")\n'
    "    return a + b\n"
)


PYTHON = {
    "tests": ("tests/test_demo.py", TESTS),
    "impl": "src/demo/__init__.py",
    "wrong": WRONG,
    "loose": LOOSE,
    "strict": STRICT,
    "review": ("tests/review/test_review_demo.py", REVIEW_TEST),
    "review_invalid": ("tests/review/test_review_invalid.py", REVIEW_INVALID),
    "valid_name": "test_add_rejects_bool",
    "invalid_name": "test_add_rejects_negative",
}

CS_TESTS = """using System;
using Demo;
using Xunit;

namespace Demo.Tests
{
    public class CalcTests
    {
        [Fact]
        [Trait("Req", "DEMO-R-001")]
        public void Add_ReturnsSum()
        {
            Assert.Equal(5, Calc.Add(2, 3));
        }

        [Fact]
        [Trait("Req", "DEMO-E-001")]
        public void Add_Overflow_Throws()
        {
            Assert.Throws<OverflowException>(() => Calc.Add(int.MaxValue, 1));
        }
    }
}
"""

CS_REVIEW = """using System;
using Demo;
using Xunit;

namespace Demo.Tests.Review
{
    public class ReviewCalcTests
    {
        // Finding: overflow below int.MinValue is not detected.
        [Fact]
        [Trait("Req", "DEMO-E-001")]
        public void Add_NegativeOverflow_Throws()
        {
            Assert.Throws<OverflowException>(() => Calc.Add(int.MinValue, -1));
        }
    }
}
"""


CS_IMPL = """using System;

namespace Demo
{
    /// <summary>Demo feature.</summary>
    public static class Calc
    {
        /// <summary>Adds two ints. Serves DEMO-R-001, DEMO-E-001.</summary>
        public static int Add(int a, int b)
        {
BODY
        }
    }
}
"""

CS_LOOSE = """\
            if (a > 0 && b > int.MaxValue - a)
            {
                throw new OverflowException("int overflow");
            }

            return a + b;"""


def cs_impl(body: str) -> str:
    return CS_IMPL.replace("BODY", body)


DOTNET = {
    "tests": ("tests/Demo.Tests/CalcTests.cs", CS_TESTS),
    "impl": "src/Demo/Calc.cs",
    "wrong": cs_impl("            return a - b;"),
    "loose": cs_impl(CS_LOOSE),
    "strict": cs_impl("            return checked(a + b);"),
    "review": ("tests/Demo.Tests/Review/ReviewCalcTests.cs", CS_REVIEW),
    "valid_name": "Add_NegativeOverflow_Throws",
}


def write(rel: str, text: str) -> None:
    p = pathlib.Path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def main() -> int:
    prompt = sys.stdin.read()
    scen_path = pathlib.Path(os.environ["FAKE_CLAUDE_SCENARIO"])
    scen = json.loads(scen_path.read_text(encoding="utf-8"))
    state_path = scen_path.with_name("fake_state.json")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    phase = pathlib.Path(".phase").read_text(encoding="utf-8").strip()
    role_file = sys.argv[sys.argv.index("--append-system-prompt-file") + 1]
    with open(scen_path.with_name("calls.jsonl"), "a", encoding="utf-8") as f:
        call = {
            "phase": phase,
            "agent_env": os.environ.get("AGENTFLOW_AGENT"),
            "argv": sys.argv[1:],
            "prompt": prompt,
            "role": pathlib.Path(role_file).read_text(encoding="utf-8"),
        }
        f.write(json.dumps(call) + "\n")

    lang = DOTNET if scen.get("language") == "dotnet" else PYTHON
    limited = state.setdefault("limited", {})
    if limited.get(phase, 0) < scen.get("usage_limit", {}).get(phase, 0):
        # refused at the start, like the real CLI: no files, a rejected rate_limit_event
        limited[phase] = limited.get(phase, 0) + 1
        state_path.write_text(json.dumps(state), encoding="utf-8")
        reset = int(time.time()) + int(scen.get("usage_limit_reset_s", 120))
        info = {"status": "rejected", "resetsAt": reset, "rateLimitType": "five_hour"}
        result = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "result": "You've hit your limit · resets 4pm (UTC)",
            "num_turns": 1,
            "total_cost_usd": 0,
            "session_id": "fake",
        }
        print(json.dumps({"type": "system", "subtype": "init", "session_id": "fake"}))
        print(json.dumps({"type": "rate_limit_event", "rate_limit_info": info}))
        print(json.dumps(result))
        return 1
    runs = state.setdefault("runs", {})
    runs[phase] = runs.get(phase, 0) + 1
    if scen.get("hang") == phase and runs[phase] == 1:
        state_path.write_text(json.dumps(state), encoding="utf-8")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        pids = {"claude": os.getpid(), "child": child.pid}
        tmp = scen_path.with_name("hang_pids.tmp")
        tmp.write_text(json.dumps(pids), encoding="utf-8")
        tmp.replace(scen_path.with_name("hang_pids.json"))
        time.sleep(120)
        return 1
    asked = state.setdefault("asked", [])
    out = pathlib.Path("out/demo")
    if phase in scen.get("questions_in", []) and phase not in asked:
        asked.append(phase)
        write("out/demo/questions.md", f"- [{phase}] Should add() accept floats?\n")
        if phase == "IMPLEMENT":  # a park request: no code
            phase = "-"
    req = pathlib.Path("specs/demo/requirements.md")
    two = req.is_file() and "DEMO-R-002" in req.read_text(encoding="utf-8")
    if phase == "SPEC_UPDATE":
        mode = scen.get("spec_update", "edit")
        ref = re.search(r"ANS-\d+", prompt)
        tag = f" (answer {ref.group(0) if ref else '?'})"
        text = req.read_text(encoding="utf-8")
        row = '| DEMO-E-001 | a non-int argument | raises TypeError matching "ints only" |'
        if mode in ("edit", "new_id", "stray"):
            text = text.replace(row, row[:-2] + tag + " |")
        if mode == "new_id":
            text = text.replace(
                tag + " |", tag + " |\n| DEMO-E-002 | add(0, 0) | returns 0" + tag + " |"
            )
            plan = pathlib.Path("specs/demo/plan.md")
            ptext = plan.read_text(encoding="utf-8")
            plan.write_text(ptext.replace("DEMO-E-001\n", "DEMO-E-001, DEMO-E-002\n", 1), "utf-8")
        if mode == "renumber":
            text = text.replace("DEMO-E-001", "DEMO-E-009")
        if mode == "stray":
            write("specs/other/requirements.md", "# other\n")
            write("specs/demo/notes.md", "notes\n")
        if mode != "none":
            req.write_text(text, encoding="utf-8")
        write("out/demo/spec_update.md", f"- DEMO-E-001: {mode}{tag}\n")
    if phase == "TESTS":
        rel, text = lang["tests"]
        if two:
            text = text.replace("from demo import add", "from demo import add, neg") + TESTS_STEP2
        if req.is_file() and "DEMO-E-002" in req.read_text(encoding="utf-8"):
            text += TESTS_E2
        amend = "answered questions at the final gate" in prompt
        if scen.get("contest_steps") and not (amend and scen.get("contest_accept")):
            text += TESTS_CONTESTED
        write(rel, text)
        write("out/demo/test_summary.md", "| DEMO-R-001 | test_add_returns_sum | sum | example |\n")
    elif phase == "TEST_REVIEW":
        if runs[phase] <= scen.get("test_review_objections", 0):
            body = (
                "Verdict: OBJECTIONS\n\n## Objections\n"
                f"- DEMO-E-001 test_add_rejects_non_int: weak (review run {runs[phase]}) -> "
                "also check the message\n"
            )
        else:
            body = "Verdict: OK\n\n## Objections\nNone.\n"
        write("out/demo/test_review.md", body)
    elif phase == "IMPLEMENT":
        path = pathlib.Path(lang["impl"])
        cur = path.read_text(encoding="utf-8") if path.is_file() else ""
        # one step ("plan step 2") or a batch ("plan steps 1, 2 in one go"): the last one counts
        m = re.search(r"implement plan steps? (\d+(?:, \d+)*)", prompt)
        nums = [int(x) for x in m.group(1).split(", ")] if m else []
        step = nums[-1] if nums else 0
        parked = state.setdefault("parked", [])
        neg = (NEG_DONE if "# neg-done" in cur or step == 2 else NEG_STUB) if two else ""
        # strict once the code is, or once a failing review test (gate feedback) asks for it
        strict = "type(a) is not int" in cur or lang["valid_name"] in prompt
        # a provisional step whose test fix the human did not approve: the tests stand
        strict = strict or "did not approve changing its contested tests" in prompt
        fails = state.get("implement_failed", 0)
        park = [n for n in nums if n in scen.get("park_steps", []) and n not in parked]
        contested = state.setdefault("contested", [])
        contest = [n for n in nums if n in scen.get("contest_steps", []) and n not in contested]
        if contest:
            contested.append(contest[0])
            write(lang["impl"], lang["loose"] + neg)
            write(
                "out/demo/park.md",
                "Park kind: tests-wrong\n"
                "Contested tests: test_demo.test_add_refuses_bool_contested\n\n"
                "1. `test_add_refuses_bool_contested` (DEMO-E-001): bool is an int in Python, the "
                "spec says ints only. Fix: delete the test.\n",
            )
        elif park:
            step = park[0]
            parked.append(step)
            write(lang["impl"], (lang["strict"] if strict else lang["loose"]) + NEG_PARTIAL)
            write("out/demo/park.md", f"- [DEMO-R-002] Step {step}: should neg(0) be 0 or -0?\n")
        elif fails < scen.get("implement_fail", 0):
            state["implement_failed"] = fails + 1
            write(lang["impl"], lang["wrong"] + neg)
        elif "currently failing tests" in prompt:
            write(lang["impl"], lang["strict"] + neg)
        else:
            write(lang["impl"], (lang["strict"] if strict else lang["loose"]) + neg)
        if not out.joinpath("park.md").exists():
            note = "Assumptions: none\n"
            if scen.get("assume") and not state.get("assumed"):
                state["assumed"] = True
                note = "- ASSUMPTION [DEMO-E-001]: bool counts as int (spec silent).\n"
            # the step note goes where the task says (agentflow appends it to steps.md)
            m = re.search(r"Step note: write it with the Write tool to (\S+\.md)", prompt)
            target = pathlib.Path(m.group(1) if m else "out/demo/steps.md")
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as f:
                f.write(f"## Step {step or 'fix'} — add\nBuilt add().\n{note}")
    elif phase == "REVIEW":
        lines = ""
        if scen.get("review_finding"):
            write(*lang["review"])
            lines += f"{lang['valid_name']} DEMO-E-001 med bool accepted\n"
        if scen.get("review_invalid"):
            write(*lang["review_invalid"])
            lines += f"{lang['invalid_name']} DEMO-R-001 low negatives accepted\n"
        write("out/demo/review.md", lines or "No findings.\n")
    elif phase == "TRIAGE":
        lines = ""
        if lang["valid_name"] in prompt:
            lines += f"- {lang['valid_name']}: VALID — DEMO-E-001 says ints only\n"
        if lang.get("invalid_name", "-") in prompt:
            pathlib.Path(lang["review_invalid"][0]).unlink()
            lines += f"- {lang['invalid_name']}: INVALID — the spec allows negative ints\n"
        write("out/demo/triage.md", lines)
    elif phase == "DIAGNOSIS":
        owner = scen.get("diagnosis", "src")
        if owner != "none":
            write(
                "out/demo/diagnosis.md",
                "Root cause: add() returns a - b (src/demo/__init__.py:5).\n"
                f"Fix owner: {owner}\n"
                "Proposed fix: return a + b.\nEvidence:\n- ran the step tests\n",
            )
    elif phase == "PR":
        write("out/demo/final_summary.md", "# Final summary\nSummary.\n")
    elif phase == "RETRO":
        inp = pathlib.Path("out/demo/retro_input.md")
        ids = re.findall(r"^### ([QFERVGDAP]\d+) ", inp.read_text(encoding="utf-8"), re.M)
        if runs[phase] <= scen.get("retro_omit_runs", 1):
            ids = [i for i in ids if i not in scen.get("retro_omit", [])]
        body = "".join(f"- {i}: one-off\n" for i in ids) or "none\n"
        know = "".join(
            f"- K{i} [{s}]: {t}\n" for i, (s, t) in enumerate(scen.get("knowledge", []), 1)
        )
        write(
            "out/demo/retro_proposals.md",
            "# Retro\n\nNot acted on:\n" + body + "\n## Project knowledge\n" + (know or "None.\n"),
        )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "num_turns": 3,
        "total_cost_usd": 0.01,
        "session_id": "fake",
        "permission_denials": [],
    }
    if runs[phase] == 1:
        result["permission_denials"] = scen.get("denials", {}).get(phase, [])
    api_turns = 2
    if runs[phase] <= scen.get("max_turns", {}).get(phase, 0):
        cap = int(sys.argv[sys.argv.index("--max-turns") + 1])
        result.update(subtype="error_max_turns", is_error=True, num_turns=cap + 1)
        result.update(terminal_reason="max_turns")
        del result["result"]
        api_turns = cap
    elif runs[phase] <= scen.get("max_budget", {}).get(phase, 0):
        cap_usd = float(sys.argv[sys.argv.index("--max-budget-usd") + 1])
        result.update(subtype="error_max_budget_usd", is_error=True, total_cost_usd=cap_usd + 0.01)
        result.update(terminal_reason="budget_exhausted", errors=["Reached maximum budget"])
        del result["result"]
    fmt = sys.argv[sys.argv.index("--output-format") + 1]
    if fmt != "stream-json":
        print(json.dumps(result))
        return 0
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "fake"}))
    for i in range(api_turns):
        # one API turn can arrive as several lines (one per content block), same message id
        for block in ("thinking", "tool_use"):
            msg = {"id": f"msg_{i}", "role": "assistant", "content": [{"type": block}]}
            print(json.dumps({"type": "assistant", "message": msg}))
        print(json.dumps({"type": "user", "message": {"role": "user", "content": []}}))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
