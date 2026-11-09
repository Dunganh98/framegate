"""Command-form check for the Bash / PowerShell tools during an agentflow run.

Agents get only plain command prefixes pre-approved (--allowedTools). A pipe, a chain (`;`,
`&&`, `||`), several lines, or a `cd` / env-variable prefix makes Claude Code's permission check
deny the call with a generic message, and the agent keeps guessing. The PreToolUse hook blocks
such a call first with the reason and what to do instead (exit 2: stderr reaches the agent).
"""

from __future__ import annotations

import re

SHELL_TOOLS = {"Bash", "PowerShell"}
_PREFIX = re.compile(r"^\s*(cd|pushd|Set-Location|sl)\s", re.I)
_ENV_PREFIX = re.compile(r"^\s*(export\s+)?[A-Za-z_][A-Za-z0-9_]*=\S*\s+\S")
_PS_ENV = re.compile(r"^\s*\$env:", re.I)

OUTPUT_HINTS = {
    "dotnet": (
        'Narrow `dotnet test` with its own options instead: `--filter "<expr>"`, '
        '`--logger "console;verbosity=minimal"` (or `quiet`/`normal`), `--no-build`; or run the '
        "command alone and read the result file it writes (a .trx under TestResults/, or the "
        "junit/trx files agentflow names in the gate report) with the Read tool."
    ),
    "python": (
        "Narrow pytest with its own options instead: `-k <expr>`, `-q`, `--tb=short`, "
        "`-x`, `--junitxml=<file>`; or run the command alone and read the file it writes with "
        "the Read tool."
    ),
}
GENERIC_HINT = (
    "Use the command's own options to narrow its output, or read the file it writes with the "
    "Read tool. Use Grep / Glob / Read instead of shell pipes for searching files."
)


def form_problem(command: str) -> str | None:
    """What makes `command` more than one plain command ('a pipe (|)', 'a chain (;)', ...), or
    None. Quotes are respected (`--logger "console;verbosity=minimal"` is fine); `\\` and the
    PowerShell backtick escape the next character; a single `&` (`2>&1`, PowerShell's call
    operator) is allowed."""
    if _PREFIX.match(command):
        return "a `cd` prefix"
    if _ENV_PREFIX.match(command) or _PS_ENV.match(command):
        return "an environment-variable prefix"
    quote = ""
    i, n = 0, len(command.rstrip())
    while i < n:
        c = command[i]
        if c in "\\`" and quote != "'":
            i += 2
            continue
        if quote:
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
        elif c == "|":
            return "a chain (||)" if command[i : i + 2] == "||" else "a pipe (|)"
        elif c == ";":
            return "a chain (;)"
        elif c == "&" and command[i : i + 2] == "&&":
            return "a chain (&&)"
        elif c == "\n":
            return "several command lines"
        i += 1
    return None


def message(phase: str, command: str, problem: str, language: str) -> str:
    short = command.strip().splitlines()[0] if command.strip() else command
    if len(short) > 160:
        short = short[:160] + "..."
    return (
        f"[{phase}] Blocked: `{short}` contains {problem}. In an agentflow run only plain single "
        "commands are pre-approved: one command per call, from the repository root, no pipes, "
        "no `;` / `&&` / `||` chains, no `cd` or environment-variable prefixes (such calls are "
        "denied by the permission check). Run each command as its own call. "
        + OUTPUT_HINTS.get(language, GENERIC_HINT)
    )
