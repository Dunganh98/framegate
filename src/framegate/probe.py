"""`framegate probe`: the handful of project facts every run depends on, read in seconds.

For `/framegate:setup`: one table the user confirms once per project. Seen live (da-argos):
the setup trusted what it read in files, so the agents ran in the machine's Python instead
of `.venv` and were told lint / format commands the project does not use.

Only instant checks: files are read, git is asked, and each tool is asked for `--version`
(short timeout, all at once). The test suite, a linter over the code or an agent never run
here; `framegate doctor` does the thorough check once the config is written.

Works before a `framegate.toml` exists (the language and the folders are guessed, the
commands are the ones the project's own files point to, else framegate's defaults) and with
one (the commands are the configured ones).
"""

from __future__ import annotations

import fnmatch
import json
import os
import pathlib
import re
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from framegate import hosts
from framegate.adapters import detect_language, get_adapter
from framegate.adapters.base import GATE_NAMES, TIMEOUT_CODE, LanguageAdapter, run_command
from framegate.agent_commands import command_text
from framegate.checks.frame_diff import (
    core_protected,
    maybe_pipeline_file,
    path_matches,
    protected_paths,
)
from framegate.config import (
    CONFIG_FILE,
    DEPLOY_CI_FOLDERS,
    GATE_COMMAND_KEYS,
    Config,
    ConfigError,
    find_root,
    load_config,
    parse_config,
)

#: seconds a tool gets to answer `--version`
VERSION_TIMEOUT = 30
#: commits looked at to tell a repository of one's own from a team's
AUTHOR_COMMITS = 200
LIST_MAX = 8
BASE_NAMES = ("main", "master", "develop", "dev")

#: top-level folders whose name says deploy / CI but can mean something else: not built in,
#: proposed for `[protected] extra` when they exist
DEPLOY_DIRS = {"ci", "deploy", "deployment", "deployments", "k8s", "kubernetes", "helm"}
DEPLOY_DIRS |= {"charts", "infra", "infrastructure", "terraform", "pipelines"}
DEPLOY_DIRS |= {".azuredevops", ".gitlab", ".buildkite"}
#: file names (lower case globs) that are deploy / CI files wherever they lie and are not
#: built in (config.DEPLOY_CI_NAMES): proposed by their path
DEPLOY_FILES: tuple[str, ...] = ("procfile", "databricks.yml", "cloudbuild.y*ml")
#: generated files no feature edits by hand
GENERATED = ("*_pb2.py", "*_pb2_grpc.py", "*.g.cs", "*.designer.cs")
#: scripts a CI file calls (looked for in the CI files' text)
SCRIPT_SUFFIXES = (".sh", ".bash", ".ps1", ".cmd", ".bat")
YAML_SUFFIXES = (".yml", ".yaml")
#: the content looks of the lock candidates stay within this: files read, bytes of each
READ_MAX = 40
READ_BYTES = 8192

ROLE_NAMES = {"test": "Test", "lint": "Lint", "format": "Format", "typecheck": "Types"}


