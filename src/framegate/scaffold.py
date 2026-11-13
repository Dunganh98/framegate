"""`framegate init` and `framegate spec-brief`."""

from __future__ import annotations

import os
import pathlib
from collections.abc import Callable

from framegate import references
from framegate.adapters import detect_language, get_adapter
from framegate.adapters.base import LanguageAdapter
from framegate.config import (
    CONFIG_FILE,
    Config,
    ConfigError,
    find_root,
    load_config,
    parse_config,
)
from framegate.orchestrator import package_text
from framegate.specs import feature_mode, template_dir

#: spec-brief lines per mode (the feature line names the stubs; these replace it)
MODE_BRIEF = {
    "bugfix": [
        "- Mode: bug fix. requirements.md is a bug report: reproduction rows (R IDs: the test "
        "FAILS on today's code and passes once fixed) and must-not-change rows (I IDs: the "
        "test passes today and after). No interface stubs: the code exists. Do not fix "
        "anything during the spec phase.",
        "- Measure 'Observed now' by running today's code; never write it from memory.",
    ],
    "refactor": [
        "- Mode: refactor. requirements.md lists the code in scope and the behavior to "
        "preserve (R and I IDs: characterization tests that PASS on today's code, before and "
        "after every step). No interface stubs, no behavior change. Golden values are "
        "measured from today's code.",
        "- Existing tests first: find the tests that already exercise the code in scope and "
        "list them in requirements.md ('Existing tests of the code in scope'). The test "
        "writer tags them with the row they pin and writes new tests only for the gaps, so "
        "a row says what must hold, not that a new test is needed.",
        "- plan.md: small steps (one move, split or rename each); step 1 covers every ID; "
        "every step is checked with all the characterization tests.",
    ],
}

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
            "--language generic for any other language (configured in framegate.toml)"
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

    toml = package_text("templates", "framegate.toml").replace("{language}", lang)
    if adapter is not None:
        extra = adapter.config_template().rstrip()
    elif lang == "generic":  # unconfigured yet: the adapter cannot start without its section
        from framegate.adapters.generic import TEMPLATE

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

    echo(f"framegate init ({lang}) in {root}")
    for rel in created:
        echo(f"  created  {rel}")
    for rel in skipped:
        echo(f"  skipped  {rel} (exists, not overwritten)")
    echo(f"  .gitignore: {'added ' + ', '.join(missing) if missing else 'already complete'}")

    setup = (
        adapter.ci_setup()
        if adapter is not None
        else "      # install the project's toolchain and framegate here\n"
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
        from framegate.adapters.generic import INIT_NOTE

        note = INIT_NOTE
    else:
        note = ""
    if note:
        echo("\nNote: " + note)
    echo(
        "\nNext: `framegate install-tools` (puts the tools the workflow needs into the "
        "project's environment), `framegate doctor` (checks the configured tools and "
        "commands, no agents), "
        "commit these files, then in Claude Code (framegate plugin installed): "
        "/framegate:feature <feature>"
        '\nAllow the CLI for the chat skills: add "Bash(framegate *)" and '
        '"PowerShell(framegate *)" to permissions.allow in .claude/settings.local.json'
    )
    return 0


def install_tools(
    dry_run: bool = False,
    start: pathlib.Path | None = None,
    echo: Callable[[str], None] = print,
) -> int:
    """`framegate install-tools`: the tools the workflow needs, into the project's environment.
    Works before `framegate init` (the language is detected, the defaults apply)."""
    here = (start or pathlib.Path(os.getcwd())).resolve()
    root = find_root(here)
    if root is not None:
        cfg = load_config(root)
    else:
        lang = detect_language(here)
        if lang is None:
            echo(
                "error: no framegate.toml here and the project language could not be detected; "
                "run this in the repository root."
            )
            return 1
        cfg = parse_config(here, {"project": {"language": lang}})
    try:
        adapter = get_adapter(cfg)
    except ConfigError as e:  # e.g. generic without its section yet
        echo(f"Nothing to install yet: {e}")
        return 0
    return adapter.install_tools(dry_run, echo)


def spec_brief(cfg: Config, feature: str) -> str:
    adapter = get_adapter(cfg)
    p = cfg.paths
    mode = feature_mode(cfg, feature)
    folder = cfg.spec_dir(feature)
    state = "exists" if folder.is_dir() else f"missing — run: framegate spec {feature}"
    kpath = cfg.root / cfg.knowledge
    knowledge = (
        [
            f"Project knowledge ({cfg.knowledge}; use it for the questions and the spec, e.g. "
            "what must never change, how fixtures are made):",
            kpath.read_text(encoding="utf-8").strip(),
            "",
        ]
        if kpath.is_file()
        else [f"(No project knowledge file {cfg.knowledge}; `framegate init` creates one.)", ""]
    )
    from framegate.checks.frame_diff import setup_entries
    from framegate.checks.structure import oversized

    long_files, total = oversized(adapter)
    extra = [e for e in cfg.protected_extra if e not in setup_entries(adapter)]
    sizes = (
        [
            f"- Source files over {cfg.max_file_lines} lines ([gates] max_file_lines): "
            + ", ".join(f"{path} ({n})" for path, n in long_files)
            + (f", and {total - len(long_files)} more" if total > len(long_files) else "")
            + ". A plan step that would add code to one of them names the new file the code "
            'goes to instead ("New file: <path> (<what goes there>)").'
        ]
        if long_files
        else []
    )
    return "\n".join(
        [
            f"framegate spec brief: feature '{feature}', language {cfg.language}",
            f"- Spec folder: {p.specs}/{feature}/ ({state}); templates in "
            f"{p.specs}/{template_dir(mode)}/ (framegate's own if the project has none).",
            "- IDs: <CODE>-R-001 (requirement), <CODE>-E-001 (edge case), <CODE>-I-001 "
            f"(invariant); regex {cfg.id_pattern}",
            '- plan.md is parsed: "## Step N: <title>" headings, each with one '
            '"Covers: ID, ID" line. Every ID is covered by exactly one step.',
            *(
                MODE_BRIEF[mode]
                if mode in MODE_BRIEF
                else [
                    f"- Interface stubs go in {'/, '.join(cfg.src_roots)}/. Do not write tests "
                    "or implementation."
                ]
            ),
            f"- Run files (gate briefs, answers.md, the spec gate brief spec_brief.md): "
            f"{p.out}/{feature}/",
            f"- Code map (per module: purpose, key classes, how they work together): "
            f"{cfg.code_map} ("
            + ("exists: read it first" if (cfg.root / cfg.code_map).is_file() else "missing")
            + ")",
            *sizes,
            "- Setup files (change them now if the task needs a new package / source folder, a "
            "dependency or a tool setting; the approval commits them with the spec and they "
            "are frozen from then on): "
            + ", ".join(setup_entries(adapter))
            + (
                f". The approval then runs [env] refresh ({' '.join(cfg.env_refresh)})."
                if cfg.env_refresh
                else "."
            ),
            "- Pipeline follow-up: deploy and CI files (Dockerfile, compose, k8s, CI yml / "
            "scripts"
            + (f"; here also [protected] extra: {', '.join(cfg.protected_extra)}" if extra else "")
            + ") are never changed by a feature, not even now; they stay locked. If the feature "
            "means one must change (a new folder the image must copy, a new dependency it must "
            "install, a new test selection for CI), write it under 'Pipeline follow-up' in "
            "requirements.md (file, what, why) and show it on the spec brief: it is done as a "
            "separate step after the feature and raised again in the final summary.",
            *references.brief_lines(cfg),
            "",
            adapter.conventions_brief().rstrip(),
            "",
            *knowledge,
            f"When the spec is done: framegate approve-spec {feature}",
        ]
    )
