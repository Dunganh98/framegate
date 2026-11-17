# framegate run protocol (chat)

Standing instructions for the rest of the conversation. `<f>` is the feature slug, `<out>` the
out folder (`[paths] out`, default `out`; `framegate gate-summary` prints the paths), `<specs>`
the spec folder (`[paths] specs`, default `specs`). Run every
command from the repository root, one command per call. The user never types `framegate`
commands.

**The user is the manager of this feature.** They give clear input and check outcomes; they do
not read specs, gate files or logs. Every message you write to them:
- puts the decision and its default first (one line), details after, and only on request;
- is short: one or two lines for states and errors, about one screen for a gate brief;
- uses plain words: no spec ID, test name or file path without its meaning in words ("the
  'no shot without a free kick' rule", not "FK-R-004").
"More detail" / "show me X": show the relevant part of the file (final_summary.md,
final_gates.md, tests_gate.md, test_summary.md, the spec files, retro_proposals.md, the pull
request's description `<specs>/<f>/pr_description.md`), not all of it.

**Notes for the retro.** The retro reads the run's events, not this chat. Record in one line
each, with `framegate note <f> '<text>'`: a question you asked the user outside a gate brief
and the reply (`asked: ... -> user: ...`), anything you did by hand (`did: ...`), anything
you or the user refused (`refused: ...`). Gate answers and approvals are recorded already.

## A. Start (after "spec OK") and continue (after every gate decision)

1. Only the first time: `framegate approve-spec <f>`. If it lists spec-check problems, fix
   the spec (they are mechanical: add the missing spec ID, copy the code line as it is) and
   approve again; `--force` only when the user says so. It commits the setup files changed in
   the spec phase with the spec (it prints which) and runs `[env] refresh` if the project has
   one. For a bug fix (and where framegate.toml sets `regression_baseline = true`) it
   then runs the whole suite once (this can take minutes). If it prints "Regression
   baseline: N test(s) already fail" with N above 0, pass that on to the user in your next
   status message, in plain words (these tests fail today and are left out). Do not stop
   for it and do not ask: going on is the default. If it refuses because of other uncommitted protected files (e.g. CLAUDE.md, CI or
   deploy files, `[protected] extra`) or a failed refresh: say which and why in one line and
   ask the user what to do (commit or revert the files; fix the setup); never commit or
   revert them yourself. A deploy / CI change the feature needs is never part of it: it
   belongs under `## Pipeline follow-up` of requirements.md and is done as a separate step.
   Something outside the repository (a CI variable, a secret, a real database) goes there
   as `- OUTSIDE [<where>]: <what> (<why>)`: a person does it by hand, never framegate.
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
- A line `usage limit reached; waiting until <when> ...`: the run waits by itself and tries
  again every few minutes; one line to the user: the Claude usage limit is reached, the run
  goes on at <when> at the latest, and "say so when you have refilled the limit". When the
  user says the limit is back (refilled, extra usage bought): run `framegate wake <f>` (the
  run tries again within seconds; it is not a second run). Never wake it on your own.
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
  - The user gives the task up before DONE: offer the retro of what happened (default
    yes): `framegate retro <f>` (the task's state stays as it is), then D.1.

## C. At a gate

1. Run `framegate gate-summary <f>` and show its output with every word unchanged (it is
   the brief: the decision line first, then about one screen in plain words), laid out like
   the spec brief: as markdown text, never in a code block; each section title (`Steps:`,
   `Struggles:`, `Outcomes:`, ...) in bold on its own line, its lines as a list under it;
   file paths stay as written (they open in the editor). Add nothing but one line if a
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
   After answers at the final gate that only took the defaults, that run may end in DONE
   without a second final gate (nothing changed: approved with the checks already shown).
   A command that fails: its error in one line, in words, and the decision with a default.

## D. DONE: wrap-up and cleanup

0. `framegate status <f> --brief` printed `framegate: the plugin is X, the command-line tool
   Y`: one line to the user: a framegate update is ready, `/framegate:update` gets it.
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
1b. Pipeline follow-up, before the publish question: when `framegate status <f> --brief`
   prints `Pipeline follow-up: N open item(s)` (deploy / CI changes this task made
   necessary and did not make), ask in one line:
   "Do the pipeline follow-up now? (`/framegate:pipeline <f>`)", default yes: done first,
   the product and the deploy / CI change go out in one push, as two commits of one pull
   request. On yes: tell the user to
   type `/framegate:pipeline <f>` and stop here (that skill ends with the publish and the
   cleanup questions). On no: go on with step 2 and say in one line that the same command
   does it any time later (after the merge it gets a pull request of its own).
   A line `Outside the repository: N item(s) to do by hand` (a CI variable, a secret, a
   real database: no file here) is not work for that command or for you: say it in one
   line, as it is. Those items are an unticked checklist in the final summary and near the
   top of the pull request's description, so they are seen at merge time. Never do one
   yourself.
2. Run `framegate publish <f> --dry-run` (it changes nothing). Its first line decides
   which of the two offers below applies:
   - `State branch: off`: the feature branch itself is what gets pushed (2a, 3a).
   - `Publish <f> (dry run ...)`: the project keeps framegate's files apart from the code
     (`[git] state_branch`): the pull request gets a branch with the product only (2b, 3b).
   Then ask in one message, each with its default. The publish question is ONE question,
   its default is yes, and the yes includes the pull request: the user finished a task to
   get it reviewed. Nothing is pushed before the user's reply to it.
   - 2a. "Push <branch> and create the pull request into <base>? (default: yes; 'publish
     without a pull request' and 'no' are fine too)". Its description is
     `<specs>/<f>/pr_description.md`: what the task is, the changes, how it is proven, what
     is left to check or do by hand; framegate wrote it at DONE.
   - 2b. Say in two or three lines what the dry run found: how many product files go to the
     branch for the team (the dry run's `Product branch <name>`; it is not the branch of
     this checkout, which is the local work branch `framegate-work/<f>`; name the ones that are not source or tests, e.g.
     `.gitignore` or a packaging file, so the user can say no), that framegate's own files
     go to the state branch and never to the pull request, and where the pull request
     goes. The pull request gets one commit; the commits of this feature stay on the local
     work branch, which publish never rewrites: do not offer a backup branch. Ask: "Publish
     it and create the pull request into <base>? (default: yes; 'publish without a pull
     request' and 'no' are fine too)".
   - In both cases also: "Clean up the run files now (specs and metrics stay)?", default
     yes. The order of the two does not matter: the description is kept with the spec, so
     a cleaned-up task can still be published and get its pull request later.
3. Map the reply to the publish question. "ok" / "yes" / "go" / "defaults": publish and
   create the pull request. "publish without a pull request" / "push only": publish, no
   pull request. "no" / "not yet": nothing is pushed; say in one line that
   `/framegate:resume <f>` offers it again. No reply to that question: never push.
   - 3a. `framegate pr-description <f>` (it brings the description up to date and prints
     its file), then `git push -u origin <branch>`. With a pull request, on GitHub:
     `gh pr create --base <[git] base, default main> --head <branch> --title "<f>: <one line>"
     --body-file <the file it printed>`. On any other host there is no tool here: give the
     branch name, the address to open the pull request if the host shows one, and the
     description file as its text.
   - 3b. `framegate publish <f> --pr --title "<f>: <one line>"` (without `--pr` when the
     user wants no pull request). Never `git push` yourself here: the work branch in this
     checkout (`framegate-work/<f>`) holds framegate's files and stays local (a pre-push
     guard refuses it; never use `--no-verify`); `publish` builds and pushes the product
     branch and the state branch, and creates the pull request with `gh` (GitHub) or `az`
     (Azure DevOps). If it refuses (uncommitted changes, a remote branch of that name with
     other content, a state branch that moved): its reason in one line and the decision
     with your default (an uncommitted file: commit it, restore it, or, for tool state of
     this machine, add it to `[git] machine_files`; files in a tool's dot folder such as
     `.serena/` never block); `--force-with-lease` only when the user says to replace the remote
     branch.
   - The result, in two lines: what was pushed, and the pull request's address as the
     command printed it (`Pull request ... created: <address>`). Any other line about the
     pull request means it does NOT exist yet (`not created: <why>`: the tool is missing,
     not logged in, another host; `... failed (<why>)`): say so with the reason in one
     line, and give the address to open it by hand and the description file. Never say a
     pull request was created without its address from the command.
4. Cleanup on yes: `framegate cleanup <f>`. Its result in one line. Nothing has to be
   copied or saved first.
5. One line: after the PR is merged, `/framegate:status <f>` offers removing the feature's tags.
