# agentflow

Spec-driven, test-first workflow orchestrator for Claude Code (package + plugin). Design:
docs/AGENTIC_WORKFLOW_SETUP.md (Part 8 = target architecture).

You approve the spec and the test summary; everything between those gates runs as fresh headless
`claude -p` runs inside a frame of deterministic checks (red-against-stubs, traceability, test
smells, frozen tests via a tag-based frame diff, lint/types, diff coverage, mutation score).

## Parts

| Part | Where | Docs |
|---|---|---|
| Python package + CLI `agentflow` | `src/agentflow/` | this file, docs/adapters.md, docs/claude-cli-notes.md |
| Claude Code plugin (`/spec`, hooks) | `plugin/` | docs/plugin.md |

## Daily use

```
agentflow init [--language python|dotnet]   # agentflow.toml, specs/_template/, .gitignore lines
git checkout -b feature/<f>
agentflow spec <f>                          # then in claude: /spec <f>
agentflow approve-spec <f>                  # tag spec-approved/<f>
agentflow run <f>                           # runs until something needs you, then exits
agentflow status <f>                        # what is waiting, and what to do
agentflow approve <f>                       # tests gate / review-findings gate
agentflow reject <f> --reason "..."         # reason goes to the agent as feedback
agentflow answer <f> [--abort]              # after writing out/<f>/answers.md (questions/escalation)
agentflow gates <f> --scope step|final      # the frame checks (also used in CI)
agentflow spec-brief <f>                    # conventions for the SPEC session (used by /spec)
```

`run` never waits on the keyboard. It advances TESTS -> IMPLEMENT (per plan step) -> REVIEW ->
PR -> RETRO -> DONE and stops at the next human gate, agent questions or escalation, writing
`out/<f>/progress.json` (`waiting`) and printing what to review. Exit codes: 0 done/ok, 1 error,
10 human gate, 11 questions, 12 escalation (step failed `max_attempts` times), 13 spec change
required. Run `agentflow run <f>` again after `approve` / `reject` / `answer`; it also resumes
after an interruption.

Hooks (called by the plugin): `agentflow hook pre-tool-use` (phase-based write protection) and
`agentflow hook stop` (IMPLEMENT can't stop while the step's tests fail). Both read the hook
JSON on stdin, exit 0 silently without `agentflow.toml` or `.phase`, and block with exit 2.

Configuration: `agentflow.toml` (all keys optional; see the commented template written by
`init`). `[agents] claude` or env `AGENTFLOW_CLAUDE` selects the claude executable.

Python projects: add agentflow (pinned) to the dev dependencies; its pytest plugin provides the
`req` marker and `--req ID,ID` selection.

## Development

```
uv sync
uv run pytest          # unit + end-to-end tests (fake claude, no API calls)
uv run ruff check src tests ; uv run ruff format --check src tests ; uv run mypy
```
