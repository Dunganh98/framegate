---
name: setup
description: Set up agentflow in this repository - read the repo, draft agentflow.toml and the project knowledge file, ask one batch of questions, check the setup with agentflow doctor, offer a smoke run. Run only when the user invokes it.
disable-model-invocation: true
allowed-tools: Bash(agentflow *) PowerShell(agentflow *) Read Write Edit Glob Grep
---
You set up agentflow for this repository. The user answers questions and approves; you read the
repo, draft the files, run the `agentflow` commands. You never change the project's source or
tests (one exception, step 3: a tagging helper the user approved).

## 1. Read the repo (no edits yet)

Find out, from files only (Read / Glob / Grep):
- language, build and test tools: `pyproject.toml`, `*.sln` / `*.csproj`, `package.json`,
  `go.mod`, `Cargo.toml`, ...; the test framework and how tests are run, filtered and reported
  (JUnit XML?), how slow tests are marked (traits, markers, categories);
- the layout: source and test folders, where specs could live, generated / build folders;
- CI files (`.github/workflows/`, ...), formatter / linter / type checker configs;
- `CLAUDE.md`, `README`, docs: architecture rules, conventions, things that must not change;
- tests that pin outputs (golden files, hashes, fingerprints, snapshots, save formats) and how
  test data is made (seeds, factories, recordings, scan or generator commands);
- whether `agentflow.toml` and `.agentflow/project.md` exist already (then you update them).

## 2. Pick the adapter

`python` (pytest) and `dotnet` (xUnit) have built-in adapters. Any other language or test
framework: `generic`, configured entirely in agentflow.toml `[generic]` (`agentflow init
--language generic` writes a commented example): the test commands with placeholders (`{junit}`
report path, `{ids}` / `{ids_regex}` spec-ID selection, `{paths}`), a `tag_pattern` regex that
finds a test's spec IDs, the `stub_marker` a not-implemented stub prints, optional coverage
(Cobertura / LCOV + `diff-cover`), mutation (command + score regex) and `format_fix`. If the
framework has no way to tag a test with IDs and select by them, propose the smallest helper (for
example a `req("X-R-001")` tag that the test runner can filter on) and ask before adding it.

## 3. Draft, then one batch of questions

Show the user, as text (do not write files yet):
- the agentflow.toml settings that differ from the defaults: `[paths]`, `[git] base`,
  `[gates.commands]`, `[gates] diff_coverage` / `mutation_score`, the slow-test filter
  (e.g. `[dotnet] base_filter`), `[gates.extra]` project checks, `[phases.<P>] tools` rules for
  project tools agents may run, `[generic]` if used;
- the knowledge file `.agentflow/project.md`, sections Architecture rules / Never changes / Test
  harness / Tools / Off-limits, one short verifiable line per fact, with the file it comes from.

Then ask, in one message, numbered, each with your recommended default, only what the code
cannot tell:
1. Which tests are slow or need special setup, and must stay out of the step checks?
2. What must never change (hashes, save / wire formats, public APIs, golden files)? How do the
   tests leave new members out (an exclusion list, an attribute)?
3. How is test data made (seeds, factories, recordings)? How does one find data for a rare
   situation (a scan command, a generator)?
4. What is off-limits for agents (folders, files, practices)?
5. Thresholds: diff coverage (default 90) and mutation score (default 80; lower for glue code
   than for libraries). Should mutation testing run at all?
6. The base branch features start from, and anything about the CI the gates should mirror.

## 4. Write

After the answers: if there is no agentflow.toml yet, run `agentflow init` (or
`agentflow init --language generic`); it creates agentflow.toml, `specs/_template/`,
`.agentflow/project.md` and `.gitignore` lines, and never overwrites a file. Then edit
agentflow.toml and `.agentflow/project.md` with the drafted settings and the answers. Keep the
knowledge file short (under ~150 lines): facts, not prose.

## 5. Check: `agentflow doctor`

Run `agentflow doctor` and show its output in a code block. Fix every FAIL that is a config
problem and run it again until nothing FAILs; explain each WARN in one line (findings in the
existing code are expected: a run checks only what a feature changes). Offer
`agentflow doctor --full`: it also runs the whole test suite and shows how long the final
regression takes.

## 6. Offer a smoke run

A small throwaway feature through the whole flow proves the agents, the gates and the hooks
work before a real feature costs money (about USD 2-4 and 15-30 minutes). If the user wants it:
tell them to run `/agentflow:feature smoke` with this requirement (adapt the names to the
language):

    A pure function clamp(value, low, high) in the source folder: returns value limited to
    [low, high]; low > high raises an argument error with the message "low > high". One plan
    step. No other code changes.

and to remove it afterwards: `agentflow cleanup smoke --tags --force`, then switch back to the
base branch and delete the branch `feat/smoke`.

## 7. Finish

List what to commit (agentflow.toml, `.agentflow/project.md`, `specs/_template/`,
`.gitignore`, a tag helper if added) and remind the user to allow the CLI for the chat skills:
`"Bash(agentflow *)"` and `"PowerShell(agentflow *)"` in `permissions.allow` of
`.claude/settings.local.json`. Do not commit or push yourself.
