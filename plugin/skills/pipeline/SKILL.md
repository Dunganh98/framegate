---
name: pipeline
description: Make the deploy / CI changes a framegate task left as its pipeline follow-up, or a deploy / CI change of its own - the list first, the edits made in this chat, one mechanical check, a short brief to approve, then the publish. Run only when the user invokes it.
argument-hint: <task-name or a new name>
disable-model-invocation: true
allowed-tools: Bash(framegate *) PowerShell(framegate *) Bash(uv tool install *) PowerShell(uv tool install *) Bash(git diff *) PowerShell(git diff *) Read Write Edit Glob Grep
---
You do the pipeline task `$ARGUMENTS` in this chat: the separate step that changes deploy
files (Dockerfile, compose, k8s manifests) and CI files (yml, scripts). No feature, bug fix or
refactor changes such a file; what it made necessary there was kept as a list, and this step
works that list off. `$ARGUMENTS` is the name of that task, or a new name for a change of its
own ("an update to the pipeline only"). The user is the manager: they say what should change
and check the outcome; you run every `framegate` command and make the edits yourself. There
is no spec, no test and no background run here: a yml change has no unit test.

## Start check (your first step)

This plugin is version 0.13.3. Before anything else run this command yourself:

    framegate preflight $ARGUMENTS --plugin-version 0.13.3

