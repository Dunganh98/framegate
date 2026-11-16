"""Product branch and state branch (`[git] state_branch`, off by default).

A target project is a shared repository: a pull request must carry the product (source, tests,
the project's own setup files) and nothing of framegate. With `[git] state_branch` set:

- **framegate's files** (workflow_paths: framegate.toml, the specs, the metrics, `.framegate/`,
  the knowledge file, the roles' extra folder, the code map) live on the **state branch**
  (`framegate/<base>`). Its tree holds only those files and its history shares no commit
  with the base, so it cannot be merged into the base by accident. It is pushed.
- The **work branch** (`<work_prefix><f>`, local only, never pushed) is the base plus a
  merge of the state branch: one working tree with both kinds of files, as the agents and
  the gates have always seen it. `framegate spec` creates it (join_state). It never has
  the product branch's name (adopt_work_branch), and a pre-push hook refuses a push of
  framegate's files to any branch but the state branch (install_guard).
- `framegate publish <f>` (after DONE, on the user's OK) writes the feature's framegate files
  to the state branch as one commit, and builds the **product branch**: one commit on the
  base commit the feature started from, holding the work branch's tree without framegate's
  files. Both are checked mechanically before anything is pushed (verify_product). The
  product commit is pushed as `<branch_prefix><f>`: the name the team sees.

Nothing here force-pushes (the one exception is asked for by a flag and is a
`--force-with-lease` on the product branch), touches the base branch or rewrites the work
branch. docs/reference.md "Product branch and state branch" has the life cycle.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial

from framegate import description, followup, hosts
from framegate.config import CONFIG_FILE, Config, ConfigError, parse_config
from framegate.state import GitError, commit_paths, feature_finished

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

Echo = Callable[[str], None]

#: where discovery looks for a state branch when a checkout has no framegate.toml (the
#: default name `framegate/{base}`; a project with another name starts from a branch that
#: has the config)
STATE_PREFIX = "framegate/"
#: the last product commit built for a feature: a private ref (no push publishes it)
PRODUCT_REF = "refs/framegate/product/{feature}"
#: a deploy / CI change with its own branch (the pipeline task of a merged task, or one
#: without a task): the last commit built for it
PIPELINE_REF = "refs/framegate/pipeline-product/{feature}"
#: set while a pipeline task is open (pipeline.py): the commit it started on
PIPELINE_START_REF = "refs/framegate/pipeline/{feature}"
#: the pipeline task's brief ({out}/<f>/): what the description of its own pull request is
#: written from (description.py)
PIPELINE_BRIEF_FILE = description.PIPELINE_BRIEF
LIST_MAX = 30

#: replaced in the tests: nothing there calls gh or az
which = shutil.which
run_tool = subprocess.run


class PublishError(GitError):
    """A publish / state step that is refused (reported like a git error: exit 1)."""


# ---------- git ----------
def _run(
    root: pathlib.Path,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=stdin,
        # C locale: two messages are read below ("couldn't find remote ref")
        env={**os.environ, "LC_ALL": "C", **(env or {})},
    )


def _git(
    root: pathlib.Path,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> str:
    r = _run(root, *args, env=env, stdin=stdin)
    if r.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def _rev(root: pathlib.Path, name: str) -> str | None:
    r = _run(root, "rev-parse", "--verify", "-q", f"{name}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _ancestor(root: pathlib.Path, older: str, newer: str) -> bool:
    return _run(root, "merge-base", "--is-ancestor", older, newer).returncode == 0


is_ancestor = _ancestor


def _tree(root: pathlib.Path, rev: str) -> str:
    return _git(root, "rev-parse", f"{rev}^{{tree}}").strip()


def _files(root: pathlib.Path, rev: str) -> list[str]:
    out = _git(root, "ls-tree", "-r", "-z", "--name-only", "--full-tree", rev)
    return [x for x in out.split("\0") if x]


def _current_branch(root: pathlib.Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


@contextmanager
def _index() -> Iterator[dict[str, str]]:
    """A throwaway index: trees are built without touching the checkout or its index."""
    folder = tempfile.mkdtemp(prefix="framegate-index-")
    try:
        yield {"GIT_INDEX_FILE": os.path.join(folder, "index")}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _commit_tree(root: pathlib.Path, tree: str, parents: Sequence[str], message: str) -> str:
    args = ["commit-tree", tree]
    for parent in parents:
        args += ["-p", parent]
    return _git(root, *args, "-m", message).strip()


# ---------- names ----------
def enabled(cfg: Config) -> bool:
    return bool(cfg.state_branch)


@dataclass(frozen=True)
class Names:
    #: the remote things are pushed to ("" = the repository has none: everything stays local)
    remote: str
    #: `[git] base` as a branch name on the remote (`origin/dev` -> `dev`)
    base: str
    #: the state branch (`framegate/dev`)
    state: str

    @property
    def state_ref(self) -> str:
        return f"refs/heads/{self.state}"

    @property
    def remote_state_ref(self) -> str:
        return f"refs/remotes/{self.remote}/{self.state}"


def names(cfg: Config) -> Names:
    remotes = _git(cfg.root, "remote").split()
    first, _, rest = cfg.git_base.partition("/")
    if rest and first in remotes:
        remote, base = first, rest
    else:
        remote, base = ("origin" if "origin" in remotes else ""), cfg.git_base
    return Names(remote, base, cfg.state_branch.replace("{base}", base))


def product_branch(cfg: Config, feature: str) -> str:
    """The branch the team sees on the remote (and the local work branch's name)."""
    return cfg.branch_prefix + feature


def product_ref(feature: str) -> str:
    return PRODUCT_REF.format(feature=feature)


def pipeline_branch(cfg: Config, feature: str) -> str:
    """The branch of a deploy / CI change that gets its own pull request."""
    return cfg.pipeline_prefix + feature


def pipeline_ref(feature: str) -> str:
    return PIPELINE_REF.format(feature=feature)


def pipeline_start_ref(feature: str) -> str:
    return PIPELINE_START_REF.format(feature=feature)


def pipeline_title(feature: str) -> str:
    """The message of the commit that holds the deploy / CI files."""
    return f"{feature}: deploy / CI changes"


def _require(cfg: Config) -> None:
    if not enabled(cfg):
        raise PublishError(
            'The state branch is off: set [git] state_branch = "framegate/{base}" in '
            f'{CONFIG_FILE} (docs/reference.md, "Product branch and state branch").'
        )
    top = pathlib.Path(_git(cfg.root, "rev-parse", "--show-toplevel").strip())
    if top.resolve() != cfg.root.resolve():
        raise PublishError(
            f"[git] state_branch needs {CONFIG_FILE} in the repository root ({top}), not in "
            f"{cfg.root}."
        )


# ---------- what is framegate's, what is the product ----------
def workflow_paths(cfg: Config) -> list[str]:
    """framegate's own files and folders (repo-relative, no trailing slash): the state
    branch holds exactly these, the product branch none of them. Everything else a feature
    changed is the product. The code map is product documentation with `[git]
    publish_code_map = true`."""
    wanted = [
        CONFIG_FILE,
        ".framegate",
        cfg.paths.specs,
        cfg.paths.metrics,
        cfg.knowledge,
        cfg.roles_extra_dir or "",
    ]
    if not cfg.publish_code_map:
        wanted.append(cfg.code_map)
    out: list[str] = []
    for raw in wanted:
        rel = raw.replace("\\", "/").strip("/")
        while rel.startswith("./"):
            rel = rel[2:]
        if rel and rel not in out:
            out.append(rel)
    # a path inside another one (.framegate/project.md in .framegate) is covered by it
    return [p for p in out if not any(p.startswith(q + "/") for q in out)]


def is_workflow(cfg: Config, path: str) -> bool:
    """True for a repo-relative posix path that is one of framegate's files."""
    return any(path == p or path.startswith(p + "/") for p in workflow_paths(cfg))


def _split(cfg: Config, files: Sequence[str]) -> tuple[list[str], list[str]]:
    """(framegate's, the product's) of `files`."""
    paths = workflow_paths(cfg)
    mine = [f for f in files if any(f == p or f.startswith(p + "/") for p in paths)]
    theirs = [f for f in files if f not in set(mine)]
    return mine, theirs


def workflow_tree(cfg: Config, rev: str) -> str:
    """The tree that holds only framegate's files of `rev`."""
    listing = _git(cfg.root, "ls-tree", "-r", "-z", "--full-tree", rev, "--", *workflow_paths(cfg))
    with _index() as env:
        if listing:
            _git(cfg.root, "update-index", "-z", "--index-info", env=env, stdin=listing)
        return _git(cfg.root, "write-tree", env=env).strip()


def checkout_workflow_tree(cfg: Config) -> str:
    """The tree of framegate's files as they are in this checkout, committed or not (what
    the project's .gitignore leaves out stays out)."""
    with _index() as env:
        for rel in workflow_paths(cfg):
            if (cfg.root / rel).exists():
                try:
                    _git(cfg.root, "add", "-A", "--", rel, env=env)
                except GitError:
                    continue  # ignored by the project
        return _git(cfg.root, "write-tree", env=env).strip()


def product_tree(cfg: Config, rev: str) -> str:
    """The tree of `rev` without framegate's files."""
    with _index() as env:
        _git(cfg.root, "read-tree", rev, env=env)
        _git(
            cfg.root,
            *("rm", "-r", "-q", "-f", "--cached", "--ignore-unmatch", "--"),
            *workflow_paths(cfg),
            env=env,
        )
        return _git(cfg.root, "write-tree", env=env).strip()


#: the paths below are file names, never patterns
_LITERAL = {"GIT_LITERAL_PATHSPECS": "1"}


def _names(root: pathlib.Path, *args: str) -> list[str]:
    """The file names `git diff --name-only <args>` lists."""
    out = _git(root, "diff", "--no-renames", "--name-only", "-z", *args, env=_LITERAL)
    return [x for x in out.split("\0") if x]


def pipeline_changed(cfg: Config, start: str, rev: str = "HEAD") -> list[str]:
    """The deploy / CI files (frame_diff.is_pipeline_file) that differ between `start` and
    `rev`: what a pipeline task changed on this branch. A feature never changes one."""
    from framegate.adapters import get_adapter
    from framegate.checks.frame_diff import is_pipeline_file

    try:
        adapter = get_adapter(cfg)
    except ConfigError:
        return []
    names = _names(cfg.root, start, rev)
    return sorted(f for f in names if not is_workflow(cfg, f) and is_pipeline_file(adapter, f))


def tree_with(cfg: Config, tree: str, source: str, paths: Sequence[str]) -> str:
    """`tree` with `paths` as they are in `source` (missing there: removed)."""
    if not paths:
        return tree
    with _index() as index:
        env = {**index, **_LITERAL}
        _git(cfg.root, "read-tree", tree, env=env)
        remove = ("rm", "-r", "-q", "-f", "--cached", "--ignore-unmatch", "--")
        _git(cfg.root, *remove, *paths, env=env)
        listing = _git(cfg.root, "ls-tree", "-r", "-z", "--full-tree", source, *paths, env=env)
        if listing:
            _git(cfg.root, "update-index", "-z", "--index-info", env=env, stdin=listing)
        return _git(cfg.root, "write-tree", env=env).strip()


def base_tip(cfg: Config, n: Names) -> str:
    """The newest commit of the base this clone can know: the remote's branch, fetched
    (unless the local base has commits it lacks), else `[git] base` as it is here."""
    root = cfg.root
    local = _rev(root, cfg.git_base)
    if n.remote:
        ref = f"refs/remotes/{n.remote}/{n.base}"
        _run(root, "fetch", "-q", n.remote, f"+refs/heads/{n.base}:{ref}")
        there = _rev(root, ref)
        if there and (not local or _ancestor(root, local, there)):
            return there
    if not local:
        raise PublishError(f"Base branch '{cfg.git_base}' not found.")
    return local


def contained(root: pathlib.Path, tree: str, start: str, tip: str) -> bool:
    """True when the change from `start` to `tree` is in `tip` already (merged, also as a
    squash): every file it changed has the same content there, or merging it into `tip`
    changes nothing. Read from git alone; no host is asked."""
    changed = _names(root, start, tree)
    if not changed or not _names(root, tip, tree, "--", *changed):
        return True
    probe = _commit_tree(root, tree, [start], "framegate: merged?")  # never referenced
    r = _run(root, "merge-tree", "--write-tree", tip, probe)  # git >= 2.38; older: not merged
    return r.returncode == 0 and r.stdout.split()[:1] == [_tree(root, tip)]


def verify_pipeline(
    cfg: Config, commit: str, start: str, paths: Sequence[str], work: str = "HEAD"
) -> None:
    """Refuse a pipeline commit that holds one of framegate's files, that changes anything
    but the deploy / CI files `paths`, or whose files differ from the work branch's."""
    inside, _ = _split(cfg, _files(cfg.root, commit))
    if inside:
        raise PublishError(
            "refused: the pipeline commit would hold framegate's files: " + _listed(inside)
        )
    other = [x for x in _names(cfg.root, start, commit) if x not in set(paths)]
    if other:
        raise PublishError(
            "refused: the pipeline commit would change files that are not deploy / CI files: "
            + _listed(other)
        )
    late = _names(cfg.root, commit, work, "--", *paths)
    if late:
        raise PublishError(
            "refused: the pipeline commit differs from the work branch in: " + _listed(late)
        )


def verify_product(cfg: Config, product: str, work: str = "HEAD") -> None:
    """Refuse a product commit that holds one of framegate's files, or that differs from the
    work branch in anything but framegate's files. Checked on the commit itself, apart from
    how it was built."""
    inside, _ = _split(cfg, _files(cfg.root, product))
    if inside:
        raise PublishError(
            "refused: the product commit would hold framegate's files: " + _listed(inside)
        )
    diff = _git(cfg.root, "diff", "--no-renames", "--name-only", "-z", product, work)
    _, other = _split(cfg, [x for x in diff.split("\0") if x])
    if other:
        raise PublishError(
            "refused: the product commit differs from the work branch in files that are not "
            "framegate's: " + _listed(other)
        )


def _listed(items: Sequence[str], limit: int = LIST_MAX) -> str:
    more = f", and {len(items) - limit} more" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


# ---------- this clone's own git files ----------
def ensure_excludes(cfg: Config, extra: Sequence[str] = ()) -> list[str]:
    """The run files (`out/`, `.phase`, `.reqs`, the adapter's work folders) are ignored
    through this clone's `.git/info/exclude`, not through the project's `.gitignore` (a
    product file). Returns the lines added."""
    wanted = [cfg.paths.out + "/", cfg.paths.phase_file, cfg.paths.reqs_file, *extra]
    return _add_lines(cfg, "exclude", wanted)


def drop_ignore_lines(cfg: Config, n: Names, wanted: Sequence[str], echo: Echo) -> bool:
    """The migration of an old-style feature branch: `.gitignore` lines for the run files
    that this branch added (they are not on the base) are taken out again and committed:
    `.gitignore` is a product file, and the clone ignores them through `.git/info/exclude`
    now. Nothing is done when the file has uncommitted changes or is not tracked."""
    root = cfg.root
    path = root / ".gitignore"
    if not path.is_file() or _run(root, "ls-files", "--error-unmatch", ".gitignore").returncode:
        return False
    if _run(root, "status", "--porcelain", "--", ".gitignore").stdout.strip():
        return False
    base = _run(root, "show", f"{cfg.git_base}:.gitignore")
    on_base = {x.strip() for x in base.stdout.splitlines()} if base.returncode == 0 else set()
    drop = {w for w in wanted if w not in on_base}
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    kept = [x for x in lines if x.decode("utf-8", "replace").strip() not in drop]
    if len(kept) == len(lines):
        return False
    if base.returncode != 0 and not b"".join(kept).strip():
        path.unlink()  # the base has none, and nothing else is in it
    else:
        path.write_bytes(b"".join(kept))
    _git(root, "add", "--", ".gitignore")
    _git(root, "commit", "-q", "-m", ".gitignore: framegate's run files are ignored locally")
    echo(
        ".gitignore: took out the lines framegate once added "
        f"({', '.join(sorted(drop))}) and committed that: it is a product file."
    )
    return True


def ensure_union_merge(cfg: Config) -> None:
    """The metrics file gets one row per feature at its end: two features finished side
    by side both add one, which git reports as a conflict. The rows are independent, so
    the merge keeps both (`merge=union`, in this clone's `.git/info/attributes`)."""
    _add_lines(cfg, "attributes", [f"{cfg.paths.metrics}/workflow_metrics.csv merge=union"])


def _add_lines(cfg: Config, name: str, wanted: Sequence[str]) -> list[str]:
    """Add the missing ones of `wanted` to this clone's `.git/info/<name>`."""
    rel = _git(cfg.root, "rev-parse", "--git-path", f"info/{name}").strip()
    path = cfg.root / rel
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    have = {x.strip() for x in text.splitlines()}
    missing = [w for w in dict.fromkeys(wanted) if w not in have]
    if missing:
        path.parent.mkdir(parents=True, exist_ok=True)
        lead = "" if not text or text.endswith("\n") else "\n"
        path.write_text(text + lead + "\n".join(missing) + "\n", encoding="utf-8")
    return missing


# ---------- the state branch ----------
def missing_text(n: Names) -> str:
    where = f"not here, not on {n.remote}" if n.remote else "this repository has no remote"
    return (
        f"The state branch {n.state} does not exist yet ({where}). Create it once from this "
        "checkout's framegate files: framegate state init"
    )


def fetch_state(cfg: Config, n: Names, echo: Echo) -> None:
    if not n.remote:
        return
    r = _run(cfg.root, "fetch", "-q", n.remote, f"+{n.state_ref}:{n.remote_state_ref}")
    if r.returncode == 0:
        return
    if "couldn't find remote ref" in r.stderr.lower():
        if _rev(cfg.root, n.remote_state_ref):  # deleted there since the last fetch
            _git(cfg.root, "update-ref", "-d", n.remote_state_ref)
        return
    last = (r.stderr.strip().splitlines() or ["no output"])[-1]
    echo(f"NOTE: {n.remote} could not be reached ({last}); using the local {n.state}.")


def state_tip(
    cfg: Config, n: Names, echo: Echo, *, fetch: bool = True, write: bool = True
) -> str | None:
    """The newest commit of the state branch, local and remote brought in line: a missing
    local branch is created from the remote's, a local one that is behind is moved forward.
    None = it exists nowhere. Refuses when both sides have commits the other lacks."""
    if fetch:
        fetch_state(cfg, n, echo)
    local = _rev(cfg.root, n.state_ref)
    there = _rev(cfg.root, n.remote_state_ref) if n.remote else None
    if not local or not there or local == there:
        if there and not local and write:
            _git(cfg.root, "update-ref", n.state_ref, there)
            echo(f"Fetched the state branch {n.state} from {n.remote}.")
        return local or there
    if _ancestor(cfg.root, local, there):
        if write:
            _git(cfg.root, "update-ref", n.state_ref, there, local)
        return there
    if _ancestor(cfg.root, there, local):
        return local  # ahead: the next push sends it
    raise PublishError(
        f"The state branch {n.state} has diverged: this clone and {n.remote} both have commits "
        "the other lacks (a push that failed earlier?). Nothing was changed. Bring both into "
        f"your feature branch, then take the remote's as the base:\n"
        f"  git merge --allow-unrelated-histories {n.state}\n"
        f"  git merge --allow-unrelated-histories {n.remote}/{n.state}\n"
        f"  git branch -f {n.state} {n.remote}/{n.state}\n"
        "and run this command again."
    )


def _refuse_foreign(cfg: Config, n: Names, tip: str) -> None:
    _, foreign = _split(cfg, _files(cfg.root, tip))
    if foreign:
        raise PublishError(
            f"The state branch {n.state} holds files that are not framegate's (someone "
            f"committed there by hand?): {_listed(foreign)}. Remove them from that branch; "
            "nothing was merged."
        )


def _clear_copies(cfg: Config, n: Names, tip: str) -> None:
    """Untracked copies of framegate's files in this checkout (left by the setup, before
    the state branch held them) would stop the merge. One that equals the state branch's
    file is removed (the merge brings it back, tracked); a different one refuses."""
    there = [p for p in workflow_paths(cfg) if (cfg.root / p).exists()]
    if not there:
        return
    out = _git(cfg.root, "ls-files", "--others", "--exclude-standard", "-z", "--", *there)
    untracked = {x for x in out.split("\0") if x}
    if not untracked:
        return
    in_state: dict[str, str] = {}
    for entry in _git(cfg.root, "ls-tree", "-r", "-z", "--full-tree", tip).split("\0"):
        if entry:
            meta, _, path = entry.partition("\t")
            in_state[path] = meta.split()[2]
    both = sorted(untracked & set(in_state))
    if not both:
        return
    hashes = _git(cfg.root, "hash-object", "--stdin-paths", stdin="\n".join(both) + "\n").split()
    differ = [p for p, h in zip(both, hashes, strict=True) if h != in_state[p]]
    if differ:
        raise PublishError(
            f"These untracked files differ from the state branch {n.state}: {_listed(differ)}. "
            "Delete them (the state branch's version comes in with the merge), or move them "
            "away and make your change on the feature branch afterwards."
        )
    for path in both:
        (cfg.root / path).unlink()


def merge_state(cfg: Config, n: Names, tip: str, echo: Echo) -> None:
    """Merge the state branch into the current (work) branch. Its tree holds only
    framegate's files, so the product files are never touched; a conflict (two features
    changed the same framegate file) is undone and reported."""
    _refuse_foreign(cfg, n, tip)
    _clear_copies(cfg, n, tip)
    ensure_union_merge(cfg)
    options = ("-q", "--no-ff", "--no-edit", "--allow-unrelated-histories")
    r = _run(cfg.root, "merge", *options, "-m", f"framegate: workflow files from {n.state}", tip)
    if r.returncode == 0:
        return
    _run(cfg.root, "merge", "--abort")
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-8:])
    raise PublishError(
        f"The state branch {n.state} does not merge cleanly into {_current_branch(cfg.root)} "
        "(the merge was undone). Merge it by hand, resolve, commit, then run this again:\n"
        f"  git merge --allow-unrelated-histories {n.state}\n{tail}"
    )


