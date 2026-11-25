---
name: setup
description: Set up framegate in this repository - probe the project (interpreter, test / lint / format / type commands, folders, branch, remote), show one table of these facts for the user to confirm, write framegate.toml, the project knowledge file and the code map, check with framegate doctor, look once at what is red on the code as it is (failing tests, lint / format debt), commit the setup's files, offer a smoke run. Run only when the user invokes it.
disable-model-invocation: true
allowed-tools: Bash(framegate *) PowerShell(framegate *) Bash(uv tool install *) PowerShell(uv tool install *) Read Write Edit Glob Grep
---
You set up framegate for this repository, once per project. Two commands do the work:
`framegate setup --plan` and `framegate setup --apply`. You run them, show their output as
it is, and take the user's reply. The user is the manager: they confirm one table. Plain
words, the decision first. Do not run the project's tests or linters yourself (`--apply` runs
them once), do not probe or edit framegate.toml by hand where a row or `--set` does it, and
never change the project's source or tests.

## 0. The command-line tool

Run `framegate --version`. If the command is not found, tell the user in one line
("Installing the framegate CLI 0.13.3 for your user") and run

    uv tool install "git+https://github.com/Dunganh98/framegate@v0.13.3"

then `framegate --version` again. If `uv` is missing or the install fails, show the error and
the two ways out, then stop: install uv (https://docs.astral.sh/uv/) and run the setup again,
or clone the repository and run `uv tool install --editable <clone>`. An installed CLI of
another version than 0.13.3 (this plugin's): do not install over it yourself. Run
`framegate preflight --plugin-version 0.13.3` and act on its lines: `RUN: <command>`: run it,
then `framegate --version` again (say nothing when it worked, one line when it failed).
`KEPT: ...`: a task is in progress, the update waits; say so in one line and go on. If the
command fails with `invalid choice: 'preflight'` or `invalid choice: 'setup'` (a tool too
old), run the install command above with `--force`.

Automatic updates (ask at most once per machine): read
`~/.claude/plugins/known_marketplaces.json`. If its `framegate` entry has no
`"autoUpdate": true`, ask in one line: "Keep framegate up to date automatically? (default:
yes)". On yes, tell the user the one step only they can do: `/plugin` > Marketplaces >
framegate > Enable auto-update. The file is missing or has no `framegate` entry: skip this
without a word.

## 1. Plan: `framegate setup --plan`

Tell the user in one line that this takes a few minutes the first time (an agent maps the
folders), start it in the background and wait for it. It installs the workflow tools into the
project's environment, maps the source and test folders from the code (once; `--survey` maps
them again), probes the interpreter, the commands and git, picks the adapter, and prints the
confirmation table with the decisions it needs.

Show its output as it is: the table, the lines under it, the decisions. Do not rewrite, sort
or shorten it, and do not replace a value by one you assume. Then act on what it says:

- "This repository is set up already": say so and stop.
- `error:` (the tools are not installed): show it and stop.
- A line `CHANGED versions`: show it as it is (a pinned package moved: the user decides).
- `Blocker:` lines and "Decisions the repository cannot answer": the user answers each with
  the table. Generic adapter (language not detected, or another test framework): run
  `framegate init --language generic`, fill its `[generic]` section from the project's files
  (test commands with `{junit}`, `{ids}` / `{ids_regex}`, `{paths}`; `tag_pattern`;
  `stub_marker`), then the plan again. No test framework at all, or no way to tag a test
  with spec IDs: say so with the smallest helper you would add, and wait.
- `**Environment**` lines (a framework the project declares and the environment lacks): show
  them; nothing to decide now, the spec dialogue of a task that needs one asks.

What the rows mean, when the user asks: row 9 yes = framegate's own files live on a branch
`framegate/<base>` that is pushed and never merged, pull requests carry only source and
tests; row 10 = deploy and CI paths no task may change (a built-in name comes off with
`--set builtin_off=<name>`); row 13 = the project's own rule commands, run at every step
gate; row 14 = mutation testing on or off; the machine files line = tool state of this
machine, ignored by framegate (`--set machine_files=<path>` adds one, `--set
project_folders=.config/` takes a dot folder back).

## 2. Apply: `framegate setup --apply`

