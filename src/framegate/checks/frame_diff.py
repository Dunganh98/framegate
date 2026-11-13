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
from framegate.config import CONFIG_FILE, Config
from framegate.state import GitError, commit_paths, git, tag_exists

CORE_PROTECTED = (
    ".claude/",
    ".framegate/",
    "CLAUDE.md",
    CONFIG_FILE,
    ".github/",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
)


def approved_tag(feature: str) -> str:
    return f"tests-approved/{feature}"


def spec_update_tag(feature: str) -> str:
    return f"spec-update/{feature}"


def tests_update_tag(feature: str) -> str:
    return f"tests-update/{feature}"


def knowledge_tag(feature: str) -> str:
    return f"knowledge-update/{feature}"


def allowed_spec_changes(cfg: Config, feature: str, untracked: set[str]) -> set[str]:
    """Spec files of the feature changed by the tagged SPEC_UPDATE commit and test files
    changed by the tagged test update, and the knowledge file changed by `framegate learn`
    (since the tests were approved), still identical to the tagged commit."""
    return (
        _tagged(cfg, feature, spec_update_tag(feature), f"{cfg.paths.specs}/{feature}/", untracked)
        | _tagged(
            cfg, feature, tests_update_tag(feature), cfg.paths.tests.rstrip("/") + "/", untracked
        )
        | _tagged(cfg, feature, knowledge_tag(feature), cfg.knowledge, untracked)
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
        base = [cfg.paths.tests + "/", cfg.paths.specs + "/", *CORE_PROTECTED]
        if cfg.roles_extra_dir:
            base.append(cfg.roles_extra_dir.rstrip("/") + "/")
        base += adapter.default_protected_paths()
    return base + list(cfg.protected_extra)


def spec_locked(cfg: Config) -> list[str]:
    """Protected entries that stay locked in the spec phase too: the tests, the specs (the
    spec dialogue writes them through its own permission), the workflow's own rules and the
    CI files."""
    locked = [cfg.paths.tests + "/", cfg.paths.specs + "/"]
    locked += [e for e in CORE_PROTECTED if e != CONFIG_FILE]
    if cfg.roles_extra_dir:
        locked.append(cfg.roles_extra_dir.rstrip("/") + "/")
    return locked


#: Claude Code's per-machine settings file (permission rules of this checkout; not shared
#: by its own convention). Never part of the frame: uncommitted, it does not refuse a spec
#: approval, and a change to it does not fail the frame check. The agents still cannot write
#: it (no phase's write paths hold `.claude/`).
MACHINE_FILES = (".claude/settings.local.json",)


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


def setup_state(adapter: LanguageAdapter) -> tuple[list[str], list[str]]:
    """Uncommitted (changed, deleted or untracked) protected files, split in two:
    (setup files: `approve-spec` commits them with the spec; stray files: protected, not a
    setup file and not in a path the workflow commits, e.g. CLAUDE.md, .claude/, CI files,
    or in `[protected] extra` wherever it lies (deploy, CI, generated files): `approve-spec`
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
        elif extra_locked(adapter, path) or (
            path_matches(path, protected)
            and not any(path == p or path.startswith(p + "/") for p in committed)
        ):
            stray.append(path)
    return setup, stray


GLOB_CHARS = ("*", "?", "[")


def path_matches(path: str, entries: list[str]) -> bool:
    """True if `path` (posix, relative to the root) starts with one of the prefix entries or
    matches one of the glob entries (entries containing * ? [; `*` also matches `/`, so
    `*.csproj` matches a project file at any depth; case-insensitive)."""
    for entry in entries:
        if any(ch in entry for ch in GLOB_CHARS):
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
    """(all changed files since ref incl. untracked, untracked files)."""
    untracked = set(git(cfg, "ls-files", "--others", "--exclude-standard").splitlines())
    changed = set(git(cfg, "diff", "--name-only", ref).splitlines()) | untracked
    return changed, untracked


def changed_src_files(adapter: LanguageAdapter, ref: str) -> list[str]:
    cfg = adapter.cfg
    changed, _ = changed_files(cfg, ref)
    return sorted(f for f in changed if cfg.in_src(f) and (cfg.root / f).is_file())


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
    protected = protected_paths(adapter)
    exceptions = cfg.frame_exceptions()
    problems = [
        f"{PROTECTED_CHANGED}{f}"
        for f in sorted(changed)
        if path_matches(f, protected)
        and not path_matches(f, exceptions)
        and f not in spec_ok
        and f not in MACHINE_FILES
    ]
    added = [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    for f in sorted(untracked):
        if cfg.in_src(f) and f.endswith(adapter.source_suffixes):
            path = cfg.root / f
            if path.is_file():
                added += path.read_text(encoding="utf-8", errors="replace").splitlines()
    rx = forbidden_regex(adapter)
    if rx is not None:
        problems += [f"SUPPRESSION ADDED: {line.strip()}" for line in added if rx.search(line)]
    return CheckResult("FAIL" if problems else "PASS", "\n".join(problems) or "Frame intact.")
