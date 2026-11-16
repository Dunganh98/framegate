---
name: bugfix
description: Drive one framegate bug fix from this chat - bug report dialogue, a short brief to approve, then the background run - tests that fail on today's code first, the fix, review, the gate briefs answered in plain words, cleanup. Run only when the user invokes it.
argument-hint: <bugfix-slug>
disable-model-invocation: true
allowed-tools: Bash(framegate *) PowerShell(framegate *) Bash(uv tool install *) PowerShell(uv tool install *) Monitor Read Write Edit Glob Grep
---
You drive the framegate workflow for the bug fix `$ARGUMENTS` in this chat. The user is the
manager: they describe the bug, answer a few questions and check outcomes; you run every
`framegate` command. The spec files are precise and written for the agents; the user reads
short briefs in plain words instead (no spec IDs without their meaning). The CLI is the engine:
never edit `progress.json`, tags or anything under the out folder by hand (the exceptions:
`answers.md`, see the run protocol, and `spec_brief.md`, below).

A bug fix differs from a feature in its first proof: the tests must FAIL on today's code, for
the reason the user reports, before anything is fixed. So the report must be exact.

## Setup (already done when this skill loaded)

`framegate spec $ARGUMENTS --mode bugfix` created or switched to the branch and set the SPEC
phase:

!`framegate spec $ARGUMENTS --mode bugfix`

If the output above says the spec is **already approved**, do not start the dialogue: run
`framegate status $ARGUMENTS --brief`, tell the user in one line where it stands and ask:
continue it (default; then the run protocol below, by state as in its section B), or change the
report (that needs `framegate approve-spec` again and restarts from the tests). If it says the
folder is a spec of another kind, tell the user and stop.

## Start check (your first step)

This plugin is version 0.12.5. Before anything else run this command yourself (it is not run
at load: a command-line tool older than this plugin does not have it yet):

    framegate preflight $ARGUMENTS --plugin-version 0.12.5

It compares this plugin with the command-line tool and with the workflow tools in the
project's environment, updates what it may and prints the rest. Act on every line, then go on:

- `Start check: ok ...` and `KEPT: ...` lines: without a word (work in progress keeps the
  versions it was started with; the next new feature, bug fix or refactor brings them in line).
- `UPDATED: ...`: one line to the user: what was brought to which version; a `CHANGED
  versions` part as it is (a pinned package moved: the user decides whether to keep it).
- `RUN: <command>`: the command-line tool is not at this plugin's version and cannot replace
  itself while it runs. Run the command, then the start check again and act on its lines.
  Then run `framegate spec-brief $ARGUMENTS` again and use its output in place of the one
  under "Project conventions" below (that one came from the old version). When it worked, say
  nothing about it: the user saw the one update line when the session started. If it fails
  (no network, no access, the tool is running in another window), say so in one line and go
  on with the installed version.
- `TELL THE USER: ...` (the plugin was updated while this session ran, so these skills are
  the old ones): say it as it is and stop. Nothing else from this skill until Claude Code was
  restarted.
- `PROBLEM: ...`: say it in one line with its fix and ask: fix it first (default), or go on.
- The command itself fails with `invalid choice: 'preflight'`: the installed tool is older
  than this plugin and has no start step. If the output under "Setup" above says the spec is
  **already approved**, go on without a word (work in progress keeps its tool). Otherwise
  bring the tool to this plugin's version:

      uv tool install --force "git+https://github.com/Dunganh98/framegate@v0.12.5"

  then run the start check again and act on its lines as above (`framegate spec-brief
  $ARGUMENTS` again included).

## Project conventions for the spec

Printed by `framegate spec-brief $ARGUMENTS`: the spec folder, templates, source and test paths,
the language, how tests are written here, the code map and the project knowledge.

!`framegate spec-brief $ARGUMENTS`

## Part 1: the bug report dialogue

1. Read what the user reports, then find out everything you can yourself: read the code map,
   the code on the path from the input to the wrong result, and the project knowledge above
   (dot folders of tools such as `.serena/`, `.vscode/`, `.venv/` are machine files, not
   project context: do not read through them, and no spec names one).
   - Reproduce it: run today's code with the reported input (the project's run / scan command,
     or the smallest existing test) and copy the wrong result. If you cannot reproduce it, say
     so and ask for the exact input; do not write a report from a guess.
   - Expected result: where it follows from (an earlier spec, a rule, the docs). If nothing
     says what is right, that is a question for the user.
   - Must not change: the cases next to the bug that are right today and that a fix could
     break (other inputs, other callers of the same code, saved data, hashes, goldens).
   - Suspected causes: at least two, each with where to look (file: method). You only list
     them; the implementer confirms one and rules out the others.
   - Existing data: every field the report names resolved to its exact member, with the line
     that assigns it copied into the spec.
   - references: when the task builds on another project listed under "References" in the
     conventions above (a sibling repository), read what you need there and list the ones
     used in requirements.md, "References" (name, files / folders, what for): only those are
     given to the agents, read-only, pinned at their commit at "spec OK". Name them in the
     spec brief, so the user approves them.
