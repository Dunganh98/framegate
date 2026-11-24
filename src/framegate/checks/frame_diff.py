"""Hard frame check: since the tests were approved, nothing outside the frame may change.

Fails if protected paths changed (tests, specs, config, dependency and workflow files) or if
added source lines contain suppression patterns. Tool-independent: it compares against the
`tests-approved/<feature>` tag, so it also catches writes made through shells.

Two exceptions, both from answers at the final gate (after the tests were approved): the
SPEC_UPDATE step writes them into the spec (framegate tags that commit `spec-update/<feature>`),
and the test writer applies the ones that need a test change (tagged `tests-update/<feature>`).
A spec file of the feature / a test file that the tagged commit changed is allowed only while its
content is exactly the tagged one (any further edit is a frame violation again).
"""

from __future__ import annotations

import fnmatch
import re

from framegate.adapters.base import CheckResult, LanguageAdapter
from framegate.config import CONFIG_FILE, Config, drop_machine_files, is_machine_file
from framegate.state import GitError, commit_paths, git, tag_exists

#: the workflow's own files, protected in every project. The built-in deploy / CI paths
#: (config.DEPLOY_CI_FOLDERS / DEPLOY_CI_NAMES, minus `[protected] builtin_off`) come on top:
#: core_protected. With `[protected] extra` those are what the pipeline task may change.
WORKFLOW_PROTECTED = (".claude/", ".framegate/", "CLAUDE.md", CONFIG_FILE)


def core_protected(cfg: Config) -> list[str]:
    """What every project has protected: the workflow's own files and the built-in deploy /
    CI paths this project did not take off the list."""
    return [*WORKFLOW_PROTECTED, *cfg.deploy_ci_paths()]


def approved_tag(feature: str) -> str:
    return f"tests-approved/{feature}"


def spec_update_tag(feature: str) -> str:
    return f"spec-update/{feature}"


def tests_update_tag(feature: str) -> str:
    return f"tests-update/{feature}"


def knowledge_tag(feature: str) -> str:
    return f"knowledge-update/{feature}"


def config_update_tag(feature: str) -> str:
    return f"config-update/{feature}"


def allowed_spec_changes(cfg: Config, feature: str, untracked: set[str]) -> set[str]:
    """Spec files of the feature changed by the tagged SPEC_UPDATE commit and test files
    changed by the tagged test update, and the knowledge file changed by `framegate learn`
    (since the tests were approved), still identical to the tagged commit. The same for
    framegate.toml changed by an approved diagnosis (`Fix owner: config`; redo.py)."""
    return (
        _tagged(cfg, feature, spec_update_tag(feature), f"{cfg.paths.specs}/{feature}/", untracked)
        | _tagged(
            cfg, feature, tests_update_tag(feature), cfg.paths.tests.rstrip("/") + "/", untracked
        )
        | _tagged(cfg, feature, knowledge_tag(feature), cfg.knowledge, untracked)
        | _tagged(cfg, feature, config_update_tag(feature), CONFIG_FILE, untracked)
    )


def _tagged(cfg: Config, feature: str, tag: str, folder: str, untracked: set[str]) -> set[str]:
    if not tag_exists(cfg, tag):
        return set()
    cand = set(git(cfg, "diff", "--name-only", approved_tag(feature), tag, "--", folder).split())
    if not cand:
        return set()
    differs = set(git(cfg, "diff", "--name-only", tag, "--", *sorted(cand)).split())
    return cand - differs - untracked


def protected_paths(adapter: LanguageAdapter) -> list[str]:
    cfg = adapter.cfg
    if cfg.protected_paths is not None:
        base = list(cfg.protected_paths)
    else:
        base = [cfg.paths.tests + "/", cfg.paths.specs + "/", *core_protected(cfg)]
        if cfg.roles_extra_dir:
            base.append(cfg.roles_extra_dir.rstrip("/") + "/")
        base += adapter.default_protected_paths()
    return base + list(cfg.protected_extra)


