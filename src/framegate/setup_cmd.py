"""`framegate setup --plan` and `framegate setup --apply`: the setup as two commands.

--plan   installs the workflow tools, runs the survey (once, without a framegate.toml),
         probes, picks the adapter and prints one confirmation table with the decisions it
         needs: finished text the chat shows as it is. The plan is kept in
         {out}/setup_plan.json.
--apply  writes the confirmed plan (`--set row=value` for each correction, `--red
         test=reason` for each red test the user agreed to list): `framegate init` when
         there is no framegate.toml, the keys that differ from the defaults, the tools the
         gate commands name, `framegate doctor --tree --brief`, the state branch once the
         tree is settled, then what to commit and what is left. It can run again with more
         `--set` / `--red`: every run starts from the kept plan.

Nothing here decides for the user: a row is a proposal from the project's own files (probe),
and only `ok` or a `--set` makes it a line of framegate.toml.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import sys
from collections.abc import Callable, Sequence
from typing import Any

from framegate import doctor, probe, publish, risk, rules, scaffold, survey
from framegate.adapters.base import LanguageAdapter
from framegate.checks.frame_diff import maybe_pipeline_file
from framegate.config import (
    CONFIG_FILE,
    Config,
    ConfigError,
    drop_machine_files,
    find_root,
    load_config,
    parse_config,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

Echo = Callable[[str], None]
PLAN_FILE = "setup_plan.json"
#: the rows of the table, in their fixed order: key -> what
ROWS = {
    "language": "Language / adapter",
    "interpreter": "Interpreter",
    "test": "Test command",
    "lint": "Lint",
    "format": "Format",
    "typecheck": "Types",
    "folders": "Source / test folders",
    "base": "Base branch",
    "state_branch": "Shared repository: framegate's files on their own branch",
    "locked": "Locked deploy / CI paths",
    "refresh": "Reinstall when a spec changes packaging",
    "references": "Reference projects",
    "rules": "Project rule checks (every step gate)",
    "mutation": "Mutation testing",
}
#: `--set` keys that are no row
EXTRA_KEYS = ("rules_text", "builtin_off", "machine_files", "project_folders", "metrics")
#: tools `framegate install-tools` brings once the gate commands name them
INSTALLED_LATER = ("ruff", "mypy", "black")
_HEADER = re.compile(r"^(# )?\[([\w.]+)\]\s*(#.*)?$")


class SetupError(Exception):
    pass


# ---------- framegate.toml: one key at a time ----------
def literal(value: Any) -> str:
    """A TOML literal for a bool, a string, a list of strings or a list of tables."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
        rows = [", ".join(f"{k} = {json.dumps(v)}" for k, v in x.items()) for x in value]
        return "[\n" + "".join(f"  {{ {r} }},\n" for r in rows) + "]"
    return json.dumps(value, ensure_ascii=False)


def _depth(text: str) -> int:
    code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', text.split(" #")[0])
    return code.count("[") + code.count("{") - code.count("]") - code.count("}")


def set_key(text: str, section: str, key: str, value: str) -> str:
    """framegate.toml with `key = value` in `[section]`: an existing line of the key
    (commented or not) is replaced, a commented section header is switched on, a missing
    section is added at the end. Comments stay."""
    lines = text.splitlines()
    heads = [(i, m) for i, x in enumerate(lines) if (m := _HEADER.match(x))]
    at = next((n for n, (_, m) in enumerate(heads) if m.group(2) == section), None)
    new = f"{key} = {value}".splitlines()
    if at is None:
        return "\n".join([*lines, "", f"[{section}]", *new]) + "\n"
    start, m = heads[at]
    lines[start] = f"[{section}]" + (f"  {m.group(3)}" if m.group(3) else "")
    end = heads[at + 1][0] if at + 1 < len(heads) else len(lines)
    pattern = re.compile(rf"^(# )?{re.escape(key)}\s*=")
    live = next((i for i in range(start + 1, end) if lines[i].startswith(f"{key} ")), None)
    hit = live or next((i for i in range(start + 1, end) if pattern.match(lines[i])), None)
    if hit is None:
        lines[start + 1 : start + 1] = new
        return "\n".join(lines) + "\n"
    last = hit
    if not lines[hit].startswith("#"):  # a value over several lines goes as a whole
        depth = _depth(lines[hit].split("=", 1)[1])
        while depth > 0 and last + 1 < len(lines):
            last += 1
            depth += _depth(lines[last])
    lines[hit : last + 1] = new
    return "\n".join(lines) + "\n"


