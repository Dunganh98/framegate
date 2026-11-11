---
name: feature
description: Drive one agentflow feature from this chat - spec dialogue, approval, the background run with a live view, the gates answered in plain words, cleanup. Run only when the user invokes it.
argument-hint: <feature-slug>
disable-model-invocation: true
allowed-tools: Bash(agentflow *) PowerShell(agentflow *) Monitor Read Write Edit Glob Grep
---
You drive the agentflow workflow for feature `$ARGUMENTS` in this chat. The user only types
requirements, answers and gate decisions; you run every `agentflow` command. The CLI is the
engine: never edit `progress.json`, tags or anything under the out folder by hand (the one
exception is `answers.md`, see the run protocol).

## Setup (already done when this skill loaded)

`agentflow spec $ARGUMENTS` created or switched to the feature branch and set the SPEC phase:

!`agentflow spec $ARGUMENTS`

If the output above says the spec is **already approved**, do not start the dialogue: run
`agentflow status $ARGUMENTS --brief`, tell the user in one line where the feature stands and
ask: continue it (default; then the run protocol below, by state as in its section B), or change
the spec (that needs `agentflow approve-spec` again and restarts from the tests).

## Project conventions for the spec

Printed by `agentflow spec-brief $ARGUMENTS`: the spec folder, templates, source and test paths,
the language, and how interface stubs and tests are written here. It overrides any default you
would otherwise assume.

!`agentflow spec-brief $ARGUMENTS`

## Part 1: the spec dialogue

1. Read the spec folder for `$ARGUMENTS` and the requirements the user gives.
2. Before writing anything, ask clarifying questions, grouped by: requirements and acceptance
   criteria; edge cases (empty, NaN, missing, out-of-range, units, timing, ordering); failure
   behavior; non-goals; test data and fixtures; verification harness on real data; dependencies
   and architecture constraints. Always also ask (each gap below cost a live run questions,
   assumptions or a parked step):
   - overlaps: which requirements can apply to the same input, and which one wins; which stored
     value is copied or derived from another, and can the source change afterwards;
   - fixtures: for each case that needs particular data (a seed, a recording, a rare situation),
     how a test sees from outside that the situation happened, and whether a check over picked
     cases may pass with zero cases;
   - input domain and copies: the valid range of each public method's inputs (NaN, infinity,
     null, undefined enum values) and what a copy / clone shares;
   - serialized data: which existing golden / hash / snapshot / save tests serialize a type
     this feature changes, and is the new member in or out of them;
   - existing data: every existing concept a requirement names in words ("the moment's goal
     event", "his rating") resolved to one exact member and where it is set; read the code,
     propose the member, and ask only when two members could be meant;
   - mirrored code (only when a requirement says "as X does"): the method and lines of X and
     the choices to copy (cut-offs, raw vs clamped values, tie-breaks, order of checks); for a
     variant (another choice, grade, side), which input fields change and how;
   - new functions: which inputs each clamps itself, what every output holds on "no result",
     and which error wins when two inputs are bad.
   At most ~10 questions per round, numbered, each with a recommended default. Repeat until
   nothing is open.
3. Write requirements.md, design.md and plan.md following the templates exactly (the ID format,
   "## Step N: title" headings and "Covers:" lines described above). Every ID must be covered by
   exactly one plan step. Fill the "Overlaps and sources of truth", fixture, "Input domain and
   copy semantics" and "Data flow" sections from the answers (if the project's templates lack
   them, add them anyway), and "Existing data this spec reads" / "Mirrored code" likewise. A step that builds on an earlier step's code, or changes the same
   method, gets a "Depends on: Step N" line.
4. Create interface stubs in the source paths named above, in the stub style named above: full
   typed signatures, doc comments that reference the IDs, bodies that only signal "not
   implemented". Stubs must compile or import cleanly.
5. Do not write tests or implementation. The test tagging conventions are for reference only;
   the TESTS phase uses them.
6. End with a short list of what the user should review most carefully, and ask: "Is the spec
   OK? Then I start the run." Changes the user asks for: make them and ask again.

## Part 2: from "spec OK" to DONE

When the user says the spec is OK (any clear approval), follow the run protocol below from then
on, for the rest of this conversation: approve the spec, run in the background with the live
view, answer the gates, clean up at the end. `<f>` there is `$ARGUMENTS`.

!`agentflow protocol`