def spec_locked(cfg: Config) -> list[str]:
    """Protected entries that stay locked in the spec phase too: the tests, the specs (the
    spec dialogue writes them through its own permission), the workflow's own rules and the
    built-in deploy / CI paths."""
    locked = [cfg.paths.tests + "/", cfg.paths.specs + "/"]
    locked += [e for e in core_protected(cfg) if e != CONFIG_FILE]
    if cfg.roles_extra_dir:
        locked.append(cfg.roles_extra_dir.rstrip("/") + "/")
    return locked


#: Claude Code's per-machine settings file (permission rules of this checkout; not shared
#: by its own convention). Never part of the frame: uncommitted, it does not refuse a spec
#: approval, and a change to it does not fail the frame check. The agents still cannot write
#: it (no phase's write paths hold `.claude/`).
MACHINE_FILES = (".claude/settings.local.json",)
#: what framegate itself writes into {specs}/<f>/ when the task ends and after it: the kept
#: follow-up lists (followup.py; the pipeline task ticks them) and the pull request's
#: descriptions (description.py). No agent can write them (the spec folder is locked), and
#: nothing in a run reads them: they are records, not part of the frame.
GENERATED_SPEC_FILES = (
    "pipeline_followup.md",
    "reference_followup.md",
    "pr_description.md",
    "pipeline_pr_description.md",
)


def generated_spec_files(cfg: Config, feature: str) -> set[str]:
    return {f"{cfg.paths.specs}/{feature}/{name}" for name in GENERATED_SPEC_FILES}


def setup_entries(adapter: LanguageAdapter) -> list[str]:
    """The setup paths as configured (prefixes / globs), for messages: the config file and
    the language's packaging / test / tool-config files."""
    locked = spec_locked(adapter.cfg)
    rest = [e for e in adapter.setup_paths() if e not in locked and e != CONFIG_FILE]
    return [CONFIG_FILE, *dict.fromkeys(rest)]


def is_setup_file(adapter: LanguageAdapter, path: str) -> bool:
    """A setup file: the config file, or one of the language's packaging / test / tool-config
    files (adapter.setup_paths) outside the spec-locked folders. The human's chat may change
    these in the spec phase, and `approve-spec` commits them with the spec, so the frame check
    starts from a clean baseline; after "spec OK" they are frozen like the rest.

    Not `[protected] extra`: deploy, CI and generated files are never a feature's to change,
    not even at spec time (extra_locked). A path listed there that is also one of the
    language's setup files (pyproject.toml, pytest.ini, ...) is a setup file all the same."""
    if path == CONFIG_FILE:
        return True
    cfg = adapter.cfg
    return path_matches(path, adapter.setup_paths()) and not path_matches(path, spec_locked(cfg))


def extra_locked(adapter: LanguageAdapter, path: str) -> bool:
    """A `[protected] extra` path that is not a setup file: locked in the spec phase too, also
    inside a source folder, and never committed by the approval."""
    return path_matches(path, list(adapter.cfg.protected_extra)) and not is_setup_file(
        adapter, path
    )


def pipeline_entries(cfg: Config) -> list[str]:
    """The deploy / CI paths as entries for path_matches: the built-in ones and `[protected]
    extra`."""
    return [*cfg.deploy_ci_paths(), *cfg.protected_extra]


def pipeline_entries_text(cfg: Config) -> str:
    """The deploy / CI paths in a few words, for messages."""
    extra = f"; [protected] extra: {', '.join(cfg.protected_extra)}" if cfg.protected_extra else ""
    return f"the built-in names: Dockerfile, compose files, Jenkinsfile, CI yml and folders{extra}"


def own_folders(cfg: Config) -> list[str]:
    """Folders where a built-in deploy / CI name is not a deploy file: the tests (test data),
    the specs and the workflow's own folders."""
    p = cfg.paths
    return [p.tests + "/", p.specs + "/", p.out + "/", ".claude/", ".framegate/"]


