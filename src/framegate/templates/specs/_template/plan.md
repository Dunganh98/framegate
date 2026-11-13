# <Feature name> — Implementation plan

<!-- Parsed by framegate: keep "## Step N: title" and one "Covers:" line per step.
     Every spec ID must be covered by exactly one step. Per step, when it needs an earlier one
     (it builds on its code, or both change the same method): "Depends on: Step 1". If step 1
     gets parked, step 2 waits (parked with it, redone after it) instead of building half of
     step 1 itself.
     A step that adds code to a source file already over [gates] max_file_lines (default 400)
     names where the new code goes instead: "New file: <path> (<what goes there>)". Seen live:
     two steps added to files of 1,093 and 835 lines, and the split cost a fix run after the
     review. `framegate spec-brief` lists the files that are over the limit. -->

## Step 1: <short title>
Covers: XXX-R-001, XXX-E-001
<what to build in this step; small enough for one fresh agent context with room for fix cycles>

## Step 2: <short title>
Covers: XXX-R-002, XXX-I-001
Depends on: Step 1
...
