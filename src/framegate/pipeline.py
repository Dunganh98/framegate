"""The pipeline task (`/framegate:pipeline <name>`): the separate step that changes deploy
and CI files.

No feature, bug fix or refactor changes a deploy / CI file (followup.py): what it makes
necessary there is kept as a list, {specs}/<f>/pipeline_followup.md. This step works that
list off, or a list the chat writes for a change of its own (no task before it). It has no
spec, no tests and no agent run: a yml change has no unit test. The chat edits the files
itself, inside a guard phase of its own:

- `framegate pipeline <name>`: switch to the branch, set the phase `PIPELINE <name>` and
  remember the commit it started on (a private ref). In that phase the write guard
  (hooks/protect_paths.py) allows only deploy / CI files (frame_diff.is_pipeline_file: the
  built-in deploy / CI names and `[protected] extra`) that the list names, the list itself
  and the brief ({out}/<name>/pipeline_brief.md). Everything else stays locked.
- A list line `OUTSIDE [<where>]: ...` (followup.py) is no file of this repository: the
  task shows it under a line of its own and never does, counts or checks it.
- `framegate pipeline-check <name>`: mechanical checks, no agent (check).
- `framegate pipeline-approve <name>` (after the user's OK on the brief): the check again,
  the done items ticked, the files and the list committed, the phase cleared.

How the change reaches the team is `framegate publish` (publish.py): a second commit on the
task's product branch while that is not merged, else a branch of its own. Without `[git]
state_branch` the branch is chosen here, at the start (work_branch below), and the chat
pushes it.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from framegate import followup, procs, publish
from framegate.adapters import get_adapter
from framegate.adapters.base import LanguageAdapter
from framegate.checks.frame_diff import (
    is_pipeline_file,
    path_matches,
    pipeline_entries,
    pipeline_entries_text,
)
from framegate.config import CONFIG_FILE, Config, ConfigError, load_config
from framegate.state import (
    GitError,
    clear_phase,
    commit_paths,
    feature_finished,
    git,
    log_event,
    read_phase,
    set_phase,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

if TYPE_CHECKING:
    from framegate.orchestrator import Orchestrator

Echo = Callable[[str], None]

PHASE = "PIPELINE"
YAML_SUFFIXES = (".yml", ".yaml")
#: a token of an added line that may be a path of this repository: `tests/test_x.py`,
#: `./src/app`; not `/app/x` (a path in an image), `$(dir)/x`, `host.com/x`
_PATH = re.compile(r"(?<![\w./\\$}~:@-])(?:\./)?([\w.-]+(?:/[\w.-]+)+)/?")


class PipelineError(GitError):
    """A pipeline step that is refused (reported like a git error: exit 1)."""


def phase_text(name: str) -> str:
    return f"{PHASE} {name}"


def active(cfg: Config) -> str:
    """The name of the pipeline task whose phase is set here ("" = none)."""
    phase, _, name = (read_phase(cfg) or "").partition(" ")
    return name.strip() if phase == PHASE else ""


def brief_rel(cfg: Config, name: str) -> str:
    return f"{cfg.paths.out}/{name}/{publish.PIPELINE_BRIEF_FILE}"


def list_rel(cfg: Config, name: str) -> str:
    return f"{cfg.paths.specs}/{name}/{followup.PIPELINE.file}"


# ---------- the write guard's rule ----------
def write_problem(cfg: Config, name: str, rel: str) -> str:
    """Why the pipeline task `name` may not write the repo-relative path `rel` ("" = it
    may): only a deploy / CI file that the list names, the list and the brief."""
    if rel in (list_rel(cfg, name), brief_rel(cfg, name)):
        return ""
    entries = pipeline_entries_text(cfg)
    try:
        pipeline_file = is_pipeline_file(get_adapter(cfg), rel)
    except ConfigError:
        pipeline_file = False
    if not pipeline_file:
        return (
            f"[{PHASE}] Blocked write to {rel}: a pipeline task changes deploy / CI files "
            f"only ({entries}). Source, tests, setup files, specs and framegate's own files "
            "stay locked: such a change is a feature, a bug fix or a refactor. If this is a "
            "deploy / CI file of the project, tell the user: its folder belongs in "
            f"[protected] extra of {CONFIG_FILE} (changed outside this task). Something that "
            "is no file of this repository (a CI variable, a secret, a database) is never "
            f"done here: it is a line `{followup.OUTSIDE_FORMAT}` of the list, for a person."
        )
    if not any(it.covers(rel) for it in followup.read_list(cfg, name)):
        return (
            f"[{PHASE}] Blocked write to {rel}: it is a deploy / CI file, but not on the list "
            f"({list_rel(cfg, name)}). Only when the user says it should change too: add the "
            f"line `{followup.LINE_FORMAT}` to the list first, then edit it."
        )
    return ""


def outside_lines(outside: list[followup.ListItem]) -> list[str]:
    """The list's items outside the repository, under a line of their own: never this
    task's work, never counted, never a problem ([] when there is none)."""
    if not outside:
        return []
    todo = [it for it in outside if not it.ticked]
    return [
        f"Outside the repository: by hand, not this task: {len(todo)} to do"
        + (f", {len(outside) - len(todo)} ticked" if len(todo) < len(outside) else ""),
        *(f"  - {'[x] ' if it.ticked else ''}{it.shown()}" for it in outside),
    ]


