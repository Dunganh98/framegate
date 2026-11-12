---
name: refactor
description: Drive one agentflow refactor from this chat - scope dialogue, a short brief to approve, then the background run - tests that pin today's behavior first, the restructuring in small steps, review, the gate briefs answered in plain words, cleanup. Run only when the user invokes it.
argument-hint: <refactor-slug>
disable-model-invocation: true
allowed-tools: Bash(agentflow *) PowerShell(agentflow *) Monitor Read Write Edit Glob Grep
---
You drive the agentflow workflow for the refactor `$ARGUMENTS` in this chat. The user is the
manager: they say what should get better, answer a few questions and check outcomes; you run
every `agentflow` command. The spec files are precise and written for the agents; the user
reads short briefs in plain words instead (no spec IDs without their meaning). The CLI is the
engine: never edit `progress.json`, tags or anything under the out folder by hand (the
exceptions: `answers.md`, see the run protocol, and `spec_brief.md`, below).

A refactor differs from a feature in its first proof: the tests must PASS on today's code and
keep passing after every step. They are the only proof that nothing changed, so they are
written before any code moves. A refactor changes no behavior: a behavior change the user
wants is a feature (`/agentflow:feature`), a wrong result is a bug fix (`/agentflow:bugfix`).

## Setup (already done when this skill loaded)

`agentflow spec $ARGUMENTS --mode refactor` created or switched to the branch and set the SPEC
phase:

!`agentflow spec $ARGUMENTS --mode refactor`

If the output above says the spec is **already approved**, do not start the dialogue: run
`agentflow status $ARGUMENTS --brief`, tell the user in one line where it stands and ask:
continue it (default; then the run protocol below, by state as in its section B), or change the
spec (that needs `agentflow approve-spec` again and restarts from the tests). If it says the
folder is a spec of another kind, tell the user and stop.

## Project conventions for the spec

Printed by `agentflow spec-brief $ARGUMENTS`: the spec folder, templates, source and test paths,
the language, how tests are written here, the code map and the project knowledge.

!`agentflow spec-brief $ARGUMENTS`

## Part 1: the scope dialogue

1. Read what the user wants, then decide everything you can yourself: read the code map, the
   code in question, its callers and its existing tests, and the project knowledge above.
   - Scope: the files / types / methods that change and what happens to each (split into,
     moved to, renamed, merged). Everything else stays untouched.
   - Behavior to preserve: what callers and users rely on in that code: outputs for given
     inputs, errors, order, side effects, stored formats. Which of it existing tests already
     pin, and which needs new characterization tests. Golden values come from running today's
     code (the project's run / scan command), never from reading it.
   - Fixed points: golden / hash / snapshot / save-file tests that touch the code in scope
     must stay byte-identical; public signatures other code uses stay, or every caller is
     listed.
   - Structure targets: how one can see the refactor is done (file X under 400 lines, class Y
     only does Z, no method over 60 lines). Checkable statements, not "cleaner".
   - Steps: one move, split or rename per step; the code builds and every test passes after
     each. Order them so no step needs a later one.
2. Ask only what those sources cannot decide (how far to go, names, what must stay public).
   Numbered, plain words, each "A: ... (default) / B: ..." on one line. A point with one
   sensible answer is a decision you make and list, not a question.
3. Write requirements.md (the refactor brief), design.md (the target structure) and plan.md
   following the templates exactly: step 1 has the "Covers:" line with every ID; later steps
   need none (every step is checked with all the characterization tests) but get a "Depends
   on: Step N" line. Do not write tests and do not move any code.
   Then run `agentflow spec-check $ARGUMENTS` and fix what it lists.
4. Write the spec gate brief to `<out>/$ARGUMENTS/spec_brief.md` (the out folder above) and
   show it in the chat. About one screen, plain words, no spec IDs, one line per bullet;
   exactly these sections:

       **Goal** — what gets better and why now
       **What moves** — the code in scope and where each part goes
       **Plan** — the steps, one line each
       **How it is proven** — what the tests pin before anything moves; the structure targets
       **Not proven** — behavior no test pins (name it), what to check by using it
       **Decisions I made for you** — one line each, with what it rules out
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

!`agentflow protocol`
