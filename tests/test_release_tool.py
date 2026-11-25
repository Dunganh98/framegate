from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("release_tool", ROOT / "tools" / "release.py")
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

NOTE = "the release is one command and stops at the first red run."
NOTES = '{\n  "1.2.3": "an older line that is long enough.",\n  "1.2.2": "x"\n}\n'


def tree(root: pathlib.Path) -> pathlib.Path:
    files = {
        "pyproject.toml": '[project]\nversion = "1.2.3"\ndependencies = ["dep>=1.2.3"]\n',
        "src/framegate/__init__.py": '__version__ = "1.2.3"\n',
        "plugin/.claude-plugin/plugin.json": '{\n  "version": "1.2.3",\n  "name": "x"\n}\n',
        "docs/plugin.md": "name framegate, version 1.2.3\n",
        "docs/migrate-from-agentflow.md": "install @v1.2.3 then 1.2.3.\n",
        "plugin/skills/setup/SKILL.md": "This plugin is version 1.2.3. Not 11.2.3 or 1.2.30.\n",
        "plugin/skills/status/SKILL.md": "no version here\n",
        "plugin/whats-new.json": NOTES,
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def test_the_bump_replaces_the_version_where_it_is_named(tmp_path: pathlib.Path) -> None:
    out = tool.bumped_files(tree(tmp_path), "1.2.3", "1.3.0", NOTE)

    assert out["pyproject.toml"] == '[project]\nversion = "1.3.0"\ndependencies = ["dep>=1.2.3"]\n'
    assert out["src/framegate/__init__.py"] == '__version__ = "1.3.0"\n'
    assert '"version": "1.3.0",' in out["plugin/.claude-plugin/plugin.json"]
    assert out["docs/migrate-from-agentflow.md"] == "install @v1.3.0 then 1.3.0.\n"
    skill = out["plugin/skills/setup/SKILL.md"]
    assert skill == "This plugin is version 1.3.0. Not 11.2.3 or 1.2.30.\n"
    assert "plugin/skills/status/SKILL.md" not in out
    assert list(json.loads(out["plugin/whats-new.json"])) == ["1.3.0", "1.2.3", "1.2.2"]
    assert tool.current_version(out["pyproject.toml"]) == "1.3.0"


def test_a_file_that_lost_its_version_stops_the_bump(tmp_path: pathlib.Path) -> None:
    root = tree(tmp_path)
    (root / "docs/plugin.md").write_text("no version\n", encoding="utf-8")
    with pytest.raises(tool.Stop, match="docs/plugin.md does not name version 1.2.3"):
        tool.bumped_files(root, "1.2.3", "1.3.0", NOTE)
    (root / "src/framegate/__init__.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(tool.Stop, match="src/framegate/__init__.py: expected one line"):
        tool.bumped_files(root, "1.2.3", "1.3.0", NOTE)


def test_the_new_line_goes_first_and_the_rest_stays() -> None:
    out = tool.add_note(NOTES, "1.3.0", NOTE)
    assert out == f'{{\n  "1.3.0": "{NOTE}",\n' + NOTES.split("\n", 1)[1]
    with pytest.raises(tool.Stop, match="has a line for 1.2.3 already"):
        tool.add_note(NOTES, "1.2.3", NOTE)


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ('says "ok" at the end of a task', "double quote"),
        ("a path with a back\\slash in the line", "backslash"),
        ("two\nlines in the what's-new text", "line break"),
        ("too short", "has 9 characters"),
        ("x" * 240, "has 240 characters"),
    ],
)
def test_a_line_the_file_cannot_hold_is_refused(line: str, problem: str) -> None:
    assert problem in tool.note_problem(line)
    with pytest.raises(tool.Stop):
        tool.add_note(NOTES, "1.3.0", line)


def test_a_line_of_239_characters_is_taken() -> None:
    assert tool.note_problem("x" * 239) == ""


