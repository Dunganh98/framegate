# framegate run protocol (chat)

Standing instructions for the rest of the conversation. `<f>` is the feature slug, `<out>` the
out folder (`[paths] out`, default `out`; `framegate gate-summary` prints the paths). Run every
command from the repository root, one command per call. The user never types `framegate`
commands.

**The user is the manager of this feature.** They give clear input and check outcomes; they do
not read specs, gate files or logs. Every message you write to them:
- puts the decision and its default first (one line), details after, and only on request;
- is short: one or two lines for states and errors, about one screen for a gate brief;
- uses plain words: no spec ID, test name or file path without its meaning in words ("the
  'no shot without a free kick' rule", not "FK-R-004").
"More detail" / "show me X": show the relevant part of the file (final_summary.md,
final_gates.md, tests_gate.md, test_summary.md, the spec files, retro_proposals.md), not all
of it.

## A. Start (after "spec OK") and continue (after every gate decision)

1. Only the first time: `framegate approve-spec <f>`. If it lists spec-check problems, fix
   the spec (they are mechanical: add the missing spec ID, copy the code line as it is) and
   approve again; `--force` only when the user says so.
2. Start the run in the background: Bash tool, `run_in_background: true`, command
   `framegate run <f>` (PowerShell tool the same if Bash is not available). Note its task id.
3. Start the live view: Monitor tool, command `framegate watch <f>`, description
   `framegate <f>`, `timeout_ms: 1800000`.
4. Tell the user in one line that the run started and they can keep chatting. End the turn.

Never start a second `framegate run` while one is active (`framegate status <f> --brief` says
"run active"). If the user asks to stop: TaskStop on the run's task id; the next run resumes.

While a run is active, do not build, test, format or run the checks yourself (no `dotnet
build/test`, `pytest`, etc.), and do not stop or kill test processes: the run's agents build and
test in the same checkout, and a second run next to theirs locks files and makes their checks
fail for no reason. Reading files and git log/diff/status are fine.

## B. Live view

- Each Monitor event is one or more progress lines (`HH:MM:SS <what> [$cost so far]`). Relay
  them as they are, as one compact block, without commentary. Add one short sentence only for
  `PARKED`, `ERROR` or `INTERRUPTED`.
- Monitor deadline notice (30 min) while the run is still active: start the same Monitor again
  (it continues where it stopped; nothing is lost or repeated).
- The run has ended when either the background task's completion notification arrives or a
  `watch ended: ...` line comes. On the first of the two, run `framegate status <f> --brief`
  and act on its first line once (the second signal is then ignored):
  - `waiting at the tests gate` / `waiting at the final gate`: section C.
  - `DONE`: section D.
  - `interrupted ... (usage limit, resets <when> ...)`: one line: the Claude usage limit
    stopped the run, it resets at <when>; "Continue then?" (default: yes, after <when>). Start
    A.2 only when the user says so after that time (a run started earlier stops again at once).
  - `interrupted ...` (any other reason): one line: where it stopped; "Continue the run?"
    (default yes), then A.2.
  - `phase X, not running` after an error: read the last 20 lines of the background task's
    output file (Read); tell the user in one or two lines what failed, in words, and the one
    decision needed with your default (e.g. "Retry the run? (default: yes, it looks like a
    timeout)"). The lines themselves on request. Do not retry blindly.

## C. At a gate

1. Run `framegate gate-summary <f>` and show its output unchanged (it is the brief: the
   decision line first, then about one screen in plain words). Add nothing but one line if a
   numbered question needs a word of context. Number the answers as the brief numbers its
   questions; framegate maps them to the gate items (and answers the decided and left-out
   ones itself). The left-out items: on request (final_brief.md).
2. End the turn and wait. Never decide a gate yourself.
3. Map the user's reply:

| Gate | User says | You run |
|---|---|---|
| any | "ok" / "yes" / "go" / "defaults" while the brief has numbered questions or items | write `<out>/<f>/answers.md` with every item's default as its answer (`A: Default: <the default, as the brief says it>`), then `framegate answer <f>` |
| any | answers to some numbered items ("1 yes, 3: use 0") | write `<out>/<f>/answers.md` (below), then `framegate answer <f>`; items not named: their default, unless the user said "only these" |
| tests | approval ("ok", "approve", "looks good", "go") with no numbered questions | `framegate approve <f>` |
| tests | rejection with a reason | `framegate reject <f> --reason '<reason in the user's words>'` |
| final | approval: "ok", "approve", "done", "finish", "looks good", "retro" with no numbered items, or "approve as is" | `framegate approve <f>` (retro, then DONE) |
| final | explicitly no retro: "no retro", "skip the retro", "without retro" | `framegate finish <f> --no-retro` |

   - The tests gate comes only with `[gates] tests = "human"` or `"auto"`; by default the run
     goes from the spec straight to the final gate, and the tests' questions are numbered there.
   - Answers that need a test change (other seeds, a fixed review test) are applied by the test
     writer; never ask the user to edit tests. An answer to a file-size item ("split it" /
     "leave it") goes to the implementer, who restructures the code with the tests unchanged.
   - `finish --no-retro` only when the reply says "no retro" (or the same in other words).
     "done" and "finish" mean approve. If the user wants the retro after a `finish --no-retro`:
     `framegate approve <f>` still queues it, then A.2.
   - Reject reason in single quotes; write a `'` inside as `'\''`.
   - A rejection without a reason, a final-gate "reject", or a reply that fits two rows: ask one
     short question with your default and wait. A wish to change the spec at the final gate:
     say in one line that it means editing the spec and `framegate approve-spec <f>` (restarts
     from the tests), ask "Change the spec?" (default no).
   - A parked item with a proposed fix: "ok" / "approve <n>" for it is the answer
     `Approved: apply the proposed fix as written.`; the next run sends it to the phase that
     owns the file (spec, tests, code).
   - The provisional-steps item (code kept, test fixes proposed): "ok" / "yes" / "approve" for
     it, or for the whole gate while it is listed, is the answer `Approved: apply all proposed
     test fixes as written.` (`framegate approve` refuses while it is open). A "no" to one fix
     is an answer too, in the user's words: that step's code is reverted and redone.
4. `answers.md` format (overwrite the file; keep the user's wording, do not improve it):

   ```
   # Answers (<tests|final> gate)

   1. Q: <the item as numbered in the brief>
      A: <the user's words, or "Default: <the default>">
   2. Q: ...
      A: ...
   ```

5. After the command succeeds, continue with A.2 (finish --no-retro ends in DONE: section D).
   A command that fails: its error in one line, in words, and the decision with a default.

## D. DONE: wrap-up and cleanup

1. If `<out>/<f>/retro_proposals.md` exists: show its `## Brief` (the proposals as a short
   numbered list: problem -> fix, with the retro's recommendation take / skip / fold into N),
   then its `## Project knowledge` facts (K1, ...) as short lines. Ask in one line: "Which
   proposals and facts to keep? (default: the ones marked take, and all facts)". On the reply
   run `framegate keep <f> 1 3 5` for the proposals (the numbers the user keeps; `take` =
   the ones marked take, `all`, `none`) and `framegate learn <f> K1 K3` (or `all`) for the
   facts; nothing is added without the user's pick. `keep` writes the pick to the retro
   backlog (tracked: it survives the cleanup; proposals not kept are counted if they come
   back) and prints the kept ones; say in one line where they are (`framegate backlog`).
   Applying proposals is separate work, only if the user asks; the full file on request.
2. Then ask in one message, each with its default:
   1. "Create the PR with the final summary as its description? (pushes feat/<f>)", default no;
   2. "Clean up the run files now (specs and metrics stay)?", default yes.
3. PR only on an explicit yes: `git push -u origin <branch>`, then
   `gh pr create --base <[git] base, default main> --head <branch> --title "<f>: <one line>"
   --body-file <out>/<f>/final_summary.md`. Do it before the cleanup (the cleanup deletes
   the file). Never push without that yes.
4. Cleanup on yes: `framegate cleanup <f>`. Its result in one line.
5. One line: after the PR is merged, `/framegate:status <f>` offers removing the feature's tags.
