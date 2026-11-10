"""Hard frame check: since the tests were approved, nothing outside the frame may change.

Fails if protected paths changed (tests, specs, config, dependency and workflow files) or if
added source lines contain suppression patterns. Tool-independent: it compares against the
`tests-approved/<feature>` tag, so it also catches writes made through shells.

Two exceptions, both from answers at the final gate (after the tests were approved): the
SPEC_UPDATE step writes them into the spec (agentflow tags that commit `spec-update/<feature>`),
and the test writer applies the ones that need a test change (tagged `tests-update/<feature>`).
A spec file of the feature / a test file that the tagged commit changed is allowed only while its
content is exactly the tagged one (any further edit is a frame violation again).
"""

from __future__ import annotations

import fnmatch
import re

from agentflow.adapters.base import CheckResult, LanguageAdapter
from agentflow.config import CONFIG_FILE, Config
from agentflow.state import GitError, git, tag_exists

CORE_PROTECTED = (
    ".claude/",
    ".agentflow/",
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


def allowed_spec_changes(cfg: Config, feature: str, untracked: set[str]) -> set[str]:
    """Spec files of the feature changed by the tagged SPEC_UPDATE commit and test files
    changed by the tagged test update (since the tests were approved), still identical to the
    tagged commit."""
    return _tagged(
        cfg, feature, spec_update_tag(feature), f"{cfg.paths.specs}/{feature}/", untracked
    ) | _tagged(
        cfg, feature, tests_update_tag(feature), cfg.paths.tests.rstrip("/") + "/", untracked
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
    src = cfg.paths.src.rstrip("/") + "/"
    return sorted(f for f in changed if f.startswith(src) and (cfg.root / f).is_file())


def check(adapter: LanguageAdapter, feature: str) -> CheckResult:
    cfg = adapter.cfg
    tag = approved_tag(feature)
    try:
        if not tag_exists(cfg, tag):
            return CheckResult("FAIL", f"Tag {tag} not found — tests were never approved.")
        changed, untracked = changed_files(cfg, tag)
        diff = git(cfg, "diff", "-U0", tag, "--", cfg.paths.src)
    except GitError as e:
        return CheckResult("FAIL", str(e))
    try:
        spec_ok = allowed_spec_changes(cfg, feature, untracked)
    except GitError as e:
        return CheckResult("FAIL", str(e))
    protected = protected_paths(adapter)
    exceptions = cfg.frame_exceptions()
    problems = [
        f"PROTECTED PATH CHANGED: {f}"
        for f in sorted(changed)
        if path_matches(f, protected) and not path_matches(f, exceptions) and f not in spec_ok
    ]
    added = [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    src = cfg.paths.src.rstrip("/") + "/"
    for f in sorted(untracked):
        if f.startswith(src) and f.endswith(adapter.source_suffixes):
            path = cfg.root / f
            if path.is_file():
                added += path.read_text(encoding="utf-8", errors="replace").splitlines()
    rx = forbidden_regex(adapter)
    if rx is not None:
        problems += [f"SUPPRESSION ADDED: {line.strip()}" for line in added if rx.search(line)]
    return CheckResult("FAIL" if problems else "PASS", "\n".join(problems) or "Frame intact.")