# ---------- the plan ----------
def _answer(row: dict[str, Any]) -> str:
    state = row.get("answers", "")
    if state == "ok":
        return f"OK ({row.get('version') or 'answers'})"
    if state == "missing":
        tool = row.get("command", "")
        later = any(t in tool for t in INSTALLED_LATER)
        return "MISSING, installed by `setup --apply`" if later else "MISSING"
    return state.upper() if state else ""


def _has_code(cfg: Config, adapter: LanguageAdapter | None) -> bool:
    if adapter is None:
        return False
    try:
        return bool(survey.code_files(cfg, adapter))
    except OSError:
        return True


def _row(key: str, value: str, source: str = "", answers: str = "") -> dict[str, Any]:
    return {"key": key, "value": value, "from": source, "answers": answers, "edits": []}


def build(start: pathlib.Path) -> dict[str, Any]:
    """The plan from the probe: rows (value, where it comes from, the framegate.toml keys
    an `ok` writes), decisions the repository cannot answer, and notes."""
    data = probe.collect(start)
    cfg, adapter, _ = probe.load(start)
    has_config = bool(data["config"])
    rows: list[dict[str, Any]] = []
    decisions: list[str] = []
    lang = data["language"]["name"] or "generic"
    rows.append(_row("language", f"{lang} / {lang}", data["language"]["source"]))
    if not data["language"]["name"]:
        decisions.append(
            "Blocker: the language was not detected. The generic adapter needs its "
            "[generic] section (test commands, tag pattern, stub marker): `framegate init "
            "--language generic` writes a commented example to fill in."
        )

    interp = data.get("interpreter")
    if interp and "framegate's own interpreter" in str(interp.get("source")):
        rows.append(_row("interpreter", "none found", interp["source"]))
        decisions.append(
            "Blocker: which interpreter does the project run in? (row 2: the path, e.g. "
            ".venv/Scripts/python.exe)"
        )
    elif interp:
        rows.append(_row("interpreter", interp["path"], interp["source"], _answer(interp)))
    else:
        rows.append(_row("interpreter", "none to name for this language"))

    tools = {r["role"]: r for r in data["tools"]}
    test = tools.get("test")
    rows.append(
        _row("test", test["command"], test["source"], _answer(test))
        if test
        else _row("test", "not probed", str(data.get("tools_error") or ""))
    )
    if test and "names no test runner" in test["source"] and not _has_code(cfg, adapter):
        pass  # a project with no code yet: framegate's default runner
    elif test and test.get("answers") == "missing":
        decisions.append(
            "Blocker: the test runner does not answer in this interpreter. Install it, or "
            "name the test command (row 3)."
        )
    for key in ("lint", "format", "typecheck"):
        r = tools.get(key)
        if r is None:
            rows.append(_row(key, "off", "no default for this language"))
            continue
        if r.get("answers") == "off":
            rows.append(_row(key, "off", r["source"]))
            continue
        row = _row(key, r["command"], r["source"], _answer(r))
        detected = adapter.detected_tools() if adapter is not None else {}
        if r.get("set") and key in detected:
            row["edits"].append(["gates.commands", key, list(detected[key][0])])
            if key == "format" and "format_fix" in detected:
                row["edits"].append(["gates.commands", "format_fix", detected["format_fix"][0]])
        elif not has_config and r.get("named") is False and _has_code(cfg, adapter):
            # agents are never told a command the project does not use
            row.update(value="off", answers="")
            row["from"] = "the project names no such tool"
            row["edits"].append(["gates.commands", key, []])
        if row["answers"] == "MISSING":
            decisions.append(
                f"Row {len(rows) + 1}: `{r['command']}` is MISSING: how is it installed?"
            )
        rows.append(row)

    src = [p["path"] for p in data["src"]]
    tests = data["tests"]["path"]
    folders = _row("folders", f"{', '.join(src)} / {tests}", data["layout_source"])
    if not has_config:
        if src != ["src"]:
            folders["edits"].append(["paths", "src", src[0] if len(src) == 1 else src])
        if tests != "tests":
            folders["edits"].append(["paths", "tests", tests])
    rows.append(folders)
    mapped = survey.load(cfg) or {}
    for left in [mapped.get("uncovered") or []]:
        if left:
            decisions.append(
                f"{len(left)} code file(s) are under no folder ({', '.join(left[:5])}"
                + (" ..." if len(left) > 5 else "")
                + "): a source folder, or not the project's code? (default: add their "
                "folder to the source folders, row 7)"
            )

    base = data["base"]
    row = _row("base", base["branch"] or "unknown", base["source"])
    if not has_config and base["branch"] and base["branch"] != "main":
        row["edits"].append(["git", "base", base["branch"]])
    rows.append(row)

    state, remote = data["state_branch"], data["remote"]
    people = f"{data['authors']} author address(es)"
    where = f"remote on {remote['host']}, {people}" if remote else "no remote"
    if state["value"]:
        rows.append(_row("state_branch", f"yes, {state['value']}", f"{CONFIG_FILE} [git]"))
    else:
        short = (base["branch"] or "main").split("/")[-1]
        row = _row("state_branch", f"yes, framegate/{short}" if state["suggested"] else "no", where)
        if state["suggested"] and not has_config:
            row["edits"].append(["git", "state_branch", "framegate/{base}"])
        rows.append(row)

    locked = [*data["locked"], *data["lock_candidates"]]
    row = _row("locked", ", ".join(locked) or "none found", "probe")
    if data["lock_candidates"]:
        extra = [e for e in data["locked"] if e not in data["locked_builtin"]]
        row["edits"].append(["protected", "extra", [*extra, *data["lock_candidates"]]])
    rows.append(row)

    edit = data["editable"]
    if edit["configured"] or edit["packages"]:
        source = CONFIG_FILE if edit["configured"] else "editable install found"
        row = _row("refresh", " ".join(edit["refresh"]), source)
        if not edit["configured"]:
            row["edits"].append(["env", "refresh", list(edit["refresh"])])
        rows.append(row)
    else:
        rows.append(_row("refresh", "none", "the project is not installed editable"))
    rows.append(_row("references", ", ".join(data["references"]) or "none"))

    if cfg.rule_commands:
        rows.append(_row("rules", "; ".join(cfg.rule_commands), f"{CONFIG_FILE} [checks]"))
    else:
        files = drop_machine_files(cfg, probe.tracked_files(cfg.root))
        # any yml may be a CI definition (a folder such as ci/ is no built-in name)
        ci = [f for f in files if maybe_pipeline_file(cfg, f) or f.endswith(probe.YAML_SUFFIXES)]
        found = rules.detect(cfg.root, files, ci[: probe.READ_MAX])
        row = _row("rules", "; ".join(found.get("rules") or []) or "none", found.get("why", ""))
        if found:
            row["edits"].append(["checks", "rules", found["rules"]])
            if found.get("rules_text"):
                row["edits"].append(["checks", "rules_text", found["rules_text"]])
                row["value"] += f" (rules printed by `{found['rules_text']}`)"
        rows.append(row)

    rows.append(_mutation_row(cfg, adapter, len(rows) + 1, decisions))
    notes = [
        "**Machine files, ignored by framegate** - "
        + (", ".join(data["machine_files"]) or "none tracked"),
        '**Never approved without you** (only with `[gates] final = "auto"`; default: every '
        f"final gate is yours) - {risk.never_auto_words(cfg)}",
        *(f"**Environment** - {line}" for line in data.get("env_gaps") or []),
        "Defaults taken for everything else (which tests the step checks leave out, coverage "
        "and mutation thresholds, risk limits, the simplify pass): change them later in "
        f"{CONFIG_FILE}.",
    ]
    return {
        "root": data["root"],
        "config": has_config,
        "language": lang,
        "rows": rows,
        "decisions": decisions,
        "notes": notes,
        "files": data["files"],
        "set": {},
        "red": {},
    }


