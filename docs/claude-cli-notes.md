# Claude CLI notes (claude 2.1.319, checked 2026-11-09)

What the orchestrator relies on, and how it was verified. No real `claude -p` agent run was made
(cost); checks used `claude --help` and invocations that fail before any API call.

| Item | Result | How verified |
|---|---|---|
| `-p` / `--print` reads the prompt from stdin | Yes. With empty stdin: `Error: Input must be provided either through stdin or as a prompt argument when using --print` | `claude -p --output-format json < /dev/null` |
| `--append-system-prompt-file <path>` | **Exists** (not listed in `--help`, but named in the `--bare` help text as `--append-system-prompt[-file]`). A missing file gives `Error: Append system prompt file not found: <path>`; an unknown flag gives `error: unknown option` | `claude -p --append-system-prompt-file ./nonexistent.md < /dev/null` vs. `claude -p --bogus-flag` |
| `--append-system-prompt <text>` | Listed. Not used: the role text goes through the file variant (no multi-line text and no 32k command-line limit on Windows) | `--help` |
| `--allowedTools` rule syntax | `--allowedTools, --allowed-tools <tools...>`: "Comma or space-separated list of tool names to allow (e.g. "Bash(git *) Edit")". agentflow passes **one** comma-joined argument and uses the space form `Bash(<prefix> *)` (the guide's `Bash(cmd:*)` is the older prefix syntax) | `--help` |
| `--permission-mode` | Choices: `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`. Default used: `acceptEdits` (`[agents] permission_mode`) | `--help` |
| `--permission-prompts` | New: `host` (default) or `none` (anything that would prompt is denied). Not passed | `--help` |
| `--max-turns <turns>` | **Hidden** (not in `--help`) but accepted: `--max-turns notanumber` -> `error: option '--max-turns <turns>' argument 'notanumber' is invalid. must be a number`; `--max-turns 3` reaches the stdin check. What it counts: see "Turns" below | `claude -p --max-turns ... < /dev/null` |
| `--max-budget-usd <amount>` | Listed ("only works with --print"). Checked after each API turn, so the final cost can pass the cap by one turn. Hit: `is_error: true`, `subtype: "error_max_budget_usd"`, `terminal_reason: "budget_exhausted"`, `errors: ["Reached maximum budget ($0.004)"]`, no `result` | Probe 2026-11-10 (claude 2.1.321, `--model haiku`, cap $0.004: stopped at $0.0045 after 5 API turns) |
| `--output-format stream-json` | Needs `--verbose` with `-p`. One JSON message per line: `system` (init, hooks), `assistant` (one line **per content block**; the blocks of one API turn share `message.id`), `user` (tool results), `rate_limit_event`, and the `result` object last (same fields as `json`) | Probe 2026-11-10 |
| `--output-format json` | Choices `text`, `json`, `stream-json`. The fields read (`is_error`, `result`, `total_cost_usd`, `num_turns`) are the documented result-message fields; **not verified by a live run**. Unparseable output is recorded as an error result | `--help`; docs |
| `--bare` | Skips hooks and plugins (and CLAUDE.md discovery, OAuth). **Never passed**: it would disable the phase lock and the Stop hook | `--help`; plugin agent's docs check (docs/plugin.md) |
| Hooks in `-p` mode | Fire (per the hooks/headless docs, checked by the plugin agent) | docs/plugin.md |
| Windows `.cmd` shim | `shutil.which("claude")` resolves the npm `claude.cmd` shim (here `C:\Users\D.Anh\AppData\Roaming\npm\claude`). Arguments contain spaces, `*`, `(`, `)` and `,` but no `%`, `^`, `&`, `|`, `"`, so cmd.exe passes them through when quoted. The prompt never goes on the command line. A native `claude.exe` (`claude install`) avoids cmd.exe entirely | `which claude` |

## Orchestrator invocation

```
<claude> -p --append-system-prompt-file out/<f>/prompts/<role>.system.md
         --allowedTools "<rule>,<rule>,..." --permission-mode acceptEdits
         --max-turns <cap of the phase> --output-format stream-json --verbose
         [--max-budget-usd <cap of the phase>]   (task on stdin)
```

`<claude>` = `[agents] claude` (default `claude`) or env `AGENTFLOW_CLAUDE`; a `.py` path is run
with the current interpreter (used by the tests' fake claude). Each run's task prompt and raw
output are saved to `out/runs/<stamp>-<feature>-<phase>.prompt.md` / `.json` (the result object
+ `api_turns`) / `.stream.jsonl` (the whole message stream, for debugging). The process starts
in its own process group and, on Windows, suspended in a kill-on-close job object, then resumed
(`procs.popen(job=True)`): `claude.cmd` -> cmd.exe -> node would otherwise spawn before the
assignment. From the result: `permission_denials` -> `permission_denials` event + retro D items;
`subtype: "error_max_turns"` (or `terminal_reason: "max_turns"`) -> `max_turns` event,
`subtype: "error_max_budget_usd"` (or `terminal_reason: "budget_exhausted"`) -> `max_budget`
event; both take the cap handling (docs/reference.md).

## Turns: `--max-turns` vs `num_turns`

`--max-turns N` caps the **API round trips** (assistant messages). The result's `num_turns`
counts **messages on the user side**: the prompt + one per tool result, so parallel tool calls
inflate it. Evidence: the live FCSim RETRO (`--max-turns 30`) reported `num_turns` 46 and ended
`success`; its transcript has 23 distinct assistant message ids (23 round trips <= 30) and 45 tool
results (+ 1 prompt = 46). Probe (claude 2.1.321, haiku, 8 sequential `echo` calls,
`--max-turns 3`): stopped with `error_max_turns` after 3 round trips, `num_turns` 4. So the cap
worked; only the reading of `num_turns` as turns was wrong. agentflow now reads the message stream
(`--output-format stream-json --verbose`) and logs `turns` = distinct assistant message ids (what
the cap counts) next to `messages` = `num_turns`; the phase table shows `turns`.

## Verified in the live FCSim smoke run (2026-11-09, claude 2.1.319, Windows 11)

| Item | Result |
|---|---|
| JSON result fields | `is_error`, `result`, `total_cost_usd`, `num_turns`, `session_id`, `permission_denials` (tool name + input of every denied call) all present |
| `--max-turns` hit | `is_error: true`, `subtype: "error_max_turns"`, `terminal_reason: "max_turns"`, no `result` field |
| Multi-line task via stdin through the npm `.cmd` shim | Arrives intact (agent echoed three lines verbatim) |
| Plugin hooks in `claude -p` (local-scope install) | PreToolUse blocks (`PreToolUse:Write hook error: ... [IMPLEMENT] Blocked write to tests/...`); Stop blocks on red tests (`Stop hook feedback: ... Tests are still failing`), then the agent continues |
| Shell tool on Windows | Agents use the **PowerShell** tool as often as Bash. `Bash(...)` rules don't cover it; `PowerShell(<prefix> *)` rules do (agentflow now emits both). `cd ... &&`, `;` chains and PATH prefixes are denied |
| Project CLAUDE.md | Loaded in every headless run; FCSim's "read HANDOVER.md first" was not followed (0 reads in 9 runs) |

Still open: whether `Bash(python -m pytest *)` matches the bare command without arguments.
