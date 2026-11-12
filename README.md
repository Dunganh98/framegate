# agentflow

Build features with Claude Code agents, test-first, inside checks they cannot switch off. You
are the manager: you write the requirements and make two decisions per feature, **spec OK**
and **result OK**, each from a short brief in plain words (the detailed files are there on
request).

```
spec (you) -> tests -> code -> review -> final gate (you) -> retro -> done
```

## Install (once per machine)

```
claude plugin marketplace add Dunganh98/agentflow
claude plugin install agentflow@agentflow
```

That is all: `/agentflow:setup` in a repository installs the `agentflow` command (with
[uv](https://docs.astral.sh/uv/), from this repository's release tag) and what the project's
environment needs. You need read access to this repository and git credentials for it on the
machine.

Working on agentflow itself, from a clone:

```
uv tool install --editable <path-to-agentflow>
claude plugin marketplace add <path-to-agentflow>
claude plugin install agentflow@agentflow
```

Release: one version for the CLI and the plugin (`pyproject.toml`, `src/agentflow/__init__.py`,
`plugin/.claude-plugin/plugin.json`, the setup skill), then `git tag v<version>` and push the tag.

## Use

1. **Set up the repo (once):** in Claude Code run `/agentflow:setup`: it first installs the
   tools the workflow needs into the project's environment, then asks its questions; commit
   the files it lists.
2. **Start a feature:** `/agentflow:feature <name>`, describe what you want, answer the few
   questions (each has a default), check the spec brief (above all "Decisions I made for
   you"), then say **"spec OK"**.
3. **Wait:** the agents run in the background; the chat shows progress and cost. If a step
   fails the same way twice, a diagnosis agent finds the cause: a code fix is retried at
   once, a test or spec fix waits for you at the final gate. If only a step's tests are
   wrong, its code is kept as provisional and later steps build on it; you approve the
   proposed test fixes at the final gate in one reply.
4. **Final gate:** read the brief (~20 lines: plan followed?, numbers, struggles, what is
   proven and what to check by hand, the decisions made for you, and only the real decisions as
   questions), then reply **"ok"** (takes every default of the numbered questions, or approves
   when there are none), **"no retro"**, or answer questions by number (only the affected work
   is redone).
5. **Done:** pick the retro proposals and facts by number (the proposals go to the retro
   backlog, `agentflow backlog`, so none is lost and repeats are counted), create the PR if
   you want.

A bug or a refactor runs the same way with another first proof: `/agentflow:bugfix <name>`
(the tests must fail on today's code, for the reported reason, before anything is fixed) and
`/agentflow:refactor <name>` (the tests must pass on today's code and after every step).

Chat closed? `/agentflow:resume <name>`. Overview: `/agentflow:status`.

## Commands

In the chat:

| Command | Does |
|---|---|
| `/agentflow:setup` | set up agentflow in this repo |
| `/agentflow:feature <name>` | build a feature, start to finish |
| `/agentflow:bugfix <name>` | fix a bug: first a test that fails because of it |
| `/agentflow:refactor <name>` | restructure code: first tests that pin what it does today |
| `/agentflow:resume [name]` | reconnect the chat to a feature |
| `/agentflow:status [name]` | show where features stand |

In a terminal (the chat runs these for you):

| Command | Does |
|---|---|
| `agentflow install-tools` | install what the workflow needs into the project's environment (setup does this first) |
| `agentflow doctor` | check the setup (no agents, no cost) |
| `agentflow run <name>` | continue a feature until it needs you |
| `agentflow status <name>` | show its state |
| `agentflow approve <name>` | accept the waiting gate |
| `agentflow answer <name>` | apply your answers from `out/<name>/answers.md` |
| `agentflow cleanup <name>` | delete the run files after DONE |
| `agentflow stats [name]` | what the features cost: money, agent and check time, final-gate runs |
| `agentflow backlog` | the retro proposals you accepted and what became of them |

All commands and options: `agentflow --help` and [docs/reference.md](docs/reference.md).

## Docs

- [docs/reference.md](docs/reference.md): phases, gates, every command, config, retro, hooks
- [docs/plugin.md](docs/plugin.md): the Claude Code plugin
- [docs/adapters.md](docs/adapters.md): languages (Python, .NET, generic for any other)
- [docs/AGENTIC_WORKFLOW_SETUP.md](docs/AGENTIC_WORKFLOW_SETUP.md): the design

## Development

```
uv sync
uv run pytest          # unit + end-to-end tests (fake claude, no API calls), ~3 min
uv run pytest -m slow  # real toolchains: .NET e2e (dotnet SDK) ~1.5 min, mutmut via WSL ~15 s
uv run pytest -m "slow or not slow"   # everything
uv run ruff check src tests ; uv run ruff format --check src tests ; uv run mypy
```