def _mutation_row(
    cfg: Config, adapter: LanguageAdapter | None, number: int, decisions: list[str]
) -> dict[str, Any]:
    if (cfg.mutation_tool or "").lower() == "none":
        return _row("mutation", "off", f"{CONFIG_FILE} [gates] mutation_tool")
    try:
        report = adapter.tool_report() if adapter is not None else []
    except Exception:  # the doctor check of --apply says it again
        report = []
    broken = [(n, d) for n, found, d in report if "(mutation)" in n and not found]
    if not broken:
        return _row("mutation", "on", "the adapter's tool", "checked by `setup --apply`")
    tool, why = broken[0]
    decisions.append(
        f"Row {number}: mutation testing cannot run here ({tool.replace(' (mutation)', '')}: "
        f"{why.rstrip('.')}). Make it run, or switch it off for this project: `{number}: off`."
    )
    return _row("mutation", "on", "the adapter's tool", "CANNOT RUN HERE")


def render(plan: dict[str, Any]) -> list[str]:
    """The plan as the text the chat shows."""
    out = [
        '**Please confirm the setup** - reply "ok", or the row and the change ("5: use black")',
        "",
        "| # | What | Value | From | Answers |",
        "|---|---|---|---|---|",
    ]
    for n, r in enumerate(plan["rows"], 1):
        cells = [str(n), ROWS[r["key"]], r["value"], r["from"], r["answers"]]
        out.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    out += ["", *plan["notes"]]
    if plan["decisions"]:
        out += ["", "**Decisions the repository cannot answer:**"]
        out += [f"- {d}" for d in plan["decisions"]]
    out += [
        "",
        "Then: `framegate setup --apply` (each correction as `--set <row>=<value>`, e.g. "
        '`--set "5=python -m black --check app tests"`, `--set 6=off`, `--set 9=no`).',
    ]
    return out