# ---------- start ----------
def _current(cfg: Config) -> str:
    return git(cfg, "rev-parse", "--abbrev-ref", "HEAD").strip()


def _exists(cfg: Config, rev: str) -> bool:
    try:
        git(cfg, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}")
    except GitError:
        return False
    return True


def work_branch(orch: Orchestrator, name: str) -> None:
    """Switch to the branch the pipeline task is done on.

    With `[git] state_branch`: the task's local work branch (the parent task's, or a new
    one from the base); `framegate publish` decides later where the change goes. Without
    it, the branch is what gets pushed, so it is chosen here: the task's feature branch
    while that is not merged into the base, else `<pipeline_prefix><name>` from the base."""
    cfg = orch.cfg
    if not cfg.git_branch:
        return
    if cfg.state_branch:
        orch.feature_branch(name)
        return
    own = publish.pipeline_branch(cfg, name)
    feat = cfg.work_branch(name)
    current = _current(cfg)
    if current == own:
        return
    tip = publish.base_tip(cfg, publish.names(cfg))
    if _exists(cfg, f"refs/heads/{feat}") and not _exists(cfg, f"refs/heads/{own}"):
        start = git(cfg, "merge-base", feat, tip).strip()
        tree = git(cfg, "rev-parse", f"{feat}^{{tree}}").strip()
        if not publish.contained(cfg.root, tree, start, tip):
            orch.feature_branch(name)  # not merged yet: one more commit on its branch
            return
    dirty = git(cfg, "status", "--porcelain", "--untracked-files=no").rstrip()
    if dirty:
        raise PipelineError(
            f"Uncommitted changes to tracked files on {current}; commit or stash them before "
            f"framegate switches to {own}:\n{dirty[:1500]}"
        )
    if _exists(cfg, f"refs/heads/{own}"):
        git(cfg, "checkout", "-q", own)
        orch.echo(f"Switched to the pipeline branch {own}.")
        return
    git(cfg, "checkout", "-q", "--no-track", "-b", own, tip)
    git(cfg, "config", f"branch.{own}.framegateFeature", name)
    orch.echo(f"Created the pipeline branch {own} from {cfg.git_base} ({tip[:8]}).")


