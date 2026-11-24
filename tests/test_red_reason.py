"""A red test fails for the right reason, and the asserts it never reached are listed for the
test reviewer (checks/red_stubs.py; the Python adapter reads pytest's stack traces)."""

from __future__ import annotations

import pathlib

from framegate.adapters.base import TestCase
from framegate.checks import red_stubs
from framegate.scaffold import package_text
from test_checks import adapter_for, write

SRC = """\
def message(n: int) -> dict[str, object]:
    raise NotImplementedError


def status() -> dict[str, object]:
    return {"ok": True}


def parse(text: str) -> int:
    return int(text)
"""
TESTS = """\
import pytest

from pkg.a import message, parse, status


@pytest.mark.req("FT-R-001")
def test_on_the_stub_then_two_asserts() -> None:
    msg = message(1)
    assert msg["age"] is None
    old = {k: v for k, v in msg.items() if k != "age"}
    assert old == {"status": {}}


@pytest.mark.req("FT-R-001")
def test_on_an_assert() -> None:
    assert "age_error" in status()
    assert status()["age_error"] is False


@pytest.mark.req("FT-R-001")
def test_inside_the_code() -> None:
    assert parse("1.5") == 1


@pytest.mark.req("FT-R-001")
def test_stops_in_the_test() -> None:
    flags = status()
    assert flags["age_error"] is False
    assert flags == {"ok": True}
"""


def repo(root: pathlib.Path) -> None:
    write(root, "specs/f/requirements.md", "FT-R-001")
    write(root, "src/pkg/a.py", SRC)
    write(root, "tests/test_f.py", TESTS)
    write(root, "pyproject.toml", "[tool.pytest.ini_options]\npythonpath = ['src']\n")


def test_a_test_that_stops_before_the_code_under_test_is_sent_back(tmp_path: pathlib.Path) -> None:
    repo(tmp_path)
    a = adapter_for(tmp_path)

    res = red_stubs.check(a, "f")

    assert res.status == "FAIL", res.output
    wrong = [x for x in res.output.splitlines() if x.startswith("FAILS FOR ANOTHER REASON")]
    assert len(wrong) == 1, res.output
    assert wrong[0].startswith(
        "FAILS FOR ANOTHER REASON THAN THE STUB: tests.test_f::test_stops_in_the_test — stops "
        "on `KeyError: 'age_error'` at tests/test_f.py:28, before the code under test"
    )
    assert red_stubs.OTHER_REMEDY in res.output
    # the stub, an assertion and an error inside the code under test are right reasons
    for name in ("on_the_stub", "on_an_assert", "inside_the_code"):
        assert f"test_{name}" not in res.output, name


def test_the_asserts_a_red_test_never_reached_are_listed(tmp_path: pathlib.Path) -> None:
    repo(tmp_path)
    a = adapter_for(tmp_path)
    assert red_stubs.not_reached(a, "f") == []  # no red run yet

    red_stubs.check(a, "f")

    assert red_stubs.not_reached(a, "f") == [
        "tests/test_f.py::test_on_the_stub_then_two_asserts: red at :8; asserts at :9, :11 "
        "never ran",
        "tests/test_f.py::test_on_an_assert: red at :16; asserts at :17 never ran",
        "tests/test_f.py::test_stops_in_the_test: red at :28; asserts at :29 never ran",
    ]


def test_the_reason_is_read_from_the_stack_trace(tmp_path: pathlib.Path) -> None:
    repo(tmp_path)
    a = adapter_for(tmp_path)
    trace = "KeyError: 'k'\ndef test_x():\n>   d['k']\nE   KeyError: 'k'\n\n"
    trace += "tests\\test_f.py:5: KeyError"
    native = (
        'TypeError: f() got an unexpected keyword argument\n  File "tests/test_f.py", line 7, in t'
    )
    inside = trace + "\nsrc\\pkg\\a.py:11: in parse"

    assert a.failure_frames(TestCase("t", "failed", trace)) == [("tests/test_f.py", 5)]
    assert a.failure_frames(TestCase("t", "failed", native)) == [("tests/test_f.py", 7)]
    assert "tests/test_f.py:5" in (a.stub_failure_problem(TestCase("t", "failed", trace)) or "")
    assert "tests/test_f.py:7" in (a.stub_failure_problem(TestCase("t", "failed", native)) or "")
    for fine in (
        inside,
        "assert 1 == 2\n" + trace,
        "AssertionError: no\n" + trace,
        "Failed: DID NOT RAISE <class 'ValueError'>\n" + trace,
        trace.replace("KeyError: 'k'", "NotImplementedError"),
        "KeyError: 'k'",  # no stack trace to tell by
    ):
        assert a.stub_failure_problem(TestCase("t", "failed", fine)) is None, fine


def test_the_roles_say_what_to_do_with_it() -> None:
    review = " ".join(package_text("roles", "test_review.md").split())
    assert "walk every assert of every test by hand with the spec's example" in review
    assert 'under "Asserts not reached at red"' in review
    writer = " ".join(package_text("roles", "tests.md").split())
    assert "A red test fails for the right reason" in writer