def _git(root: pathlib.Path, *args: str) -> str | None:
    """A git answer (stripped), None when git fails or is not there."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERSION_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


# ---------- tools ----------
def version_command(tokens: Sequence[str]) -> list[str]:
    """The command that asks the tool of a command line for its version: `python -m ruff
    check src` -> `python -m ruff --version`, `dotnet format ...` -> `dotnet --version`."""
    if len(tokens) >= 3 and tokens[1] == "-m":
        return [tokens[0], "-m", tokens[2], "--version"]
    return [tokens[0], "--version"]


def ask_version(cmd: Sequence[str], cwd: pathlib.Path) -> tuple[str, str]:
    """(`ok` | `missing` | `timeout` | `no answer`, the version line or the reason)."""
    code, out = run_command(list(cmd), cwd, timeout=VERSION_TIMEOUT)
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    if code == 0:
        return "ok", (lines[0] if lines else "")[:60]
    if code == TIMEOUT_CODE:
        return "timeout", f"no answer within {VERSION_TIMEOUT} s"
    if code == 127 or "No module named" in out:
        return "missing", ""
    return "no answer", f"`--version` ended with exit {code}"


def toml_list(tokens: Sequence[str]) -> str:
    return json.dumps(list(tokens))


def tool_rows(adapter: LanguageAdapter) -> list[dict[str, Any]]:
    """One row per tool: the test runner, then the gate commands. `command` is what framegate
    runs with today's config, except where framegate.toml does not settle it and the
    project's files point to another tool than framegate's default: then it is the project's
    one, with `default` (what framegate runs until it is set) and `set` (the framegate.toml
    lines)."""
    cfg = adapter.cfg
    detected = adapter.detected_tools()
    defaults = adapter.default_gate_commands()
    rows: list[dict[str, Any]] = []
    asks: list[list[str]] = []

    def row(role: str, text: str, source: str, ask: Sequence[str], **more: Any) -> None:
        asks.append(list(ask))
        rows.append({"role": role, "command": text, "source": source, **more})

    def agent_text(cmd: Sequence[str]) -> str:
        return command_text(adapter.agent_form(cmd))

    runner = adapter.test_runner()
    test_source = detected.get("test", ([], ""))[1] or (
        f"{CONFIG_FILE} [{cfg.language}]"
        if cfg.loaded_from_file and cfg.language_options.get(cfg.language)
        else "framegate's default: the project's files name no test runner"
    )
    row("test", adapter.test_command(), test_source, [*runner, "--version"] if runner else [])
    for key in GATE_COMMAND_KEYS:
        default = defaults.get(key)
        found = detected.get(key)
        if key in cfg.gate_commands:
            cmd = cfg.gate_commands[key]
            where = f"{CONFIG_FILE} [gates.commands]"
            if not cmd:
                rows.append({"role": key, "command": "", "source": where, "answers": "off"})
                asks.append([])
                continue
            row(key, agent_text(cmd), where, version_command(adapter.resolve_command(cmd)))
        elif found and (not default or adapter.agent_form(found[0]) != adapter.agent_form(default)):
            lines = [f"{key} = {toml_list(found[0])}"]
            if key == "format" and "format_fix" in detected:
                lines.append(f"format_fix = {toml_list(detected['format_fix'][0])}")
            row(
                key,
                agent_text(found[0]),
                found[1],
                version_command(adapter.resolve_command(found[0])),
                default=agent_text(default) if default else "",
                set="[gates.commands] " + ", ".join(lines),
            )
        elif default:
            where = found[1] if found else "framegate's default: the project's files do not name it"
            row(
                key,
                agent_text(default),
                where,
                version_command(adapter.resolve_command(default)),
                named=bool(found),
            )
    unique = [list(x) for x in dict.fromkeys(tuple(a) for a in asks if a)]
    with ThreadPoolExecutor(max_workers=max(1, len(unique))) as pool:
        answers = dict(
            zip(
                (tuple(u) for u in unique),
                pool.map(lambda c: ask_version(c, adapter.root), unique),
                strict=True,
            )
        )
    for r, ask in zip(rows, asks, strict=True):
        if ask:
            r["answers"], r["version"] = answers[tuple(ask)]
    return rows


# ---------- git ----------
def base_branch(root: pathlib.Path) -> tuple[str, str]:
    """(branch, where the guess comes from): what `origin/HEAD` points to, else a branch
    with a usual name, else the current branch."""
    head = _git(root, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
    if head and "/" in head:
        return head.split("/", 1)[1], "origin/HEAD"
    refs = (_git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads") or "").split()
    remote = (_git(root, "for-each-ref", "--format=%(refname:short)", "refs/remotes") or "").split()
    names = set(refs) | {r.split("/", 1)[1] for r in remote if "/" in r}
    for name in BASE_NAMES:
        if name in names:
            return name, "a branch of that name; origin/HEAD is not set"
    current = _git(root, "branch", "--show-current")
    if current:
        return current, "the current branch; origin/HEAD is not set"
    return "", "no branch found"


def remote_facts(root: pathlib.Path) -> dict[str, Any] | None:
    """The remote things are pushed to (`origin`, else the first), its host, and how many
    people wrote the last commits. None = the repository has no remote."""
    remotes = (_git(root, "remote") or "").split()
    if not remotes:
        return None
    name = "origin" if "origin" in remotes else remotes[0]
    host = hosts.parse_remote(_git(root, "remote", "get-url", name) or "")
    return {
        "name": name,
        "host": host.name if host.kind != hosts.OTHER else "another host",
        "web": host.web,
    }


def author_count(root: pathlib.Path) -> int:
    log = _git(root, "log", f"-{AUTHOR_COMMITS}", "--format=%ae") or ""
    return len({x.strip().lower() for x in log.splitlines() if x.strip()})


def tracked_files(root: pathlib.Path) -> list[str]:
    """The tracked files; outside git (or before the first commit): the root's entries."""
    listed = _git(root, "ls-files")
    if listed:
        return listed.splitlines()
    return [f"{p.name}/" if p.is_dir() else p.name for p in sorted(root.iterdir())]