def start(orch: Orchestrator, name: str) -> int:
    """`framegate pipeline <name>`: open (or come back to) the pipeline task."""
    cfg = orch.cfg
    lock = procs.read_lock(cfg.out_dir)
    if procs.lock_alive(lock):
        raise PipelineError(
            f"a framegate run is active (feature {(lock or {}).get('feature')}); a branch "
            "switch now would break it. Wait until it stops."
        )
    ref = publish.pipeline_start_ref(name)
    phase = read_phase(cfg)
    resumed = phase == phase_text(name)
    if phase and not resumed:
        raise PipelineError(
            f"Another phase is open in this checkout ({phase}): finish it first (a spec "
            "dialogue: approve the spec; a pipeline task: approve it), or clear it: framegate "
            "unlock"
        )
    if not resumed:
        before = _current(cfg)
        work_branch(orch, name)
        if (cfg.root / CONFIG_FILE).is_file():
            cfg = load_config(cfg.root)  # the branch's own config counts from here on
        spec = cfg.spec_dir(name)
        if (spec / "requirements.md").is_file() and not feature_finished(cfg, name):
            if _current(cfg) != before:
                git(cfg, "checkout", "-q", before)  # back to where the command started
            raise PipelineError(
                f"{name} is a task in progress (not DONE): finish it first; its pipeline "
                "follow-up list is written at DONE. A change of its own needs another name."
            )
        set_phase(cfg, phase_text(name))
        log_event(cfg, name, "pipeline_started", branch=_current(cfg))
    # the commit the check compares with; one left by a task given up elsewhere is replaced
    echo = orch.echo
    if not _exists(cfg, ref) or not publish.is_ancestor(cfg.root, ref, "HEAD"):
        git(cfg, "update-ref", ref, "HEAD")
        remember_dirty(cfg, name)
    items = followup.read_list(cfg, name)
    after = (cfg.spec_dir(name) / "requirements.md").is_file()
    echo(
        f"Pipeline task {name}"
        + (f" (after the task {name})" if after else " (on its own: no task of this name)")
        + f", branch {_current(cfg)}"
        + (", continued." if resumed else ".")
    )
    echo(
        f"May be written: deploy / CI files ({pipeline_entries_text(cfg)}), of those "
        f"only the ones on the list; the list ({list_rel(cfg, name)}); the brief "
        f"({brief_rel(cfg, name)}). Everything else is locked."
    )
    earlier = changed_before(cfg, name)
    if earlier:
        echo(
            "Changed before this task started (uncommitted; not part of it, the check leaves "
            f"them out while they stay as they are): {', '.join(earlier)}. Ask the user once "
            "what to do with them; `framegate publish` needs them committed or restored."
        )
    if not items:
        echo(
            f"No list yet: write it to {list_rel(cfg, name)}, one line per change "
            f"(`{followup.LINE_FORMAT}`; something that is no file of this repository: "
            f"`- [ ] {followup.OUTSIDE_FORMAT[2:]}`, never done here), and show it for an OK "
            "before any edit."
        )
        return 0
    outside = [it for it in items if it.outside]
    items = [it for it in items if not it.outside]
    todo = [it for it in items if not it.ticked]
    echo(f"List ({list_rel(cfg, name)}): {len(todo)} open of {len(items)} item(s)")
    adapter = get_adapter(cfg)
    for line in outside_lines(outside):
        echo(line)
    for i, it in enumerate(items, 1):
        note = ""
        if it.ticked:
            note = "  [done earlier]"
        elif it.reason:
            note = f"  [left open: {it.reason}]"
        elif not it.file:
            note = "  [names no file: write the line as `<file>: <what> (<why>)`]"
        elif not is_pipeline_file(adapter, it.file):
            note = "  [not a deploy / CI file here: locked; see [protected] extra]"
        echo(f"  {i}. {it.text}{note}")
    echo(
        f"Then: framegate pipeline-check {name}; after the user's OK on the brief: framegate "
        f"pipeline-approve {name}. An item not done: `{followup.LEFT_OPEN_FORMAT}` at the "
        "end of its line."
    )
    return 0


# ---------- check ----------
@dataclass
class Result:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: one line per list item
    items: list[str] = field(default_factory=list)
    #: every file changed since the start (the list itself left out)
    changed: list[str] = field(default_factory=list)
    #: the changed deploy / CI files that the list names: what the approval commits
    done: list[str] = field(default_factory=list)
    #: the list's items outside the repository (outside_lines): shown, never checked
    outside: list[str] = field(default_factory=list)


def _before_file(cfg: Config, name: str) -> pathlib.Path:
    rel = git(cfg, "rev-parse", "--git-path", f"framegate/pipeline-before/{name}.json").strip()
    return (cfg.root / rel).resolve()


def _blob(cfg: Config, rel: str) -> str:
    """The content of a working-tree file as git would store it ("" when it is gone)."""
    if not (cfg.root / rel).is_file():
        return ""
    return git(cfg, "hash-object", "--", rel).strip()


def remember_dirty(cfg: Config, name: str) -> None:
    """Keep what is uncommitted at the start of the task (tracked files): those changes are
    not the task's, and the check must not hold them against it (seen live: a tool's own
    config file and an edit of the user blocked the check)."""
    path = _before_file(cfg, name)
    dirty = git(cfg, "diff", "--no-renames", "--name-only", "HEAD").splitlines()
    if not dirty:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({rel: _blob(cfg, rel) for rel in dirty}), encoding="utf-8")


def forget_dirty(cfg: Config, name: str) -> None:
    _before_file(cfg, name).unlink(missing_ok=True)


