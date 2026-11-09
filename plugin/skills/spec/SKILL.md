---
name: spec
description: Interactive SPEC phase of the agentflow workflow for one feature - clarifying questions, requirements/design/plan, interface stubs. Run only when the user invokes it.
argument-hint: <feature-slug>
disable-model-invocation: true
allowed-tools: Bash(agentflow spec-brief *) PowerShell(agentflow spec-brief *)
---
You are in the SPEC phase of the agentflow workflow for feature `$ARGUMENTS`.

## Project conventions for this phase

The following was printed by `agentflow spec-brief $ARGUMENTS`. It names the spec folder, the
templates, the source and test paths, the language, and how interface stubs and tests are written in
this repository. It overrides any default you would otherwise assume.

!`agentflow spec-brief $ARGUMENTS`

## Steps

1. Read the spec folder for `$ARGUMENTS` and any requirements I provide.
2. Before writing anything, ask clarifying questions, grouped by: requirements and acceptance
   criteria; edge cases (empty, NaN, missing, out-of-range, units, timing, ordering); failure
   behavior; non-goals; test data and fixtures; verification harness on real data; dependencies
   and architecture constraints. At most ~10 questions per round. Repeat until nothing is open.
3. Write requirements.md, design.md and plan.md following the templates exactly (the ID format,
   "## Step N: title" headings and "Covers:" lines described above). Every ID must be covered by
   exactly one plan step.
4. Create interface stubs in the source paths named above, in the stub style named above: full
   typed signatures, doc comments that reference the IDs, bodies that only signal "not
   implemented". Stubs must compile or import cleanly.
5. Do not write tests or implementation. Test tagging conventions above are for reference only;
   the TESTS phase uses them.
6. End with: (a) a short list of what I should review most carefully, (b) the command that ends
   this phase:

   ```
   agentflow approve-spec $ARGUMENTS
   ```
