# agentflow Claude Code plugin

The plugin is the Claude Code side of agentflow: the `/spec` command and two hooks that call the
`agentflow` CLI (the Python package in `src/`). All logic lives in the package; the plugin only
registers things. Installed **user-wide**, it stays inert in repos without `agentflow.toml`.

## Layout

```
.claude-plugin/marketplace.json     # marketplace "agentflow", one entry: source "./plugin"
plugin/
  .claude-plugin/plugin.json        # name agentflow, version 0.1.0
  hooks/hooks.json                  # PreToolUse (Write|Edit|MultiEdit|NotebookEdit) + Stop
  scripts/agentflow-hook.js         # wrapper: node agentflow-hook.js <event> -> agentflow hook <event>
  skills/spec/SKILL.md              # /spec (also /agentflow:spec)
  tests/agentflow-hook.test.js      # node --test "plugin/tests/*.test.js"
```

## Install

User-wide (what you normally want), from the local checkout:

```
claude plugin marketplace add D:\Me\Projects\agentflow
claude plugin install agentflow@agentflow
```

In a session the same is `/plugin marketplace add D:\Me\Projects\agentflow`, then
`/plugin install agentflow@agentflow`. A marketplace added from a local directory loads the plugin
**in place** (no cache copy): edits apply on the next session or `/reload-plugins`.

Later from GitHub (repo must contain `.claude-plugin/marketplace.json` at its root; a private repo
needs working git credentials):

```
claude plugin marketplace add <owner>/<repo>
claude plugin install agentflow@agentflow
```

Updates from GitHub: bump `version` in `plugin/.claude-plugin/plugin.json`, push, then
`claude plugin marketplace update agentflow` and `claude plugin update agentflow@agentflow`
(restart). Users stay on a version until it changes.

Requirements: `node` on PATH (any LTS; an npm-installed Claude Code already has it) and, in
agentflow repos, the `agentflow` CLI (`uv tool install ...`, which creates a real `agentflow.exe`
on Windows).

## Hook wrapper: why Node

| Option | Problem |
|---|---|
| Call `agentflow` directly in the hook | Not installed -> spawn error notice on every write in every repo |
| Python one-file wrapper | `python` on Windows can be the Store alias stub (it is here: `python3` -> WindowsApps); Linux often has only `python3` |
| POSIX sh script (shell form) | Shell form runs in PowerShell on Windows without Git Bash; quoting `${CLAUDE_PLUGIN_ROOT}` needed |
| **Node, exec form** (chosen) | Docs: exec form needs a real `.exe` on Windows, and "the `node` plus script-path pattern works on every platform because `node.exe` is a real binary". No shell, no quoting |

Wrapper behaviour (`plugin/scripts/agentflow-hook.js`):
- Looks for `agentflow.toml` walking up from the hook JSON `cwd`, `CLAUDE_PROJECT_DIR`, the
  target file's directory (`tool_input.file_path` / `notebook_path`) and its own cwd.
- None found -> exit 0, no output, CLI never started (~45 ms per call measured).
- Found -> runs `agentflow hook <event>` (or `$AGENTFLOW_BIN`) with the same stdin; stdout, stderr
  and exit code pass through unchanged.
