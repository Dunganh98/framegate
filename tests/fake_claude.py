"""Fake `claude -p` for the end-to-end tests (selected via AGENTFLOW_CLAUDE).

Reads the task from stdin, the phase from .phase, and a scenario from FAKE_CLAUDE_SCENARIO
(JSON). Writes canned files for feature `demo`, logs every call to calls.jsonl next to the
scenario file, and prints the `--output-format json` result shape.

Scenario keys:
  questions_in: list of phases that ask one question (first run of that phase only)
  implement_fail: number of IMPLEMENT runs that write a wrong implementation
  review_finding: true -> the reviewer writes one failing test (bool accepted as int)
  language: "python" (default) or "dotnet" (C# files for the .NET fixture, see test_dotnet_e2e)
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


PYTHON = {
    "tests": ("tests/test_demo.py", TESTS),
    "impl": "src/demo/__init__.py",
    "wrong": WRONG,
    "loose": LOOSE,
    "strict": STRICT,
    "review": ("tests/review/test_review_demo.py", REVIEW_TEST),
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
            "argv": sys.argv[1:],
            "prompt": prompt,
            "role": pathlib.Path(role_file).read_text(encoding="utf-8"),
        }
        f.write(json.dumps(call) + "\n")

    lang = DOTNET if scen.get("language") == "dotnet" else PYTHON
    runs = state.setdefault("runs", {})
    runs[phase] = runs.get(phase, 0) + 1
    asked = state.setdefault("asked", [])
    if phase in scen.get("questions_in", []) and phase not in asked:
        asked.append(phase)
        write("out/demo/questions.md", f"- [{phase}] Should add() accept floats?\n")
    elif phase == "TESTS":
        write(*lang["tests"])
        write("out/demo/test_summary.md", "| DEMO-R-001 | test_add_returns_sum | sum | example |\n")
    elif phase == "IMPLEMENT":
        fails = state.get("implement_failed", 0)
        if fails < scen.get("implement_fail", 0):
            state["implement_failed"] = fails + 1
            write(lang["impl"], lang["wrong"])
        elif "currently failing tests" in prompt:
            write(lang["impl"], lang["strict"])
        else:
            write(lang["impl"], lang["loose"])
        with open("out/demo/steps.md", "a", encoding="utf-8") as f:
            f.write("## Step 1 — add\nBuilt add(). Assumption: bool counts as int.\n")
    elif phase == "REVIEW":
        if scen.get("review_finding"):
            write(*lang["review"])
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