def join_state(cfg: Config, echo: Echo, extra_ignores: Sequence[str] = ()) -> None:
    """`framegate spec` created the work branch from the base: bring framegate's files in."""
    n = names(cfg)
    tip = state_tip(cfg, n, echo, fetch=False)
    if tip is None:
        raise PublishError(missing_text(n))
    merge_state(cfg, n, tip, echo)
    ensure_excludes(cfg, extra_ignores)
    install_guard(cfg, n, echo)
    echo(f"Brought in framegate's files from the state branch {n.state}.")


def require_state(cfg: Config, echo: Echo) -> None:
    """Before `framegate spec` leaves the current branch: the state branch must exist
    (fetched here), else the new branch would have no framegate.toml."""
    _require(cfg)
    n = names(cfg)
    if state_tip(cfg, n, echo) is None:
        raise PublishError(missing_text(n))


def _incorporated(cfg: Config, tip: str) -> bool:
    """The work branch holds everything the state commit `tip` holds: it has merged it, or
    its framegate files are exactly the commit's."""
    return _ancestor(cfg.root, tip, "HEAD") or workflow_tree(cfg, "HEAD") == _tree(cfg.root, tip)


def _record(cfg: Config, n: Names, sha: str) -> None:
    """A state commit built from this branch's files becomes one of its ancestors (a merge
    that changes no file), so a later merge of the state branch has the right base. Only
    on a clean work branch that holds exactly these files; never on the base."""
    root = cfg.root
    if _ancestor(root, sha, "HEAD") or _current_branch(root) in ("HEAD", n.base, n.state):
        return
    if _git(root, "status", "--porcelain", "--untracked-files=no").strip():
        return
    if workflow_tree(cfg, "HEAD") != _tree(root, sha):
        return
    options = ("-q", "-s", "ours", "--no-ff", "--no-edit", "--allow-unrelated-histories")
    _git(root, "merge", *options, "-m", f"framegate: state {n.state} recorded", sha)


