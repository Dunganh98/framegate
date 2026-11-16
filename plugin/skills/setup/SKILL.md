---
name: setup
description: Set up framegate in this repository - probe the project (interpreter, test / lint / format / type commands, folders, branch, remote), show one table of these facts for the user to confirm, write framegate.toml, the project knowledge file and the code map, check with framegate doctor, offer a smoke run. Run only when the user invokes it.
disable-model-invocation: true
allowed-tools: Bash(framegate *) PowerShell(framegate *) Bash(uv tool install *) PowerShell(uv tool install *) Read Write Edit Glob Grep
---
You set up framegate for this repository, once per project. The user is the manager: they
confirm one table; you probe, read, write the files and run the `framegate` commands. Plain
words, the decision first, details only on request. The whole setup takes a few minutes: do
not run the project's tests or linters yourself, and do not study the code in depth. You never
change the project's source or tests (one exception, step 3: a tagging helper the user
approved).

## 0. Install the tools first

The `framegate` command itself: run `framegate --version`. If the command is not found, tell
the user in one line ("Installing the framegate CLI 0.12.1 for your user") and run

    uv tool install "git+https://github.com/Dunganh98/framegate@v0.12.1"

then `framegate --version` again. If `uv` is missing or the install fails (no access to the
repository, no git), show the error and the two ways out, then stop: install uv
(https://docs.astral.sh/uv/) and run the setup again, or clone the repository and run
`uv tool install --editable <clone>`. An installed CLI of another version than 0.12.1 (this
plugin's): run the same command with `--force` (the tool follows the plugin's version), then
`framegate --version` again; say nothing when it worked, one line when it failed, and go on.

Automatic updates (ask at most once per machine): read `~/.claude/plugins/known_marketplaces.json`.
If its `framegate` entry has no `"autoUpdate": true`, ask in one line: "Keep framegate up to
date automatically? (default: yes)". On yes, tell the user the one step only they can do:
`/plugin` > Marketplaces > framegate > Enable auto-update. From then on Claude Code fetches
a new plugin version in the background, the next session uses it and shows one line saying
what changed, and the next new feature brings the command-line tool to the same version.
The file is missing or has no `framegate` entry (a local install): skip this without a word.

Then run `framegate install-tools --update --dry-run`: it names the project's
interpreter and what the workflow needs there and is missing (for Python: the framegate pytest
plugin, pytest, pytest-cov, hypothesis, diff-cover) or is of another version than the
command-line tool (`[update ]`: the framegate pytest plugin). Nothing to install: go on without
a word. Else tell the user in one line what goes into which interpreter ("Installing framegate,
diff-cover, hypothesis into .venv") and run `framegate install-tools --update`. Report its result in one line; if it
prints `CHANGED versions`, show that line as it is (a pinned package moved: the user decides
whether to keep it). If the install fails, show the error and stop: the setup cannot be
checked without the tools. A language framegate installs nothing for says so; go on.

Then run `framegate state`. If it prints `State branch: <name> (...)` and this checkout has
no `framegate.toml`, the repository is set up already and keeps framegate's files on that
branch (pushed by whoever set it up): do not draft anything. Say so in one line, tell the user
to start with `/framegate:feature <name>` (it fetches the branch and brings the files in), and
stop here. An error ("no framegate.toml found") or `State branch: off` means: go on.

## 1. Probe, then read a little (no edits yet)

Run `framegate probe`. In seconds, without running a test, a linter or an agent, it prints one
line per fact: the language, the interpreter every run uses, the test / lint / format / type
commands with `[OK: <version>]` or `[MISSING]` (each tool was asked for `--version` in that
interpreter) and where each comes from, the source and test folders, the base branch, the
remote and its host, the deploy and CI paths that are locked or should be, the reinstall
command, the reference projects, and which of framegate's files exist. It works before a
`framegate.toml` exists (the values are proposals from the project's own files) and with one
(the values are the configured ones). These lines fill the table of step 3: do not replace a
probed value by one you assume.

Then read, from files only (Read / Glob / Grep), just enough for the knowledge file and the
code map; a few minutes, not a study:
- `CLAUDE.md`, the README, a contributing or architecture doc: rules, conventions, things
  that must not change, the test or lint commands the team documents (one that differs from
  a probed value goes into the table with its file as the source);
- the top-level folders and, per module, a file or two: what it is for, its key classes, how
  the modules work together;
- `framegate.toml`, `.framegate/project.md`, the code map when the probe says they exist
  (then you update them).

Not now: which tests are slow, coverage or mutation numbers, test data, risk limits.

## 2. Pick the adapter

`python` (pytest) and `dotnet` (xUnit) have built-in adapters; the probe names the one it
detected. `Language: unknown`, or another test framework: `generic`, configured entirely in
framegate.toml `[generic]` (`framegate init --language generic` writes a commented example):
the test commands with placeholders (`{junit}` report path, `{ids}` / `{ids_regex}` spec-ID
selection, `{paths}`), a `tag_pattern` regex that finds a test's spec IDs, the `stub_marker`
a not-implemented stub prints, optional coverage (Cobertura / LCOV + `diff-cover`), mutation
(command + score regex) and `format_fix`. You fill its table rows from the project's files;
their "Answers" cell reads "checked in step 5".

## 3. One table to confirm

Draft (in your head, no files yet) the knowledge file `.framegate/project.md` (sections
Architecture rules / Never changes / Test harness / Tools / Off-limits, one short verifiable
line per fact) and the code map. Then show this, every time, the same rows in the same order,
no toml and no file dumps:

    **Please confirm the setup** - reply "ok", or the row and the change ("5: use black")

    | # | What | Value | From | Answers |
    |---|---|---|---|---|
    | 1 | Language / adapter | python / python | pyproject.toml | |
    | 2 | Interpreter | .venv/Scripts/python.exe | the project's .venv | OK (Python 3.12.4) |
    | 3 | Test command | python -m pytest -q | pytest.ini | OK (pytest 8.3.2) |
    | 4 | Lint | python -m ruff check app tests | pyproject.toml [tool.ruff] | OK (ruff 0.6.9) |
    | 5 | Format | python -m black --check app tests | pyproject.toml [tool.black] | OK (black 24.8.0) |
    | 6 | Types | off | the project names no type checker | |
    | 7 | Source / test folders | app / tests | the folders | |
    | 8 | Base branch | main | origin/HEAD | |
    | 9 | Shared repository: framegate's files on their own branch | yes, framegate/main | remote on Azure DevOps, 4 authors | |
    | 10 | Locked deploy / CI paths | azure-pipelines.yml, deploy/, Dockerfile | probe | |
    | 11 | Reinstall when a spec changes packaging | python -m pip install -e . --no-deps | editable install found | |
    | 12 | Reference projects | none | | |

    **What agents will know** - the knowledge facts, one short line each
    **Code map** - the modules it lists, in one line
    Defaults taken for everything else (which tests the step checks leave out, coverage and
    mutation thresholds, risk limits): change them later in framegate.toml.

How the rows are filled:
- Value, From and Answers are the probe's, word for word where it gave them. A value you
  took from a doc instead: name the file under From.
- Rows 4-6: where the probe prints a second line ("framegate's default is ...; to use the
  project's tool set ..."), the value is the project's tool, as shown. A tool the probe marks
  "framegate's default: the project's files do not name it": the value is "off" (agents are
  never told a command the project does not use); only a project with no code yet keeps
  framegate's defaults.
- `MISSING` on a tool the project names: keep the row and write "MISSING, installed in step
  5" under Answers (for Python: ruff, mypy, black); for any other tool write "MISSING" and
  ask under the table how to get it.
- Row 2, when the probe says "framegate's own interpreter: the project has no .venv": that is
  not the project's environment. Write "none found" and ask under the table which interpreter
  the project runs in (`[env] python`).
- Row 3 carries the test selection only when the project's own files give one (an `addopts`
  line, a documented `-m "not integration"`): `[python] pytest_args` / `[dotnet] base_filter`.
- Row 9, same meaning as ever: yes = framegate's own files (config, specs, metrics, project
  facts, code map) live on a separate branch `framegate/<base>` that is pushed but never
  merged, and pull requests carry only source and tests; no = they are committed with the
  code. The default is the probe's "suggested" (yes when the remote is shared with a team,
  no for a repository of your own or without a remote); "no remote" under From when there
  is none.
- Row 10: the exact files and folders the probe names: its locked paths ("built in" ones
  such as Dockerfile, compose files and CI yml are locked in every project, nothing to write)
  plus its lock candidates (on "ok" they go into `[protected] extra`). The user takes one
  out: a candidate is left out of `extra`; a built-in name goes into `[protected]
  builtin_off`.
- Rows 11 and 12: "none" when the probe found none.

Below the table, a line "Blocker:" only for what the repository cannot answer and the setup
cannot go on without: no test framework at all; a framework with no way to tag a test with
spec IDs and select by them (propose the smallest helper, e.g. a `req("X-R-001")` tag the
test runner can filter on); the interpreter of row 2. One line each, with your default.
Nothing else is asked. The toml, the knowledge file or the code map in full only on request.

## 4. Write

After "ok" (= every row as shown) or the changes: if there is no framegate.toml yet, run
`framegate init` (or `framegate init --language generic`); it creates framegate.toml,
`specs/_template/`, `.framegate/project.md` and `.gitignore` lines, and never overwrites a
file. Then edit framegate.toml, only the keys whose row differs from framegate's default:

- row 1 `[project] language`; row 2 `[env] python` (a `.venv` in the project needs no entry);
- row 3 `[python] pytest_args`, `[dotnet]` or `[generic]`;
- rows 4-6 `[gates.commands]` `lint` / `format` / `typecheck`: the lines the probe printed
  after "set" (with `format_fix` when it names one), `[]` for "off";
- row 7 `[paths] src` (one folder or a list, e.g. `["core", "autoload", "scenes"]`) and `tests`;
- row 8 `[git] base`; row 9 `[git] state_branch = "framegate/{base}"` for yes;
- row 10 `[protected] extra`; row 11 `[env] refresh`; row 12 `[references]`.

Write `.framegate/project.md` (under ~150 lines: facts, not prose) and the code map
(`[docs] code_map`, under ~300 lines) in this form:

    # Code map

    ## <Module or folder>
    Purpose: <one line>
    Key classes:
    - `<Type>`: <what it is for>
    How they work together: <A builds B, C reads B to ...> (a small mermaid diagram is fine)
    Where to look for: <a task> -> `<file>`

Every later feature's SUMMARY phase updates the sections it touches.

Row 9 = yes: take the lines `framegate init` added to `.gitignore` out again (`.gitignore`
is the team's file; `framegate state init` ignores the run files through
`.git/info/exclude`); if the project has a folder named like one of framegate's (`specs/`,
`metrics/`), point framegate's path elsewhere (`[paths] metrics = ".framegate/metrics"`).
For Python also add the line `req(*ids): spec IDs this test proves` to `markers` in the
project's own pytest config (`pytest.ini`, or `[tool.pytest.ini_options]` in
`pyproject.toml`; create the `markers` list if there is none): the pull request carries the
tests without framegate, and pytest must know the marker there. Tell the user about this one
line: it is the only change to a file of theirs, and the first feature's pull request brings
it to the base.

