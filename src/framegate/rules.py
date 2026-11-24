"""The project's own CI rules as gate checks: `[checks] rules` (commands) and `[checks]
rules_text` (a command that prints the rules as text).

- Every step gate and the final gate run each rule command as a check ("project rule:
  <command>"). A failure goes back to the implementer like lint output. `{files}` in a
  command is the task's changed source and test files; without it the command runs as
  written.
- Debt is not the task's: a finding counts only on a line the task wrote (red_tree, as for
  lint), and an output line the command already printed before the task (the baseline
  recorded at the spec approval) is left out.
- The implementer's and the simplifier's task gets one generated block: the commands, the
  printed rules and the linter's active rule selection. No handwritten guideline text.
- The setup proposes both from the repository (detect): a rules script, the CI step that
  calls it, and `--rules` when the script knows it.
"""

from __future__ import annotations

import functools
import json
import pathlib
import re
import shlex
import sys
from collections.abc import Sequence
from typing import Any

from framegate import red_tree
from framegate.adapters.base import Check, CheckResult, LanguageAdapter, run_command
from framegate.config import Config

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

#: the start of every rule check's name (final repair: the implementer owns it)
NAME = "project rule"
BASELINE_FILE = "rules_baseline.json"
#: most lines of the printed rules in a task
TEXT_LINES = 60
BEFORE_NOTE = "printed the same before the task (left out)"
#: where a rules script lies, and how it is named
_SCRIPT = re.compile(
    r"^(?:ci|scripts?|tools?|build|\.github/scripts)/(?:.*/)?"
    r"(?:check|lint|verify|validate|enforce)[_-]?\w*rules?\w*\.(?:py|sh|ps1)$",
    re.I,
)
_RUN_PREFIX = re.compile(r"^\s*(?:-\s*)?(?:run|script|bash|pwsh|powershell)\s*:\s*[|>]?\s*", re.I)


def split(command: str) -> list[str]:
    return shlex.split(command, posix=True)


def name(command: str) -> str:
    text = " ".join(command.split())
    return f"{NAME}: {text if len(text) <= 60 else text[:57] + '...'}"


def _task_files(adapter: LanguageAdapter, feature: str) -> set[str] | None:
    from framegate.gates import task_files

    return task_files(adapter, feature) if feature else None


def _argv(adapter: LanguageAdapter, command: str, files: Sequence[str]) -> list[str]:
    out: list[str] = []
    for arg in split(command):
        if arg == "{files}":
            out += files
        else:
            out += adapter.resolve_command([arg])
    return adapter.resolve_command(out[:1]) + out[1:] if out else out


def run_rule(adapter: LanguageAdapter, command: str, feature: str = "") -> CheckResult:
    """One rule command, raw: PASS / FAIL with its output. A command that cannot start or
    runs out of time is a tool failure (no verdict on the code)."""
    cfg = adapter.cfg
    files: list[str] = []
    if "{files}" in split(command):
        changed = _task_files(adapter, feature) or set()
        roots = (*cfg.src_roots, cfg.paths.tests)
        files = sorted(
            f
            for f in changed
            if f.startswith(tuple(r.rstrip("/") + "/" for r in roots)) and (cfg.root / f).is_file()
        )
        if not files:
            return CheckResult("PASS", "", "no changed file to check")
    code, out = run_command(
        _argv(adapter, command, files), cfg.root, env=adapter.tool_env(), timeout=cfg.extra_timeout
    )
    if code == 0:
        return CheckResult("PASS", out)
    return CheckResult("FAIL", out, tool_failure=code in (124, 127))


def _lines(text: str) -> list[str]:
    return [x.strip() for x in text.splitlines() if x.strip()]


def record_baseline(adapter: LanguageAdapter, feature: str) -> dict[str, list[str]]:
    """At the spec approval, once per task: what each rule command prints on a failure
    today (command -> its output lines; a passing command has none)."""
    cfg = adapter.cfg
    path = cfg.feature_out(feature) / BASELINE_FILE
    if not cfg.rule_commands or path.is_file():
        return baseline(cfg, feature)
    found: dict[str, list[str]] = {}
    for command in cfg.rule_commands:
        res = run_rule(adapter, command.replace("{files}", "").strip())
        found[command] = _lines(res.output) if res.status == "FAIL" else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(found, indent=1), encoding="utf-8")
    return found