def push_state(cfg: Config, n: Names, tip: str, echo: Echo) -> bool:
    """Push the state branch (never forced). False = no remote, or already there."""
    if not n.remote or _rev(cfg.root, n.remote_state_ref) == tip:
        return False
    r = _run(cfg.root, "push", "-q", n.remote, f"{n.state_ref}:{n.state_ref}")
    if r.returncode != 0:
        raise PublishError(
            f"Pushing the state branch {n.state} to {n.remote} failed (nothing was forced; "
            f"run the command again, it fetches first): {r.stderr.strip()}"
        )
    echo(f"Pushed the state branch {n.state} to {n.remote}.")
    return True


def state_init(
    cfg: Config, echo: Echo = print, *, push: bool = False, extra_ignores: Sequence[str] = ()
) -> int:
    """`framegate state init`: create the state branch from this checkout's framegate
    files (committed or not). First use in a repository, and the way an old-style feature
    branch (framegate's files committed on it) is migrated."""
    _require(cfg)
    n = names(cfg)
    if state_tip(cfg, n, echo) is not None:
        raise PublishError(
            f"The state branch {n.state} exists already. A new feature brings its files in "
            "(framegate spec <f>); changes made on a feature branch reach it with `framegate "
            "publish <f>` or `framegate state save`."
        )
    branch = _current_branch(cfg.root)
    if cfg.git_branch and branch.startswith(cfg.branch_prefix) and branch != n.base:
        # an old-style feature branch: it gets the work branch's name before anything else
        adopt_work_branch(cfg, branch[len(cfg.branch_prefix) :], echo)
    tree = checkout_workflow_tree(cfg)
    files = _files(cfg.root, tree)
    if CONFIG_FILE not in files:
        raise PublishError(f"{CONFIG_FILE} is not in this checkout (or ignored by .gitignore).")
    sha = _commit_tree(cfg.root, tree, [], f"framegate: workflow files ({n.state} created)")
    _git(cfg.root, "update-ref", n.state_ref, sha, "")
    added = ensure_excludes(cfg, extra_ignores)
    wanted = [cfg.paths.out + "/", cfg.paths.phase_file, cfg.paths.reqs_file, *extra_ignores]
    drop_ignore_lines(cfg, n, wanted, echo)
    install_guard(cfg, n, echo)
    _record(cfg, n, sha)
    echo(f"Created the state branch {n.state} with {len(files)} file(s): {_listed(files, 12)}")
    if added:
        echo(f"Run files are ignored through .git/info/exclude now: {', '.join(added)}")
    if _run(cfg.root, "status", "--porcelain", "--", ".gitignore").stdout.strip():
        echo(
            "NOTE: .gitignore has uncommitted changes. It is a product file: lines `framegate "
            "init` added there are not needed any more (git checkout -- .gitignore)."
        )
    if push:
        push_state(cfg, n, sha, echo)
    elif n.remote:
        echo(
            f"Not pushed yet: `framegate state save --push`, or the first `framegate publish` "
            f"sends it to {n.remote}."
        )
    return 0


