"""Fake `claude -p` for the end-to-end tests (selected via AGENTFLOW_CLAUDE).

Reads the task from stdin, the phase from .phase, and a scenario from FAKE_CLAUDE_SCENARIO
(JSON). Writes canned files for feature `demo`, logs every call to calls.jsonl next to the
scenario file, and prints the `--output-format json` result shape.

Scenario keys:
  questions_in: list of phases that ask one question (first run of that phase only)
  implement_fail: number of IMPLEMENT runs that write a wrong implementation
  review_finding: true -> the reviewer writes one failing test (bool accepted as int)
"""

import json
import os
import pathlib
import sys

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

REVIEW_TEST = """\
import pytest

from demo import add


# Finding: bool is a subclass of int, so add(True, 1) is accepted; the spec says ints only.
@pytest.mark.req("DEMO-E-001")
def test_add_rejects_bool() -> None:
    with pytest.raises(TypeError, match="ints only"):
        add(True, 1)
"""


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
            "argv": sys.argv[1:],
            "prompt": prompt,
            "role": pathlib.Path(role_file).read_text(encoding="utf-8"),
        }
        f.write(json.dumps(call) + "\n")

    runs = state.setdefault("runs", {})
    runs[phase] = runs.get(phase, 0) + 1
    asked = state.setdefault("asked", [])
    if phase in scen.get("questions_in", []) and phase not in asked:
        asked.append(phase)
        write("out/demo/questions.md", f"- [{phase}] Should add() accept floats?\n")
    elif phase == "TESTS":
        write("tests/test_demo.py", TESTS)
        write("out/demo/test_summary.md", "| DEMO-R-001 | test_add_returns_sum | sum | example |\n")
    elif phase == "IMPLEMENT":
        fails = state.get("implement_failed", 0)
        if fails < scen.get("implement_fail", 0):
            state["implement_failed"] = fails + 1
            write("src/demo/__init__.py", WRONG)
        elif "currently failing tests" in prompt:
            write("src/demo/__init__.py", STRICT)
        else:
            write("src/demo/__init__.py", LOOSE)
        with open("out/demo/steps.md", "a", encoding="utf-8") as f:
            f.write("## Step 1 — add\nBuilt add(). Assumption: bool counts as int.\n")
    elif phase == "REVIEW":
        if scen.get("review_finding"):
            write("tests/review/test_review_demo.py", REVIEW_TEST)
            write("out/demo/review.md", "test_add_rejects_bool DEMO-E-001 med bool accepted\n")
        else:
            write("out/demo/review.md", "No findings.\n")
    elif phase == "PR":
        write("out/demo/pr.md", "# PR\nSummary.\n")
    elif phase == "RETRO":
        write("out/demo/retro_proposals.md", "# Retro\nNone.\n")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "num_turns": 3,
        "total_cost_usd": 0.01,
        "session_id": "fake",
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