def plan_path(cfg: Config) -> pathlib.Path:
    return cfg.root / cfg.paths.out / PLAN_FILE


def _capture(run: Callable[[Echo], int]) -> tuple[int, list[str]]:
    lines: list[str] = []
    return run(lines.append), lines


def plan(start: pathlib.Path | None = None, echo: Echo = print, resurvey: bool = False) -> int:
    start = (start or pathlib.Path(os.getcwd())).resolve()
    root = find_root(start)
    if root is None:
        name, ref = publish.find_state(start, fetch=True)
        if ref:
            echo(
                f"This repository is set up already: framegate's files live on the branch "
                f"{name}. Start with /framegate:feature <name> (it brings them in)."
            )
            return 0
    # the tools first: without them nothing below can be checked
    code, missing = _capture(lambda e: scaffold.install_tools(True, start, e, update=True))
    todo = [x for x in missing if "missing" in x.lower() or "[update" in x.lower()]
    if code == 0 and todo:
        code, lines = _capture(lambda e: scaffold.install_tools(False, start, e, update=True))
        if code != 0:
            for line in lines:
                echo(line)
            echo("error: the workflow tools are not installed; the setup cannot be checked.")
            return 1
        echo("Installed the workflow tools into the project's environment.")
        for line in lines:
            if "CHANGED versions" in line:
                echo(line)
    cfg, adapter, _ = probe.load(start)
    if adapter is not None and (resurvey or (root is None and survey.load(cfg) is None)):
        facts = probe.render(probe.collect(cfg.root))
        try:
            mapped = survey.survey(cfg, adapter, facts)
            echo(f"Survey: {len(mapped['src'])} source folder(s) mapped from the code.")
        except (OSError, RuntimeError) as e:
            echo(f"Survey failed ({e}): the folders in row 7 are a guess from their names.")
    built = build(start)
    path = plan_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(built, indent=1), encoding="utf-8")
    for line in render(built):
        echo(line)
    return 0


