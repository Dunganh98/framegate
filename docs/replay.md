# framegate replay

A replay takes a real commit of a project, turns it into a task, and lets framegate deliver it
in a sandbox that holds the code before that commit. The commit's own tests are hidden and
used afterwards as the score. It measures framegate; why and by which rules:
[replay-candidates.md](replay-candidates.md).

    framegate replay candidates.md

Needs: git, the `claude` CLI with the framegate plugin installed user-wide, and `uv` (without
it: `venv` and `pip`). Python projects only: the hidden tests run with pytest.

## The candidates file

One `## <name>` section per commit. The name is the framegate feature name (a-z, 0-9, `-`).
Text before the first section is ignored. `framegate replay --template` prints an empty file.

```markdown
# Replay candidates

## released-lock-free-at-once
- repo: ../da-otto
- commit: fd83df7
- kind: bugfix
- tests: tests/test_controller.py
- probes: tests/test_lock_rules.py
- extra: tests/test_lock_after_release.py
- pythonpath: ., platform
- install: uv pip install --python {py} -r requirements.txt
- max-usd: 15
- max-hours: 2

### Task
A lock that a run released is not free for the next run until its lease time has passed.
A released lock must be free at once.

### Facts
- F1 [lease time, expiry]: a lock that was not released still expires after its lease time
- F2 [second release]: releasing a lock twice is no error
```

| Field | | Meaning |
|---|---|---|
| `repo` | required | path (relative to the file) or URL of the repository |
| `commit` | required | the commit to replay, 7 to 40 hex digits |
| `kind` | required | `feature`, `bugfix` or `refactor`: the skill the driver starts |
| `tests` | optional | the commit's test files to hide, comma-separated. Default: the files the commit changes that are test files (`test_*.py`, `*_test.py`, anything under `tests/`) |
| `probes` | optional | test files of the candidate's author, comma-separated, kept under `probes/<name>/` next to the candidates file at the path they have in the repository. They state what the task must not change: they pass on the parent and on the commit, and must pass on the result |
| `extra` | optional | more test files of the author, under `extra/<name>/` in the same way. They prove the task beyond the commit's own tests: they fail on the parent and pass on the commit, and count like hidden tests |
| `pythonpath` | optional | import roots, comma-separated. Default: `src` if it exists, and `.` |
| `python` | optional | Python version of the sandbox's environment (needs uv) |
| `install` | optional, repeatable | a command that installs the project into the sandbox's environment; `{py}` is its python; paths with `/`. Default: `requirements.txt`, then `-e .` |
| `setup` | optional | what the user tells the setup up front, sent with `/framegate:setup` (e.g. `mutation off for this project: mutation_tool = "none"`) |
| `max-usd`, `max-hours` | optional | caps of this candidate |
| `### Task` | optional | what is wanted, as a user would report it: short, no function, file or test names. Free markdown up to the next `## ` or `### Facts`; headings inside it need `###` or deeper. Never write it from the diff. Default: the commit message |
| `### Facts` | optional | what the user knows beyond the task text, one per line: `- F1 [topic words]: the fact` (a fact may go on in the next line). The driver gives a fact only when the chat asks a question that the fact answers |

A file with a mistake is refused with the line and what is wrong.

## What happens per candidate

1. **Sandbox.** A clone cut at the parent commit: the commit and everything later is not
   reachable, there is no remote. It gets its own `.venv` with the project, pytest and the
   framegate that runs the command. The commit's tests, its patch and its message go to
   `answers/`. The task text goes to the driver's folder as `task.md`, the facts as
   `facts.md`.
2. **Check.** The commit's tests run on the parent and on the commit. The tests that fail on
   the parent and pass on the commit prove the task. A candidate without one is dropped, with
   the reason in the results. So is one with a probe that does not pass on both, or an extra
   test that does not fail on the parent and pass on the commit.