2. Ask only what those sources cannot decide (the right result, how far the fix may go).
   Numbered, plain words, each "A: ... (default) / B: ..." on one line. A point with one
   sensible answer is a decision you make and list, not a question.
3. Write requirements.md (the bug report), design.md and plan.md following the templates
   exactly: reproduction rows are R IDs (each gets a test that fails today and passes once
   fixed), must-not-change rows are I IDs (each gets a test that passes today and after).
   Usually one plan step that covers every ID. Do not write tests and do not fix anything.
   Then run `framegate spec-check $ARGUMENTS` and fix what it lists.
   Setup files: if the task needs a new package or source folder, a dependency or a tool
   setting, change those files now, in the spec phase: `framegate.toml` and the project's
   packaging / test / tool-config files (e.g. `pyproject.toml`, `requirements*.txt`,
   `pytest.ini`, `ruff.toml`, a project file; `framegate spec-brief` lists them) are writable
   for you until the approval, which commits them with the spec and freezes them. Nothing
   can change them later in the run. Tests, `.claude/`, `.framegate/`, `CLAUDE.md`, CI files,
   deploy files and every `[protected] extra` path stay locked for you, now and later.
   `spec-check` prints "Setup files changed" (for the brief) and lists other uncommitted
   protected files, which the approval refuses: ask the user to commit or revert those.
   Pipeline follow-up: deploy files (Dockerfile, compose, k8s manifests) and CI files (yml,
   scripts) are never changed by a feature, not even at spec time. When the task means one of
   them must change (a new package folder the Dockerfile must COPY, a new dependency the image
   must install, a new test selection or path for CI), do not change it and do not ask to:
   write one bullet per change under "Pipeline follow-up" in requirements.md (`- <file>: <what
   must change> (<why>)`; "None." otherwise; add the section if the project's template lacks
   it). It is done as a separate step after the feature; framegate raises it again in the
   final summary. Only deploy / CI files go there, and what must be done outside the
   repository (a CI variable, a secret in the vault, a migration of the real database, an
   environment switch): `- OUTSIDE [<where>]: <what must be done> (<why>)`. Nothing in
   framegate does such an item; the brief shows it for the user to do by hand. If a reference project has the same bug
   (or should take over the fix), write one bullet under "Reference follow-up" the same way
   (`- <reference file>: <what should change> (<why>)`): the reference is never changed
   here; show it on the brief under "Pipeline follow-up" as "for <reference>: ...".
4. Write the spec gate brief to `<out>/$ARGUMENTS/spec_brief.md` (the out folder above) and
   show it in the chat. About one screen, plain words, no spec IDs, one line per bullet. Sources: the user navigates from the brief, so a line whose detail is elsewhere ends with its source in brackets, as a path from the repository root that opens in the editor: `(src/app/parser.py:120)` for code, `(<specs>/$ARGUMENTS/plan.md, Step 2)` for the spec; one source per line, the most useful one. The final gate lists these same plan steps again, each with how it went.
   Exactly these sections:

       **Bug** — what goes wrong, with the input that shows it and the result you measured
       **Right result** — what it should be, and why
       **Plan** — how it gets fixed, the steps numbered as in plan.md, one line each, ending with the main file it builds or changes
       **How it is proven** — the tests that fail today and pass after; what stays as it is
       **Not proven** — what to check by using it
       **Decisions I made for you** — one line each, with what it rules out
       **Setup files changed** — one line: the files `spec-check` lists, each with why
         ("None." when it lists none); they are approved with the spec
       **Pipeline follow-up** — deploy / CI changes this task makes necessary, one line each
         (file: what, why); not part of the task, a separate step after it ("None."); an
         OUTSIDE item as "outside the repository: by hand: <where>: <what>"
       **Questions** — the real decisions still open ("None.")

5. Ask: "Is the report OK? Then I start the run." (default: yes, with the defaults of any open
   questions). An "ok" while questions are open: write their defaults into the spec and
   spec_brief.md first, say so in one line, then Part 2. Answers or changes: update the spec
   and spec_brief.md, show the changed sections only, ask again.

## Part 2: from "report OK" to DONE

When the user says the report is OK (any clear approval), follow the run protocol below from
then on, for the rest of this conversation: approve the spec, run in the background with the
live view, answer the gates, clean up at the end. `<f>` there is `$ARGUMENTS`. At the final
gate the brief names the cause the implementer found and what it ruled out.

!`framegate protocol`