def maybe_pipeline_file(cfg: Config, path: str) -> bool:
    """is_pipeline_file without the setup-file question (no adapter needed): True for every
    deploy / CI file, and for a setup file that `[protected] extra` also lists. Never for a
    machine file (a `Dockerfile` inside `.venv/` is tool state, not the project's deploy)."""
    if is_machine_file(cfg, path):
        return False
    if path_matches(path, list(cfg.protected_extra)):
        return True
    return path_matches(path, cfg.deploy_ci_paths()) and not path_matches(path, own_folders(cfg))


def is_pipeline_file(adapter: LanguageAdapter, path: str) -> bool:
    """A deploy / CI file: a built-in deploy / CI path (not under the tests, the specs or
    the workflow's own folders) or under `[protected] extra`, and not a setup file. No
    feature, bug fix or refactor changes one, in any phase and also inside a source folder;
    the pipeline task changes nothing else (pipeline.py). A source file of the project's
    language is one only through `[protected] extra` (source_named)."""
    if not maybe_pipeline_file(adapter.cfg, path) or is_setup_file(adapter, path):
        return False
    return not source_named(adapter, path)


def source_named(adapter: LanguageAdapter, path: str) -> bool:
    """A source file of the project's language that `[protected] extra` does not list: when
    a built-in deploy / CI name matches it (`dockerfile.py`, `Dockerfile.cs` and the name
    `Dockerfile.*`), it is source all the same, not a deploy file."""
    suffixes = tuple(adapter.source_suffixes)
    return (
        bool(suffixes)
        and path.endswith(suffixes)
        and not path_matches(path, list(adapter.cfg.protected_extra))
    )


def is_protected(adapter: LanguageAdapter, path: str, protected: list[str]) -> bool:
    """`path` matches the protected entries; a source file that only a built-in deploy / CI
    name matches (source_named) does not count."""
    if not path_matches(path, protected):
        return False
    if not source_named(adapter, path):
        return True
    builtin = set(adapter.cfg.deploy_ci_paths())
    return path_matches(path, [e for e in protected if e not in builtin])


def setup_state(adapter: LanguageAdapter) -> tuple[list[str], list[str]]:
    """Uncommitted (changed, deleted or untracked) protected files, split in two:
    (setup files: `approve-spec` commits them with the spec; stray files: protected, not a
    setup file and not in a path the workflow commits, e.g. CLAUDE.md, .claude/, or a deploy
    / CI file wherever it lies (the built-in names, `[protected] extra`): `approve-spec`
    refuses while one is there, because it must not commit it and every later frame check
    would fail on it). The per-machine files (MACHINE_FILES) are neither."""
    cfg = adapter.cfg
    changed, _ = changed_files(cfg, "HEAD")
    protected, exceptions = protected_paths(adapter), cfg.frame_exceptions()
    committed = [p.rstrip("/") for p in commit_paths(cfg)]
    setup, stray = [], []
    for path in sorted(changed):
        if path in MACHINE_FILES:
            continue
        if is_setup_file(adapter, path):
            setup.append(path)
        elif path_matches(path, exceptions):
            continue
        elif is_pipeline_file(adapter, path) or (
            is_protected(adapter, path, protected)
            and not any(path == p or path.startswith(p + "/") for p in committed)
        ):
            stray.append(path)
    return setup, stray


GLOB_CHARS = ("*", "?", "[")


def path_matches(path: str, entries: list[str]) -> bool:
    """True if `path` (posix, relative to the root) starts with one of the prefix entries or
    matches one of the glob entries (entries containing * ? [; `*` also matches `/`, so
    `*.csproj` matches a project file at any depth; case-insensitive). An entry `**/<name>`
    is a file name at any depth, the root included: `**/Dockerfile`, `**/compose*.yml`
    (`<name>` is matched against the last part of the path)."""
    for entry in entries:
        if entry.startswith("**/"):
            if fnmatch.fnmatchcase(path.lower().rsplit("/", 1)[-1], entry[3:].lower()):
                return True
        elif any(ch in entry for ch in GLOB_CHARS):
            if fnmatch.fnmatchcase(path.lower(), entry.lower()):
                return True
        elif path.startswith(entry):
            return True
    return False


