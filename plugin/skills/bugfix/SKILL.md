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

## Version (checked when this skill loaded)

This plugin is version 0.8.0. The installed command-line tool is:

!`framegate --version`

- The same version: go on without a word.
- Another version, and the output under "Setup" above says the spec is **already approved**
  (work in progress that was started with that version): leave it as it is and go on without
  a word. The next new feature, bug fix or refactor brings it in line.
- Another version otherwise (nothing in progress here): bring the tool to this plugin's
  version before anything else:

      uv tool install --force "git+https://github.com/Dunganh98/framegate@v0.8.0"

  Then run `framegate spec-brief $ARGUMENTS` again and use its output in place of the one
  under "Project conventions" below (that one came from the old version). When it worked, say
  nothing about it: the user saw the one update line when the session started. If it fails
  (no network, no access, the tool is running in another window), say so in one line and go
  on with the installed version.

## Project conventions for the spec

Printed by `framegate spec-brief $ARGUMENTS`: the spec folder, templates, source and test paths,
the language, how tests are written here, the code map and the project knowledge.

!`framegate spec-brief $ARGUMENTS`

## Part 1: the bug report dialogue

1. Read what the user reports, then find out everything you can yourself: read the code map,
   the code on the path from the input to the wrong result, and the project knowledge above.
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
2. Ask only what those sources cannot decide (the right result, how far the fix may go).
   Numbered, plain words, each "A: ... (default) / B: ..." on one line. A point with one
   sensible answer is a decision you make and list, not a question.
3. Write requirements.md (the bug report), design.md and plan.md following the templates
   exactly: reproduction rows are R IDs (each gets a test that fails today and passes once
   fixed), must-not-change rows are I IDs (each gets a test that passes today and after).
   Usually one plan step that covers every ID. Do not write tests and do not fix anything.
   Then run `framegate spec-check $ARGUMENTS` and fix what it lists.
4. Write the spec gate brief to `<out>/$ARGUMENTS/spec_brief.md` (the out folder above) and
   show it in the chat. About one screen, plain words, no spec IDs, one line per bullet;
   exactly these sections:

       **Bug** — what goes wrong, with the input that shows it and the result you measured
       **Right result** — what it should be, and why
       **Plan** — how it gets fixed, one line per step
       **How it is proven** — the tests that fail today and pass after; what stays as it is
       **Not proven** — what to check by using it
       **Decisions I made for you** — one line each, with what it rules out
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
