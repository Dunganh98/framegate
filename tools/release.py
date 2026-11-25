from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = "tests.yml"
MAIN = "main"
POLL_SECONDS = 30
WAIT_MINUTES = 60
NOTE_MAX = 240
NOTE_MIN = 21
RUN_FIELDS = "databaseId,status,conclusion,event,headSha,createdAt,url"
USAGE = (
    "Releases one branch: bump on the branch, green `full` run, pull request, merge commit, "
    "green `full` run on main, tag, branch deleted. Stops at the first step that fails."
)

#: file -> pattern of its one version line ({v} = the version)
VERSION_LINES = {
    "pyproject.toml": '^version = "{v}"$',
    "src/framegate/__init__.py": '^__version__ = "{v}"$',
    "plugin/.claude-plugin/plugin.json": '^  "version": "{v}",$',
}
#: files where every mention of the version is replaced; they must name it
VERSION_TEXTS = ("docs/plugin.md", "docs/migrate-from-agentflow.md")
SKILLS = "plugin/skills/*/SKILL.md"
NOTES = "plugin/whats-new.json"


class Stop(Exception):
    pass


def parse_version(text: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", text):
        raise Stop(f"not a version (X.Y.Z): {text}")
    a, b, c = (int(x) for x in text.split("."))
    return a, b, c


def current_version(pyproject: str) -> str:
    m = re.search(VERSION_LINES["pyproject.toml"].format(v=r"([\d.]+)"), pyproject, re.M)
    if not m:
        raise Stop("pyproject.toml has no version line")
    return m.group(1)


def replace_line(text: str, pattern: str, old: str, new: str) -> str:
    rx = re.compile(pattern.format(v=re.escape(old)), re.M)
    if len(rx.findall(text)) != 1:
        raise Stop(f"expected one line {pattern.format(v=old)!r}")
    return rx.sub(lambda m: m.group(0).replace(old, new), text)


def replace_version(text: str, old: str, new: str) -> str:
    return re.sub(rf"(?<!\d)(?<!\d\.){re.escape(old)}(?!\d|\.\d)", new, text)


def note_problem(line: str) -> str:
    if '"' in line or "\\" in line or "\n" in line:
        return "the what's-new line holds a double quote, a backslash or a line break"
    if not NOTE_MIN <= len(line) < NOTE_MAX:
        return f"the what's-new line has {len(line)} characters ({NOTE_MIN} to {NOTE_MAX - 1})"
    return ""


def add_note(text: str, version: str, line: str) -> str:
    problem = note_problem(line)
    if problem:
        raise Stop(problem)
    if version in json.loads(text):
        raise Stop(f"{NOTES} has a line for {version} already")
    head, rest = text.split("\n", 1)
    out = f'{head}\n  "{version}": "{line}",\n{rest}'
    if next(iter(json.loads(out))) != version:
        raise Stop(f"{NOTES}: could not put the new line first")
    return out


def bumped_files(root: pathlib.Path, old: str, new: str, line: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel, pattern in VERSION_LINES.items():
        try:
            out[rel] = replace_line(_read(root / rel), pattern, old, new)
        except Stop as e:
            raise Stop(f"{rel}: {e}") from e
    for rel in VERSION_TEXTS:
        text = _read(root / rel)
        out[rel] = replace_version(text, old, new)
        if out[rel] == text:
            raise Stop(f"{rel} does not name version {old}")
    for path in sorted(root.glob(SKILLS)):
        text = _read(path)
        if replace_version(text, old, new) != text:
            out[path.relative_to(root).as_posix()] = replace_version(text, old, new)
    out[NOTES] = add_note(_read(root / NOTES), new, line)
    return out


def bump_state(current: str, new: str, head_subject: str) -> str:
    if current == new:
        if head_subject.startswith(f"Release {new}:"):
            return "done"
        raise Stop(f"the version is {new} already and HEAD is not its release commit")
    if parse_version(new) <= parse_version(current):
        raise Stop(f"{new} is not above the current version {current}")
    return "todo"


def run_state(runs: list[dict[str, Any]], sha: str, event: str) -> str:
    mine = [r for r in runs if r.get("headSha") == sha and r.get("event") == event]
    if not mine:
        return "none"
    last = max(mine, key=lambda r: r.get("createdAt") or "")
    if last.get("status") != "completed":
        return "pending"
    if last.get("conclusion") == "success":
        return "green"
    return f"red ({last.get('conclusion')}, {last.get('url')})"


def pr_state(prs: list[dict[str, Any]], sha: str) -> tuple[str, int]:
    for pr in prs:
        if pr.get("state") == "OPEN":
            return "open", int(pr["number"])
    for pr in prs:
        if pr.get("state") == "MERGED" and pr.get("headRefOid") == sha:
            return "merged", int(pr["number"])
    return "none", 0


def wait_green(
    what: str,
    read: Callable[[], str],
    minutes: int = WAIT_MINUTES,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    waited = 0
    while True:
        state = read()
        if state == "green":
            return
        if state.startswith("red"):
            raise Stop(f"{what}: {state}")
        if waited >= minutes * 60:
            raise Stop(f"{what}: still {state} after {minutes} min")
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def sh(*cmd: str, ok: bool = False) -> str:
    r = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if r.returncode != 0 and not ok:
        raise Stop(f"{' '.join(cmd)} failed:\n{(r.stderr or r.stdout).strip()[-2000:]}")
    return r.stdout.strip()


def full_run(sha: str, event: str = "push") -> str:
    out = sh("gh", "run", "list", "--workflow", WORKFLOW, "--commit", sha, "--json", RUN_FIELDS)
    return run_state(json.loads(out or "[]"), sha, event)


def pull_request(branch: str, sha: str) -> tuple[str, int]:
    fields = "number,state,headRefOid"
    out = sh(
        "gh", "pr", "list", "--head", branch, "--base", MAIN, "--state", "all", "--json", fields
    )
    return pr_state(json.loads(out or "[]"), sha)


def head(ref: str = "HEAD") -> str:
    return sh("git", "rev-parse", ref)


def preconditions(branch: str, version: str) -> None:
    if branch == MAIN:
        raise Stop(f"{MAIN} is not released from itself: name the branch")
    if sh("git", "status", "--porcelain"):
        raise Stop("the working tree is not clean")
    if sh("git", "rev-parse", "--abbrev-ref", "HEAD") != branch:
        raise Stop(f"check out {branch} first")
    sh("git", "fetch", "origin", "--tags", "--prune")
    if sh("git", "rev-parse", "--verify", "--quiet", f"refs/tags/v{version}", ok=True):
        raise Stop(f"tag v{version} exists")
    if sh("git", "rev-parse", "--verify", "--quiet", f"origin/{branch}", ok=True) != head():
        raise Stop(f"{branch} is not pushed (origin/{branch} is not its head)")


def bump(branch: str, old: str, new: str, line: str) -> None:
    files = bumped_files(ROOT, old, new, line)
    for rel, text in files.items():
        (ROOT / rel).write_text(text, encoding="utf-8", newline="\n")
    sh("uv", "lock")
    left = "the bump is in the working tree, uncommitted"
    try:
        sh("uv", "run", "pytest", "tests/test_release.py", "-q", "-p", "no:cacheprovider")
        tests = sorted(p.as_posix() for p in (ROOT / "plugin/tests").glob("*.test.js"))
        sh("node", "--test", *tests)
    except Stop as e:
        raise Stop(f"{e}\n{left}") from e
    sh("git", "add", "--", *files, "uv.lock")
    sh("git", "commit", "-m", f"Release {new}: {line}")
    sh("git", "push", "origin", branch)


def release(branch: str, new: str, line: str, minutes: int) -> None:
    problem = note_problem(line)
    if problem:
        raise Stop(problem)
    preconditions(branch, new)
    old = current_version(_read(ROOT / "pyproject.toml"))
    if bump_state(old, new, sh("git", "log", "-1", "--format=%s")) == "todo":
        state = full_run(head())
        if state != "green":
            raise Stop(f"`full` on the head of {branch} ({head()[:7]}) is not green: {state}")
        bump(branch, old, new, line)
        print(f"bumped to {new}, pushed {head()[:7]}")
    sha = head()
    state, number = pull_request(branch, sha)
    if state != "merged":
        wait_green(f"`full` on {sha[:7]}", lambda: full_run(sha), minutes)
        if state == "none":
            title = f"Release {new}"
            sh("gh", "pr", "create", "--head", branch, "--base", MAIN, "-t", title, "-b", line)
            state, number = pull_request(branch, sha)
            if state != "open":
                raise Stop("the pull request was created and is not listed")
            print(f"opened pull request #{number}")
        check = f"the check of pull request #{number}"
        wait_green(check, lambda: full_run(sha, "pull_request"), minutes)
        sh("gh", "pr", "merge", str(number), "--merge")
        print(f"merged #{number}")
    merged = json.loads(sh("gh", "pr", "view", str(number), "--json", "mergeCommit,state"))
    on_main = (merged.get("mergeCommit") or {}).get("oid") or ""
    if merged.get("state") != "MERGED" or not on_main:
        raise Stop(f"pull request #{number} is not merged")
    wait_green(f"`full` on {MAIN} ({on_main[:7]})", lambda: full_run(on_main), minutes)
    sh("git", "fetch", "origin", MAIN)
    sh("git", "tag", f"v{new}", on_main)
    sh("git", "push", "origin", f"v{new}")
    print(f"tagged v{new} on {on_main[:7]}")
    sh("git", "checkout", MAIN)
    sh("git", "pull", "--ff-only", "origin", MAIN)
    sh("git", "push", "origin", "--delete", branch)
    sh("git", "branch", "-d", branch)
    print(f"released {new}; {branch} deleted, on {MAIN}")


def _try(read: Callable[[], str]) -> str:
    try:
        return read()
    except (Stop, OSError, ValueError) as e:
        return f"unknown ({str(e).splitlines()[0][:120]})"


def dry_run(branch: str, new: str, line: str) -> list[str]:
    sha = _try(lambda: head(branch))
    old = _try(lambda: current_version(_read(ROOT / "pyproject.toml")))
    subject = _try(lambda: sh("git", "log", "-1", "--format=%s", branch))
    bumped = _try(lambda: bump_state(old, new, subject))
    pr, number = "unknown", 0
    try:
        pr, number = pull_request(branch, sha)
    except (Stop, OSError, ValueError) as e:
        pr = f"unknown ({str(e).splitlines()[0][:120]})"
    after = "after the bump" if bumped == "todo" else ""
    tag = sh("git", "rev-parse", "--verify", "--quiet", f"refs/tags/v{new}", ok=True)
    pushed = sh("git", "rev-parse", "--verify", "--quiet", f"origin/{branch}", ok=True) == sha
    here = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    return [
        f"working tree clean: {'yes' if not sh('git', 'status', '--porcelain') else 'NO'}",
        f"on {branch}: {'yes' if here == branch else 'NO'}",
        f"{branch} pushed (as of the last fetch): {'yes' if pushed else 'NO'}",
        f"`full` on its head {sha[:7]}: {_try(lambda: full_run(sha))}",
        f"what's-new line: {note_problem(line) or 'ok'}",
        f"version {old} -> {new}, release tests, commit, push: {bumped}",
        f"`full` on the release commit: {after or _try(lambda: full_run(sha))}",
        f"pull request: {pr}{f' #{number}' if number else ' (to open)' if pr == 'none' else ''}",
        f"pull request check: {after or _try(lambda: full_run(sha, 'pull_request'))}",
        f"merge with a merge commit: {'done' if pr == 'merged' else 'to do'}",
        f"`full` on {MAIN}'s new head: after the merge",
        f"tag v{new}: {'EXISTS' if tag else 'free'}",
        f"delete {branch} (remote, local), {MAIN} checked out and pulled: to do",
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="release.py", description=USAGE)
    p.add_argument("branch")
    p.add_argument("version")
    p.add_argument("line", help="the what's-new line (under 240 characters, no double quotes)")
    p.add_argument("--dry-run", action="store_true", help="print the steps and their state")
    p.add_argument("--wait-minutes", type=int, default=WAIT_MINUTES, help="cap of every wait")
    args = p.parse_args(argv)
    try:
        parse_version(args.version)
        if args.dry_run:
            for n, row in enumerate(dry_run(args.branch, args.version, args.line), 1):
                print(f"{n:2}. {row}")
            return 0
        release(args.branch, args.version, args.line, args.wait_minutes)
    except Stop as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