def save_state(
    cfg: Config, n: Names, echo: Echo, message: str, *, dry_run: bool = False
) -> tuple[str, list[str]]:
    """The state branch receives the current branch's framegate files as one commit on top
    of its newest commit (merged into this branch first when it is not in it yet).
    Returns (the state branch's tip afterwards, the files that changed)."""
    tip = state_tip(cfg, n, echo, write=not dry_run)
    if tip is None:
        raise PublishError(missing_text(n))
    _refuse_foreign(cfg, n, tip)
    if not _incorporated(cfg, tip):
        if dry_run:
            echo(
                f"{n.state} has commits this branch lacks: they would be merged into it "
                "first (the file lists below are without them)."
            )
        else:
            merge_state(cfg, n, tip, echo)
            echo(f"Merged the newer {n.state} into {_current_branch(cfg.root)}.")
    tree = workflow_tree(cfg, "HEAD")
    if tree == _tree(cfg.root, tip):
        return tip, []
    changed = _git(cfg.root, "diff", "--no-renames", "--name-status", tip, tree).splitlines()
    if dry_run:
        return tip, changed
    new = _commit_tree(cfg.root, tree, [tip], message)
    _git(cfg.root, "update-ref", n.state_ref, new, _rev(cfg.root, n.state_ref) or "")
    _record(cfg, n, new)
    return new, changed