Row 10, `[protected] extra`: deploy and CI files outside the built-in list (`.github/`,
`.gitlab-ci.yml`, `azure-pipelines.yml`), e.g. `ci/`, `deploy/`, `k8s/`, `Dockerfile`,
`docker-compose*.yml`, generated files such as `*_pb2.py`. They are locked for every feature,
also in its spec phase; what a feature needs there is noted in its spec ("Pipeline
follow-up"), raised in the final summary and done as a separate step. The packaging / test /
tool-config files need no entry: the language adapter protects them, and they are the setup
files a spec phase may change.

Row 11, `[env] refresh`: every spec approval runs it, so a package or source folder a feature
adds in its spec phase imports in the run without a step by hand.

## 5. Check: `framegate doctor --brief`

First `framegate install-tools` once more: with framegate.toml written it also installs the
lint / format / type tools the gate commands name (ruff, mypy, black), which step 0 could not
know yet. Then run `framegate doctor --brief`: a one-line verdict (ready / not ready), then
only the failures and the warnings that need a decision. Fix every FAIL that is a config problem yourself and run
it again until it says ready. Show the user the verdict line and, per remaining warning, one line
with the decision and your default ("mutmut is not installed: mutation testing off until it is?
Default: yes"). The full output (`framegate doctor`) on request; `framegate doctor --full` also
times the whole test suite (the final regression run), offer it in one line.

With `[git] state_branch` set, doctor warns that the state branch is missing until this
last step, done once every other point is settled (a later edit of these files in this
checkout would no longer match the branch): run `framegate state init`. It creates the
state branch from framegate's files in this checkout and pushes nothing. Its result in one
line. From here on the files are changed on a feature branch only.

## 6. Offer a smoke run, in one line

"Optional: a smoke run (a tiny throwaway feature through the whole flow, about USD 2-4 and
15-30 minutes) proves the agents, gates and hooks work before a real feature. Do it?
(default: yes)". On yes: tell them to run
`/framegate:feature smoke` with this requirement (adapt the names to the language):

    A pure function clamp(value, low, high) in the source folder: returns value limited to
    [low, high]; low > high raises an argument error with the message "low > high". One plan
    step. No other code changes.

and to remove it afterwards: `framegate cleanup smoke --tags --force`, then switch back to the
base branch and delete the branch `feat/smoke` (with a state branch: the branch is
`framegate-work/smoke`, and the smoke feature is never published).

## 7. Finish

With `[git] state_branch` set, say in two or three lines: framegate's files are on the state
branch (step 5) and nothing of them is to be committed on the base; the first `framegate
publish` (offered at the end of a feature) pushes that branch; and the allow rules below.
Else, in two or three lines: what to commit (framegate.toml, `.framegate/project.md`, the code map,
`specs/_template/`, `.gitignore`, a tag helper if added) and that the chat skills need the CLI
allowed: `"Bash(framegate *)"` and `"PowerShell(framegate *)"` in `permissions.allow` of
`.claude/settings.local.json` (`framegate doctor` warns while they are missing; offer to add
them, and write the file only on a yes). Do not commit or push yourself.
