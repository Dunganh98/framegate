"""`agentflow init` and `agentflow spec-brief`."""

from __future__ import annotations

import os
import pathlib
from collections.abc import Callable

from agentflow.adapters import detect_language, get_adapter
from agentflow.adapters.base import LanguageAdapter
from agentflow.config import CONFIG_FILE, Config, ConfigError, parse_config
from agentflow.orchestrator import package_text

SPEC_TEMPLATES = ("requirements.md", "design.md", "plan.md")


def init(
    language: str | None = None,
    root: pathlib.Path | None = None,
    echo: Callable[[str], None] = print,
) -> int:
    root = (root or pathlib.Path(os.getcwd())).resolve()
    lang = language or detect_language(root)
    if lang is None:
        echo(
            "error: could not detect the project language; pass --language python|dotnet, or "
            "--language generic for any other language (configured in agentflow.toml)"
        )
        return 1
    cfg = parse_config(root, {"project": {"language": lang}})
    try:
        adapter: LanguageAdapter | None = get_adapter(cfg)
    except ConfigError:
        adapter = None
    created, skipped = [], []

    def write(rel: str, text: str) -> None:
        path = root / rel
        if path.exists():
            skipped.append(rel)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        created.append(rel)

    toml = package_text("templates", "agentflow.toml").replace("{language}", lang)
    if adapter is not None:
        extra = adapter.config_template().rstrip()
    elif lang == "generic":  # unconfigured yet: the adapter cannot start without its section
        from agentflow.adapters.generic import TEMPLATE

        extra = TEMPLATE.rstrip()
    else:
        extra = ""
    if extra:
        toml = toml.rstrip() + "\n\n" + extra + "\n"
    write(CONFIG_FILE, toml)
    for name in SPEC_TEMPLATES:
        write(
            f"{cfg.paths.specs}/_template/{name}",
            package_text("templates", "specs", "_template", name),
        )
    write(cfg.knowledge, package_text("templates", "project.md"))

    gi = root / ".gitignore"
    gi_text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    existing = gi_text.splitlines()
    wanted = [cfg.paths.out + "/", cfg.paths.phase_file, cfg.paths.reqs_file]
    wanted += adapter.gitignore_entries() if adapter is not None else []
    missing = [w for w in wanted if w not in {x.strip() for x in existing}]
    if missing:
        prefix = "" if not gi_text or gi_text.endswith("\n") else "\n"
        with open(gi, "a", encoding="utf-8") as fh:
            fh.write(prefix + "\n".join(missing) + "\n")

    echo(f"agentflow init ({lang}) in {root}")
    for rel in created:
        echo(f"  created  {rel}")
    for rel in skipped:
        echo(f"  skipped  {rel} (exists, not overwritten)")
    echo(f"  .gitignore: {'added ' + ', '.join(missing) if missing else 'already complete'}")

    setup = (
        adapter.ci_setup()
        if adapter is not None
        else "      # install the project's toolchain and agentflow here\n"
    )
    snippet = package_text("templates", "claude_md_snippet.md")
    for key, value in (("{specs}", cfg.paths.specs), ("{out}", cfg.paths.out)):
        snippet = snippet.replace(key, value)
    echo("\n--- Add to CLAUDE.md (not written; merge it yourself) ---\n" + snippet.rstrip())
    echo(
        "\n--- CI snippet (GitHub Actions; not written) ---\n"
        + package_text("templates", "ci_github.yml").replace("{setup}", setup).rstrip()
    )
    if adapter is not None:
        note = adapter.init_note().rstrip()
    elif lang == "generic":
        from agentflow.adapters.generic import INIT_NOTE

        note = INIT_NOTE
    else:
        note = ""
    if note:
        echo("\nNote: " + note)
    echo(
        "\nNext: `agentflow doctor` (checks the configured tools and commands, no agents), "
        "commit these files, then in Claude Code (agentflow plugin installed): "
        "/agentflow:feature <feature>"
        '\nAllow the CLI for the chat skills: add "Bash(agentflow *)" and '
        '"PowerShell(agentflow *)" to permissions.allow in .claude/settings.local.json'
    )
    return 0


def spec_brief(cfg: Config, feature: str) -> str:
    adapter = get_adapter(cfg)
    p = cfg.paths
    folder = cfg.spec_dir(feature)
    state = "exists" if folder.is_dir() else f"missing — run: agentflow spec {feature}"
    kpath = cfg.root / cfg.knowledge
    knowledge = (
        [
            f"Project knowledge ({cfg.knowledge}; use it for the questions and the spec, e.g. "
            "what must never change, how fixtures are made):",
            kpath.read_text(encoding="utf-8").strip(),
            "",
        ]
        if kpath.is_file()
        else [f"(No project knowledge file {cfg.knowledge}; `agentflow init` creates one.)", ""]
    )
    return "\n".join(
        [
            f"agentflow spec brief: feature '{feature}', language {cfg.language}",
            f"- Spec folder: {p.specs}/{feature}/ ({state}); templates in {p.specs}/_template/.",
            "- IDs: <CODE>-R-001 (requirement), <CODE>-E-001 (edge case), <CODE>-I-001 "
            f"(invariant); regex {cfg.id_pattern}",
            '- plan.md is parsed: "## Step N: <title>" headings, each with one '
            '"Covers: ID, ID" line. Every ID is covered by exactly one step.',
            f"- Interface stubs go in {p.src}/. Do not write tests or implementation.",
            f"- Run files (gate digests, answers.md): {p.out}/{feature}/",
            "",
            adapter.conventions_brief().rstrip(),
            "",
            *knowledge,
            f"When the spec is done: agentflow approve-spec {feature}",
        ]
    )