- Found but CLI missing -> `pre-tool-use`: exit 2 + install hint (write blocked, Claude sees the
  reason); `stop`: exit 1 (non-blocking notice to the user; blocking Stop would only loop Claude,
  which can't install the CLI).

Timeouts: PreToolUse 30 s, Stop 600 s (the Stop hook may run a full test suite).

## Verified facts (Claude Code 2.1.319, docs read 2026-11-09)

| Fact | Result | Source |
|---|---|---|
| Plugin manifest | `.claude-plugin/plugin.json` under the plugin root; only `name` required; other dirs at plugin root | [manifest reference](https://code.claude.com/docs/en/plugins-reference) |
| Plugin hooks file | `hooks/hooks.json` auto-loaded, wrapped in top-level `"hooks"`; same shape as settings hooks; registered at session load, fire in every session | [components](https://code.claude.com/docs/en/plugins/components.md) |
| `${CLAUDE_PLUGIN_ROOT}` | Substituted in hook `command`/`args`; also exported with `CLAUDE_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR` | manifest reference, Environment variables |
| Exec vs shell form | `args` present -> exec form, `command` resolved on PATH, no shell; Windows needs a real `.exe` (`.cmd` shims fail). Shell form: `sh -c` / Git Bash / PowerShell if no Git Bash | [hooks](https://code.claude.com/docs/en/hooks) "Exec form and shell form" |
| Commands vs skills | Plugin `commands/*.md` still work but are "the older format"; new work uses `skills/<name>/SKILL.md`. Plugin skills are `/<plugin>:<name>`; with frontmatter `name`, the bare `/name` also works unless taken | components; [skills](https://code.claude.com/docs/en/slash-commands) |
| `$ARGUMENTS` | Supported (also `$0`, `$1`, `$ARGUMENTS[N]`) | skills |
| `` !`cmd` `` injection | Supported in skills/commands; runs before the content reaches Claude. **Any failure aborts the whole invocation** (`Shell command failed for pattern ...`, output shown). Command must pass permission rules -> pre-approve with `allowed-tools`. `disableSkillShellExecution` setting turns it off | skills |
| Hook input: common | `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name` (+ `prompt_id`, `effort`, `agent_id`...) | hooks, Common input fields |
| PreToolUse input | `tool_name`, `tool_input`, `tool_use_id`; Write/Edit `tool_input.file_path` (absolute); NotebookEdit uses `notebook_path` (tool schema; docs list no table) | hooks; tools reference |
| Write tool names | Current built-ins: `Write`, `Edit`, `NotebookEdit`. **`MultiEdit` is no longer listed** (kept in the matcher, harmless) | [tools reference](https://code.claude.com/docs/en/tools-reference) |
| Stop input | `stop_hook_active` (true when already continuing because of a Stop hook), `last_assistant_message`, `background_tasks`, `session_crons`. Cap: 8 consecutive Stop-hook continuations | hooks, Stop input |
| Exit codes | 0 = success, stderr never shown to Claude. 2 = block: PreToolUse -> call blocked, stderr = denial reason Claude sees; Stop -> Claude continues, stderr = reason. Other = non-blocking error, action proceeds, first stderr line shown to the user | hooks |
| Hook timeout | `timeout` field, **seconds**; default 600 for command hooks | hooks, Common fields |
| Hooks in `claude -p` | Run (settings and plugin hooks load as in an interactive session; async hooks are killed at teardown). **`--bare` skips plugins and hooks**, even with `--plugin-dir` | [headless](https://code.claude.com/docs/en/headless) |
| Marketplace manifest | `<repo>/.claude-plugin/marketplace.json`; required `name`, `owner.name`, `plugins[]` (`name`, `source`); relative source starts with `./`, resolved from the repo root | [marketplace reference](https://code.claude.com/docs/en/plugins/marketplace-reference.md) |
| Local + GitHub install | `claude plugin marketplace add <dir>` (directory source, in-place) or `<owner>/<repo>` (git clone, relative sources work); `--scope user|project|local` | `claude plugin marketplace add --help`; [create marketplace](https://code.claude.com/docs/en/plugin-marketplaces) |
| Validation | `claude plugin validate --strict ./plugin` and `claude plugin validate --strict .` both pass (only "plugin directory" catalog advice: README/LICENSE/icon, irrelevant here) | run 2026-11-09 |
| Install smoke test | Local scope in a temp repo: marketplace add + install OK; `claude plugin details agentflow` -> Skills (1) spec, Hooks (2) PreToolUse, Stop; then uninstalled and removed | run 2026-11-09 |

Not tested (by design): a live session firing the hooks, the `!` injection in `/spec`, and `claude -p`
runs. Check once in a real session after the CLI exists.

## Contract notes for the package

- `agentflow spec-brief <feature>` must exit 0 when it works; any non-zero exit aborts `/spec` and
  shows its output, so print a clear error (e.g. no feature given, no `agentflow.toml`).
- The orchestrator must **not** pass `--bare` to `claude -p`, or the phase-lock and Stop hooks don't run.
- In the hooks, read `tool_input.file_path` or `tool_input.notebook_path`; `file_path` is absolute.
- Stop: honour `stop_hook_active`; exit 2 + stderr continues Claude with that text as the reason.
- The wrapper already skips repos without `agentflow.toml`; the package's own check stays as a
  second line for direct calls.
