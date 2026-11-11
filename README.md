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
uv tool install --editable <path-to-agentflow>
claude plugin marketplace add <path-to-agentflow>
claude plugin install agentflow@agentflow
```

## Use

1. **Set up the repo (once):** in Claude Code run `/agentflow:setup`, answer its questions,
   commit the files it lists.
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
5. **Done:** pick the retro proposals and facts by number, create the PR if you want.

Chat closed? `/agentflow:resume <name>`. Overview: `/agentflow:status`.

## Commands

In the chat:

| Command | Does |
|---|---|
| `/agentflow:setup` | set up agentflow in this repo |
| `/agentflow:feature <name>` | build a feature, start to finish |
| `/agentflow:resume [name]` | reconnect the chat to a feature |
| `/agentflow:status [name]` | show where features stand |

In a terminal (the chat runs these for you):

| Command | Does |
|---|---|
| `agentflow doctor` | check the setup (no agents, no cost) |
| `agentflow run <name>` | continue a feature until it needs you |
| `agentflow status <name>` | show its state |
| `agentflow approve <name>` | accept the waiting gate |
| `agentflow answer <name>` | apply your answers from `out/<name>/answers.md` |
| `agentflow cleanup <name>` | delete the run files after DONE |

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
