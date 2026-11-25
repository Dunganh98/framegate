---
name: update
description: Bring framegate on this machine in line with the installed plugin - the command-line tool and the project's workflow tools - and say in one line what changed. Run only when the user invokes it.
disable-model-invocation: true
allowed-tools: Bash(framegate *) PowerShell(framegate *) Bash(uv tool install *) PowerShell(uv tool install *)
---
You bring framegate up to date here. Three pieces must be at one version: the plugin (these
skills, updated by Claude Code), the command-line tool (`framegate`, installed with uv) and
the workflow tools in the project's environment (for Python: the framegate pytest plugin).
The user asked for this, so nothing is kept back, also with a task in progress.

This plugin is version 0.13.3.

1. Run `framegate update --plugin-version 0.13.3` and act on every line it prints:
   - `Start check: ok ...`: everything is in line. Say so in one line with the version and
     stop.
   - `RUN: <command>`: the command-line tool is not at this plugin's version and cannot
     replace itself while it runs. Run the command, then step 1 again.
   - `UPDATED: ...`: one line to the user: what was brought to which version; a `CHANGED
     versions` part as it is (a pinned package moved: the user decides whether to keep it).
   - `TELL THE USER: ...` (a newer plugin is on this machine than the one this session
     runs): say it as it is, and that after the restart `/framegate:update` finishes the job.
     Stop.
   - `PROBLEM: ...`: say it in one line with its fix. A missing `framegate.toml` only means
     this folder is no framegate project: the command-line tool is still updated.
   - The command fails with `invalid choice: 'update'` (a tool older than this one): run

         uv tool install --force "git+https://github.com/Dunganh98/framegate@v0.13.3"

     then step 1 again.
   - An install that fails (no network, no access to the repository, `framegate` running in
     another window): the error in one line and what to do; never say it worked.
2. End with one line: the version everything is at now. If the user asked because a release
   just came out and this plugin is not that version yet: Claude Code fetches a new plugin
   when a session starts (`/plugin` > Marketplaces > framegate > Update does it at once, and
   "Enable auto-update" there keeps it current); after a restart, `/framegate:update` again.
