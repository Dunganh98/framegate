# <Feature name> — Implementation plan

<!-- Parsed by framegate: keep "## Step N: title" and one "Covers:" line per step.
     Every spec ID is covered by exactly one step. A step that needs an earlier one (it builds
     on its code, or both change the same method): "Depends on: Step 1"; it waits when that
     step is parked.
     A step that adds code to a source file already over [gates] max_file_lines names where
     the new code goes instead: "New file: <path> (<what goes there>)" (`framegate spec-brief`
     lists the files over the limit).
     No step changes a documentation file: see "Proposed doc updates" in requirements.md.
     A port (requirements.md, "Ported from"): the steps follow the files, not the spec IDs.
     One step per `code` target file, in import order (a file comes after the files it
     imports); "Covers:" lists the IDs its file serves, and an ID that two files serve goes
     to the later one. -->

## Step 1: <short title>
Covers: XXX-R-001, XXX-E-001
<what to build in this step; small enough for one fresh agent context with room for fix cycles>

## Step 2: <short title>
Covers: XXX-R-002, XXX-I-001
Depends on: Step 1
...
