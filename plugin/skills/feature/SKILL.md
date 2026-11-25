---
name: feature
description: Drive one framegate feature from this chat - spec dialogue, a short spec brief to approve, the background run with a live view, the gate briefs answered in plain words, cleanup. Run only when the user invokes it.
argument-hint: <feature-slug>
disable-model-invocation: true
allowed-tools: Bash(framegate *) PowerShell(framegate *) Bash(uv tool install *) PowerShell(uv tool install *) Monitor Read Write Edit Glob Grep
---
You drive the framegate workflow for feature `$ARGUMENTS` in this chat. The user is the
manager: they give the requirements, answer a few questions and check outcomes; you run every
`framegate` command. The spec files are precise and written for the agents; the user reads
short briefs in plain words instead (no spec IDs without their meaning). The CLI is the engine:
never edit `progress.json`, tags or anything under the out folder by hand (the exceptions:
`answers.md`, see the run protocol, and `spec_brief.md`, below).

## Setup (already done when this skill loaded)

`framegate spec $ARGUMENTS` created or switched to the feature branch and set the SPEC phase:

!`framegate spec $ARGUMENTS`

If the output above has `declared but not installed: <name>` lines (also in
out/$ARGUMENTS/env_facts.md): before you write a requirement or a test that needs that
framework, ask the user: drop those tests/requirements, allow installing it, or another
way. Write the answer into requirements.md.

If the output above says the spec is **already approved**, do not start the dialogue: run
`framegate status $ARGUMENTS --brief`, tell the user in one line where the feature stands and
ask: continue it (default; then the run protocol below, by state as in its section B), or change
the spec (that needs `framegate approve-spec` again and restarts from the tests).

The spec keeps the kind the task was started with (here: a feature, no `Mode:` line), also when
the task turns out to be a bug; `framegate approve-spec` refuses another kind.

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
the language, how interface stubs and tests are written here, the code map and the project
knowledge. It overrides any default you would otherwise assume.

!`framegate spec-brief $ARGUMENTS`

## Part 1: the spec dialogue

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
When a requirement changes what an input may be (None, another type or unit, a wider
range): run `framegate uses <name>` for that function or variable, check every use site it
lists and name in design.md each one that must change.
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

