# <The refactor> — Refactor plan

<!-- Parsed by framegate: keep "## Step N: title" and one "Covers:" line in step 1 that lists
     every spec ID. Later steps need no "Covers:" line: in a refactor every step is checked
     with ALL the characterization tests, and each step runs on its own. Keep the steps small:
     one move, split or rename each; the code builds and every test passes after each.
     Files a step names: only what its implementer can write. That is the source folders,
     and documentation files (.md / .rst under a docs folder, README, CHANGELOG): one that a
     step names by its path ("docs/layout.md", not "the docs") becomes writable for that
     step when the spec is approved. Tests, specs, setup, deploy / CI and workflow files
     never go into a step; a file the step only reads gets "(read only)" after its name.
     `framegate spec-check` lists what does not fit. What the user must change by hand in
     this repository goes under "## To do by hand" after the last step. -->

## Step 1: <short title>
Covers: XXX-R-001, XXX-I-001
<the first move: what goes where>

## Step 2: <short title>
Depends on: Step 1
<the next move>