def baseline(cfg: Config, feature: str) -> dict[str, list[str]]:
    try:
        data = json.loads((cfg.feature_out(feature) / BASELINE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def without_before(res: CheckResult, before: Sequence[str]) -> CheckResult:
    """A failing rule check without the output lines it printed before the task: PASS when
    none is left."""
    if res.status != "FAIL" or res.tool_failure or not before:
        return res
    known = set(before)
    kept = [x for x in res.output.splitlines() if x.strip() and x.strip() not in known]
    dropped = len(_lines(res.output)) - len(kept)
    if not dropped:
        return res
    note = f"{BEFORE_NOTE}: {dropped} line(s)"
    if not kept:
        return CheckResult("PASS", res.output, note)
    return CheckResult("FAIL", "\n".join(kept) + f"\n\n({note})", note)


def scoped(adapter: LanguageAdapter, command: str, feature: str) -> CheckResult:
    """The rule command judged by what the task did: lines from before the task are left
    out, and a finding that names a file counts only on a line the task wrote."""
    from framegate.gates import task_lines

    res = without_before(
        run_rule(adapter, command, feature), baseline(adapter.cfg, feature).get(command) or []
    )
    changed = _task_files(adapter, feature)
    if changed is None:
        return res
    return red_tree.in_changed(adapter, changed, res, task_lines(adapter, feature))


def checks(adapter: LanguageAdapter, feature: str) -> list[Check]:
    """One check per `[checks] rules` command (no feature: the raw run, for doctor)."""
    run = scoped if feature else run_rule
    return [
        Check(name(c), functools.partial(run, adapter, c, feature))
        for c in adapter.cfg.rule_commands
    ]


# ---------- the generated block of a task ----------
def printed_rules(adapter: LanguageAdapter) -> list[str]:
    """What `[checks] rules_text` prints ([] = none configured, or the command failed)."""
    cfg = adapter.cfg
    if not cfg.rules_text_command:
        return []
    code, out = run_command(
        _argv(adapter, cfg.rules_text_command, []), cfg.root, env=adapter.tool_env(), timeout=120
    )
    lines = [x.rstrip() for x in out.splitlines() if x.strip()] if code == 0 else []
    more = [f"... {len(lines) - TEXT_LINES} more: `{cfg.rules_text_command}`"]
    return [*lines[:TEXT_LINES], *more] if len(lines) > TEXT_LINES else lines


def _ruff_table(root: pathlib.Path) -> tuple[dict[str, Any], str]:
    for rel in ("ruff.toml", ".ruff.toml", "pyproject.toml"):
        path = root / rel
        if not path.is_file():
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        table = data.get("tool", {}).get("ruff") if rel == "pyproject.toml" else data
        if isinstance(table, dict):
            return table, rel
    return {}, ""


def lint_selection(adapter: LanguageAdapter) -> str:
    """The linter's active rule selection in one line ("" = not known). Read for ruff from
    its config file; any other linter says its rules through its findings."""
    cfg = adapter.cfg
    lint = cfg.gate_commands.get("lint", adapter.default_gate_commands().get("lint")) or []
    if not any("ruff" in pathlib.PurePath(a).name for a in lint):
        return ""
    table, rel = _ruff_table(cfg.root)
    found = table.get("lint")
    inner: dict[str, Any] = found if isinstance(found, dict) else {}
    parts = []
    for key in ("select", "extend-select", "ignore"):
        value = inner.get(key, table.get(key))
        if isinstance(value, list) and value:
            parts.append(f"{key} {', '.join(map(str, value))}")
    if table.get("line-length"):
        parts.append(f"line length {table['line-length']}")
    return f"ruff ({rel}): " + "; ".join(parts) if parts else ""


def text_block(adapter: LanguageAdapter) -> str:
    """The block for a task that writes source ("" = the project has no rule commands and
    no known lint selection). Generated on each run from the commands and the config."""
    cfg = adapter.cfg
    lint = lint_selection(adapter)
    if not cfg.rule_commands and not cfg.rules_text_command and not lint:
        return ""
    out = ["## Project rules (generated; the step gate checks them, a failure comes back to you)"]
    if cfg.rule_commands:
        out.append(
            "- Rule checks of this project, run by the gate after you stop (not yours to run): "
            + ", ".join(f"`{c}`" for c in cfg.rule_commands)
        )
    if lint:
        out.append(f"- Lint rules: {lint}")
    rules = printed_rules(adapter)
    if rules:
        out += [
            f"- The rules, as `{cfg.rules_text_command}` prints them:",
            "",
            "~~~",
            *rules,
            "~~~",
        ]
    return "\n".join(out)


# ---------- the setup's proposal ----------
def _python_form(script: str) -> str:
    return f"python {script}" if script.endswith(".py") else script


def detect(
    root: pathlib.Path, files: Sequence[str], ci_files: Sequence[str] = ()
) -> dict[str, Any]:
    """Rule commands the repository suggests: {"rules": [...], "rules_text": "...", "why":
    "..."}; {} = none. A script named like a rules check under a CI / scripts folder; the
    command is the line of a CI file that calls it, else the script run plainly. A script
    whose text knows `--rules` also gives `rules_text`."""
    scripts = [f for f in files if _SCRIPT.match(f)]
    if not scripts:
        return {}
    commands: list[str] = []
    why: list[str] = []
    text_cmd = ""
    for script in scripts:
        found = ""
        for ci in ci_files:
            try:
                text = (root / ci).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            line = next((x for x in text.splitlines() if script in x.replace("\\", "/")), "")
            if line:
                found = _RUN_PREFIX.sub("", line).strip().strip("'\"")
                why.append(f"{ci} calls {script}")
                break
        if not found:
            found = _python_form(script)
            why.append(f"{script} (run without arguments: check them)")
        if "--rules" in found.split():
            continue
        if found not in commands:
            commands.append(found)
        try:
            body = (root / script).read_text(encoding="utf-8", errors="replace")
        except OSError:
            body = ""
        if not text_cmd and "--rules" in body:
            text_cmd = f"{_python_form(script)} --rules"
    if not commands:
        return {}
    return {"rules": commands, "rules_text": text_cmd, "why": "; ".join(why)}