1. Read the requirements the user gives, then decide everything you can yourself first: read
   the docs, the code map, the code the feature touches and the project knowledge above
   (dot folders of tools such as `.serena/`, `.vscode/`, `.venv/` are machine files, not
   project context: do not read through them, and no spec names one). Work
   through this checklist and settle each point from those sources:
   - requirements and acceptance criteria; edge cases (empty, NaN, missing, out-of-range,
     units, timing, ordering); failure behavior; non-goals;
   - overlaps: which requirements can apply to the same input, and which one wins; which stored
     value is copied or derived from another, and can the source change afterwards;
   - fixtures: for each case that needs particular data (a seed, a recording, a rare
     situation), how a test sees from outside that the situation happened, and whether a check
     over picked cases may pass with zero cases;
   - edge cases against the pinned data: for each edge case, and each outcome a rule names in
     words, does the pinned data hold it? Scan for data that
     does (the project's scan command), or decide now that a test builds the case itself
     (`unit-only: <why>` in the fixture table). `framegate spec-check` refuses an edge case
     without a fixture row; undecided, the test writer and the test reviewer each ask about it;
   - numbers: every number in an acceptance criterion measured with this spec's own definition
     (run the project's scan / measure command if the project knowledge names one), never
     copied from another test; for every count the feature outputs, data where it is not 0;
   - input domain and copies: the valid range of each public method's inputs (NaN, infinity,
     null, undefined enum values) and what a copy / clone shares;
   - serialized data: which existing golden / hash / snapshot / save tests serialize a type
     this feature changes, and is the new member in or out of them;
   - existing data: every existing concept a requirement names in words resolved to
     one exact member and where it is set (read the code),
     with the line that assigns it copied into the spec (an id or an index? raw or clamped?);
   - rejected input: which existing method decides each rejected case and when it is checked;
     for a parser the accepted forms (sign, largest value, overflow); for a command, each
     option's default, what happens without a value, and the message + exit code of a bad one;
   - error rules and text output: every rule in design.md's "Error handling" has a spec ID
     with an acceptance criterion; every text output has one literal example line;
     every refusal has a source there (task, existing code, docs, or asked): none, ask;
   - mirrored code (only when a requirement says "as X does"): the method and lines of X and
     the choices to copy (cut-offs, raw vs clamped values, tie-breaks, order of checks);
   - new functions: which inputs each clamps itself, what every output holds on "no result",
     and which error wins when two inputs are bad;
   - references: when the task builds on another project listed under "References" in the
     conventions above (a sibling repository), read what you need there and list the ones
     used in requirements.md, "References" (name, files / folders, what for): only those are
     given to the agents, read-only, pinned at their commit at "spec OK". Name them in the
     spec brief under "Project details that matter", so the user approves them;
   - a port (the user wants code of a reference copied here: "port", "file for file", "same
     names as in X"): fill requirements.md, "Ported from": the reference and its commit, every
     source -> target file, the reference's test files to carry over (Kind `tests`), and
     "What may differ" (import names, things only this repository has, things left out). The
     stubs of the ported files come from the reference itself (step 4); the spec's IDs describe
     what the reference does today (read its code and tests; do not decide the behavior
     again); ask the user only about what is to differ. The agents then carry over the
     reference's tests and copy its files instead of writing their own, and every step gate
     and the final gate show how far each file is from its source. Also for a port:
     - "Kept quirks" (in "Ported from"): read the reference for behaviours a stricter reading
       of your spec would forbid (a loose parse, a value written in a non-standard form).
       Each one is a decision: kept on
       purpose -> one bullet under "Kept quirks" (the reviewer then writes no finding about
       it); to be fixed here -> a requirement or edge case of the spec, and one bullet under
       "Reference follow-up" (the reference has it too). Ask the user only where neither is
       clearly right;
     - carried tests that cannot fail before the implementation: look through the reference's
       test files for scans and negative checks ("X is not imported"). List each under "Green against stubs" with its reason, and make
       sure every ID such a test serves has another test that does fail against the stubs;
     - a reference test file that will be carried over only in part (it tests code that is
       not ported): say so in "What may differ", so the test reviewer can tell a test left
       out on purpose from a missing one (the run lists every reference test not carried);
   - tests green by design (any feature): a test that cannot fail before the implementation
     (a scan, a negative check, a structure test; an accepted input the existing code already
     accepts; an existing error or behaviour that wins) is listed under "Green against stubs"
     in requirements.md (`- <test name>: <reason>`); only listed tests may pass the red check;
   - reference follow-up: what a reference should change because of this task (a bug it
     shares that is fixed here, a deliberate improvement): one bullet under "Reference
     follow-up" in requirements.md (`- <reference file>: <what should change> (<why>)`); the
     reference itself is never changed;
   - structure: which file each new concept goes in (one concept per file; the code map says
     where things live), so no file grows past ~400 lines or mixes two concepts.
2. Ask the user: one compact round, numbered, plain words, about 8 points at most, the most
   consequential first. Each: the point in one line, then "A: ... (default) / B: ..." on one
   line, A being the answer you recommend, so the user can say "ok" to all:

       1. An empty name?
          A: refused with an error (default) / B: accepted and stored as "unnamed"

   Always a question, never your decision, unless the task text, the code or the docs
   already state it:
   - every new or changed public name, signature, parameter type, default value, error
     type or message, stored or wire format (an existing pattern in the code that it
     follows states it: name the pattern);
   - what must not change;
   - where the tests go, when existing test files cover the area;
   - which docs the task may edit (as proposed doc updates);
   - what the user wants where two or more reasonable options have different outcomes
     (behavior, priorities, trade-offs).
   Not a question: what those sources or an earlier answer state (leave it out), and a point
   with no observable effect on callers or stored data (decide it: a "decision I made for
   you", one line). The same question twice -> one. Repeat until nothing is open; usually
   one round.
   A reply that describes the task (a ticket, the requirement in the user's words) and does
   not answer your questions is not "ok" and takes no default: it is the requirement. Do
   step 1 again with it, drop the questions it settles, and ask the ones it leaves open
   again, with the new ones it raises.
   design.md, "Public interfaces": one row per new or changed public member with where its
   shape comes from (`task`, `code: <pattern>`, `docs: <file>`, `asked` = the user
   answered); `spec-check` fails a member without one.
3. Write requirements.md, design.md and plan.md following the templates exactly (the ID format,
   "## Step N: title" headings and "Covers:" lines described above). Every ID must be covered by
   exactly one plan step. Fill the "Overlaps and sources of truth", fixture, "Input domain and
   copy semantics" and "Data flow" sections (if the project's templates lack them, add them
   anyway), and "Existing data this spec reads" / "Mirrored code" likewise. A step that builds
   on an earlier step's code, or changes the same method, gets a "Depends on: Step N" line.
   A port: the plan follows the files, not the IDs: one step per `code` target file, in
   import order (leaf modules first; a file after the files it imports), each step covering
   the IDs its file serves (an ID two files serve goes to the later one).
   Files in a plan step: a step may mention any file; everything outside the source folders
   stays read-only, the docs included (see "Sources" above).
4. Create interface stubs in the source paths named above, in the stub style named above: full
   typed signatures, bodies that only signal "not implemented". No doc comment and no spec ID
   in a stub: design.md, "Public interfaces", lists the IDs each member serves, and the tests
   carry them as tags. One concept per file. Stubs must compile or import cleanly.
   Except the code files of a port: do not write those stubs by hand. Run
   `framegate port-stubs $ARGUMENTS` once "Ported from" is filled: it writes every `code`
   target as the reference's own text at the spec's commit with the function bodies replaced
   (docstrings, signatures, constants, imports and module-level code word for word; no spec
   IDs added: the tests' tags carry them). Act on its lines: `IMPORT: ...` (an import that
   will not resolve here, e.g. the reference's package name): change that import line in the
   stub and list the kind of change under "What may differ"; `kept whole` (a function that
   runs when the module is imported keeps its body): a test of it passes before the
   implementation, so list it under "Green against stubs" or expect the red check to name
   it; `KEPT: ... exists` (a file you wrote before): `--force` overwrites it; `PROBLEM:` as
   it says. `NOT SUPPORTED` (a language without a stub generator): write those stubs by
   hand with the reference's names, signatures and doc comments as they are, and tell the
   user in one line that the stubs were written by hand.
5. Do not write tests or implementation. The test tagging conventions are for reference only;
   the TESTS phase uses them.
   Then run `framegate spec-check $ARGUMENTS` and fix what it lists (a rule without a spec
   ID, copied code that is not in the named file: read the code again and correct the spec's
   wording too; a forbidden marker in a stub, e.g. a lint or type suppression: write the stub
   so that it needs none). `framegate approve-spec` runs the same check and refuses while it
   fails. When it lists "Files the plan names that are already over the size limit": ask
   the user once per file: split it in this task (add a plan step) or later; for "later"
   run `framegate deferred --later <file> --feature $ARGUMENTS`.
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
   because of this task goes under "Reference follow-up" the same way (`- <reference file>:
   <what should change> (<why>)`); show it on the brief under "Pipeline follow-up" as "for
   <reference>: ...".
6. Write the spec gate brief to `<out>/$ARGUMENTS/spec_brief.md` (the out folder above; the
   final gate compares the run with it) and show it in the chat. About one screen, plain words,
   no spec IDs, one line per bullet, no sentence that repeats a heading or another section, a
   section with nothing in it is one line ("None."). Sources: the user navigates from the brief, so a line whose detail is elsewhere ends with its source in brackets, as a path from the repository root that opens in the editor: `(src/app/parser.py:120)` for code, `(<specs>/$ARGUMENTS/plan.md, Step 2)` for the spec; one source per line, the most useful one. The final gate lists these same plan steps again, each with how it went. Exactly these sections:

       **Task** — how I understand it (2-4 lines)
       **Plan** — the steps in plain words, numbered as in plan.md, one line each, ending with the main file it builds or changes
       **How it is proven** — the 3 most important checks of the tests and gates (rest on request)
       **Not proven** — the 3 most important things to check by using it (feel, balance, UI ...)
       **Decisions I made for you** — one line each; only points with no observable effect
         on callers or stored data ("None.")
       **Project details that matter** — existing code / data it relies on, invariants it
         must keep (saves, hashes, public APIs)
       **Setup files changed** — one line: the files `spec-check` lists, each with why
         ("pyproject.toml: registers the new package"); they are approved with the spec
       **Pipeline follow-up** — deploy / CI changes this task makes necessary, one line each
         (file: what, why); not part of the task, a separate step after it ("None."); an
         OUTSIDE item as "outside the repository: by hand: <where>: <what>"
       **Questions** — the real decisions still open (step 2's rule), by impact ("None.")

   Whatever changes what a caller sees or what is stored was asked in step 2 or is under
   "Questions"; it is never under "Decisions I made for you".
7. Ask: "Is the spec OK? Then I start the run." (default: yes, with the defaults of any open
   questions). An "ok" while questions are open: write their defaults into the spec and
   spec_brief.md first, say so in one line, then Part 2. Answers or changes: update the spec,
   the stubs and spec_brief.md, show the changed brief sections only, ask again. The spec files themselves only on request ("show
   me the plan", "more detail on step 2": show that part).

## Part 2: from "spec OK" to DONE

When the user says the spec is OK (any clear approval), follow the run protocol below from then
on, for the rest of this conversation: approve the spec, run in the background with the live
view, answer the gates, clean up at the end. `<f>` there is `$ARGUMENTS`.

!`framegate protocol`