After "ok" (= every row as shown) or the changes. Each change is one `--set <row>=<value>`
in the user's words turned into the value: `--set "5=python -m black --check app tests"`,
`--set 6=off`, `--set "7=app, lib / tests"`, `--set 9=no`, `--set 2=.venv/Scripts/python.exe`,
`--set "13=python ci/check_rules.py src tests"`, `--set 14=off`. If the project has a folder
named like one of framegate's (`specs/`, `metrics/`) and row 9 is yes: `--set
metrics=.framegate/metrics`. Tell the user in one line that all tests run once now, run it
and show its output as it is. It writes framegate.toml (only what differs from the defaults)
and the templates, installs the tools the gate commands name, checks the setup, creates the
state branch once everything is settled (nothing is pushed), and ends with "Left to do".

The command can run again, with more `--set` / `--red`; earlier ones are kept. Go by the
prefix of the check's lines:

- `Verdict: NOT READY` with a FAIL that is a config problem: fix it with a `--set` and run
  the command again.
- `PROBLEM: N test(s) fail on the untouched tree and are not listed:` and one line per test.
  Every red test ends as made to run, or listed with a reason after the user's OK. Sort them
  from those lines (read the test file if you cannot place one; do not run it): only lacks a
  package (name the requirement; on the user's yes: `--apply --install "<requirement>"`;
  the command handles a failed install itself and says so in one line: show it, never
  send the user to change the machine), cannot run on this machine (a database, a service,
  another operating system), red before any task (the project's own debt). Show them in one
  message and ask once:

      **3 tests fail on the code as it is**
      I would list them; every check then leaves them out, and every final summary names
      them with the reason:
      1. tests/test_db.py (2 tests): needs a PostgreSQL server (connection refused)
      2. tests/test_price.py::test_rounding: red before any task: AssertionError: 10.01 != 10.0
      Reply "ok", or the number and the change ("2: I fix it first").

  Then one `--red "<test file[::test]>=<reason>"` per confirmed entry (the whole file when
  all its tests fail for the same reason; a debt entry's reason starts with "red before any
  task:"). Never pass an entry the user did not confirm; a test they fix first stays
  unlisted and the setup stays not ready until it is green.
- `LISTED:` lines: name the listed tests once. A `->` line under them counts the debt
  entries: suggest one task for them (`/framegate:bugfix <name>`).
- `PROBLEM: the whole suite did not run to the end` or `gave no per-test results`: fix the
  test command (`--set 3=...`) and run it again.
- `DEBT:` lines (a lint / format / type / rule check fails on the code as it is): report the
  size in one line and propose ONE clean-up task, to accept or skip
  (`/framegate:refactor cleanup-lint`). No question per file, no list of files.
- `MUTATION:` (the tool cannot run here): make it run (the fix the line names), or ask once:
  "Mutation testing cannot run here: <reason>. Switch it off for this project?" On yes:
  `--set 14=off`. The setup is not ready before.
- `Tree: green.`: say so in one line.

## 3. What is left

The last lines of `--apply` ("Left to do") name each of these that is open:

- **The knowledge file and the code map.** Read, from files only (Read / Glob / Grep), a few
  minutes: `CLAUDE.md`, the README, a contributing or architecture doc, the top-level
  folders and a file or two per module. Never a machine folder (`.serena/`, `.vscode/`,
  `.venv/`, ...). The code and the build files are the truth, docs say where the project is
  heading, a comment is no evidence. Write `.framegate/project.md` (sections Architecture
  rules / Never changes / Test harness / Tools / Off-limits; one short verifiable line per
  fact, under ~150 lines) and the code map (the path the line names, under ~300 lines):

      # Code map

      ## <Module or folder>
      Purpose: <one line>
      Key classes:
      - `<Type>`: <what it is for>
      How they work together: <A builds B, C reads B to ...>
      Where to look for: <a task> -> `<file>`

  A test or lint command a doc names that differs from the table: say so and ask; on a yes
  it is one more `--set`. Show the user the facts and the modules in a line each.
- **Row 9 = yes, Python:** add the line `req(*ids): spec IDs this test proves` to `markers`
  in the project's own pytest config and tell the user about this one line: the pull request
  carries the tests without framegate, and pytest must know the marker there.
- **The CLI allowed for the chat:** offer to add `"Bash(framegate *)"` and
  `"PowerShell(framegate *)"` to `permissions.allow` of `.claude/settings.local.json`; write
  the file only on a yes.
- **The setup's own commit.** Run `framegate commit-setup --dry-run` (with the path of a tag
  helper if you added one). "Setup files: nothing to commit.": go on without a word. Else
  show the list in one line and ask: "Commit these setup files now, in one commit of their
  own? (default: yes)". On yes run `framegate commit-setup` with the same paths and report
  its result in one line. On no: say that `framegate spec` will refuse while a tracked one of
  them is uncommitted. Never run git yourself, and do not push. With a state branch
  framegate's own files are on that branch; the first `framegate publish` pushes it.

## 4. Offer a smoke run, in one line

"Optional: a smoke run (a tiny throwaway feature through the whole flow, about USD 2-4 and
15-30 minutes) proves the agents, gates and hooks work before a real feature. Do it?
(default: yes)". On yes: tell them to run `/framegate:feature smoke` with this requirement
(adapt the names to the language):

    A pure function clamp(value, low, high) in the source folder: returns value limited to
    [low, high]; low > high raises an argument error with the message "low > high". One plan
    step. No other code changes.

and to remove it afterwards: `framegate cleanup smoke --tags --force`, then switch back to the
base branch and delete the branch `feat/smoke` (with a state branch: the branch is
`framegate-work/smoke`, and the smoke feature is never published).