def test_the_bump_is_done_once_and_only_upwards() -> None:
    assert tool.bump_state("1.2.3", "1.2.4", "some work") == "todo"
    assert tool.bump_state("1.2.4", "1.2.4", "Release 1.2.4: a line") == "done"
    with pytest.raises(tool.Stop, match="HEAD is not its release commit"):
        tool.bump_state("1.2.4", "1.2.4", "some work")
    with pytest.raises(tool.Stop, match="1.2.10 is not above the current version 1.3.0"):
        tool.bump_state("1.3.0", "1.2.10", "some work")
    with pytest.raises(tool.Stop, match="not a version"):
        tool.bump_state("1.3.0", "1.4", "some work")


def run(sha: str, status: str, conclusion: str, at: str, event: str = "push") -> dict[str, str]:
    return {
        "headSha": sha,
        "status": status,
        "conclusion": conclusion,
        "createdAt": at,
        "event": event,
        "url": "https://example.test/run/1",
    }


def test_the_latest_run_of_the_commit_and_event_decides() -> None:
    runs = [
        run("aaa", "completed", "failure", "2026-01-01T10:00:00Z"),
        run("aaa", "completed", "success", "2026-01-01T11:00:00Z"),
        run("bbb", "in_progress", "", "2026-01-01T12:00:00Z"),
        run("ccc", "completed", "cancelled", "2026-01-01T12:00:00Z"),
        run("ddd", "completed", "success", "2026-01-01T12:00:00Z", "pull_request"),
    ]
    assert tool.run_state(runs, "aaa", "push") == "green"
    assert tool.run_state(runs, "bbb", "push") == "pending"
    assert tool.run_state(runs, "ccc", "push") == "red (cancelled, https://example.test/run/1)"
    assert tool.run_state(runs, "ddd", "push") == "none"
    assert tool.run_state(runs, "ddd", "pull_request") == "green"
    assert tool.run_state([], "aaa", "push") == "none"


def test_an_open_pull_request_wins_and_a_merged_one_counts_for_its_head_only() -> None:
    merged = {"number": 4, "state": "MERGED", "headRefOid": "aaa"}
    closed = {"number": 5, "state": "CLOSED", "headRefOid": "bbb"}
    assert tool.pr_state([merged, closed], "aaa") == ("merged", 4)
    assert tool.pr_state([merged, closed], "bbb") == ("none", 0)
    opened = {"number": 6, "state": "OPEN", "headRefOid": "bbb"}
    assert tool.pr_state([merged, opened], "aaa") == ("open", 6)


def test_waiting_ends_on_green_stops_on_red_and_at_the_cap() -> None:
    slept: list[float] = []
    states = iter(["none", "pending", "green"])
    tool.wait_green("run", lambda: next(states), 5, slept.append)
    assert slept == [30, 30]

    states = iter(["pending", "red (failure, url)"])
    with pytest.raises(tool.Stop, match=r"run: red \(failure, url\)"):
        tool.wait_green("run", lambda: next(states), 5, slept.append)

    slept.clear()
    with pytest.raises(tool.Stop, match="run: still pending after 1 min"):
        tool.wait_green("run", lambda: "pending", 1, slept.append)
    assert slept == [30, 30]


def test_the_bump_of_this_repository_reaches_every_file_that_names_the_version() -> None:
    old = tool.current_version((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out = tool.bumped_files(ROOT, old, "99.0.0", NOTE)
    named = set()
    for folder in ("docs", "plugin", "src/framegate"):
        for p in (ROOT / folder).rglob("*"):
            if p.suffix in (".md", ".json", ".py", ".toml", ".js") and p.is_file():
                text = p.read_text(encoding="utf-8")
                if tool.replace_version(text, old, "99.0.0") != text:
                    named.add(p.relative_to(ROOT).as_posix())
    assert named - set(out) == set()
    assert [rel for rel, text in out.items() if old in text] == ["plugin/whats-new.json"]
