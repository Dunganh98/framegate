"""failure_filter: the failure lines of a check's output, with context and the closing tail."""

from __future__ import annotations

from framegate.failure_filter import MAX_LINE, filter_failure_output


def noise(n: int, prefix: str = "collecting module") -> list[str]:
    return [f"{prefix} {i}" for i in range(n)]


def test_short_output_is_kept_whole() -> None:
    text = "\n".join(noise(10)) + "\n"
    assert filter_failure_output(text, max_lines=40) == "\n".join(noise(10))


def test_ansi_removed_and_blank_runs_collapsed() -> None:
    text = "\x1b[31mFAILED\x1b[0m test_a\n\n\n\nsummary\n\n"
    assert filter_failure_output(text) == "FAILED test_a\n\nsummary"


def test_failure_far_above_the_tail_is_kept() -> None:
    """A plain tail of 30 lines would lose the assertion; the filter keeps it with context."""
    lines = [
        *noise(20),
        "    def test_add():",
        ">       assert add(2, 3) == 5",
        "E       assert 6 == 5",
        "tests/test_demo.py:12: AssertionError",
        *noise(60, "passed module"),
        "=== short test summary info ===",
        "FAILED tests/test_demo.py::test_add - assert 6 == 5",
        "=== 1 failed, 60 passed in 0.5s ===",
    ]
    out = filter_failure_output("\n".join(lines), max_lines=40).splitlines()
    assert "E       assert 6 == 5" in out
    assert "tests/test_demo.py:12: AssertionError" in out
    assert "    def test_add():" in out  # context before the failure
    assert out[-1] == "=== 1 failed, 60 passed in 0.5s ==="
    assert out[0] == "..." and "..." in out[1:]  # gaps are marked
    assert "collecting module 0" not in out
    assert len(out) <= 40


def test_lint_findings_kept_by_their_location() -> None:
    """Lint lines name no failure word; their file location keeps them."""
    findings = [f"src/demo/m{i}.py:{i}:1: F401 `os` imported but unused" for i in range(5)]
    cs = "src/Demo/Foo.cs(12,5): warning IDE0055: Fix formatting"
    text = "\n".join([*noise(50), *findings, cs, *noise(20), "Found 5 problems."])
    out = filter_failure_output(text, max_lines=40).splitlines()
    for f in [*findings, cs]:
        assert f in out


def test_nothing_failure_like_keeps_a_longer_tail() -> None:
    text = "\n".join(noise(100))
    out = filter_failure_output(text, max_lines=40).splitlines()
    assert out[0] == "..."
    assert out[1:] == noise(100)[-30:]


def test_over_budget_keeps_first_failures_and_the_summary() -> None:
    lines = []
    for i in range(50):
        lines += [f"FAILED test_{i}", *noise(5, f"detail {i}")]
    lines.append("=== 50 failed ===")
    out = filter_failure_output("\n".join(lines), max_lines=40, tail=8).splitlines()
    assert len(out) == 40
    assert "FAILED test_0" in out
    assert any(line.startswith("... (") and "more lines omitted" in line for line in out)
    assert out[-1] == "=== 50 failed ==="


def test_long_lines_clipped() -> None:
    out = filter_failure_output("error: " + "x" * 1000)
    assert len(out) == MAX_LINE + 3 and out.endswith("...")