# ---------- corrections ----------
def _command(value: str) -> list[str]:
    return [] if value.strip().lower() in ("off", "none", "") else shlex.split(value)


def _names(value: str) -> list[str]:
    if value.strip().lower() in ("none", ""):
        return []
    return [x.strip() for x in re.split(r"[,;]", value) if x.strip()]


def correction(plan: dict[str, Any], key: str, value: str) -> list[list[Any]]:
    """The framegate.toml keys of one `--set key=value` (key: a row's number or name)."""
    keys = list(ROWS)
    if key.isdigit() and 1 <= int(key) <= len(plan["rows"]):
        key = plan["rows"][int(key) - 1]["key"]
    value = value.strip()
    low = value.lower()
    lang = plan["language"]
    if key == "language":
        return [["project", "language", low.split("/")[0].strip()]]
    if key == "interpreter":
        return [["env", "python", value]]
    if key == "test":
        if lang == "python":
            return [["python", "pytest_args", shlex.split(value)]]
        if lang == "dotnet":
            return [["dotnet", "base_filter", value]]
        raise SetupError(f"row 3: edit the [generic] test commands in {CONFIG_FILE}")
    if key in ("lint", "format", "typecheck"):
        return [["gates.commands", key, _command(value)]]
    if key == "folders":
        src, _, tests = value.partition("/") if " / " not in value else value.partition(" / ")
        roots = _names(src)
        if not roots:
            raise SetupError('row 7: "<source folder>[, <more>] / <test folder>"')
        edits: list[list[Any]] = [["paths", "src", roots[0] if len(roots) == 1 else roots]]
        return [*edits, ["paths", "tests", tests.strip()]] if tests.strip() else edits
    if key == "base":
        return [["git", "base", value]]
    if key == "state_branch":
        if low.split(",")[0] not in ("yes", "no"):
            raise SetupError("row 9: yes or no")
        return [["git", "state_branch", "framegate/{base}" if low.startswith("yes") else ""]]
    if key == "locked":
        return [["protected", "extra", _names(value)]]
    if key == "refresh":
        return [["env", "refresh", _command(value)]]
    if key == "references":
        pairs = [x.partition("=") for x in _names(value)]
        return [["references", n.strip(), p.strip()] for n, _, p in pairs if p.strip()]
    if key == "rules":
        cmds = [] if low in ("none", "off", "") else [x.strip() for x in value.split(";")]
        return [["checks", "rules", [c for c in cmds if c]]]
    if key == "mutation":
        if low not in ("on", "off"):
            raise SetupError("mutation: on or off")
        return [["gates", "mutation_tool", "none"]] if low == "off" else []
    if key == "rules_text":
        return [["checks", "rules_text", "" if low in ("none", "off") else value]]
    if key == "builtin_off":
        return [["protected", "builtin_off", _names(value)]]
    if key in ("machine_files", "project_folders"):
        return [["git", key, _names(value)]]
    if key == "metrics":
        return [["paths", "metrics", value]]
    raise SetupError(
        f"--set {key}: no such row (rows 1-{len(plan['rows'])}: {', '.join(keys)}; also "
        f"{', '.join(EXTRA_KEYS)})"
    )