3. **Run.** One headless `claude` agent plays the user by fixed rules
   (`src/framegate/replay/driver.md`). Its order: the setup (with the candidate's `setup`
   text); then a new chat session that gets the task text first, with one line saying the
   task starts with the next message, and then the start command `/framegate:<kind> <name>`
   and nothing else, so the chat has the task before it asks anything; the spec dialogue;
   every gate; the retro. It answers by a fixed policy: a fact where a question asks for
   what the fact says, verbatim and never unasked; else the recommended or default option by
   its letter; for an item that says "No default: choose." the option that changes the code
   (a repair objection) or redoes the work (nothing built). It never publishes, pushes, opens
   a pull request or cleans up. Each chat message runs as a process of its own (`python -m
   framegate.replay.chat`, the answer with `--wait`), and `framegate run` starts itself
   detached (`FRAMEGATE_DETACH_RUN=1`), so neither a time limit of the driver's tools nor the
   chat's next turn can cut a run.
4. **Caps.** 25 USD (engine and chat) and 3 hours per candidate; time spent waiting for the
   usage limit does not count. At a cap the run is stopped. A run that did not reach DONE
   still gets a retro (`framegate retro`, where this framegate has it) and a score; a run
   that ended before framegate built anything (in the setup or the spec) gets no retro.
5. **Inside the run folder only.** Nobody answers permission prompts in a replay, so the
   chat may run any shell command. What keeps it, framegate's agents and the driver inside
   the run folder is each folder's `.claude/settings.local.json`: deny rules (push, network,
   publish, cleanup, update, `claude`, global git config, package managers of the machine;
   reading the hidden files) and a guard hook before every shell, Edit and Write call
   (`python -m framegate.replay.guard`, `guard.py`): no path outside the run folder (temp
   folders and home included; the installed plugins may be read), and none of those commands
   where a command starts. The build tries the hook once and drops the candidate if it does
   not block: a hook that cannot start blocks nothing. Windows has no folder lock for a
   process: a path the command hides (built in a script) gets past the hook.
   Hidden from the chat, framegate's agents and the driver: `answers/`, the source
   repositories, and the folder of the candidates file (when the runs are inside that
   folder: the candidates file, `probes/` and `extra/`). The chat and framegate's agents
   cannot read the driver's folder either, so `facts.md` reaches the chat only as single
   answers.
6. **Score.** The hidden tests run on a copy of the result; then the extra tests and the
   probes are laid over that copy and run, never over the sandbox. The verdicts:

   | Verdict | Meaning |
   |---|---|
   | `good` | every hidden and extra test passes, no regression |
   | `not proven` | a hidden test was skipped: a skip is not a pass |
   | `broke a rule` | the hidden tests pass, a probe fails |
   | `bad` | a hidden or extra test fails, or a test that passed on the parent fails now (a regression) |
   | `not run` | the run ended before framegate built anything (in the setup or the spec): no result for framegate. The Note has the stage and the driver's reason (its `outcome.txt`) |
   | `score failed` | a scoring run without a result (pytest could not start, was killed, timed out, or ran no test; also when the extra tests or the probes did not run): never "bad". The Note has the reason. The next run, or `--score-only`, scores that candidate again; the run itself is not repeated |

   A candidate that was not scored has its state in the Verdict column: `dropped` (the
   check), `prepared` (`--prepare-only`), `error`, `not scorable`.

## Options

| Option | |
|---|---|
| `--out DIR` | folder of the runs. Default: `<candidates file>-replay` next to the file |
| `--parallel N` | candidates at a time (3). One that fails does not stop the others |
| `--max-usd X`, `--max-hours H` | the caps per candidate (25, 3); a candidate's own `max-usd` / `max-hours` win |
| `--only NAME` | this candidate only (repeatable) |
| `--prepare-only` | build and check the sandboxes; no agent, no tokens |
| `--score-only` | score the existing runs again, without running them. A pruned run keeps its verdict: only the numbers that need no environment (the engine's events, the driver's files, git) are read again |
| `--prune` | after a candidate is scored, delete what can be rebuilt: the sandbox's environments and tool caches and the scored copy (`score/result`). Prints the MB freed. Alone on a finished round it prunes the scored candidates without running anything |
| `--force` | also run candidates that have a score. Without it a rerun skips them |
| `--template` | print an empty candidates file |

