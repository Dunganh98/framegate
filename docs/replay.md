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
- pythonpath: ., platform
- install: uv pip install --python {py} -r requirements.txt
- max-usd: 15
- max-hours: 2

### Task
A lock that a run released is not free for the next run until its lease time has passed.
A released lock must be free at once.
```

| Field | | Meaning |
|---|---|---|
| `repo` | required | path (relative to the file) or URL of the repository |
| `commit` | required | the commit to replay, 7 to 40 hex digits |
| `kind` | required | `feature`, `bugfix` or `refactor`: the skill the driver starts |
| `tests` | optional | the commit's test files to hide, comma-separated. Default: the files the commit changes that are test files (`test_*.py`, `*_test.py`, anything under `tests/`) |
| `pythonpath` | optional | import roots, comma-separated. Default: `src` if it exists, and `.` |
| `python` | optional | Python version of the sandbox's environment (needs uv) |
| `install` | optional, repeatable | a command that installs the project into the sandbox's environment; `{py}` is its python; paths with `/`. Default: `requirements.txt`, then `-e .` |
| `max-usd`, `max-hours` | optional | caps of this candidate |
| `### Task` | optional | the task as the user would say it, free markdown up to the next `## `; headings inside it need `###` or deeper. Never write it from the diff. Default: the commit message |

A file with a mistake is refused with the line and what is wrong.

## What happens per candidate

1. **Sandbox.** A clone cut at the parent commit: the commit and everything later is not
   reachable, there is no remote. It gets its own `.venv` with the project, pytest and the
   framegate that runs the command. The commit's tests, its patch and its message go to
   `answers/`, which no agent may read.
2. **Check.** The commit's tests run on the parent and on the commit. The tests that fail on
   the parent and pass on the commit prove the task. A candidate without one is dropped, with
   the reason in the results.
3. **Run.** One headless `claude` agent plays the user by fixed rules
   (`src/framegate/replay/driver.md`): setup, the task, every gate, the retro. It never
   publishes, pushes, opens a pull request or cleans up.
4. **Caps.** 25 USD (engine and chat) and 3 hours per candidate; time spent waiting for the
   usage limit does not count. At a cap the run is stopped. A run that did not reach DONE
   still gets a retro (`framegate retro`, where this framegate has it) and a score.
5. **Score.** The hidden tests run on a copy of the result. A skipped hidden test is "not
   proven", not a pass. A test that passed on the parent and fails now is a regression.

## Options

| Option | |
|---|---|
| `--out DIR` | folder of the runs. Default: `<candidates file>-replay` next to the file |
| `--parallel N` | candidates at a time (3). One that fails does not stop the others |
| `--max-usd X`, `--max-hours H` | the caps per candidate (25, 3) |
| `--only NAME` | this candidate only (repeatable) |
| `--prepare-only` | build and check the sandboxes; no agent, no tokens |
| `--score-only` | score the existing runs again |
| `--force` | also run candidates that have a score. Without it a rerun skips them |
| `--template` | print an empty candidates file |

## What you get

```
<out>/results.md, results.json   one row per candidate: verdict, hidden tests passed, regressions,
                                 stage reached, time, engine cost, chat cost, final-gate rounds, redos
<out>/retro-proposals.md         the retro proposals of every run
<out>/runs/<name>/sandbox/       the repository as the run left it
<out>/runs/<name>/driver/        the driver's log.md and report.md, every chat turn (turn-NN.json)
<out>/runs/<name>/score.json     the row, with the names of failed and skipped tests
<out>/answers/<name>/            the hidden answer and the check's logs
```
