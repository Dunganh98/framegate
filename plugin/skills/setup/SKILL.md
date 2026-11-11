---
name: setup
description: Set up agentflow in this repository - read the repo, draft agentflow.toml, the project knowledge file and the code map, show a short brief with at most 5 questions, check the setup with agentflow doctor, offer a smoke run. Run only when the user invokes it.
disable-model-invocation: true
allowed-tools: Bash(agentflow *) PowerShell(agentflow *) Read Write Edit Glob Grep
---
You set up agentflow for this repository. The user is the manager: they answer a few questions
and approve; you read the repo, draft the files, run the `agentflow` commands. Every message:
the decision and its default first, details only on request, plain words. You never change the
project's source or tests (one exception, step 3: a tagging helper the user approved).

## 1. Read the repo (no edits yet)

Find out, from files only (Read / Glob / Grep):
- language, build and test tools: `pyproject.toml`, `*.sln` / `*.csproj`, `package.json`,
  `go.mod`, `Cargo.toml`, ...; the test framework and how tests are run, filtered and reported
  (JUnit XML?), how slow tests are marked (traits, markers, categories);
- the layout: source and test folders, where specs could live, generated / build folders;
- the modules (folders / namespaces / packages): what each is for, its key classes, how they
  work together (for the code map);
- CI files (`.github/workflows/`, ...), formatter / linter / type checker configs;
- `CLAUDE.md`, `README`, docs: architecture rules, conventions, things that must not change;
- tests that pin outputs (golden files, hashes, fingerprints, snapshots, save formats) and how
  test data is made (seeds, factories, recordings, scan or generator commands);
- whether `agentflow.toml`, `.agentflow/project.md` and the code map (`[docs] code_map`,
  default `docs/code-map.md`) exist already (then you update them).

## 2. Pick the adapter

`python` (pytest) and `dotnet` (xUnit) have built-in adapters. Any other language or test
framework: `generic`, configured entirely in agentflow.toml `[generic]` (`agentflow init
--language generic` writes a commented example): the test commands with placeholders (`{junit}`
report path, `{ids}` / `{ids_regex}` spec-ID selection, `{paths}`), a `tag_pattern` regex that
finds a test's spec IDs, the `stub_marker` a not-implemented stub prints, optional coverage
(Cobertura / LCOV + `diff-cover`), mutation (command + score regex) and `format_fix`. If the
framework has no way to tag a test with IDs and select by them, propose the smallest helper (for
example a `req("X-R-001")` tag that the test runner can filter on) and it becomes a question.

## 3. Draft, then one short brief

Draft (in your head, no files yet): the agentflow.toml settings, the knowledge file
`.agentflow/project.md` (sections Architecture rules / Never changes / Test harness / Tools /
Off-limits, one short verifiable line per fact) and the code map.

Show the user a brief of about one screen, no toml and no file dumps:

    **What I found** — language and test framework, how tests run, the slow tests, what is
      protected (hashes, save formats, golden files, public APIs)
    **Settings I chose** — only those that differ from the defaults, each with why
      ("diff coverage 80, not 90: the UI glue has no tests")
    **What agents will know** — the knowledge facts, short lines
    **Code map** — the modules it lists, one line
    **Questions** — at most 5, numbered, each with your recommended default

Ask only what the code cannot tell; typical ones: which tests are slow or need special setup
(they stay out of the step checks); what must never change and how tests leave new members out;
how test data for a rare situation is found; what is off-limits for agents; whether mutation
testing should run (and its threshold for glue code vs libraries); the base branch. The toml,
the knowledge file or the code map in full only on request.

## 4. Write

After the answers ("ok" = every default): if there is no agentflow.toml yet, run `agentflow
init` (or `agentflow init --language generic`); it creates agentflow.toml, `specs/_template/`,
`.agentflow/project.md` and `.gitignore` lines, and never overwrites a file. Then edit
agentflow.toml and `.agentflow/project.md` with the drafted settings and the answers (knowledge
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

## 5. Check: `agentflow doctor --brief`

Run `agentflow doctor --brief`: a one-line verdict (ready / not ready), then only the failures
and the warnings that need a decision. Fix every FAIL that is a config problem yourself and run
it again until it says ready. Show the user the verdict line and, per remaining warning, one line
with the decision and your default ("mutmut is not installed: mutation testing off until it is?
Default: yes"). The full output (`agentflow doctor`) on request; `agentflow doctor --full` also
times the whole test suite (the final regression run), offer it in one line.

## 6. Offer a smoke run, in one line

"A smoke run (a tiny throwaway feature through the whole flow, about USD 2-4 and 15-30 minutes)
proves the agents, gates and hooks work before a real feature. Do it? (default: yes)". On yes:
tell them to run `/agentflow:feature smoke` with this requirement (adapt the names to the
language):

    A pure function clamp(value, low, high) in the source folder: returns value limited to
    [low, high]; low > high raises an argument error with the message "low > high". One plan
    step. No other code changes.

and to remove it afterwards: `agentflow cleanup smoke --tags --force`, then switch back to the
base branch and delete the branch `feat/smoke`.

## 7. Finish

In two or three lines: what to commit (agentflow.toml, `.agentflow/project.md`, the code map,
`specs/_template/`, `.gitignore`, a tag helper if added) and that the chat skills need the CLI
allowed: `"Bash(agentflow *)"` and `"PowerShell(agentflow *)"` in `permissions.allow` of
`.claude/settings.local.json`. Do not commit or push yourself.
