"""Generic adapter: a language without agentflow code, configured in agentflow.toml. The demo
project (Python) is driven through [generic] commands only, end to end."""

from __future__ import annotations

import pathlib
import sys

import pytest

from agentflow.adapters import get_adapter
from agentflow.adapters.generic import GenericAdapter, _fill
from agentflow.config import ConfigError, load_config, parse_config
from agentflow.orchestrator import EXIT_GATE
from conftest import DemoRepo

PYTEST = ["{py}", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
GENERIC = """
[generic]
source_suffixes = [".py"]
test_files = ["**/test_*.py"]
tag_pattern = 'mark\\.req\\(([^)]*)\\)'
test_all = {all}
test_ids = {ids}
test_paths = {paths}
stub_marker = "NotImplementedError"
skip_pattern = 'mark\\.skip\\b'
format_fix = ["{py}", "-m", "ruff", "format", "{{files}}"]
forbidden_patterns = ['type:\\s*ignore']
protected = ["pyproject.toml"]
conventions = "Stubs raise NotImplementedError. Tag tests with @pytest.mark.req(ID, ...)."
"""


def generic_toml(py: str) -> str:
    def cmd(*extra: str) -> str:
        parts = [x.replace("{py}", py) for x in PYTEST] + list(extra) + ["--junitxml={junit}"]
        return "[" + ", ".join(f"'{p}'" for p in parts) + "]"

    return GENERIC.format(
        all=cmd(),
        ids=cmd("--req", "{ids}"),
        paths=cmd("{paths}"),
        py=py,
    )


def use_generic(r: DemoRepo) -> None:
    p = r.root / "agentflow.toml"
    text = p.read_text(encoding="utf-8").replace('language = "python"', 'language = "generic"', 1)
    p.write_text(text + generic_toml(sys.executable.replace("\\", "/")), encoding="utf-8")


def test_the_whole_flow_on_the_generic_adapter(demo_repo: DemoRepo) -> None:
    r = demo_repo
    use_generic(r)
    r.add_toml("[gates.commands]\nlint = []\nformat = []\ntypecheck = []\n")
    r.defer_tests_gate()
    r.scenario(review_finding=True)
    adapter = get_adapter(load_config(r.root))
    assert isinstance(adapter, GenericAdapter)
    assert r.af("approve-spec", "demo") == 0
    assert r.af("run", "demo") == EXIT_GATE and r.progress()["waiting"]["gate"] == "final"
    phases = [c["phase"] for c in r.calls()]
    assert phases == ["TESTS", "TEST_REVIEW", "IMPLEMENT", "REVIEW", "TRIAGE", "IMPLEMENT", "PR"]
    checks = (r.root / "out/demo/test_checks.md").read_text(encoding="utf-8")
    assert "### Red against stubs: PASS" in checks and "### Traceability: PASS" in checks
    impl = r.calls()[2]["prompt"]
    assert "--req DEMO-R-001,DEMO-E-001" in impl and "Tag tests with @pytest.mark.req" in impl
    assert (
        f"Bash({sys.executable.replace(chr(92), '/')} *)"
        in r.calls()[2]["argv"][r.calls()[2]["argv"].index("--allowedTools") + 1]
    )
    final = (r.root / "out/demo/final_gates.md").read_text(encoding="utf-8")
    assert "ALL GATES PASSED" in final
    assert "SKIPPED: [generic] coverage is not configured" in final
    assert "SKIPPED: [generic] mutation is not configured" in final
    tags = adapter.collect_req_tags()
    assert any(w.startswith("tests/test_demo.py:") for w in tags["DEMO-E-001"])
    assert any(w.startswith("tests/review/test_review_demo.py:") for w in tags["DEMO-E-001"])


def test_generic_config_and_helpers(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ConfigError, match="needs test_all, test_ids, tag_pattern"):
        get_adapter(parse_config(tmp_path, {"project": {"language": "generic"}}))
    with pytest.raises(ConfigError, match="unknown key 'test_cmd'"):
        parse_config(tmp_path, {"project": {"language": "generic"}, "generic": {"test_cmd": []}})
    assert _fill(
        ["run", "{paths}", "-t", "{ids_regex}", "--out={junit}"],
        {
            "paths": ["a b", "c"],
            "ids_regex": "X\\-R\\-001|Y",
            "junit": "out/j.xml",
        },
    ) == ["run", "a b", "c", "-t", "X\\-R\\-001|Y", "--out=out/j.xml"]
    cfg = parse_config(
        tmp_path,
        {
            "project": {"language": "generic"},
            "generic": {
                "test_all": ["t"],
                "test_ids": ["t", "--grep", "{ids_regex}"],
                "tag_pattern": "req\\(([^)]*)\\)",
                "ids_separator": " ",
            },
        },
    )
    a = get_adapter(cfg)
    assert a.test_command(["A-R-001", "A-E-002"]) == "t --grep 'A-R-001|A-E-002'"
    assert a.test_command() == "t"
    assert a.mutation_check("f", "x", ["src/a.ts"]).status == "SKIPPED"


def test_init_generic_writes_a_parsable_template(tmp_path: pathlib.Path) -> None:
    from agentflow.scaffold import init

    lines: list[str] = []
    assert init(language="generic", root=tmp_path, echo=lines.append) == 0
    text = (tmp_path / "agentflow.toml").read_text(encoding="utf-8")
    assert "[generic]" in text and (tmp_path / ".agentflow/project.md").is_file()
    a = get_adapter(load_config(tmp_path))
    assert a.test_command(["X-R-001"]).startswith("npx vitest run -t X-R-001")
    assert "agentflow doctor" in "\n".join(lines)
