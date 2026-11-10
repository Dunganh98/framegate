# agentflow run protocol (chat)

Standing instructions for the rest of the conversation. `<f>` is the feature slug, `<out>` the
out folder (`[paths] out`, default `out`; `agentflow gate-summary` prints the paths). Run every
command from the repository root, one command per call. The user never types `agentflow`
commands; keep your own messages short.

## A. Start (after "spec OK") and continue (after every gate decision)

1. Only the first time: `agentflow approve-spec <f>`.
2. Start the run in the background: Bash tool, `run_in_background: true`, command
   `agentflow run <f>` (PowerShell tool the same if Bash is not available). Note its task id.
3. Start the live view: Monitor tool, command `agentflow watch <f>`, description
   `agentflow <f>`, `timeout_ms: 1800000`.
4. Tell the user in one line that the run started and they can keep chatting. End the turn.

Never start a second `agentflow run` while one is active (`agentflow status <f> --brief` says
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
  `watch ended: ...` line comes. On the first of the two, run `agentflow status <f> --brief`
  and act on its first line once (the second signal is then ignored):
  - `waiting at the tests gate` / `waiting at the final gate`: section C.
  - `DONE`: section D.
  - `interrupted ... (usage limit, resets <when> ...)`: say that the Claude usage limit stopped
    the run and when it resets; continue (A.2) only when the user says so after that time (a
    run started earlier stops again at once).
  - `interrupted ...` (any other reason): say where, ask "Continue the run?" (default yes),
    then A.2.
  - `phase X, not running` after an error: show the last 20 lines of the background task's
    output file (Read) and ask how to go on. Do not retry blindly.

## C. At a gate

1. Run `agentflow gate-summary <f>` and show its output in a code block, unchanged.
2. Ask for the decision in one line, matching the gate (the summary's last line), and end the
   turn. Wait for the user; never decide a gate yourself.
3. Map the user's plain-words reply:

| Gate | User says | You run |
|---|---|---|
| tests | approval ("ok", "approve", "looks good", "go") | `agentflow approve <f>` |
| tests | rejection with a reason | `agentflow reject <f> --reason '<reason in the user's words>'` |
| tests | answers to numbered questions | write `<out>/<f>/answers.md` (below), then `agentflow answer <f>` |
| final | approval: "ok", "approve", "done", "finish", "looks good", "retro" | `agentflow approve <f>` (retro, then DONE) |
| final | explicitly no retro: "no retro", "skip the retro", "without retro" | `agentflow finish <f> --no-retro` |
| final | answers to parked items / numbered questions | write `<out>/<f>/answers.md`, then `agentflow answer <f>` (answers that need a test change, e.g. other seeds or a fixed review test, are applied by the test writer; no need to ask the user to edit tests) |

   - The tests gate comes only with `[gates] tests = "human"` or `"auto"`; by default the run
     goes from the spec straight to the final gate, and the tests' questions are numbered there.
   - `finish --no-retro` only when the reply says "no retro" (or the same in other words).
     "done" and "finish" mean approve. If the user wants the retro after a `finish --no-retro`:
     `agentflow approve <f>` still queues it, then A.2.
   - Reject reason in single quotes; write a `'` inside as `'\''`.
   - A rejection without a reason, a final-gate "reject", or a reply that fits two rows: ask one
     short clarifying question and wait. A wish to change the spec at the final gate: explain
     it means editing the spec and `agentflow approve-spec <f>` (restarts from the tests), ask.
   - Answers plus an approval at the tests gate: answers win (they re-run the tests anyway).
   - A parked item with a diagnosis (root cause + proposed fix): "ok" / "approve <n>" for it is
     the answer `Approved: apply the proposed fix as written.`; the next run sends it to the
     phase that owns the file (spec, tests, code). Approving the gate itself is a separate reply.
4. `answers.md` format (overwrite the file; keep the user's wording, do not improve it):

   ```
   # Answers (<tests|final> gate)

   1. Q: <the question as numbered in the gate summary>
      A: <the user's words>
   2. Q: ...
      A: ...
   ```

   Questions the user did not answer: leave them out.
5. After the command succeeds, continue with A.2 (finish --no-retro ends in DONE: section D).
   A command that fails: show its error line and ask.

## D. DONE: wrap-up and cleanup

1. If `<out>/<f>/retro_proposals.md` exists: show its proposals as a short numbered list (one
   line each). Applying them is separate work, only if the user asks. Show its "Project
   knowledge" items (K1, ...) too and ask which to keep; on the reply run
   `agentflow learn <f> K1 K3` (or `all`). Nothing is added without the user's pick.
2. Ask in one message:
   1. "Create the PR with the final summary as its description? (pushes feat/<f>)", default no;
   2. "Clean up the run files now (`<out>/<f>/` and its run logs; specs and metrics stay)?",
      default yes.
3. PR only on an explicit yes: `git push -u origin <branch>`, then
   `gh pr create --base <[git] base, default main> --head <branch> --title "<f>: <one line>"
   --body-file <out>/<f>/final_summary.md`. Do it before the cleanup (the cleanup deletes
   the file). Never push without that yes.
4. Cleanup on yes: `agentflow cleanup <f>`. Show its output in one or two lines.
5. Tell the user: after the PR is merged, `/agentflow:status <f>` offers removing the
   feature's tags (`agentflow cleanup <f> --tags`; the CI frame diff needs them until then).