def edits(plan: dict[str, Any]) -> list[list[Any]]:
    """Every framegate.toml key the plan writes: the rows' own, a corrected row's instead."""
    corrected = {
        (plan["rows"][int(k) - 1]["key"] if k.isdigit() else k): v for k, v in plan["set"].items()
    }
    out: list[list[Any]] = []
    for row in plan["rows"]:
        if row["key"] in corrected:
            out += correction(plan, row["key"], corrected.pop(row["key"]))
        else:
            out += row["edits"]
    for key, value in corrected.items():
        out += correction(plan, key, value)
    if plan["red"]:
        listed = [{"test": t, "reason": r} for t, r in plan["red"].items()]
        out.append(["gates", "unrunnable_tests", listed])
    return out


def write_config(root: pathlib.Path, changes: Sequence[Sequence[Any]]) -> list[str]:
    """Apply `changes` to framegate.toml; an invalid result is not written. Returns the
    lines written (`[section] key = value`)."""
    path = root / CONFIG_FILE
    text = path.read_text(encoding="utf-8")
    current = tomllib.loads(text)
    done = []
    for section, key, value in changes:
        have: Any = current
        for part in section.split("."):
            have = have.get(part, {}) if isinstance(have, dict) else {}
        if isinstance(have, dict) and have.get(key) == value:
            continue
        if key == "unrunnable_tests":  # the confirmed entries are added to the listed ones
            old = have.get(key, []) if isinstance(have, dict) else []
            value = [*old, *(x for x in value if x not in old)]
        text = set_key(text, section, key, literal(value))
        done.append(f"[{section}] {key} = {literal(value)}".replace("\n", " "))
    try:
        parse_config(root, tomllib.loads(text))
    except (ConfigError, tomllib.TOMLDecodeError) as e:
        raise SetupError(f"{CONFIG_FILE} would be invalid ({e}); nothing was written") from e
    if done:
        path.write_text(text, encoding="utf-8")
    return done


def _drop_gitignore_lines(cfg: Config, adapter: LanguageAdapter) -> bool:
    """With a state branch `.gitignore` stays the team's file: the lines `framegate init`
    added go (`framegate state init` ignores the run files through .git/info/exclude)."""
    path = cfg.root / ".gitignore"
    if not path.is_file():
        return False
    mine = {cfg.paths.out + "/", cfg.paths.phase_file, cfg.paths.reqs_file}
    mine |= set(adapter.gitignore_entries())
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [x for x in lines if x.strip() not in mine]
    if kept == lines:
        return False
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return True