def _refuse_dirty(cfg: Config) -> None:
    """Uncommitted changes to tracked files, or untracked files where the workflow
    commits (they would silently stay out)."""
    out = _git(cfg.root, "status", "--porcelain", "--untracked-files=all")
    watched = [p.rstrip("/") for p in (*commit_paths(cfg), *workflow_paths(cfg))]
    dirty = []
    for line in out.splitlines():
        code, path = line[:2], line[3:].strip().strip('"')
        if code != "??" or any(path == w or path.startswith(w + "/") for w in watched):
            dirty.append(line)
    if dirty:
        raise PublishError(
            "Uncommitted changes; commit or remove them first (what is not committed cannot "
            "be published):\n" + "\n".join(dirty[:LIST_MAX])
        )


def state_save(cfg: Config, echo: Echo = print, *, push: bool = False) -> int:
    """`framegate state save`: this branch's framegate files -> the state branch (the state
    half of `framegate publish`, for a change outside a feature: a config edit, kept retro
    proposals)."""
    _require(cfg)
    n = names(cfg)
    branch = _current_branch(cfg.root)
    if branch in ("HEAD", n.base, n.state):
        raise PublishError(
            f"`framegate state save` runs on a feature branch (this is {branch}): framegate's "
            "files are changed and committed there."
        )
    _refuse_dirty(cfg)
    install_guard(cfg, n, echo)
    tip, changed = save_state(cfg, n, echo, f"framegate: workflow files from {branch}")
    echo(
        f"State branch {n.state}: "
        + (f"{len(changed)} file(s) updated." if changed else "already in line with this branch.")
    )
    if push:
        push_state(cfg, n, tip, echo)
    return 0


def state_status(cfg: Config, echo: Echo = print) -> int:
    """`framegate state [status]`: where the state branch is and what counts as framegate's."""
    if not enabled(cfg):
        echo("State branch: off ([git] state_branch is empty).")
        return 0
    n = names(cfg)
    tip = state_tip(cfg, n, echo, write=False)
    local, there = _rev(cfg.root, n.state_ref), _rev(cfg.root, n.remote_state_ref)
    if tip is None:
        echo(f"State branch: {n.state} (missing: framegate state init)")
    else:
        where = [f"local {local[:8]}" if local else "not fetched here yet"]
        if n.remote:
            where.append(f"{n.remote} {there[:8]}" if there else f"not on {n.remote} yet")
        echo(f"State branch: {n.state} ({', '.join(where)})")
    echo(f"framegate's files: {', '.join(workflow_paths(cfg))}")
    ok, detail = guard_state(cfg, n)
    echo("Push guard (pre-push hook): " + ("in place" if ok else detail))
    echo(f"Work branches: {cfg.work_prefix}<feature>, local only.")
    echo(f"Product: everything else; pull requests go to {n.base}.")
    return 0


# ---------- the work branch's name, the push guard ----------
def adopt_work_branch(cfg: Config, feature: str, echo: Echo) -> bool:
    """A local branch under the product branch's name that holds framegate's files (built
    before `[git] state_branch`, or before the work branch had its own name) is renamed to
    the work branch's name: what is pushed as `<branch_prefix><f>` is only ever the product.
    Not renamed, and refused, when the remote has a branch of that name that is neither
    this commit nor the product this clone published: then it is someone's branch."""
    root = cfg.root
    old, new = product_branch(cfg, feature), cfg.work_branch(feature)
    mine = _rev(root, f"refs/heads/{old}")
    if old == new or not mine or _rev(root, f"refs/heads/{new}"):
        return False
    if _run(root, "cat-file", "-e", f"{mine}:{CONFIG_FILE}").returncode != 0:
        return False  # no framegate file on it: not a work branch
    how = f"rename it yourself if it is this feature's work: git branch -m {old} {new}"
    n = names(cfg)
    try:
        there = _remote_tip(cfg, n, old)
    except PublishError as e:
        raise PublishError(f"{e} The local branch {old} holds framegate's files; {how}") from e
    if there and there not in (mine, _rev(root, product_ref(feature))):
        raise PublishError(
            f"The local branch {old} holds framegate's files, and {n.remote} has a branch "
            f"{old} with other content ({there[:8]}): not renamed. Look at both; {how}"
        )
    _git(root, "branch", "-m", old, new)
    echo(
        f"Renamed the local branch {old} to {new}: it holds framegate's files and stays "
        f"local; `framegate publish` pushes the product as {old}."
    )
    return True


GUARD_MARK = "# framegate pre-push guard"
GUARD_VERSION = 1
#: an existing pre-push hook of the user's is moved here and run first by the guard
GUARD_USER_HOOK = "pre-push.user"