def builtin_found(
    cfg: Config, files: Sequence[str], source_suffixes: tuple[str, ...] = ()
) -> list[str]:
    """The built-in deploy / CI paths that exist here, by name: a built-in folder as
    `name/`, a built-in file name by the path of each file that has it. A source file of
    the language (`source_suffixes`: `dockerfile.py`) is none."""
    out: list[str] = []
    builtin = cfg.deploy_ci_paths()
    for f in files:
        if not path_matches(f, builtin) or not maybe_pipeline_file(cfg, f):
            continue
        if source_suffixes and f.endswith(source_suffixes):
            continue
        folder = next((d for d in DEPLOY_CI_FOLDERS if f.startswith(d)), "")
        if (folder or f) not in out:
            out.append(folder or f)
    return out


def _head(root: pathlib.Path, rel: str) -> str:
    """The first bytes of a file ("" when it cannot be read)."""
    try:
        with open(root / rel, "rb") as fh:
            return fh.read(READ_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _is_manifest(text: str) -> bool:
    """A Kubernetes manifest: a yml document with `apiVersion:` and `kind:` at the top level."""
    return bool(re.search(r"^apiVersion:", text, re.M) and re.search(r"^kind:", text, re.M))


def ci_scripts(root: pathlib.Path, files: Sequence[str], ci_files: Sequence[str]) -> list[str]:
    """Tracked scripts (SCRIPT_SUFFIXES) that a CI file names by their path."""
    scripts = [f for f in files if f.lower().endswith(SCRIPT_SUFFIXES)]
    if not scripts:
        return []
    out: list[str] = []
    for rel in ci_files:
        text = _head(root, rel).replace("\\", "/")
        out += [s for s in scripts if s not in out and s in text]
    return out


def lock_candidates(
    files: Sequence[str],
    locked: Sequence[str],
    root: pathlib.Path | None = None,
    keep: Sequence[str] = (),
    ci_files: Sequence[str] = (),
) -> dict[str, str]:
    """Deploy / CI / generated paths that are not locked yet -> why each is proposed
    (`[protected] extra` entries: a folder as `name/`, a file by its path, generated files
    as a glob). By name: the top-level folders of DEPLOY_DIRS, the files of DEPLOY_FILES, a
    folder with a Dockerfile, a Helm `Chart.yaml` or Terraform `*.tf` files. With `root`
    also by a look into at most READ_MAX files (the first READ_BYTES of each): folders with
    Kubernetes manifests, scripts that `ci_files` (the CI definitions) name. `keep`: folders
    never proposed as a whole (the source, test and spec folders, and what holds them):
    there the file is."""
    found: dict[str, str] = {}
    is_locked = partial(path_matches, entries=list(locked))

    def overlaps(folder: str) -> bool:
        return any(k.startswith(folder) or folder.startswith(k) for k in keep)

    def add(cand: str, why: str) -> None:
        if cand and cand not in found and not is_locked(cand):
            found[cand] = why

    def add_folder(f: str, why: str, or_file: bool = True) -> None:
        """The folder that holds `f`; the file itself at the root or in a kept folder."""
        folder = f.rsplit("/", 1)[0] + "/" if "/" in f else ""
        if folder and not overlaps(folder):
            add(folder, why)
        elif or_file:
            add(f, why)

    reads = 0
    yml: list[str] = []
    for f in files:
        parts = f.split("/")
        name = parts[-1].lower()
        if len(parts) > 1 and parts[0].lower() in DEPLOY_DIRS:
            add(parts[0] + "/", "a folder with a deploy / CI name")
        elif any(fnmatch.fnmatchcase(name, g) for g in DEPLOY_FILES):
            add(f, "a deploy / CI file by its name")
        elif name == "chart.yaml":
            add_folder(f, "a Helm chart (Chart.yaml)")
        elif name.endswith(".tf"):
            add_folder(f, "Terraform files (*.tf)")
        elif fnmatch.fnmatchcase(name, "dockerfile*") or name.endswith(".dockerfile"):
            # the Dockerfile itself is built in; what lies next to it is the image's too
            add_folder(f, "holds a Dockerfile", or_file=False)
        elif generated := next((g for g in GENERATED if fnmatch.fnmatchcase(name, g)), ""):
            add(generated, "generated files")
        elif name.endswith(YAML_SUFFIXES):
            yml.append(f)
    if root is None:
        return _outermost(found)
    # shallow files first: a manifest folder is usually near the root
    for f in sorted(yml, key=lambda x: (x.count("/"), x)):
        if reads >= READ_MAX:
            break
        if is_locked(f) or any(f.startswith(c) for c in found if c.endswith("/")):
            continue
        reads += 1
        if _is_manifest(_head(root, f)):
            add_folder(f, "Kubernetes manifests (yml with apiVersion and kind)")
    ci = [f for f in ci_files if _is_ci_file(f)][: max(0, READ_MAX - reads)]
    for script in ci_scripts(root, files, ci):
        if not any(script.startswith(c) for c in found if c.endswith("/")):
            add(script, "a script a CI file calls")
    return _outermost(found)


def _is_ci_file(f: str) -> bool:
    """A CI definition whose text may name scripts (not a Dockerfile, not an ignore file)."""
    name = f.rsplit("/", 1)[-1].lower()
    return name.endswith(YAML_SUFFIXES) or name == "jenkinsfile"


def _outermost(found: dict[str, str]) -> dict[str, str]:
    """Without the folders and files that lie inside another proposed folder."""
    folders = [c for c in found if c.endswith("/")]
    return {
        c: why for c, why in found.items() if not any(c != d and c.startswith(d) for d in folders)
    }


# ---------- the whole probe ----------
def load(start: pathlib.Path) -> tuple[Config, LanguageAdapter | None, str]:
    """(config, adapter, where the language comes from). Without a framegate.toml: the
    detected language with the guessed folders; no adapter when the language is unknown or
    the adapter cannot start yet (`generic` without its section)."""
    root = find_root(start)
    if root is not None:
        cfg = load_config(root)
        source = CONFIG_FILE
    else:
        lang = detect_language(start)
        if lang is None:
            return parse_config(start, {}), None, "not detected"
        from framegate.adapters import adapter_class

        src, tests = adapter_class(lang).guess_layout(start)
        cfg = parse_config(start, {"project": {"language": lang}, "paths": {"src": src}})
        cfg.paths.tests = tests
        source = "detected from the project's files"
    try:
        return cfg, get_adapter(cfg), source
    except ConfigError as e:
        return cfg, None, f"{source}; the adapter is not configured yet: {e}"


def collect(start: pathlib.Path | None = None) -> dict[str, Any]:
    start = (start or pathlib.Path(os.getcwd())).resolve()
    cfg, adapter, lang_source = load(start)
    root, has_config = cfg.root, cfg.loaded_from_file
    known = adapter is not None or has_config
    data: dict[str, Any] = {
        "root": str(root),
        "config": has_config,
        "language": {"name": cfg.language if known else "", "source": lang_source},
    }
    data["interpreter"] = None
    data["tools"] = []
    facts: dict[str, Any] = {}
    if adapter is not None:
        try:
            named = adapter.interpreter()
            facts = adapter.env_facts() if named else {}
            if named:
                data["interpreter"] = {
                    "path": named[0],
                    "source": named[1],
                    "answers": "ok" if facts.get("version") else "missing",
                    "version": facts.get("version", ""),
                }
            data["tools"] = tool_rows(adapter)
        except ConfigError as e:
            data["tools_error"] = str(e)
    data["src"] = [{"path": p, "exists": (root / p).is_dir()} for p in cfg.src_roots]
    data["tests"] = {"path": cfg.paths.tests, "exists": (root / cfg.paths.tests).is_dir()}
    data["layout_source"] = f"{CONFIG_FILE} [paths]" if has_config else "guessed from the folders"

    guess, guess_source = base_branch(root)
    data["base"] = (
        {"branch": cfg.git_base, "source": f"{CONFIG_FILE} [git] base", "guess": guess}
        if has_config
        else {"branch": guess, "source": guess_source, "guess": guess}
    )
    remote = remote_facts(root)
    authors = author_count(root)
    data["remote"] = remote
    data["authors"] = authors
    data["state_branch"] = {
        "value": cfg.state_branch,
        "suggested": bool(remote) and authors > 1,
    }

    files = tracked_files(root)
    locked = protected_paths(adapter) if adapter is not None else core_protected(cfg)
    suffixes = tuple(adapter.source_suffixes) if adapter is not None else ()
    builtin = builtin_found(cfg, files, suffixes)
    data["locked"] = [*builtin, *cfg.protected_extra]
    data["locked_builtin"] = builtin
    data["builtin_off"] = list(cfg.protected_builtin_off)
    keep = [p.rstrip("/") + "/" for p in (*cfg.src_roots, cfg.paths.tests, cfg.paths.specs)]
    ci_files = [f for f in files if maybe_pipeline_file(cfg, f)]
    why = lock_candidates(files, locked, root, keep, ci_files)
    data["lock_candidates"] = list(why)
    data["lock_why"] = why

    data["editable"] = {
        "packages": facts.get("editable", []),
        "refresh": cfg.env_refresh or facts.get("refresh", []),
        "configured": bool(cfg.env_refresh),
    }
    data["references"] = sorted(cfg.references)
    data["files"] = {
        CONFIG_FILE: has_config,
        cfg.knowledge: (root / cfg.knowledge).is_file(),
        cfg.code_map: (root / cfg.code_map).is_file(),
    }
    return data


def _listed(items: Sequence[str]) -> str:
    more = f", and {len(items) - LIST_MAX} more" if len(items) > LIST_MAX else ""
    return ", ".join(items[:LIST_MAX]) + more


def _answer(row: dict[str, Any]) -> str:
    state = row.get("answers", "")
    if state == "ok":
        return f"[OK: {row.get('version') or 'answers'}]"
    if state == "missing":
        return "[MISSING]"
    return f"[{state.upper()}: {row.get('version', '')}]" if state else "[not asked]"


def render(data: dict[str, Any]) -> list[str]:
    """The probe as plain lines: `<Label>: <value> [<answer>] (<where it comes from>)`."""
    config = data["config"]
    head = "with framegate.toml" if config else "no framegate.toml yet: proposals"
    out = [f"framegate probe: {data['root']} ({head})"]
    lang = data["language"]
    out.append(f"Language: {lang['name'] or 'unknown'} ({lang['source']})")
    interp = data["interpreter"]
    if interp:
        out.append(f"Interpreter: {interp['path']} {_answer(interp)} ({interp['source']})")
    elif lang["name"]:
        out.append("Interpreter: none to name for this language")
    if data.get("tools_error"):
        out.append(f"Tools: not probed: {data['tools_error']}")
    for row in data["tools"]:
        label = ROLE_NAMES.get(row["role"], GATE_NAMES.get(row["role"], row["role"]).capitalize())
        if row.get("answers") == "off":
            out.append(f"{label}: off ({row['source']})")
            continue
        out.append(f"{label}: {row['command']} {_answer(row)} ({row['source']})")
        if row.get("set"):
            now = f"framegate's default is `{row['default']}`" if row["default"] else "no default"
            out.append(f"  {now}; to use the project's tool set {row['set']}")
    marks = [p["path"] + ("" if p["exists"] else " (missing)") for p in data["src"]]
    out.append(f"Source folders: {', '.join(marks)} ({data['layout_source']})")
    tests = data["tests"]
    out.append(
        f"Test folder: {tests['path']}{'' if tests['exists'] else ' (missing)'} "
        f"({data['layout_source']})"
    )
    base = data["base"]
    differs = config and base["guess"] and base["guess"] != base["branch"].split("/")[-1]
    note = f"; origin/HEAD or the usual name says {base['guess']}" if differs else ""
    out.append(f"Base branch: {base['branch'] or 'unknown'} ({base['source']}{note})")
    remote, authors = data["remote"], data["authors"]
    people = f"{authors} author address(es) in the last {AUTHOR_COMMITS} commits"
    if remote:
        web = f", {remote['web']}" if remote["web"] else ""
        out.append(f"Remote: {remote['name']} on {remote['host']}{web}; {people}")
    else:
        out.append(f"Remote: none; {people}")
    state = data["state_branch"]
    if state["value"]:
        out.append(f"State branch: {state['value']} ({CONFIG_FILE} [git] state_branch)")
    else:
        why = (
            "a remote and more than one author address"
            if state["suggested"]
            else "a remote, one author address"
            if remote
            else "no remote"
        )
        out.append(f"State branch: off; suggested: {'yes' if state['suggested'] else 'no'} ({why})")
    extra = [e for e in data["locked"] if e not in data["locked_builtin"]]
    parts = [
        f"built in: {_listed(data['locked_builtin'])}" if data["locked_builtin"] else "",
        f"[protected] extra: {_listed(extra)}" if extra else "",
    ]
    out.append(f"Locked deploy / CI paths: {'; '.join(p for p in parts if p) or 'none found'}")
    if data["builtin_off"]:
        out.append(f"  taken off ([protected] builtin_off): {', '.join(data['builtin_off'])}")
    why = data["lock_why"]
    out.append(
        "Lock candidates ([protected] extra): "
        + (_listed([f"{c} ({why[c]})" for c in data["lock_candidates"]]) or "none")
    )
    edit = data["editable"]
    if edit["configured"]:
        out.append(f"Reinstall ([env] refresh): {toml_list(edit['refresh'])} ({CONFIG_FILE})")
    elif edit["packages"]:
        out.append(
            f"Reinstall ([env] refresh): {toml_list(edit['refresh'])} (candidate: "
            f"{', '.join(edit['packages'])} is installed editable in the interpreter)"
        )
    else:
        out.append("Reinstall ([env] refresh): none (the project is not installed editable)")
    out.append(f"References: {', '.join(data['references']) or 'none'}")
    out.append(
        "Files: "
        + ", ".join(
            f"{name} {'exists' if there else 'missing'}" for name, there in data["files"].items()
        )
    )
    return out


def run(
    as_json: bool = False,
    start: pathlib.Path | None = None,
    echo: Callable[[str], None] = print,
) -> int:
    data = collect(start)
    if as_json:
        echo(json.dumps(data, indent=1))
    else:
        for line in render(data):
            echo(line)
    return 0