`Start check: ok`: without a word. `KEPT:` lines (a task in progress, here or in another
repository, keeps the versions it was started with): one line to the user in all: an update
is waiting for that task, `/framegate:update` does it now. `RUN: <command>` (the command-line tool
is not at this plugin's version): run it, then the start check again. `UPDATED: ...`: one
line to the user. A `TELL THE USER:` line (the plugin was
updated while this session ran: restart Claude Code): say it as it is and stop. A `PROBLEM:`
line: say it in one line with its fix and ask: fix it first (default), or go on. If this
command, or `framegate pipeline` below, fails with `invalid choice`, the installed
command-line tool is older than this plugin. Bring it to this version, then repeat the
command:

    uv tool install --force "git+https://github.com/Dunganh98/framegate@v0.13.3"

## 1. Start: the list

Run `framegate pipeline $ARGUMENTS`. It switches to the task's branch, opens the pipeline
phase and prints the list. In this phase only deploy / CI files that the list names can be
written (and the list and your brief); source, tests, setup files and specs are locked.

- It refuses (another phase is open, a run is active, the task is not DONE, uncommitted
  changes): its reason in one line, and the one decision needed with your default.
- `List (...): N open of M item(s)`: the list is the input; there is no new dialogue. Show the
  open items, numbered, in plain words, one line each, and go on with step 2. An item marked
  `[not a deploy / CI file here ...]` or `[names no file ...]` cannot be done from here as
  written: it becomes a question of the brief (step 4), with "not needed: <reason>" as its
  default. An item marked `[not needed: ...]` is closed: show it as such, do nothing.
- `Outside the repository: by hand, not this task: N to do`: items that are no file of this
  repository (a CI variable, a secret, a real database, an environment). You never do them,
  not even when you could. Show them under their own line, after the numbered items: "By
  hand, outside the repository: ...". They are never open work of this task.
- `Changed before this task started`: files with uncommitted changes that are not this
  task's. The check leaves them out, so do not stash, restore or commit them yourself. Ask
  once, now, with the list: "<files> were changed before; commit them, restore them, or
  leave them until the publish? (default: leave)". A file of framegate's own that the user
  changed on purpose (framegate.toml) is committed on its own (`git commit <file> -m
  "framegate: <what>"`) after this task's approval; a tool's own file (an editor or MCP
  config) is usually restored. `framegate publish` refuses while any is left. Files in a
  tool's dot folder (`.serena/`, `.vscode/`, ...) are machine files: framegate ignores them,
  they are never on this list and never asked about; a tool file elsewhere that keeps
  coming back belongs in `[git] machine_files` (propose it once, default yes).
- `No list yet`: a change of its own. Ask what should change if the user has not said it.
  Read the deploy / CI files concerned, then write the list to the file the output names,
  one line per change: `- [ ] <file>: <what must change> (<why>)`. Show it numbered and ask:
  "Is this the list? Then I make the changes." (default: yes). Edit nothing before that OK.

The user wants one more file changed: add its line to the list first, then edit it. Only
deploy / CI files can be on the list, and what a person must do outside the repository, as
`- [ ] OUTSIDE [<where>]: <what must be done> (<why>)` (add such a line when a change you
make needs one, e.g. a new variable the yml reads).

## 2. The changes

Make each open item's change yourself, with the Edit / Write tools:

- Read the file first. Make the smallest change that does what the item says, in the file's
  own style. Change nothing the list does not ask for.
- A write that is blocked: do not work around it (no shell redirection, no other file).
  Say in one line what was blocked and why; the user decides.
- An item you cannot do or should not guess (it needs a secret, an access right, a value
  only the team knows): do not attempt it. End its line in the list with
  `(left open: <reason>)`. It stays open and comes up again.
- An item that turns out to be already in place (read the file: the change is there), or
  that cannot be done as written (it names no deploy / CI file): change nothing, and do not
  ask an open "what should happen?". Propose it in the brief's Questions as
  "<item>: not needed: <reason>? (default: yes)". When the user agrees (an "ok" does), end
  its line in the list with `(not needed: <reason>)`: that closes it for good, where
  `(left open: ...)` would keep it open in every status. Until the answer the check lists
  it as open; write the brief all the same.
- Never start a pipeline, a deployment or a build from here.

## 3. The check

Run `framegate pipeline-check $ARGUMENTS` (no agent, a few seconds). Fix what its `- ...`
lines name (a file changed that is not on the list or not a deploy / CI file: revert it; a
file that does not parse: fix it; an open item: do it or mark it left open) and run it again
until it says `OK`, or until the only lines left are `OPEN ITEM` lines of items you propose
as not needed (they close with the user's answer to the brief). `NOTE:` lines do not block:
each one goes into the brief.

## 4. The brief (the one decision)

Write the brief to `<out>/$ARGUMENTS/pipeline_brief.md` (`<out>` is the folder the start
output names for the brief) and show it in the chat, as markdown text, never in a code block.
Plain words, one line per bullet, about one screen. A line whose detail is in a file ends with
its source in brackets, as a path from the repository root: `(ci/azure-pipelines.yml:42)`.
Exactly these sections:

    **What was asked** — the list, numbered, one line each
    **What changed** — per item, same numbers: what was changed, with its source; an item
      left open: "left open: <reason>"; one you propose to close: "not needed: <reason>"
    **Outside the repository: by hand** — the `Outside the repository` items of the check,
      one line each (where: what); nobody did them here ("None.")
    **Checks** — what the check proved, one or two lines (only listed deploy / CI files
      changed, the files parse); each `NOTE:` line as one line
    **Not proven** — first line, as it is:
      "The pipeline itself has not run: the pull request's CI run is the proof."
      Then at most two things only that run can show
    **Questions** — real decisions still open, each with a default ("None."); an item
      that is already in place or impossible as written:
      "<item>: not needed: <reason>? (default: yes)", never an open "what should happen?"

Ask: "Are the changes OK? (the diff on request)", default yes. An "ok" takes every default:
write `(not needed: <reason>)` at the end of those items' lines now, and run the check once
more (it must say `OK`). The diff on request:
`git diff -- <file>` for that file (a new file: show its content). Changes wanted: make them,
run the check again, show the changed lines of the brief, ask again. Never approve yourself.

## 5. After the OK

Run `framegate pipeline-approve $ARGUMENTS`: it checks once more, ticks the done items in the
list (and the ones closed as not needed), commits the deploy / CI files, writes the pull
request's description again and closes the phase. Its result in one line.

## 6. How it reaches the team

Run `framegate publish $ARGUMENTS --dry-run` (it changes nothing). Then one question whose
default is yes and includes the pull request; nothing is pushed before the user's reply to
it ("publish without a pull request" and "no" are fine answers). The description is a file
framegate writes and keeps in the folder of the list (`pr_description.md` for the task's
pull request, `pipeline_pr_description.md` for a deploy / CI change of its own: your brief,
with what is left to do by hand outside the repository as an unticked checklist on top).
The cleanup does not delete it: nothing has to be copied first.

- `State branch: off`: the branch of this checkout is what gets pushed; the `Next:` line of
  step 5 names it.
  - A branch of its own (`pipeline/...`): ask "Push it and create its pull request?
    (default: yes)". On yes `git push -u origin <branch>`; on GitHub then `gh pr create
    --base <base> --head <branch> --title "$ARGUMENTS: deploy / CI changes" --body-file
    <the description file the `Next:` line names>`; on any other host say the branch and
    that this file is the text for the pull request.
  - The task's own branch: the change is one more commit on it. Ask "Push <branch>?
    (default: yes)"; a pull request that is open gets the commit. No pull request yet: say
    so, and create it as above with the file `framegate pr-description $ARGUMENTS` prints
    as its text.
- `Publish $ARGUMENTS (dry run ...)`: say in two lines what it found, then ask.
  - `Product branch <name>: ... deploy / CI files in a commit of their own`: the task is
    not merged yet, so its pull request carries two commits: the product, then the deploy /
    CI change. With the line `<name> is on origin already`: ask "Publish it? (default:
    yes)", then `framegate publish $ARGUMENTS` (no `--pr`: the branch's pull request, if
    one is open, now has a second commit; say that in one line. If the user says none is
    open yet, run it with `--pr`). Without that line nothing was published yet: ask "Publish it and create the pull request? (default:
    yes)", then `framegate publish $ARGUMENTS --pr --title "$ARGUMENTS: <one line>"`
    (without `--pr` when the user wants none).
  - `Pipeline branch <name> (...)`: the task is merged already, or there was none: the
    change gets a branch and a pull request of its own. Ask "Publish it and create the
    pull request? (default: yes)", then `framegate publish $ARGUMENTS --pr` (without
    `--pr` when the user wants none).
  - Never `git push` yourself here: the branch of this checkout holds framegate's files and
    stays local. If `publish` refuses, its reason in one line and the decision with your
    default; `--force-with-lease` only when the user says to replace the remote branch.
  Relay the result in two lines: what was pushed, and the pull request's address as the
  command printed it (`Pull request ... created: <address>`). Any other line about the pull
  request (`not created: <why>`, `... failed (<why>)`) means it does not exist yet: say so
  with the reason in one line, and give the address to open it by hand and the description
  file. Never say a pull request was created without its address from the command.

Last, when the publish is done or declined: "Clean up the run files now (the list and the
specs stay)?", default yes; on yes `framegate cleanup $ARGUMENTS`, its result in one line. If
items were left open, say in one line that `/framegate:pipeline $ARGUMENTS` picks them up
again. After the pull request is merged, `/framegate:status $ARGUMENTS` offers removing the
task's tags.