def apply(
    sets: Sequence[str] = (),
    red: Sequence[str] = (),
    start: pathlib.Path | None = None,
    echo: Echo = print,
) -> int:
    start = (start or pathlib.Path(os.getcwd())).resolve()
    cfg, _, _ = probe.load(start)
    path = plan_path(cfg)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        echo("error: no setup plan here. Run `framegate setup --plan` first and show it.")
        return 1
    try:
        for item in sets:
            key, sep, value = item.partition("=")
            if not sep or not key.strip():
                raise SetupError(f'--set {item}: write it as <row>=<value>, e.g. --set "6=off"')
            correction(plan, key.strip(), value)  # refused here, before anything is written
            plan["set"][key.strip()] = value
        for item in red:
            test, sep, reason = item.partition("=")
            if not sep or not test.strip() or not reason.strip():
                raise SetupError(f'--red {item}: write it as "<test file[::test]>=<reason>"')
            plan["red"][test.strip().replace("\\", "/")] = reason.strip()
        changes = edits(plan)
        root = pathlib.Path(plan["root"])
        if not (root / CONFIG_FILE).is_file():
            lang = next((v for s, k, v in changes if (s, k) == ("project", "language")), "")
            code, lines = _capture(lambda e: scaffold.init(str(lang or plan["language"]), root, e))
            if code != 0:
                for line in lines:
                    echo(line)
                return 1
            echo("Written: " + ", ".join(x.split()[1] for x in lines if "  created  " in x))
        written = write_config(root, changes)
    except SetupError as e:
        echo(f"error: {e}")
        return 1
    path.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    echo(f"{CONFIG_FILE}: " + ("; ".join(written) if written else "nothing to change"))

    try:
        cfg = load_config(root)
    except ConfigError as e:
        echo(f"error: {e}")
        return 1
    from framegate.adapters import get_adapter

    try:
        adapter = get_adapter(cfg)
    except ConfigError as e:
        echo(f"error: the adapter is not configured yet: {e}")
        return 1
    if publish.enabled(cfg) and _drop_gitignore_lines(cfg, adapter):
        echo(".gitignore: the lines `framegate init` added are taken out again (state branch).")
    # the tools the gate commands name, known only now
    code, lines = _capture(lambda e: scaffold.install_tools(False, root, e))
    if code != 0:
        for line in lines:
            echo(line)
        echo("error: the tools of the gate commands are not installed.")
        return 1
    echo("")
    echo("Setup check (all tests ran once):")
    verdict, lines = _capture(lambda e: doctor.run(cfg, e, only_brief=True, tree=True))
    for line in lines:
        echo(line)
    settled = verdict == 0 and not any(x.startswith(("PROBLEM:", "MUTATION:")) for x in lines)
    if settled and publish.enabled(cfg) and not state_branch_there(cfg):
        code, lines = _capture(
            lambda e: publish.state_init(cfg, e, extra_ignores=adapter.gitignore_entries())
        )
        echo("State branch: " + (lines[-1] if lines else "created") + " (nothing pushed)")
    echo("")
    for line in left_to_do(cfg, settled, lines):
        echo(line)
    return 0 if settled else 1


def state_branch_there(cfg: Config) -> bool:
    from framegate.state import GitError, git

    try:
        git(cfg, "rev-parse", "--verify", "-q", publish.names(cfg).state_ref)
    except GitError:
        return False
    return True


def left_to_do(cfg: Config, settled: bool, check: Sequence[str]) -> list[str]:
    """What is left after an apply: what to commit, what only the chat or the user does."""
    out = ["Left to do:"]
    if not settled:
        out.append(
            "- The setup is NOT ready (lines above). Red tests the user agrees to list: "
            '`framegate setup --apply --red "<test file[::test]>=<reason>"` (a debt entry\'s '
            'reason starts with "red before any task:"); mutation testing that cannot run: '
            "`--set mutation=off` after the user's yes. Then this command again."
        )
    knowledge = cfg.root / cfg.knowledge
    template = scaffold.package_text("templates", "project.md")
    if not knowledge.is_file() or knowledge.read_text(encoding="utf-8") == template:
        out.append(f"- {cfg.knowledge}: still the empty template. Write the project's facts.")
    if not (cfg.root / cfg.code_map).is_file():
        out.append(
            f"- {cfg.code_map}: no code map yet. Write it (per module: purpose, key classes)."
        )
    rules_missing = doctor.missing_chat_rules(cfg)
    if rules_missing:
        out.append(
            f"- Allow the CLI for the chat: add {', '.join(rules_missing)} to "
            "permissions.allow in .claude/settings.local.json (on the user's yes)."
        )
    try:
        files = scaffold.setup_files(cfg)
    except Exception:  # no git repository yet: nothing to commit
        files = []
    if files:
        out.append(
            f"- Commit the setup's files ({', '.join(files)}) in one commit of their own: "
            "`framegate commit-setup` (after the two files above; nothing is pushed)."
        )
    if publish.enabled(cfg):
        out.append(
            "- framegate's files are on the state branch; the first `framegate publish` pushes it."
        )
    return out if len(out) > 1 else ["Left to do: nothing. The setup is ready."]