def forbidden_regex(adapter: LanguageAdapter) -> re.Pattern[str] | None:
    cfg = adapter.cfg
    pats = (
        cfg.forbidden_patterns
        if cfg.forbidden_patterns is not None
        else adapter.default_forbidden_patterns()
    )
    return re.compile("|".join(f"(?:{p})" for p in pats)) if pats else None


def changed_files(cfg: Config, ref: str) -> tuple[set[str], set[str]]:
    """(all changed files since ref incl. untracked, untracked files), without the machine
    files (config.is_machine_file): tool state of this machine is no change of a task, so
    no check built on this (frame, setup state, coverage, structure, mutation, smells,
    formatting) ever sees one."""
    listed = git(cfg, "ls-files", "--others", "--exclude-standard").splitlines()
    untracked = set(drop_machine_files(cfg, listed))
    tracked = drop_machine_files(cfg, git(cfg, "diff", "--name-only", ref).splitlines())
    return set(tracked) | untracked, untracked


def changed_src_files(adapter: LanguageAdapter, ref: str) -> list[str]:
    cfg = adapter.cfg
    changed, _ = changed_files(cfg, ref)
    return sorted(f for f in changed if cfg.in_src(f) and (cfg.root / f).is_file())


def new_suppressions(diff: str, rx: re.Pattern[str]) -> list[str]:
    """The added lines of a `git diff -U0` that hold a suppression the file did not have
    before. A suppression counts by its text (what the pattern matches), per file: a line
    that was re-wrapped, moved or had its comment shortened removes one and adds the same
    one, which is no new suppression. Seen live: the formatter split two existing `noqa`
    lines of a file the feature touched, and the frame check failed every attempt on
    "SUPPRESSION ADDED". More of the same suppression than before are still reported."""
    found: list[str] = []
    removed: dict[str, int] = {}
    added: list[tuple[str, str]] = []

    def close() -> None:
        for key, line in added:
            if removed.get(key, 0) > 0:
                removed[key] -= 1
            else:
                found.append(line)
        removed.clear()
        added.clear()

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            close()
        elif line.startswith(("+++", "---")):
            continue
        elif line.startswith(("+", "-")):
            m = rx.search(line[1:])
            if not m:
                continue
            key = " ".join(m.group(0).split())
            if line[0] == "+":
                added.append((key, line[1:]))
            else:
                removed[key] = removed.get(key, 0) + 1
    close()
    return found


#: the check's name in the gate reports
CHECK_NAME = "frame diff"
PROTECTED_CHANGED = "PROTECTED PATH CHANGED: "


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    cfg = adapter.cfg
    tag = approved_tag(feature)
    try:
        if not tag_exists(cfg, tag):
            return CheckResult("FAIL", f"Tag {tag} not found — tests were never approved.")
        changed, untracked = changed_files(cfg, tag)
        diff = git(cfg, "diff", "-U0", tag, "--", *cfg.src_roots)
    except GitError as e:
        return CheckResult("FAIL", str(e))
    try:
        spec_ok = allowed_spec_changes(cfg, feature, untracked)
    except GitError as e:
        return CheckResult("FAIL", str(e))
    spec_ok |= generated_spec_files(cfg, feature)
    protected = protected_paths(adapter)
    exceptions = cfg.frame_exceptions()
    problems = [
        f"{PROTECTED_CHANGED}{f}"
        for f in sorted(changed)
        if is_protected(adapter, f, protected)
        and not path_matches(f, exceptions)
        and f not in spec_ok
        and f not in MACHINE_FILES
    ]
    rx = forbidden_regex(adapter)
    if rx is not None:
        added = new_suppressions(diff, rx)
        for f in sorted(untracked):
            if cfg.in_src(f) and f.endswith(adapter.source_suffixes):
                path = cfg.root / f
                if path.is_file():
                    text = path.read_text(encoding="utf-8", errors="replace")
                    added += [line for line in text.splitlines() if rx.search(line)]
        problems += [f"SUPPRESSION ADDED: {line.strip()}" for line in added]
    return CheckResult("FAIL" if problems else "PASS", "\n".join(problems) or "Frame intact.")