def _sh(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def guard_text(cfg: Config, n: Names) -> str:
    """The pre-push hook: plain sh and git, no framegate on PATH needed (git runs it with
    its own sh, on Windows too). It refuses a push whose commit holds one of framegate's
    files unless the remote ref is the state branch. The paths and the state branch are
    written into it: `framegate doctor` says when the config has moved on."""
    paths = " ".join(_sh(p) for p in workflow_paths(cfg))
    return f"""#!/bin/sh
{GUARD_MARK} v{GUARD_VERSION}. Written by framegate ([git] state_branch); do not edit:
# `framegate state guard` rewrites it. It refuses a push that would send framegate's own
# files anywhere but to the state branch. Your own hook, if you had one, is
# {GUARD_USER_HOOK} next to this file and runs first.
state_ref={_sh(n.state_ref)}
input=$(cat)
user_hook="$(dirname "$0")/{GUARD_USER_HOOK}"
if [ -f "$user_hook" ]; then
    printf '%s\\n' "$input" | "$user_hook" "$@" || exit $?
fi
printf '%s\\n' "$input" | while read -r local_ref local_sha remote_ref remote_sha; do
    [ -n "$local_sha" ] || continue
    case "$local_sha" in *[!0]*) ;; *) continue ;; esac
    [ "$remote_ref" = "$state_ref" ] && continue
    found=$(git ls-tree -r --name-only --full-tree "$local_sha" -- {paths} | head -n 3)
    if [ -n "$found" ]; then
        echo "framegate: push refused: $local_ref holds framegate's files (e.g. $found)" >&2
        echo "and would send them to $remote_ref. Only the state branch carries them;" >&2
        echo "the team's branch is pushed by: framegate publish <feature>" >&2
        exit 1
    fi
done || exit 1
exit 0
"""


def _guard_path(cfg: Config) -> pathlib.Path:
    rel = _git(cfg.root, "rev-parse", "--git-path", "hooks").strip()
    return cfg.root / rel / "pre-push"


def guard_state(cfg: Config, n: Names) -> tuple[bool, str]:
    """(in place and current, where it is / what to do otherwise)."""
    custom = _run(cfg.root, "config", "--get", "core.hooksPath").stdout.strip()
    if custom:
        return False, (
            f"core.hooksPath is set ({custom}): framegate does not write into hooks it does "
            "not own, so no push guard is installed. Never push a work branch yourself; "
            "`framegate publish` refuses and repairs a branch that got framegate's files"
        )
    path = _guard_path(cfg)
    if not path.is_file():
        return False, "not installed: framegate state guard"
    text = path.read_bytes().decode("utf-8", "replace")
    if GUARD_MARK not in text:
        return False, "another pre-push hook is in its place: framegate state guard"
    if text != guard_text(cfg, n):
        return False, "out of date (the config or framegate changed): framegate state guard"
    return True, str(path)


def install_guard(cfg: Config, n: Names, echo: Echo) -> bool:
    """Write the pre-push guard into this clone's hooks (idempotent). A pre-push hook of
    the user's is kept: moved to `pre-push.user`, which the guard runs first. Not installed
    (said, never an error) with `core.hooksPath`, or when both file names are taken."""
    ok, detail = guard_state(cfg, n)
    if ok:
        return True
    if detail.startswith("core.hooksPath"):
        echo(f"NOTE: push guard: {detail}.")
        return False
    path = _guard_path(cfg)
    if path.is_file() and GUARD_MARK not in path.read_bytes().decode("utf-8", "replace"):
        keep = path.with_name(GUARD_USER_HOOK)
        if keep.exists():
            echo(
                f"NOTE: push guard not installed: {path} is a hook of yours and {keep.name} "
                "exists too. Merge the two by hand, then: framegate state guard"
            )
            return False
        path.rename(keep)
        echo(f"Your pre-push hook was kept as {keep.name}; the push guard runs it first.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(guard_text(cfg, n).encode("utf-8"))
    path.chmod(0o755)
    echo(f"Push guard installed (pre-push hook): framegate's files only go to {n.state}.")
    return True


def state_guard(cfg: Config, echo: Echo = print) -> int:
    """`framegate state guard`: (re)install the pre-push guard; 1 = it could not be."""
    _require(cfg)
    n = names(cfg)
    if not install_guard(cfg, n, echo):
        return 1
    echo(f"Push guard in place: {guard_state(cfg, n)[1]}")
    return 0


# ---------- a checkout without framegate.toml ----------
def _toplevel(start: pathlib.Path) -> pathlib.Path | None:
    r = _run(start, "rev-parse", "--show-toplevel")
    return pathlib.Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def _candidates(top: pathlib.Path) -> dict[str, str]:
    """State branches by the default name (`framegate/...`) -> a ref that has one (the
    local branch, else a remote's)."""
    out = _run(top, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").stdout
    found: dict[str, str] = {}
    for ref in sorted(out.split()):  # refs/heads before refs/remotes
        m = re.match(rf"refs/(?:heads|remotes/[^/]+)/({re.escape(STATE_PREFIX)}.+)$", ref)
        if m:
            found.setdefault(m.group(1), ref)
    return found


def find_state(start: pathlib.Path | None = None, *, fetch: bool = False) -> tuple[str, str]:
    """(state branch, ref) for a checkout that has no framegate.toml: the one for the
    current branch (`framegate/<branch>`), else the only one; ("", "") = none."""
    top = _toplevel(start or pathlib.Path(os.getcwd()))
    if top is None:
        return "", ""
    found = _candidates(top)
    if not found and fetch and "origin" in _run(top, "remote").stdout.split():
        spec = f"+refs/heads/{STATE_PREFIX}*:refs/remotes/origin/{STATE_PREFIX}*"
        _run(top, "fetch", "-q", "origin", spec)
        found = _candidates(top)
    r = _run(top, "rev-parse", "--abbrev-ref", "HEAD")
    mine = STATE_PREFIX + r.stdout.strip()
    if mine in found:
        return mine, found[mine]
    if len(found) == 1:
        return next(iter(found.items()))
    if found:
        raise PublishError(
            f"No {CONFIG_FILE} here, and several state branches: {', '.join(sorted(found))}. "
            "Switch to the base branch of the one you mean, then start the feature."
        )
    return "", ""


def config_from_state(start: pathlib.Path | None = None) -> Config | None:
    """The config for a checkout without framegate.toml (the base branch, a fresh clone),
    read from the state branch: `framegate spec <f>` starts a feature from there. None =
    no state branch, or its config does not name it."""
    top = _toplevel(start or pathlib.Path(os.getcwd()))
    name, ref = find_state(start, fetch=True)
    if top is None or not ref:
        return None
    r = _run(top, "show", f"{ref}:{CONFIG_FILE}")
    if r.returncode != 0:
        return None
    try:
        cfg = parse_config(top, tomllib.loads(r.stdout))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{CONFIG_FILE} on {name}: {e}") from e
    cfg.loaded_from_file = True
    if not enabled(cfg) or names(cfg).state != name:
        return None
    return cfg


def missing_config_hint(start: pathlib.Path | None = None) -> str:
    """One line for "no framegate.toml here" when a state branch holds it."""
    try:
        name, _ = find_state(start)
    except PublishError:
        name = STATE_PREFIX + "..."
    if not name:
        return ""
    return (
        f"framegate's files are on the state branch {name}, not in this checkout: `framegate "
        "spec <feature>` starts a feature from here and brings them in; a feature in progress "
        "is on its own branch (git switch <branch>)."
    )


# ---------- publish ----------
def _remote_tip(cfg: Config, n: Names, branch: str) -> str | None:
    if not n.remote:
        return None
    r = _run(cfg.root, "ls-remote", n.remote, f"refs/heads/{branch}")
    if r.returncode != 0:
        raise PublishError(f"{n.remote} could not be reached: {r.stderr.strip()}")
    return r.stdout.split()[0] if r.stdout.split() else None


def _product_commit(
    cfg: Config,
    n: Names,
    feature: str,
    tree: str,
    start: str,
    title: str,
    there: str | None,
    lease: bool,
    *,
    first: str = "",
    branch: str = "",
    ref: str = "",
) -> str:
    """The commit to push as the product branch: the one built before when nothing changed,
    a commit on top of it when it is what the remote has (a fast-forward), else one commit
    on `start`, the base commit the feature started from. `there` = the remote branch's
    tip; one that is neither refuses unless `lease` (--force-with-lease) is asked for.

    `first` (a tree other than `tree`): the product without the deploy / CI files a
    pipeline task changed. The branch is then two commits, `first` and on top of it `tree`;
    on a published branch that holds `first`, the second one is the fast-forward.
    `branch` / `ref`: another branch than the product's (the pipeline's own)."""
    root = cfg.root
    branch = branch or product_branch(cfg, feature)
    prev = _rev(root, ref or product_ref(feature))
    two = bool(first) and first != tree

    def same(commit: str) -> bool:
        return _tree(root, commit) == tree and _ancestor(root, start, commit)

    if prev and there in (None, prev):
        if same(prev):
            return prev
        if there == prev:  # published: add to it, never rewrite it
            parents = [prev] if _ancestor(root, start, prev) else [prev, start]
            only_pipeline = two and _tree(root, prev) == first
            message = pipeline_title(feature) if only_pipeline else title
            return _commit_tree(root, tree, parents, message)
    if there and there != prev:
        _run(root, "fetch", "-q", n.remote, f"refs/heads/{branch}")
        if _rev(root, there) and same(there):
            return there  # built and pushed from another clone, or the ref was cleaned up
        if not lease:
            raise PublishError(
                f"{n.remote} already has a branch {branch} with other content ({there[:8]}; "
                "someone pushed there, or it was built from another state of the feature). "
                "Nothing was pushed. Look at it (git fetch; git log "
                f"{n.remote}/{branch}); to replace it with this result: framegate publish "
                f"{feature} --force-with-lease"
            )
    if two:
        product = _commit_tree(root, first, [start], title)
        return _commit_tree(root, tree, [product], pipeline_title(feature))
    return _commit_tree(root, tree, [start], title)


def _push_product(cfg: Config, n: Names, sha: str, branch: str, lease: str | None) -> None:
    args = ["push", "-q"]
    if lease:
        args.append(f"--force-with-lease=refs/heads/{branch}:{lease}")
    r = _run(cfg.root, *args, n.remote, f"{sha}:refs/heads/{branch}")
    if r.returncode != 0:
        raise PublishError(f"Pushing {branch} to {n.remote} failed: {r.stderr.strip()}")


def _batch_safe(exe: str, text: str) -> str:
    """A tool that is a batch file on Windows (az.cmd) gets its arguments through cmd.exe:
    its special characters are taken out of free text."""
    if not exe.lower().endswith((".cmd", ".bat")):
        return text
    return re.sub(r'[&|<>^%!"]', "", text)


def pull_request(
    cfg: Config,
    n: Names,
    feature: str,
    title: str,
    echo: Echo,
    *,
    create: bool,
    head: str = "",
    body: str = "",
    write: bool = True,
) -> bool:
    """Create the pull request (`create`: the user said yes) with the host's tool, or say
    where to open it and where its description is. False = creating it failed. `head` /
    `body` (a repo-relative file): another branch and description than the product's (the
    pipeline's own).

    The description is {specs}/<f>/pr_description.md (description.py: written at DONE and
    by `publish`, kept by the cleanup, short enough for every host). What the task left to
    do by hand outside the repository is an unticked checklist in it, so it is seen at merge
    time. `write` False (a dry run): the file may not be written yet."""
    head = head or product_branch(cfg, feature)
    if not n.remote:
        return True
    host = hosts.parse_remote(_git(cfg.root, "remote", "get-url", n.remote).strip())
    link = hosts.pr_url(host, n.base, head)
    rel = body or description.rel(cfg, feature)
    summary = cfg.root / rel
    outside = followup.pr_checklist(cfg, feature)
    if outside:
        echo(
            f"Outside the repository: {len(outside)} item(s) to do by hand; an unticked "
            "checklist in the pull request's description."
        )
    if summary.is_file():
        text_at = f"its description: {rel}"
    elif write:
        text_at = f"no description file ({rel} is missing)"
    else:
        text_at = f"its description: {rel} (written by the publish)"
    by_hand = f"open it at {link}" if link else f"open it on your host: {head} -> {n.base}"
    tool = hosts.pr_tool(host)
    exe = which(tool) if tool else None
    why = f"no tool for {host.name}" if not tool else f"`{tool}` is not installed"
    if exe and host.kind == hosts.AZURE:
        probe = run_tool(
            [exe, "extension", "show", "--name", "azure-devops", "--output", "none"],
            cwd=cfg.root,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            exe, why = None, "`az` has no azure-devops extension (az extension add --name it)"
    if not create or not exe:
        how = "not created (publish with --pr creates it)" if exe else f"not created: {why}"
        echo(f"Pull request {head} -> {n.base}: {how}; {by_hand}; {text_at}.")
        return True
    body = str(summary) if summary.is_file() else ""
    cmd = hosts.pr_command(host, n.base, head, _batch_safe(exe, title), body)
    r = run_tool([exe, *cmd[1:]], cwd=cfg.root, capture_output=True, text=True)
    if r.returncode != 0:
        tail = " / ".join((r.stderr or r.stdout or "").strip().splitlines()[-3:])
        what = " ".join(cmd[: cmd.index("create") + 1])
        echo(f"Pull request: `{what}` failed ({tail}); {by_hand}; {text_at}.")
        return False
    out = (r.stdout or "").strip()
    page = hosts.azure_pr_page(host, out) if host.kind == hosts.AZURE and out else out
    echo(f"Pull request {head} -> {n.base} created: {page}")
    return True


def write_description(
    cfg: Config,
    feature: str,
    echo: Echo,
    *,
    own: bool = False,
    dry_run: bool = False,
) -> str:
    """Bring the pull request's description up to date and commit it when it changed (only
    that file: it is one of framegate's, kept with the spec). `own`: the deploy / CI change
    gets a pull request of its own, with its own text. Returns the file the pull request
    uses (repo-relative). A dry run writes nothing."""
    alone = not (cfg.spec_dir(feature) / "requirements.md").is_file()
    pipeline = own or alone
    body = description.rel(cfg, feature, pipeline=pipeline)
    if dry_run:
        note = "" if alone else description.compose(cfg, feature, own=own)[1]
        if note:
            echo(note)
        return body
    written = [description.refresh_pipeline(cfg, feature)] if pipeline else []
    if not alone:
        written.append(description.refresh(cfg, feature, own=own))
    for w in written:
        if w.note:
            echo(w.note)
    changed = [w.rel for w in written if w.changed]
    if changed:
        _git(cfg.root, "add", "--", *changed)
        message = f"{feature}: pull request description"
        _git(cfg.root, "commit", "-q", "-m", message, "--", *changed)
    return body


def describe(cfg: Config, feature: str, echo: Echo = print, *, pipeline: bool = False) -> int:
    """`framegate pr-description <f> [--pipeline]`: write the description again from what is
    there now, commit it when it changed, and say where it is. For a pull request that is
    created without `framegate publish` (no `[git] state_branch`)."""
    if not cfg.spec_dir(feature).is_dir():
        raise PublishError(f"No feature '{feature}' here ({cfg.paths.specs}/{feature}/ missing).")
    before = _rev(cfg.root, "HEAD")
    try:
        rel = write_description(cfg, feature, echo, own=pipeline)
        state = " (updated, committed)" if _rev(cfg.root, "HEAD") != before else ""
    except GitError as e:  # ignored by the project, no identity: the file is there all the same
        rel = description.rel(cfg, feature, pipeline=pipeline)
        state = f" (written, not committed: {str(e).splitlines()[-1]})"
    echo(f"Pull request description: {rel}{state}")
    return 0


def publish(
    cfg: Config,
    feature: str,
    echo: Echo = print,
    *,
    dry_run: bool = False,
    pr: bool = False,
    title: str = "",
    force_with_lease: bool = False,
    unfinished: bool = False,
) -> int:
    """`framegate publish <f>`: framegate's files of the feature -> the state branch (one
    commit, pushed); the product -> the product branch (one commit on the base, checked,
    pushed as `<branch_prefix><f>`); with `pr` the pull request. Run again, it finds
    nothing left to do. It needs the spec folder and git, never the out folder: a task
    that is cleaned up can still be published, and get its pull request."""
    if not enabled(cfg):
        if dry_run:
            echo(
                "State branch: off ([git] state_branch is empty): the feature branch holds "
                "framegate's files too, and is pushed as it is."
            )
            return 0
    _require(cfg)
    root = cfg.root
    n = names(cfg)
    branch = product_branch(cfg, feature)
    if cfg.git_branch and not dry_run:
        adopt_work_branch(cfg, feature, echo)
    work = _current_branch(root)
    # a dry run changes nothing: an old-style branch is read under its old name
    allowed = (cfg.work_branch(feature), *([branch] if dry_run else []))
    if cfg.git_branch and work not in allowed:
        raise PublishError(
            f"{feature} is published from its work branch: git switch {cfg.work_branch(feature)}"
        )
    if not cfg.spec_dir(feature).is_dir():
        raise PublishError(f"No feature '{feature}' here ({cfg.paths.specs}/{feature}/ missing).")
    if not unfinished and _rev(root, pipeline_start_ref(feature)):
        raise PublishError(
            f"The pipeline task of {feature} is open (not approved yet): finish it first "
            f"(/framegate:pipeline {feature}; by hand: framegate pipeline-approve {feature})."
        )
    # a pipeline task without a parent task has a list and no spec: nothing to be DONE
    alone = not (cfg.spec_dir(feature) / "requirements.md").is_file()
    if not unfinished and not alone and not feature_finished(cfg, feature):
        raise PublishError(
            f"{feature} is not DONE yet (no metrics row). Finish it first; --unfinished "
            "publishes the work so far (a later run's frame check then sees the merged "
            "state files as changes)."
        )
    _refuse_dirty(cfg)
    try:
        start = _git(root, "merge-base", "HEAD", cfg.git_base).strip()
    except GitError as e:
        raise PublishError(f"No common commit with the base {cfg.git_base}: {e}") from e
    on_base, _ = _split(cfg, _files(root, start))
    if on_base:
        raise PublishError(
            f"The base ({cfg.git_base}, {start[:8]}) holds files under framegate's paths: "
            f"{_listed(on_base, 8)}. With [git] state_branch they must not be on the base: "
            "remove them there, or point framegate's path elsewhere (e.g. [paths] metrics)."
        )
    title = title.strip() or feature
    echo(f"Publish {feature}" + (" (dry run: nothing is changed or pushed)" if dry_run else ""))

    # --- the product: built and checked before anything is changed (it refuses here) ---
    tree = product_tree(cfg, "HEAD")
    if tree == _tree(root, start):
        raise PublishError(f"{feature} changed no product file since {cfg.git_base}: no branch.")
    # deploy / CI files a pipeline task changed here are a commit of their own: the second
    # one of the product branch, or (the product is merged, or there is none) a branch and
    # pull request of their own
    pipe = pipeline_changed(cfg, start)
    first = tree_with(cfg, tree, start, pipe)
    no_product = first == _tree(root, start)
    own = bool(pipe) and (no_product or contained(root, first, start, base_tip(cfg, n)))
    ref = product_ref(feature)
    verify: Callable[[str], None] = partial(verify_product, cfg)
    if own:
        branch, ref = pipeline_branch(cfg, feature), pipeline_ref(feature)
        if title == feature:
            title = pipeline_title(feature)
        tree, first = tree_with(cfg, _tree(root, start), "HEAD", pipe), ""
        verify = partial(verify_pipeline, cfg, start=start, paths=pipe)
    # the pull request's description (its own text for a pipeline branch): one of
    # framegate's files, so the commit moves the work branch and never the product
    body = write_description(cfg, feature, echo, own=own, dry_run=dry_run)

    there = _remote_tip(cfg, n, branch)
    product = _product_commit(
        *(cfg, n, feature, tree, start, title, there, force_with_lease),
        first=first,
        branch=branch,
        ref=ref,
    )
    verify(product)

    # --- framegate's files -> the state branch ---
    tip, changed = save_state(
        cfg, n, echo, f"framegate: workflow files after {feature}", dry_run=dry_run
    )
    verb = "to update" if dry_run else "updated"
    echo(f"State branch {n.state}: " + (f"{len(changed)} file(s) {verb}" if changed else "in line"))
    for line in changed[:LIST_MAX]:
        echo("  " + line.replace("\t", " "))
    # a merge of the state branch moved the work branch: it touches framegate's files only
    verify(product)

    files = _git(root, "diff", "--no-renames", "--name-status", start, product).splitlines()
    left_out, _ = _split(cfg, _files(root, "HEAD"))
    behind = len(_git(root, "rev-list", f"{start}..{cfg.git_base}").split())
    if own:
        why = "this branch changed nothing else" if no_product else "the product is merged"
        shape = f"Pipeline branch {branch} ({why}): {len(files)} deploy / CI file(s), one commit"
    elif pipe:
        shape = (
            f"Product branch {branch}: {len(files)} file(s), {len(pipe)} of them deploy / CI "
            f'files in a commit of their own ("{pipeline_title(feature)}") after'
        )
    else:
        shape = f"Product branch {branch}: {len(files)} file(s), one commit"
    echo(
        f'{shape} "{title}" on '
        f"{cfg.git_base} ({start[:8]}"
        + (f"; {behind} newer commit(s) there: the pull request merges them" if behind else "")
        + f"); {len(left_out)} framegate file(s) left out"
    )
    if there and there != product and _ancestor(root, there, product):
        echo(
            f"{branch} is on {n.remote} already: this adds to it (and to its pull request, if "
            "one is open)."
        )
    for line in files[:LIST_MAX]:
        echo("  " + line.replace("\t", " "))
    if len(files) > LIST_MAX:
        echo(f"  ... and {len(files) - LIST_MAX} more")
    if dry_run:
        if n.remote:
            state_there = _rev(root, n.remote_state_ref)
            echo(
                f"Would push to {n.remote}: {n.state}"
                + (" (in line already)" if not changed and state_there == tip else "")
                + f", {branch}"
                + (" (in line already)" if there == product else "")
            )
            pull_request(
                cfg, n, feature, title, echo, create=False, head=branch, body=body, write=False
            )
        else:
            echo("No remote: nothing would be pushed.")
        return 0

    install_guard(cfg, n, echo)
    # --- push: the state first (a failed product push leaves nothing half-told) ---
    push_state(cfg, n, tip, echo)
    if not n.remote:
        _git(root, "update-ref", ref, product)
        echo(f"No remote: nothing pushed. The {'pipeline' if own else 'product'} commit: {ref}")
        return 0
    if there != product:
        _push_product(cfg, n, product, branch, there if force_with_lease and there else None)
        echo(f"Pushed {branch} to {n.remote} ({product[:8]}).")
    else:
        echo(f"{branch} on {n.remote} is in line already ({product[:8]}).")
    _git(root, "update-ref", ref, product)
    created = pull_request(cfg, n, feature, title, echo, create=pr, head=branch, body=body)
    return 0 if created else 1


def forget(cfg: Config, feature: str) -> bool:
    """Remove the feature's product ref (`framegate cleanup --tags`, after the merge)."""
    found = [r for r in (product_ref(feature), pipeline_ref(feature)) if _rev(cfg.root, r)]
    for ref in found:
        _git(cfg.root, "update-ref", "-d", ref)
    return bool(found)
