---
name: refactor
description: Drive one framegate refactor from this chat - scope dialogue, a short brief to approve, then the background run - tests that pin today's behavior first, the restructuring in small steps, review, the gate briefs answered in plain words, cleanup. Run only when the user invokes it.
argument-hint: <refactor-slug>
disable-model-invocation: true
allowed-tools: Bash(framegate *) PowerShell(framegate *) Bash(uv tool install *) PowerShell(uv tool install *) Monitor Read Write Edit Glob Grep
---
You drive the framegate workflow for the refactor `$ARGUMENTS` in this chat. The user is the
manager: they say what should get better, answer a few questions and check outcomes; you run
every `framegate` command. The spec files are precise and written for the agents; the user
reads short briefs in plain words instead (no spec IDs without their meaning). The CLI is the
engine: never edit `progress.json`, tags or anything under the out folder by hand (the
exceptions: `answers.md`, see the run protocol, and `spec_brief.md`, below).

A refactor differs from a feature in its first proof: the tests must PASS on today's code and
keep passing after every step. They are the only proof that nothing changed, so they are
written before any code moves. A refactor changes no behavior: a behavior change the user
wants is a feature (`/framegate:feature`), a wrong result is a bug fix (`/framegate:bugfix`).

## Setup (already done when this skill loaded)

`framegate spec $ARGUMENTS --mode refactor` created or switched to the branch and set the SPEC
phase:

!`framegate spec $ARGUMENTS --mode refactor`

If the output above says the spec is **already approved**, do not start the dialogue: run
`framegate status $ARGUMENTS --brief`, tell the user in one line where it stands and ask:
continue it (default; then the run protocol below, by state as in its section B), or change the
spec (that needs `framegate approve-spec` again and restarts from the tests). If it says the
folder is a spec of another kind, tell the user and stop.

## Version (checked when this skill loaded)

This plugin is version 0.9.0. The installed command-line tool is:

!`framegate --version`

- The same version: go on without a word.
- Another version, and the output under "Setup" above says the spec is **already approved**
  (work in progress that was started with that version): leave it as it is and go on without
  a word. The next new feature, bug fix or refactor brings it in line.
- Another version otherwise (nothing in progress here): bring the tool to this plugin's
  version before anything else:

      uv tool install --force "git+https://github.com/Dunganh98/framegate@v0.9.0"

  Then run `framegate spec-brief $ARGUMENTS` again and use its output in place of the one
  under "Project conventions" below (that one came from the old version). When it worked, say
  nothing about it: the user saw the one update line when the session started. If it fails
  (no network, no access, the tool is running in another window), say so in one line and go
  on with the installed version.

## Project conventions for the spec

Printed by `framegate spec-brief $ARGUMENTS`: the spec folder, templates, source and test paths,
the language, how tests are written here, the code map and the project knowledge.

!`framegate spec-brief $ARGUMENTS`

## Part 1: the scope dialogue

1. Read what the user wants, then decide everything you can yourself: read the code map, the
   code in question, its callers and its existing tests, and the project knowledge above.
   - Scope: the files / types / methods that change and what happens to each (split into,
     moved to, renamed, merged). Everything else stays untouched.
   - Behavior to preserve: what callers and users rely on in that code: outputs for given
     inputs, errors, order, side effects, stored formats. Existing tests first: search the
     tests for the types and methods in scope and list the files in requirements.md
     ("Existing tests of the code in scope"). The test writer tags those that already pin
     a row and writes new characterization tests only for the gaps. Golden values come from running today's
     code (the project's run / scan command), never from reading it.
   - Fixed points: golden / hash / snapshot / save-file tests that touch the code in scope
     must stay byte-identical; public signatures other code uses stay, or every caller is
     listed.
   - Structure targets: how one can see the refactor is done (file X under 400 lines, class Y
     only does Z, no method over 60 lines). Checkable statements, not "cleaner".
   - Steps: one move, split or rename per step; the code builds and every test passes after
     each. Order them so no step needs a later one.
   - references: when the task builds on another project listed under "References" in the
     conventions above (a sibling repository), read what you need there and list the ones
     used in requirements.md, "References" (name, files / folders, what for): only those are
     given to the agents, read-only, pinned at their commit at "spec OK". Name them in the
     spec brief, so the user approves them.
2. Ask only what those sources cannot decide (how far to go, names, what must stay public).
   Numbered, plain words, each "A: ... (default) / B: ..." on one line. A point with one
   sensible answer is a decision you make and list, not a question.
3. Write requirements.md (the refactor brief), design.md (the target structure) and plan.md
   following the templates exactly: step 1 has the "Covers:" line with every ID; later steps
   need none (every step is checked with all the characterization tests) but get a "Depends
   on: Step N" line. Do not write tests and do not move any code.
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
   final summary.
4. Write the spec gate brief to `<out>/$ARGUMENTS/spec_brief.md` (the out folder above) and
   show it in the chat. About one screen, plain words, no spec IDs, one line per bullet;
   exactly these sections:

       **Goal** — what gets better and why now
       **What moves** — the code in scope and where each part goes
       **Plan** — the steps, one line each
       **How it is proven** — what the tests pin before anything moves; the structure targets
       **Not proven** — behavior no test pins (name it), what to check by using it
       **Decisions I made for you** — one line each, with what it rules out
       **Setup files changed** — one line: the files `spec-check` lists, each with why
         ("None." when it lists none); they are approved with the spec
       **Pipeline follow-up** — deploy / CI changes this task makes necessary, one line each
         (file: what, why); not part of the task, a separate step after it ("None.")
       **Questions** — the real decisions still open ("None.")

5. Ask: "Is the plan OK? Then I start the run." (default: yes, with the defaults of any open
   questions). An "ok" while questions are open: write their defaults into the spec and
   spec_brief.md first, say so in one line, then Part 2. Answers or changes: update the spec
   and spec_brief.md, show the changed sections only, ask again.

## Part 2: from "plan OK" to DONE

When the user says the plan is OK (any clear approval), follow the run protocol below from
then on, for the rest of this conversation: approve the spec, run in the background with the
live view, answer the gates, clean up at the end. `<f>` there is `$ARGUMENTS`. If the final
gates find the tests too weak (coverage or mutation score of the moved code), no agent repairs
that in a refactor: tests written after the move would pin the new code. Tell the user and
ask: approve as is, or start a new refactor with more tests first.

!`framegate protocol`
