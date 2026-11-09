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

Questions and failed retries feed the retro. An agent question (`questions.md`) pauses the run
but is not a failed attempt: it marks a spec gap. Every step-gate run is logged in
`out/events.jsonl` (`gate`: phase, step, attempt, `failed` check names, trimmed `excerpt`), as are
escalations (phase, step, attempts, failing checks), questions (phase, step, text), answers
(text; also appended to `questions_archive.md` under the questions), review findings (failing
reviewer tests) and final gates (`failed`, `skipped`). Before the RETRO agent runs, agentflow
writes `out/<f>/retro_input.md`: counts, then every question (Q1..), failed attempt (F1..),
escalation (E1..), human rejection (R1..), review finding (V1..) and final-gate failure (G1..).
The retro must mention every ID in `retro_proposals.md` (a proposal, or "Not acted on" with a
one-line reason); if IDs are missing it is re-run once with them as feedback, then a warning is
printed and logged (`retro_coverage` event).

`metrics/workflow_metrics.csv` (one row per finished feature):

| Column | Meaning |
|---|---|
| `human_rejections` | `reject` at the tests or review gate |
| `escalations` | steps that failed `max_attempts` times in a row |
| `questions` | question rounds (`questions.md` files; one round can hold several questions) |
| `gate_failures` | failed step-gate runs = failed implementation attempts (incl. review fixes; questions don't count). This is the "failed attempts" count; there is no separate column |
| `cost_usd` | sum of `total_cost_usd` of all agent runs |

Hooks (called by the plugin): `agentflow hook pre-tool-use` (phase-based write protection) and
`agentflow hook stop` (IMPLEMENT can't stop while the step's tests fail). Both read the hook
JSON on stdin, exit 0 silently without `agentflow.toml` or `.phase`, and block with exit 2.

Configuration: `agentflow.toml` (all keys optional; see the commented template written by
`init`). `[agents] claude` or env `AGENTFLOW_CLAUDE` selects the claude executable.

Python projects: add agentflow (pinned) to the dev dependencies; its pytest plugin provides the
`req` marker and `--req ID,ID` selection. mutmut needs fork, so on Windows the mutation gate runs
through WSL (`[gates] mutation_runner = "auto"`, default; `[env] wsl_distro` picks the
distribution) in a WSL-side venv with the project's dependencies. Install once inside WSL:
`curl -LsSf https://astral.sh/uv/install.sh | sh` (or `sudo apt install python3-venv`). Without
WSL the gate is SKIPPED with the reason. Details: docs/adapters.md.

.NET projects (xUnit, `language = "dotnet"`, detected from a root `*.sln`/`*.slnx`/`*.csproj`):
install agentflow with the `dotnet` extra (`pip install "agentflow[dotnet]"`, brings diff-cover)
and Stryker.NET (`dotnet tool install -g dotnet-stryker`). Tag tests with
`[Trait("Req", "XXX-R-001")]`; selection uses `dotnet test --filter "(Req=A|Req=B)&(<base_filter>)"`.
Put `[paths] review_tests` inside a test project folder and set the `[dotnet]` keys
(`base_filter`, `test_projects`, `configuration = "Release"`, ...). Details and the FCSim notes:
docs/adapters.md.

## Development

```
uv sync
uv run pytest          # unit + end-to-end tests (fake claude, no API calls), ~45 s
uv run pytest -m slow  # real toolchains: .NET e2e (dotnet SDK) ~1.5 min, mutmut via WSL ~15 s
uv run pytest -m "slow or not slow"   # everything
uv run ruff check src tests ; uv run ruff format --check src tests ; uv run mypy
```
