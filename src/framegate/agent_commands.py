"""The shell commands an agent's task names, and the `--allowedTools` rules that allow them.

Both come from the same list: the project's effective lint / format / typecheck / imports
commands (`[gates.commands]`, else the adapter's defaults), as an agent types them. Seen
live (da-argos): the brief named `ruff check orchestrator tests` and `ruff format ...`
while the project checks with `python -m ruff check` over five source folders and formats
with black; the reviewer and the triage agent ran the brief's command and were denied, and
`ruff format` would have reformatted a black project.

A rule is `Bash(<prefix> *)`: Claude Code allows a call whose command line starts with the
prefix (`with_powershell` in adapters/base.py adds the PowerShell twin).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

#: stands for the files an agent passes to a format command (`{files}` / `{changed}`)
FILES_ARG = "<files>"
_RULE = re.compile(r"^(?:Bash|PowerShell)\((.*)\)$")
_PLAIN = re.compile(r"[\w@%+=:,./<>|-]+")


def command_text(tokens: Sequence[str]) -> str:
    """A command line as it is typed (an argument with a space is quoted)."""
    return " ".join(t if _PLAIN.fullmatch(t) else '"' + t.replace('"', '\\"') + '"' for t in tokens)


def command_prefix(tokens: Sequence[str], paths: Iterable[str]) -> list[str]:
    """The tokens before the first file argument: `<files>`, or one of `paths` (the project's
    source and test folders, a solution file), or something below one."""
    roots = [p.rstrip("/") for p in paths if p]
    out: list[str] = []
    for tok in tokens:
        norm = tok.replace("\\", "/").rstrip("/")
        if tok == FILES_ARG or any(norm == r or norm.startswith(r + "/") for r in roots):
            break
        out.append(tok)
    return out


def command_rules(tokens: Sequence[str], paths: Iterable[str]) -> list[str]:
    """The allow rules for one command: its prefix followed by anything (the same command on
    other files); a command without a file argument also as it stands."""
    if not tokens:
        return []
    prefix = command_prefix(tokens, paths) or list(tokens[:1])
    text = command_text(prefix)
    whole = len(prefix) == len(tokens)
    return ([f"Bash({text})"] if whole else []) + [f"Bash({text} *)"]


def rule_allows(rules: Iterable[str], command: str) -> bool:
    """Does one of the `Bash(...)` / `PowerShell(...)` rules allow this command line? A bare
    `Bash` allows every command; `<prefix> *` (older spelling `<prefix>:*`) allows the prefix
    alone or followed by arguments."""
    command = " ".join(command.split())
    for rule in rules:
        if rule in ("Bash", "PowerShell"):
            return True
        m = _RULE.match(rule.strip())
        if not m:
            continue
        body = " ".join(m.group(1).split())
        if body.endswith(":*"):
            body = body[:-2] + " *"
        if body == "*" or command == body:
            return True
        if body.endswith(" *") and (command == body[:-2] or command.startswith(body[:-1])):
            return True
    return False


def lint_line(commands: Sequence[str], allowed: Sequence[str] | None) -> str:
    """The brief's line with the lint / format / type commands this run may use (`allowed`:
    the run's tool rules; None = every command). Commands the run may not use are left out; a
    run that may use none is told so, instead of being denied when it tries."""
    if not commands:
        return ""
    mine = [c for c in commands if allowed is None or rule_allows(allowed, c)]
    if not mine:
        return (
            "- Lint / format / types: not yours to run (those calls are denied in this run); "
            "the gates check them."
        )
    note = f" ({FILES_ARG} = the files you changed)" if any(FILES_ARG in c for c in mine) else ""
    return "- Lint / format / types: " + ", ".join(f"`{c}`" for c in mine) + note + "."
