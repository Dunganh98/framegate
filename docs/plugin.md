# agentflow Claude Code plugin

The plugin is the Claude Code side of agentflow: three skills that drive the workflow from the chat
(`/agentflow:feature`, `/agentflow:resume`, `/agentflow:status`) and two hooks, all calling the
`agentflow` CLI (the Python package in `src/`). All logic lives in the package (including the
chat run protocol, `agentflow protocol`); the plugin only registers things. Installed
**user-wide**, it stays inert in repos without `agentflow.toml`.

## Layout

```
.claude-plugin/marketplace.json     # marketplace "agentflow", one entry: source "./plugin"
plugin/
  .claude-plugin/plugin.json        # name agentflow, version 0.2.2
  hooks/hooks.json                  # PreToolUse (Write|Edit|MultiEdit|NotebookEdit|Bash|PowerShell) + Stop
  scripts/agentflow-hook.js         # wrapper: node agentflow-hook.js <event> -> agentflow hook <event>
  skills/feature/SKILL.md           # /agentflow:feature <f>: branch + spec dialogue, then the run
                                    # protocol (injected: agentflow protocol)
  skills/resume/SKILL.md            # /agentflow:resume [f]: reattach after a session restart
  skills/status/SKILL.md            # /agentflow:status [f]: where features stand, tag cleanup
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
- `Bash` / `PowerShell` calls: checked only during an agentflow run. Without the phase file
  (`[paths] phase_file`, default `.phase`, read from `agentflow.toml` by a regex) the wrapper exits
  0 without starting the CLI, so interactive sessions in an agentflow repo pay no Python start per
  shell call. With it, `agentflow hook pre-tool-use` blocks a pipe / chain / several lines /
  `cd` or env prefix (not in SPEC) with the reason and the alternatives
  (`src/agentflow/hooks/shell_form.py`). CLI missing -> exit 1 (not blocked: the permission check
  still applies).
- The plugin loads in place from a local marketplace: the new matcher applies to sessions started
  after the update (or `/reload-plugins`).

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

Live check (FCSim smoke run, 2026-11-09, local-scope install): both hooks fire in `claude -p`
runs (see docs/claude-cli-notes.md).

## Chat flow: how the skills drive the CLI

| Step | Mechanism |
|---|---|
| Skill load | `` !`agentflow spec <f>` ``, `` !`agentflow spec-brief <f>` ``, `` !`agentflow protocol` `` are injected (pre-approved by `allowed-tools: Bash(agentflow *) PowerShell(agentflow *)`). A refusal (dirty tree, foreign branch, active run) exits 1 and aborts the invocation with the message shown |
| Run | Bash tool `run_in_background: true`: `agentflow run <f>`. One completion notification when it exits (exit 10 shows as status `failed`; the protocol reads the state from `agentflow status <f> --brief`, not from the code) |
| Live view | Monitor tool: `agentflow watch <f>`, `timeout_ms` 1800000. Each progress line = one event (lines within 200 ms batch). The watch exits when the run's lock is gone (`watch ended: <state>`); at the 30-min deadline the skill re-arms it, and `progress.cursor` makes it continue without repeats or gaps |
| Gate | `agentflow gate-summary <f>` shown in a code block; the skill ends its turn (Claude Code's own "waiting for input"); agentflow shows a Windows toast. The reply is mapped to approve / reject / answers.md + answer / finish (table in `src/agentflow/templates/chat_protocol.md`), then the run starts again in the background |
| Resume | `/agentflow:resume [f]` injects `agentflow status [f] --brief` (always exit 0) and the protocol; a run started by another session has no task notification, so its end is the watch's last line |
| Wrap-up | retro proposals, PR offer (`gh pr create --body-file out/<f>/final_summary.md`, only on yes, before the cleanup), `agentflow cleanup <f>`; later `--tags` |

Permissions: an `allowed-tools` grant only lasts for the turn the skill was invoked in. Every
later turn (gate replies, run restarts, the Monitor) needs `Bash(agentflow *)` /
`PowerShell(agentflow *)` in the project's `permissions.allow` (or "always allow" at the first
prompt); `gh` / `git push` stay unapproved on purpose.

## Verified facts for the chat flow (Claude Code 2.1.322, docs read 2026-11-10)

| Fact | Result | Source |
|---|---|---|
| Background Bash | `run_in_background: true` -> task id + output file; one notification when the command exits. A local interactive session has **no time limit** for background commands; `-p` runs: 10 min default (text prompt), max 2 h with `timeout`; `-p` waits for them after its result | [tools reference](https://code.claude.com/docs/en/tools-reference) "Background commands" |
| Monitor | Runs a command in the background, each stdout line is an event fed back to Claude; deadline 5 min default, **max 30 min** (10 min in `-p`), one notice at expiry -> re-arm; same permission rules as Bash; on Windows only with Git Bash installed; a deferred tool (loaded via ToolSearch on first use) | tools reference "Monitor tool"; Monitor tool description; `-p` check below |
| Monitor output | stdout only, must flush per line (`watch` prints with `flush=True`); too many events -> the monitor is stopped (the progress stream is ~10-30 lines per feature) | Monitor tool description |
| Skill string substitutions | `$ARGUMENTS`, `$N`, `${CLAUDE_SKILL_DIR}`, `${CLAUDE_PROJECT_DIR}`, `${CLAUDE_PLUGIN_ROOT}` (plugin skills) | [skills](https://code.claude.com/docs/en/skills) |
| `` !`cmd` `` injection | Bash when Git Bash exists, else PowerShell; **any non-zero exit aborts the invocation** (exit 1 tolerated only for search/compare commands); `cat` of a file outside the session's working directories is **blocked** ("may only concatenate files from the allowed working directories"), so the protocol comes from `agentflow protocol`, not a plugin file | skills; `-p` check below |
| Skill content lifetime | The rendered SKILL.md stays in the conversation for later turns (not re-read); the `allowed-tools` grant clears at the next user message | skills |
| Reading plugin files | A `Read` of a plugin file from a later turn needs a permission (denied in `-p`) | `-p` check below |

Headless check (2026-11-10, temp git repo, `--plugin-dir plugin`, Haiku, total $0.05): (1)
`/agentflow:feature demo` loaded, its injected commands ran (branch `feat/demo` created from
`main`, `specs/demo/` copied), Claude opened the spec dialogue; (2) `/agentflow:status` printed
the overview; (3) with a written spec and the fake claude (`AGENTFLOW_CLAUDE`), the reply "spec
is OK, start the run" (`--resume`, `--allowedTools "Bash(agentflow *)" Monitor`) made Claude run
`approve-spec`, start `agentflow run demo` in the background and `agentflow watch demo` in a
Monitor; the run stopped at the tests gate (5 progress lines), the task notification came, Claude
ran `status --brief` + `gate-summary`, showed the gate with the numbered question and waited; the
watch then ended with `watch ended: waiting at the tests gate`. Not tested: an interactive
session, and whether the toast is visible (`powershell.exe` exits 0; the user confirms).

## Contract notes for the package

- Injected commands (`spec`, `spec-brief`, `status --brief`, `protocol`) must exit 0 when they
  work; any non-zero exit aborts the skill and shows its output, so print a clear error.
  `status --brief` always exits 0 (also at a gate); `gate-summary` exits 0 at a gate, 1 otherwise.
- The orchestrator must **not** pass `--bare` to `claude -p`, or the phase-lock and Stop hooks don't run.
- In the hooks, read `tool_input.file_path` or `tool_input.notebook_path`; `file_path` is absolute.
- Stop: honour `stop_hook_active`; exit 2 + stderr continues Claude with that text as the reason.
- The wrapper already skips repos without `agentflow.toml`; the package's own check stays as a
  second line for direct calls.