A pruned run keeps the evidence: `driver/`, `score.json`, `retro_proposals.md`,
`score/result.xml` and `.log`, and the sandbox's code, git history, `out/`, `specs/`,
`.framegate/` and `framegate.toml`. Its environment is gone, so its tests cannot run again;
`--force` runs the candidate from the start. A candidate whose score failed is not pruned.

`FRAMEGATE_REPLAY_PLUGIN=<folder>` makes the chat load the plugin from that folder (a checkout's
`plugin/`) instead of the installed one: for measuring a version before its release.
`FRAMEGATE_REPLAY_CHAT_MODEL` and `FRAMEGATE_REPLAY_DRIVER_MODEL` set the models of the chat and
the driver (default for both: `sonnet`).

## What you get

```
<out>/results.md, results.json   the two tables below, one row per candidate
<out>/retro-proposals.md         the retro proposals of every run
<out>/runs/<name>/sandbox/       the repository as the run left it
<out>/runs/<name>/driver/        task.md, facts.md, setup.txt; the driver's log.md, report.md,
                                 outcome.txt and facts_used.json; every chat turn (turn-NN.json)
<out>/runs/<name>/score.json     the row, with the names of failed and skipped tests
<out>/runs/<name>/score/         result.xml and result.log of the hidden tests, extra.xml,
                                 probes.xml; result/ is the scored copy
<out>/answers/<name>/            the hidden answer and the check's logs
```

The main table of results.md:

| Column | Meaning |
|---|---|
| Candidate, Kind | from the candidates file |
| Verdict | see "Score" above |
| Hidden tests | passed / proving tests, and how many were skipped |
| Extra | passed / extra tests; `-` without extra tests |
| Probes | passed / probes; `-` without probes |
| Regressions | tests that passed on the parent and fail now |
| Stage | the phase the run reached (`SETUP`, `SPEC`, ..., `DONE`) |
| Time | wall time without usage-limit waits |
| Total USD | engine + chat + driver |
| Note | see below |

The details table (scored runs only):

| Column | Meaning |
|---|---|
| Engine USD, Chat USD | the cost of framegate's agents; the cost of the chat sessions |
| Final rounds | final-gate runs |
| Redos | redo results of parked work |
| Facts asked | facts the driver gave on a question / facts of the candidate |
| Spec questions | questions with options the chat asked before the spec approval |
| Questions | questions the engine's agents collected for a gate |
| Late bugs | review findings judged valid + failed final-gate runs |
| Send-backs | test-check rounds and test-review rounds sent back + step retries |
| Source lines (run / commit) | added + removed lines, without tests, documents, config, dot folders, framegate's files and format-only commits; the run's from the task's start, the commit's from its patch |

`-` means no data. What the Note can hold, joined by `;`:

| Note | Meaning |
|---|---|
| `score failed (<reason>): run --score-only again` | the scoring run gave no result |
| `probes failed: <names>`, `extra tests failed: <names>` | which ones |
| `cost cap: ...`, `time cap: ...` | the run was stopped at a cap |
| `ended at <stage>`, then the driver's `outcome.txt` line | the run did not reach DONE |
| `retro ...` | the result of the retro of a run that stopped before DONE |
| `nothing built` | the engine ran and the run left no source change |
| `ran as <mode>, not <kind>` | the task ran in another mode than the candidate's kind |
| `approved with failed checks: <checks>` | the final gate was approved while final checks failed |
| the reason | for a dropped candidate, an error, or a run that cannot be scored |

`framegate replay` exits 1 if a candidate ended in an error or its score failed.
