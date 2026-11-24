---
name: setup
description: Set up framegate in this repository - probe the project (interpreter, test / lint / format / type commands, folders, branch, remote), show one table of these facts for the user to confirm, write framegate.toml, the project knowledge file and the code map, check with framegate doctor, look once at what is red on the code as it is (failing tests, lint / format debt), commit the setup's files, offer a smoke run. Run only when the user invokes it.
disable-model-invocation: true
allowed-tools: Bash(framegate *) PowerShell(framegate *) Bash(uv tool install *) PowerShell(uv tool install *) Read Write Edit Glob Grep
---
You set up framegate for this repository, once per project. The user is the manager: they
confirm one table; you probe, read, write the files and run the `framegate` commands. Plain
words, the decision first, details only on request. The whole setup takes a few minutes plus
one run of the project's tests: do not run the project's tests or linters yourself (step 5's
`framegate doctor --tree` runs them once), and do not study the code in depth. You never
change the project's source or tests (one exception, step 3: a tagging helper the user
approved).

## 0. Install the tools first

The `framegate` command itself: run `framegate --version`. If the command is not found, tell
the user in one line ("Installing the framegate CLI 0.12.7 for your user") and run

    uv tool install "git+https://github.com/Dunganh98/framegate@v0.12.7"

then `framegate --version` again. If `uv` is missing or the install fails (no access to the
repository, no git), show the error and the two ways out, then stop: install uv
(https://docs.astral.sh/uv/) and run the setup again, or clone the repository and run
`uv tool install --editable <clone>`. An installed CLI of another version than 0.12.7 (this
plugin's): do not install over it yourself. The tool is one per machine, and a task in
progress here or in another repository keeps the version it was started with. Run
`framegate preflight --plugin-version 0.12.7` and act on its lines: `RUN: <command>`: run it
(the tool follows the plugin's version), then `framegate --version` again; say nothing when
it worked, one line when it failed, and go on. `KEPT: ...`: a task is in progress, the update
waits until it is DONE; say so in one line and go on with the installed version. If the
command fails with `invalid choice: 'preflight'` (a tool too old to have it), run the install
command above with `--force`.

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

## 1. Survey, probe, then read a little (no edits yet)

Without a `framegate.toml` (or when the user asks to map the folders again), first run
`framegate survey` (a few minutes; start it in the background and wait for it). An agent reads
the code and the build and test files and maps the source folders, the test folder and what
is not the project's code; every code file must end up in one of them. It prints one line
each, with the file it took the answer from, and writes `out/survey.json`, which the probe
then reads for its folder lines. A `PROBLEM:` line names code files under no folder: put
them under the table as a question (default: add their folder to the source folders).

Then run `framegate probe`. In seconds, without running a test, a linter or an agent, it prints one
line per fact: the language, the interpreter every run uses, the test / lint / format / type
commands with `[OK: <version>]` or `[MISSING]` (each tool was asked for `--version` in that
interpreter) and where each comes from, the source and test folders, the base branch, the
remote and its host, the deploy and CI paths that are locked or should be, the tracked
machine files (tool state such as `.serena/`, `.vscode/`: ignored by framegate), the reinstall
command, the reference projects, and which of framegate's files exist. It works before a
`framegate.toml` exists (the values are proposals from the project's own files) and with one
(the values are the configured ones). These lines fill the table of step 3: do not replace a
probed value by one you assume.

Then read, from files only (Read / Glob / Grep), just enough for the knowledge file and the
code map; a few minutes, not a study:
The code and the build files are the truth, the docs say where the project is heading, and
comments or docstrings are no evidence of anything: write no fact into the knowledge file or
the code map that rests only on a comment.
- `CLAUDE.md`, the README, a contributing or architecture doc: rules, conventions, things
  that must not change, the test or lint commands the team documents (one that differs from
  a probed value goes into the table with its file as the source);
- the top-level folders and, per module, a file or two: what it is for, its key classes, how
  the modules work together. Not the machine folders (top-level dot folders of tools:
  `.serena/`, `.vscode/`, `.idea/`, `.venv/`, `.aws/`, ...): never read or glob through
  them, and none is a source folder, a test folder, a lock candidate or a module of the
  code map;
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
    | 7 | Source / test folders | app / tests | the survey (pyproject.toml packages) | |
    | 8 | Base branch | main | origin/HEAD | |
    | 9 | Shared repository: framegate's files on their own branch | yes, framegate/main | remote on Azure DevOps, 4 authors | |
    | 10 | Locked deploy / CI paths | azure-pipelines.yml, deploy/, Dockerfile | probe | |
    | 11 | Reinstall when a spec changes packaging | python -m pip install -e . --no-deps | editable install found | |
    | 12 | Reference projects | none | | |

    **Machine files, ignored by framegate** - .serena/project.yml (tool state of this machine, not the project's)
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
- Row 7: the survey's folders (the probe shows them), with the survey's evidence under From.
  A probe that still says "guessed from the folders" means the survey did not run: run it.
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
- The line "Machine files, ignored by framegate": the probe's "Machine files (tracked,
  ignored by framegate)" list, word for word; "none tracked" when it found none. These are
  tool state of this machine (what lies in a top-level dot folder such as `.serena/`,
  `.vscode/`, `.idea/`, `.venv/`, `.aws/`; not `.github/` and the other CI folders, not
  `.claude/`, `.framegate/`, `.devcontainer/`, not dot files at the root). framegate never
  holds them against a step (no check, no refusal at `spec` or `publish`, never in a pull
  request) and no agent writes them. The user changes the rule in one reply: "X is a
  machine file too" -> `[git] machine_files = ["X"]`; ".config/ is part of the project" ->
  `[git] project_folders = [".config/"]`.

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

## 5. Check: `framegate doctor --tree --brief`

First `framegate install-tools` once more: with framegate.toml written it also installs the
lint / format / type tools the gate commands name (ruff, mypy, black), which step 0 could not
know yet. Then tell the user in one line that the whole test suite runs once now ("Checking
the setup and running all tests once; on a large suite this takes a few minutes") and run
`framegate doctor --tree --brief`: a one-line verdict (ready / not ready), then only the
failures and the warnings that need a decision, then the lines of the tree check (below).
Fix every FAIL that is a config problem yourself and run it again until it says ready. Show
the user the verdict line and, per remaining warning, one line with the decision and your
default. The
full output (`framegate doctor --tree`) on request.

### The tree as it is (once per project)

A feature or a refactor starts on a green tree: what is red before any task is found and
handled here, not blamed on the first task. The command ran the whole suite and the lint /
format / type checks once; you run nothing yourself. Go by the prefix of its lines:

- `PROBLEM: N test(s) fail on the untouched tree and are not listed:` and one line per test
  (name | first line of the failure). The verdict is "not ready" while one is left: every
  red test ends as made to run, or listed with a reason after the user's OK. Sort them,
  from those lines and what you read in step 1 (a failure you cannot place: read the test
  file, do not run it): **only lacks a package** (install it: a workflow tool, `framegate
  install-tools` did that; a dependency of the project, name the command for the user),
  **cannot run on this machine** (a database, a service, a network address, a licence,
  another operating system) and **red before any task** (the machine runs it and it
  fails: a placeholder, a known bug; the project's own debt). Show them in one message and
  ask once:

      **3 tests fail on the code as it is**
      I would list them; every check then leaves them out, and every final summary names
      them with the reason:
      1. tests/test_db.py (2 tests): needs a PostgreSQL server (connection refused, localhost:5432)
      2. tests/test_price.py::test_rounding: red before any task: AssertionError: 10.01 != 10.0
      Reply "ok", or the number and the change ("2: I fix it first").

  After the answer write the confirmed entries to framegate.toml. `test` names the file:
  `tests/db/test_x.py::test_connect`, or the whole file when all its tests fail for the same
  reason (a bare test name is refused). The reason of a debt entry starts with "red before
  any task:":

      [gates]
      unrunnable_tests = [
        { test = "tests/test_db.py", reason = "needs a PostgreSQL server" },
        { test = "tests/test_price.py::test_rounding", reason = "red before any task: rounding bug" },
      ]

  Never write an entry the user did not confirm; a test the user wants fixed first stays
  unlisted, and the setup stays not ready until it is green. Then run `framegate doctor
  --tree --brief` again: the entries show as `LISTED:` lines.
- `LISTED:` lines: name the listed tests once in your summary. Under them, a `->` line
  counts the debt entries: suggest one task for them, with a name ("2 tests were red
  before any task; fix them when you like: `/framegate:bugfix rounding`").
- `PROBLEM: the whole suite did not run to the end` or `gave no per-test results`: nothing
  is known about the tests. Fix the test command (row 3) or `[gates] regression_timeout`
  and run the check again.
- `DEBT:` lines (a lint / format / type / imports check fails on the code as it is): report
  the size in one line and propose ONE clean-up task, to accept or skip: "Lint: 353 lines
  in 41 files, format: 12 files. Clean this up in one task after the setup:
  `/framegate:refactor cleanup-lint`? You can skip it: a task formats the files it touches
  anyway, the other files stay as they are." No question per file, no list of files.
- `MUTATION:` (the mutation tool cannot run here: doctor's tool check, or its trial of a few
  seconds failed, e.g. the tests import the code under other names than mutmut gives it):
  decide it here. The setup is not ready before. Make it run (the fix the line names; WSL: the default distribution is
  tried first, then the others), or ask once: "Mutation testing cannot run here: <reason>.
  Switch it off for this project?" On yes write `mutation_tool = "none"` under `[gates]` in
  framegate.toml: off on purpose is no risk and no stop at the final gate.
- `Tree: green.`: say so in one line.

### The state branch (after the tree is settled)

With `[git] state_branch` set, doctor warns that the state branch is missing until this
last step, done once every other point is settled, the `unrunnable_tests` entries included (a later edit of these files in this
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

The chat skills need the CLI allowed: `"Bash(framegate *)"` and `"PowerShell(framegate *)"`
in `permissions.allow` of `.claude/settings.local.json` (`framegate doctor` warns while they
are missing; offer to add them, and write the file only on a yes).

Then the setup's own commit, so the first task does not start on uncommitted files (`framegate
spec` refuses those). Run `framegate commit-setup --dry-run` (with the path of a tag helper
if you added one: `framegate commit-setup --dry-run tests/tagging.py`). It lists what the
setup wrote and nobody committed: framegate.toml, `.framegate/project.md`, the code map,
`specs/_template/`, `.gitignore`, a changed packaging or test config file (the `req` marker
line). "Setup files: nothing to commit.": go on without a word. Else show the list in one
line and ask: "Commit these setup files now, in one commit of their own? (default: yes)".
On yes run `framegate commit-setup` with the same paths and report its result in one line;
it commits only these files, on the current branch, and pushes nothing. On no: say in one
line that `framegate spec` will refuse while a tracked one of them is uncommitted, and that
the refusal prints the command to run. Never run git yourself, and do not push.

With `[git] state_branch` set, framegate's own files are on the state branch (step 5) and
are never committed on the base: `commit-setup` then lists only the product-side files (the
`req` marker line), if any. Say in one line that the first `framegate publish` (offered at
the end of a feature) pushes the state branch.
