---
name: resume
description: Reattach this chat to an agentflow feature after a session restart - says in one line where it stands, then continues with the live view, the waiting gate or the wrap-up. Run only when the user invokes it.
argument-hint: "[feature-slug]"
disable-model-invocation: true
allowed-tools: Bash(agentflow *) PowerShell(agentflow *) Monitor Read Write Edit Glob Grep
---
Reattach to the agentflow feature `$ARGUMENTS` (none given: pick from the list below).

## Where things stand

!`agentflow status $ARGUMENTS --brief`

## What to do

1. No feature was given and the list above shows several: ask which one in one line, with the
   one waiting at a gate or running as the default. Exactly one: take it. None: say so in one
   line and stop.
2. Follow the run protocol at the end of this message from then on, for the rest of this
   conversation. `<f>` is the chosen feature.
3. Tell the user in one line where the feature stands, in words (not the raw state line), then
   by state:
   - `run active`: start the live view only (protocol A.3; the run is already going; do not
     start a second one). Its end shows as `watch ended: ...` (there is no task notification
     for a run another session started).
   - `waiting at the ... gate`: protocol C.
   - `DONE`: protocol D.
   - `interrupted` or `not running` (spec approved): "Continue the run?" (default yes), then
     protocol A.2.
   - `SPEC (not approved yet)`: the spec dialogue continues with `/agentflow:feature <f>`;
     tell the user to type that (it reloads the spec conventions).

## Run protocol

!`agentflow protocol`
