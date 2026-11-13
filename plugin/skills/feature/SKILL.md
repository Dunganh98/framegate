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

If the output above says the spec is **already approved**, do not start the dialogue: run
`framegate status $ARGUMENTS --brief`, tell the user in one line where the feature stands and
ask: continue it (default; then the run protocol below, by state as in its section B), or change
the spec (that needs `framegate approve-spec` again and restarts from the tests).

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
the language, how interface stubs and tests are written here, the code map and the project
knowledge. It overrides any default you would otherwise assume.

!`framegate spec-brief $ARGUMENTS`

## Part 1: the spec dialogue

1. Read the requirements the user gives, then decide everything you can yourself first: read
   the docs, the code map, the code the feature touches and the project knowledge above. Work
   through this checklist and settle each point from those sources (each gap cost a live run
   questions, assumptions or a parked step):
   - requirements and acceptance criteria; edge cases (empty, NaN, missing, out-of-range,
     units, timing, ordering); failure behavior; non-goals;
   - overlaps: which requirements can apply to the same input, and which one wins; which stored
     value is copied or derived from another, and can the source change afterwards;
   - fixtures: for each case that needs particular data (a seed, a recording, a rare
     situation), how a test sees from outside that the situation happened, and whether a check
     over picked cases may pass with zero cases;
   - edge cases against the pinned data: for each edge case, and each outcome a rule names in
     words ("saved", "blocked", "no result"), does the pinned data hold it? Scan for data that
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
   - existing data: every existing concept a requirement names in words ("the moment's goal
     event", "his rating") resolved to one exact member and where it is set (read the code),
     with the line that assigns it copied into the spec (an id or an index? raw or clamped?);
   - rejected input: which existing method decides each rejected case and when it is checked;
     for a parser the accepted forms (sign, largest value, overflow); for a command, each
     option's default, what happens without a value, and the message + exit code of a bad one;
   - error rules and text output: every rule in design.md's "Error handling" has a spec ID
     with an acceptance criterion; every text output has one literal example line;
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
     stubs mirror the reference's public names and signatures exactly; the spec's IDs describe
     what the reference does today (read its code and tests; do not decide the behavior
     again); ask the user only about what is to differ. The agents then carry over the
     reference's tests and copy its files instead of writing their own, and the final gate
     shows how far each file is from its source;
   - structure: which file each new concept goes in (one concept per file; the code map says
     where things live), so no file grows past ~400 lines or mixes two concepts.
2. Ask only what those sources cannot decide: what the user wants (behavior, priorities,
   trade-offs), not what the code can tell. Numbered, plain words.
   A point is a question only when it is a real decision: two or more reasonable options with
   different outcomes, and the docs, the code, `.framegate/project.md` and earlier answers don't
   settle it. Otherwise it is not a question: only one sensible option -> decide it (a "decision
   I made for you", one line); already answered or resolved, or a note -> leave it out; the same
   question twice -> one. No limit on the number; order them by impact (what changes most if
   answered differently first). Each: the problem in one line, then "A: ... (default) / B: ..."
   on one line:

       1. A free kick inside the box?
          A: not allowed, the rules forbid it (default) / B: allowed as an indirect kick

   Repeat until nothing is open; usually one round.
3. Write requirements.md, design.md and plan.md following the templates exactly (the ID format,
   "## Step N: title" headings and "Covers:" lines described above). Every ID must be covered by
   exactly one plan step. Fill the "Overlaps and sources of truth", fixture, "Input domain and
   copy semantics" and "Data flow" sections (if the project's templates lack them, add them
   anyway), and "Existing data this spec reads" / "Mirrored code" likewise. A step that builds
   on an earlier step's code, or changes the same method, gets a "Depends on: Step N" line.
4. Create interface stubs in the source paths named above, in the stub style named above: full
   typed signatures, a one-line doc comment per public type and method saying what it is for
   (in words) plus the IDs it serves, bodies that only signal "not implemented". One concept
   per file. Stubs must compile or import cleanly.
5. Do not write tests or implementation. The test tagging conventions are for reference only;
   the TESTS phase uses them.
   Then run `framegate spec-check $ARGUMENTS` and fix what it lists (a rule without a spec
   ID, copied code that is not in the named file: read the code again and correct the spec's
   wording too; a forbidden marker in a stub, e.g. a lint or type suppression: write the stub
   so that it needs none). `framegate approve-spec` runs the same check and refuses while it
   fails.
   Setup files: if the task needs a new package or source folder, a dependency or a tool
   setting, change those files now, in the spec phase: `framegate.toml`, the packaging /
   build / tool-config files (e.g. `pyproject.toml`, `pytest.ini`, a project file) and the
   `[protected] extra` paths are writable for you until the approval, which commits them with
   the spec and freezes them. Nothing can change them later in the run. Tests, `.claude/`,
   `.framegate/`, `CLAUDE.md` and CI files stay locked for you: if one must change, tell the
   user. `spec-check` prints "Setup files changed" (for the brief) and lists other uncommitted
   protected files, which the approval refuses: ask the user to commit or revert those.
6. Write the spec gate brief to `<out>/$ARGUMENTS/spec_brief.md` (the out folder above; the
   final gate compares the run with it) and show it in the chat. About one screen, plain words,
   no spec IDs, one line per bullet, no sentence that repeats a heading or another section, a
   section with nothing in it is one line ("None."); exactly these sections:

       **Task** — how I understand it (2-4 lines)
       **Plan** — the steps in plain words, one line each
       **How it is proven** — the 3 most important checks of the tests and gates (rest on request)
       **Not proven** — the 3 most important things to check by using it (feel, balance, UI ...)
       **Decisions I made for you** — one line each, with what it rules out
         ("Goals from rebounds count for the free kick: rules out crediting the rebounder.")
       **Project details that matter** — existing code / data it relies on, invariants it
         must keep (saves, hashes, public APIs)
       **Setup files changed** — one line: the files `spec-check` lists, each with why
         ("pyproject.toml: registers the new package"); they are approved with the spec
       **Questions** — the real decisions still open (step 2's rule), by impact ("None.")

   "Decisions I made for you" is the part the user really checks: list the ones that change
   what they will see or that close off an option; the mechanical ones stay in the spec.
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
