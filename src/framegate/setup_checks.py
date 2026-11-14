"""Two `framegate doctor` checks that need no tool run: names of an earlier version that are
still read, and commands an agent's task names that its tool rules would deny."""

from __future__ import annotations

from framegate.adapters.base import LanguageAdapter
from framegate.agent_commands import rule_allows
from framegate.config import ROLE_ALIASES, Config

#: the phases whose task carries the conventions brief (orchestrator.BRIEF_PHASES, by phase)
BRIEF_RUN_PHASES = ("TESTS", "TEST_REVIEW", "IMPLEMENT", "REVIEW", "TRIAGE", "DIAGNOSIS")


def renamed_names(cfg: Config) -> list[str]:
    """What the project still names as an earlier version did: `[phases.PR]` in
    framegate.toml, `pr.md` in the roles' extra folder. Both are read; rename them."""
    notes = list(cfg.renamed)
    if cfg.roles_extra_dir:
        folder = cfg.roles_extra_dir.rstrip("/")
        for new, old in ROLE_ALIASES.items():
            if (cfg.root / folder / f"{old}.md").is_file():
                notes.append(f"{folder}/{old}.md is now {folder}/{new}.md")
    return notes


def denied_brief_commands(cfg: Config, adapter: LanguageAdapter) -> list[str]:
    """`<phase>: <command>` for every command the conventions brief of a phase names that
    the phase's tool rules do not allow (the agent would run it and be denied). With the
    adapter's own rules there is none; a `[phases.<P>] tools` list can leave one out."""
    out = []
    for phase in BRIEF_RUN_PHASES:
        allowed = cfg.phase_tools.get(phase) or adapter.allowed_tools(phase)
        out += [
            f"{phase}: `{cmd}`"
            for cmd in adapter.brief_commands(allowed)
            if not rule_allows(allowed, cmd)
        ]
    return out