def changed_before(cfg: Config, name: str) -> list[str]:
    """The files that were changed before the task started and still look the same."""
    try:
        was = json.loads(_before_file(cfg, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(rel for rel, blob in was.items() if _blob(cfg, rel) == blob)


def changed_files(cfg: Config, name: str) -> list[str]:
    """What changed since the pipeline task started: tracked files (committed or not), and
    untracked ones where a commit or a publish would look (the user's own notes elsewhere
    are not the task's business). The list and the brief are the task's own files."""
    ref = publish.pipeline_start_ref(name)
    tracked = set(git(cfg, "diff", "--no-renames", "--name-only", ref).splitlines())
    tracked -= set(changed_before(cfg, name))
    untracked = git(cfg, "ls-files", "--others", "--exclude-standard").splitlines()
    watched = [p.rstrip("/") for p in (*commit_paths(cfg), *publish.workflow_paths(cfg))]
    entries = pipeline_entries(cfg)
    for path in untracked:
        if path_matches(path, entries) or any(
            path == w or path.startswith(w + "/") for w in watched
        ):
            tracked.add(path)
    return sorted(tracked - {list_rel(cfg, name), brief_rel(cfg, name)})


class _TagLoader(yaml.SafeLoader):
    """SafeLoader that takes any tag of the file's own dialect (`!reference` in GitLab CI,
    `!Ref` in CloudFormation) as a plain value: the check asks whether the file parses, not
    what a tag means."""


def _any_tag(loader: yaml.Loader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return node.value


_TagLoader.add_multi_constructor("!", _any_tag)


def parse_problem(cfg: Config, rel: str) -> tuple[str, str]:
    """(problem, note) of a changed yml / json / toml file that does not parse. YAML is
    parsed with PyYAML (every document of the file; tags of the file's own dialect are
    accepted). A yml file that is a template (Helm, Jinja: `{{- ... }}`, `{% ... %}`) and
    does not parse as plain YAML is a note, not a problem."""
    path = cfg.root / rel
    if not path.is_file():
        return "", ""
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            json.loads(text)
        elif suffix == ".toml":
            tomllib.loads(text)
        elif suffix in YAML_SUFFIXES:
            list(yaml.load_all(text, Loader=_TagLoader))
    except Exception as e:  # each parser has its own error type
        lines = [x.strip() for x in str(e).splitlines() if x.strip()]
        first = lines[0] if lines else type(e).__name__
        if suffix in YAML_SUFFIXES and re.search(r"\{\{-|\{%|^\s*\{\{", text, re.M):
            return "", f"{rel} is a template, not plain YAML: not parsed ({first})"
        mark = getattr(e, "problem_mark", None)  # a YAML error knows its line
        where = f"{rel}:{mark.line + 1}" if mark is not None else rel
        return f"DOES NOT PARSE: {where}: {first}", ""
    return "", ""


def missing_paths(cfg: Config, name: str, rel: str) -> list[str]:
    """Paths the added lines of `rel` name that look like paths of this repository (their
    first part is a folder here) and do not exist."""
    try:
        diff = git(cfg, "diff", "--no-renames", "-U0", publish.pipeline_start_ref(name), "--", rel)
    except GitError:
        diff = ""
    added = [x[1:] for x in diff.splitlines() if x.startswith("+") and not x.startswith("+++")]
    path = cfg.root / rel
    if not diff.strip() and path.is_file():  # untracked: every line is new
        added = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[str] = []
    for line in added:
        for m in _PATH.finditer(line):
            token = m.group(1)
            first = token.split("/")[0]
            if first in (".", "..") or not (cfg.root / first).is_dir():
                continue
            if not (cfg.root / token).exists() and token not in out:
                out.append(token)
    return out


def check(cfg: Config, name: str) -> Result:
    """The mechanical checks of the pipeline task: every changed file is a deploy / CI file
    and on the list, nothing else changed since the start, yml / json / toml files parse,
    every open item is done (its file changed) or left open with a reason. Not blocking
    (notes): repo paths the added lines name that do not exist. Items outside the repository
    (`OUTSIDE [..]`) are no work of this task: listed, never open, never a problem."""
    if not _exists(cfg, publish.pipeline_start_ref(name)):
        raise PipelineError(
            f"No pipeline task '{name}' is open here (start it: /framegate:pipeline {name})."
        )
    res = Result()
    adapter: LanguageAdapter = get_adapter(cfg)
    listed = followup.read_list(cfg, name)
    items = [it for it in listed if not it.outside]
    res.outside = outside_lines([it for it in listed if it.outside])
    res.changed = changed_files(cfg, name)
    if not listed:
        res.problems.append(f"NO LIST: {list_rel(cfg, name)} has no `- [ ]` line")
    for rel in res.changed:
        if not is_pipeline_file(adapter, rel):
            res.problems.append(f"NOT A DEPLOY / CI FILE: {rel} changed (revert it)")
        elif not any(it.covers(rel) for it in items):
            res.problems.append(f"NOT ON THE LIST: {rel} changed (add its line, or revert it)")
        else:
            res.done.append(rel)
            problem, note = parse_problem(cfg, rel)
            res.problems += [problem] if problem else []
            res.notes += [note] if note else []
            gone = missing_paths(cfg, name, rel)
            if gone:
                res.notes.append(
                    f"{rel} names path(s) that do not exist here: {', '.join(gone)} (made by "
                    "the pipeline itself? else a typo)"
                )
    for i, it in enumerate(items, 1):
        if it.ticked:
            res.items.append(f"{i}. done earlier: {it.text}")
        elif it.reason:
            res.items.append(f"{i}. left open ({it.reason}): {it.text}")
        elif any(it.covers(rel) for rel in res.done):
            files = ", ".join(rel for rel in res.done if it.covers(rel))
            res.items.append(f"{i}. done ({files}): {it.text}")
        else:
            res.items.append(f"{i}. OPEN: {it.text}")
            res.problems.append(
                f"OPEN ITEM {i}: its file did not change (do it, or end its line with "
                f"`{followup.LEFT_OPEN_FORMAT}`): {it.text}"
            )
    return res


def report(cfg: Config, name: str, res: Result, echo: Echo) -> None:
    echo(
        f"Pipeline check {name}: "
        + (f"{len(res.problems)} problem(s)" if res.problems else "OK")
        + f"; {len(res.done)} deploy / CI file(s) changed"
    )
    for line in res.items:
        echo(f"  {line}")
    for line in res.outside:
        echo(line)
    for line in res.problems:
        echo(f"- {line}")
    for line in res.notes:
        echo(f"NOTE: {line}")
    if res.done:
        stat = git(cfg, "diff", "--stat", publish.pipeline_start_ref(name), "--", *res.done)
        for line in stat.splitlines()[:-1]:
            echo(f"  {line.strip()}")


def run_check(cfg: Config, name: str, echo: Echo = print) -> int:
    """`framegate pipeline-check <name>`; exit 1 on a problem."""
    res = check(cfg, name)
    report(cfg, name, res, echo)
    return 1 if res.problems else 0


# ---------- approve ----------
def approve(cfg: Config, name: str, echo: Echo = print) -> int:
    """`framegate pipeline-approve <name>` (the user said OK): the check again, the done
    items ticked, the deploy / CI files and the list committed, the phase cleared."""
    res = check(cfg, name)
    if res.problems:
        report(cfg, name, res, echo)
        echo("Not approved: fix the problem(s) above first.")
        return 1
    ticked = followup.tick(cfg, name, set(res.done))
    paths = [*res.done, list_rel(cfg, name)]
    git(cfg, "add", "-A", "--", *paths)
    committed = bool(git(cfg, "diff", "--cached", "--name-only", "--", *paths).strip())
    if committed:
        git(cfg, "commit", "-q", "-m", publish.pipeline_title(name), "--", *paths)
    git(cfg, "update-ref", "-d", publish.pipeline_start_ref(name))
    forget_dirty(cfg, name)
    if active(cfg) == name:
        clear_phase(cfg)
    left = followup.open_count(cfg, name)
    log_event(cfg, name, "pipeline_done", files=res.done, ticked=ticked, left_open=left)
    branch = _current(cfg)
    echo(
        f"Pipeline task {name} approved: {ticked} item(s) ticked, {left} left open; "
        + (f"{len(res.done)} file(s) committed on {branch}." if committed else "nothing to commit.")
    )
    for line in res.notes:
        echo(f"NOTE: {line}")
    for line in res.outside:
        echo(line)
    if not res.done:
        return 0
    if cfg.state_branch:
        echo(
            f"Next: framegate publish {name} --dry-run says where it goes (a second commit on "
            "the task's product branch, or a branch of its own)."
        )
    elif branch == publish.pipeline_branch(cfg, name):
        echo(
            f"Next: push {branch} and open its own pull request into {cfg.git_base} "
            f"(description: {brief_rel(cfg, name)})."
        )
    else:
        echo(
            f"Next: push {branch}; the change is one more commit on that branch (and on its "
            "pull request, if one is open)."
        )
    return 0
