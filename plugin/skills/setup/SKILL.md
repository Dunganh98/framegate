---
name: setup
description: Set up framegate in this repository - read the repo, draft framegate.toml, the project knowledge file and the code map, show a short brief that asks only the real decisions, check the setup with framegate doctor, offer a smoke run. Run only when the user invokes it.
disable-model-invocation: true
allowed-tools: Bash(framegate *) PowerShell(framegate *) Bash(uv tool install *) PowerShell(uv tool install *) Read Write Edit Glob Grep
---
You set up framegate for this repository. The user is the manager: they answer a few questions
and approve; you read the repo, draft the files, run the `framegate` commands. Every message:
the decision and its default first, details only on request, plain words. You never change the
project's source or tests (one exception, step 3: a tagging helper the user approved).

## 0. Install the tools first

The `framegate` command itself: run `framegate --version`. If the command is not found, tell
the user in one line ("Installing the framegate CLI 0.8.0 for your user") and run

    uv tool install "git+https://github.com/Dunganh98/framegate@v0.8.0"

then `framegate --version` again. If `uv` is missing or the install fails (no access to the
repository, no git), show the error and the two ways out, then stop: install uv
(https://docs.astral.sh/uv/) and run the setup again, or clone the repository and run
`uv tool install --editable <clone>`. An installed CLI of another version than 0.8.0 (this
plugin's): run the same command with `--force` (the tool follows the plugin's version), then
`framegate --version` again; say nothing when it worked, one line when it failed, and go on.

Automatic updates (ask at most once per machine): read `~/.claude/plugins/known_marketplaces.json`.
If its `framegate` entry has no `"autoUpdate": true`, ask in one line: "Keep framegate up to
date automatically? (default: yes)". On yes, tell the user the one step only they can do:
`/plugin` > Marketplaces > framegate > Enable auto-update. From then on Claude Code fetches
a new plugin version in the background, the next session uses it and shows one line saying
what changed, and the next new feature brings the command-line tool to the same version.
The file is missing or has no `framegate` entry (a local install): skip this without a word.

Then run `framegate install-tools --dry-run`: it names the project's
interpreter and what the workflow needs there and is missing (for Python: the framegate pytest
plugin, pytest, pytest-cov, hypothesis, diff-cover). Nothing missing: go on without a word. Else
tell the user in one line what goes into which interpreter ("Installing framegate, diff-cover,
hypothesis into .venv") and run `framegate install-tools`. Report its result in one line; if it
prints `CHANGED versions`, show that line as it is (a pinned package moved: the user decides
whether to keep it). If the install fails, show the error and stop: the setup cannot be
checked without the tools. A language framegate installs nothing for says so; go on.

## 1. Read the repo (no edits yet)

Find out, from files only (Read / Glob / Grep):
- language, build and test tools: `pyproject.toml`, `*.sln` / `*.csproj`, `package.json`,
  `go.mod`, `Cargo.toml`, ...; the test framework and how tests are run, filtered and reported
  (JUnit XML?), how slow tests are marked (traits, markers, categories);
- the layout: source and test folders (several source folders are fine: `[paths] src` takes
  a list, e.g. `["core", "autoload", "scenes"]`), where specs could live, generated / build
  folders;
- the modules (folders / namespaces / packages): what each is for, its key classes, how they
  work together (for the code map);
- CI files (`.github/workflows/`, ...), formatter / linter / type checker configs;
- `CLAUDE.md`, `README`, docs: architecture rules, conventions, things that must not change;
- tests that pin outputs (golden files, hashes, fingerprints, snapshots, save formats) and how
  test data is made (seeds, factories, recordings, scan or generator commands);
- whether `framegate.toml`, `.framegate/project.md` and the code map (`[docs] code_map`,
  default `docs/code-map.md`) exist already (then you update them).

## 2. Pick the adapter

`python` (pytest) and `dotnet` (xUnit) have built-in adapters. Any other language or test
framework: `generic`, configured entirely in framegate.toml `[generic]` (`framegate init
--language generic` writes a commented example): the test commands with placeholders (`{junit}`
report path, `{ids}` / `{ids_regex}` spec-ID selection, `{paths}`), a `tag_pattern` regex that
finds a test's spec IDs, the `stub_marker` a not-implemented stub prints, optional coverage
(Cobertura / LCOV + `diff-cover`), mutation (command + score regex) and `format_fix`. If the
framework has no way to tag a test with IDs and select by them, propose the smallest helper (for
example a `req("X-R-001")` tag that the test runner can filter on) and it becomes a question.

## 3. Draft, then one short brief

Draft (in your head, no files yet): the framegate.toml settings, the knowledge file
`.framegate/project.md` (sections Architecture rules / Never changes / Test harness / Tools /
Off-limits, one short verifiable line per fact) and the code map.

Show the user a brief of about one screen, no toml and no file dumps:

    **What I found** — language and test framework, how tests run, the slow tests, what is
      protected (hashes, save formats, golden files, public APIs)
    **Settings I chose** — only those that differ from the defaults, each with why
      ("diff coverage 80, not 90: the UI glue has no tests")
    **What agents will know** — the knowledge facts, short lines
    **Code map** — the modules it lists, one line
    **Questions** — numbered, by impact; each: the problem in one line, then
      "A: ... (default) / B: ..." on one line ("None." when nothing is open)

One line per bullet; a section with nothing in it is one line. A point is a question only when
it is a real decision: two or more reasonable options with different outcomes, and the repo,
`.framegate/project.md` and earlier answers don't settle it. Otherwise: one sensible option ->
decide it (a line under "Settings I chose" or "What agents will know"); already settled or a
note -> leave it out; the same question twice -> one. No limit on the number. Ask only what
the code cannot tell; typical ones: which tests are slow or need special setup
(they stay out of the step checks); what must never change and how tests leave new members out;
how test data for a rare situation is found; what is off-limits for agents; whether mutation
testing should run (and its threshold for glue code vs libraries); the base branch; which
paths must always get the user's own look at the final gate (`[risk] never_auto`). The toml,
the knowledge file or the code map in full only on request.

## 4. Write

After the answers ("ok" = every default): if there is no framegate.toml yet, run `framegate
init` (or `framegate init --language generic`); it creates framegate.toml, `specs/_template/`,
`.framegate/project.md` and `.gitignore` lines, and never overwrites a file. Then edit
framegate.toml and `.framegate/project.md` with the drafted settings and the answers (knowledge
file under ~150 lines: facts, not prose), and write the code map (`[docs] code_map`, under
~300 lines) in this form:

    # Code map

    ## <Module or folder>
    Purpose: <one line>
    Key classes:
    - `<Type>`: <what it is for>
    How they work together: <A builds B, C reads B to ...> (a small mermaid diagram is fine)
    Where to look for: <a task> -> `<file>`

Every later feature's PR phase updates the sections it touches.

If the project is installed into its environment from its packaging files (a Python editable
install, for example), set `[env] refresh` in framegate.toml to the command that redoes it,
e.g. `refresh = ["python", "-m", "pip", "install", "-e", ".", "--no-deps"]`: every spec
approval runs it, so a package or source folder a feature adds in its spec phase imports in
the run without a step by hand.

Deploy and CI files outside the built-in list (`.github/`, `.gitlab-ci.yml`,
`azure-pipelines.yml`): put them in `[protected] extra` (e.g. `ci/`, `deploy/`, `k8s/`,
`Dockerfile`, `docker-compose*.yml`, generated files such as `*_pb2.py`). They are locked for
every feature, also in its spec phase; what a feature needs there is noted in its spec
("Pipeline follow-up"), raised in the final summary and done as a separate step. The
packaging / test / tool-config files need no entry: the language adapter protects them, and
they are the setup files a spec phase may change.

## 5. Check: `framegate doctor --brief`

First `framegate install-tools` once more: with framegate.toml written it also installs the
lint / format / type tools the gate commands name (ruff, mypy, black), which step 0 could not
know yet. Then run `framegate doctor --brief`: a one-line verdict (ready / not ready), then
only the failures and the warnings that need a decision. Fix every FAIL that is a config problem yourself and run
it again until it says ready. Show the user the verdict line and, per remaining warning, one line
with the decision and your default ("mutmut is not installed: mutation testing off until it is?
Default: yes"). The full output (`framegate doctor`) on request; `framegate doctor --full` also
times the whole test suite (the final regression run), offer it in one line.

## 6. Offer a smoke run, in one line

"A smoke run (a tiny throwaway feature through the whole flow, about USD 2-4 and 15-30 minutes)
proves the agents, gates and hooks work before a real feature. Do it? (default: yes)". On yes:
tell them to run `/framegate:feature smoke` with this requirement (adapt the names to the
language):

    A pure function clamp(value, low, high) in the source folder: returns value limited to
    [low, high]; low > high raises an argument error with the message "low > high". One plan
    step. No other code changes.

and to remove it afterwards: `framegate cleanup smoke --tags --force`, then switch back to the
base branch and delete the branch `feat/smoke`.

## 7. Finish

In two or three lines: what to commit (framegate.toml, `.framegate/project.md`, the code map,
`specs/_template/`, `.gitignore`, a tag helper if added) and that the chat skills need the CLI
allowed: `"Bash(framegate *)"` and `"PowerShell(framegate *)"` in `permissions.allow` of
`.claude/settings.local.json` (`framegate doctor` warns while they are missing; offer to add
them, and write the file only on a yes). Do not commit or push yourself.
