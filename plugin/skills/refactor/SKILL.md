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

## Start check (your first step)

This plugin is version 0.13.3. Before anything else run this command yourself (it is not run
at load: a command-line tool older than this plugin does not have it yet):

    framegate preflight $ARGUMENTS --plugin-version 0.13.3

It compares this plugin with the command-line tool and with the workflow tools in the
project's environment, updates what it may and prints the rest. Act on every line, then go on:

- `Start check: ok ...`: without a word.
- `KEPT: ...` lines: an update is waiting. A task in progress (this one or another one, also
  in another repository) keeps the versions it was started with, so nothing was updated.
  One line to the user in all: which update waits for which task, and that
  `/framegate:update` does it now. Never run an install command for a `KEPT:` line.
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

      uv tool install --force "git+https://github.com/Dunganh98/framegate@v0.13.3"

  then run the start check again and act on its lines as above (`framegate spec-brief
  $ARGUMENTS` again included).

## Project conventions for the spec

Printed by `framegate spec-brief $ARGUMENTS`: the spec folder, templates, source and test paths,
the language, how tests are written here, the code map and the project knowledge.

!`framegate spec-brief $ARGUMENTS`

## Part 1: the scope dialogue

If the user gave only a name (`$ARGUMENTS`), it is just the branch and folder name: find out
in step 1 what it refers to (the code map, the code, the docs) and ask in step 2 only what
you still cannot decide, the requirement itself included if the code and the docs do not
tell.
A task text the user gave earlier in this conversation is the requirement: read it before
you ask anything.

Sources, by rank:
- The code is the current state. Where docs and code disagree, the code says what exists.
- The user's task is the requirement. Where docs and the task disagree, the task wins.
- Docs say where the project is heading: context only. A doc can suggest a requirement; it
  is one only when the user says so.
- A comment or docstring is no source.
Deferred work: read the entries `framegate deferred --file <path>` prints for the files the
task touches; when the task does one, write `Resolves: D<n>` in requirements.md.
Requirements come from the task only: a spec never requires text in a docstring, a comment
or a doc, and neither you nor an agent of the run edits a documentation file. What a doc
should say afterwards (the work makes it out of date, or the task goes another way than the
documented plan) is one bullet under `## Proposed doc updates` of requirements.md. A file
under docs/ (plan, design, architecture, reference): the file, where in it, what it says
today, the proposed text, why; the user answers it at the final gate (default: later).
Where the task's code now contradicts the doc: `today: <...> -> code now: <...>`; the user
then approves the code (the doc updated now or later) or rejects it with a solution. A
README, CHANGELOG or handover note: one brief line of what changes; no question. After the
final gate is approved, one docs step applies the brief ones and the approved ones.
A docstring or comment in a source file that the task's code will contradict is code text,
not a doc: the plan step that changes the file rewords it (name the lines in that step); in
a file no step changes, one bullet `- code text: <source file>:<lines>: today: <quoted> ->
code now: <what the code does>`, which the final summary lists (no question, no docs step).
Each proposed text states what this task builds, nothing more: before the final gate it is
checked against the code and corrected to it.
Program output (an error message, a log line) is behaviour, not documentation: write every
error message out in full for one concrete input, with one notation for placeholders in the
whole spec (repr, str, or str in literal quotes); the tests match the whole message.

1. Read what the user wants, then decide everything you can yourself: read the code map, the
   code in question, its callers and its existing tests, and the project knowledge above
   (dot folders of tools such as `.serena/`, `.vscode/`, `.venv/` are machine files, not
   project context: do not read through them, and no spec names one).
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
2. Ask the user: one compact round, numbered, plain words, about 8 points at most, the most
   consequential first, each "A: ... (default) / B: ..." on one line, A being the answer you
   recommend, so the user can say "ok" to all. What those sources cannot decide: how far to
   go, what must stay public.
   Always a question, never your decision, unless the task text, the code or the docs
   already state it:
   - every new or changed public name, signature, parameter type, default value, error
     type or message, stored or wire format (an existing pattern in the code that it
     follows states it: name the pattern);
   - what must not change;
   - where the tests go, when existing test files cover the area;
   - which docs the task may edit (as proposed doc updates);
   Only a point with no observable effect on callers or stored data is a decision you make
   and list.
   A reply that describes the task (a ticket, the requirement in the user's words) and does
   not answer your questions is not "ok" and takes no default: it is the requirement. Do
   step 1 again with it, drop the questions it settles, and ask the ones it leaves open
   again, with the new ones it raises.
   design.md, "Public interfaces": one row per new or changed public member with where its
   shape comes from (`task`, `code: <pattern>`, `docs: <file>`, `asked` = the user
   answered); `spec-check` fails a member without one.
3. Write requirements.md (the refactor brief), design.md (the target structure) and plan.md
   following the templates exactly: step 1 has the "Covers:" line with every ID; later steps
   need none (every step is checked with all the characterization tests) but get a "Depends
   on: Step N" line. Do not write tests and do not move any code.
   Files in a plan step: a step may mention any file; everything outside the source folders
   stays read-only, the docs included (see "Sources" above).
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
   framegate does such an item; the brief shows it for the user to do by hand. What a reference project should change
   because of this refactor goes under "Reference follow-up" the same way (`- <reference
   file>: <what should change> (<why>)`): the reference is never changed here.
4. Write the spec gate brief to `<out>/$ARGUMENTS/spec_brief.md` (the out folder above) and
   show it in the chat. About one screen, plain words, no spec IDs, one line per bullet. Sources: the user navigates from the brief, so a line whose detail is elsewhere ends with its source in brackets, as a path from the repository root that opens in the editor: `(src/app/parser.py:120)` for code, `(<specs>/$ARGUMENTS/plan.md, Step 2)` for the spec; one source per line, the most useful one. The final gate lists these same plan steps again, each with how it went.
   Exactly these sections:

       **Goal** — what gets better and why now
       **What moves** — the code in scope and where each part goes
       **Plan** — the steps, numbered as in plan.md, one line each, ending with the main file it builds or changes
       **How it is proven** — what the tests pin before anything moves; the structure targets
       **Not proven** — behavior no test pins (name it), what to check by using it
       **Decisions I made for you** — one line each; only points with no observable effect
         on callers or stored data ("None.")
       **Setup files changed** — one line: the files `spec-check` lists, each with why
         ("None." when it lists none); they are approved with the spec
       **Pipeline follow-up** — deploy / CI changes this task makes necessary, one line each
         (file: what, why); not part of the task, a separate step after it ("None."); an
         OUTSIDE item as "outside the repository: by hand: <where>: <what>"
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
