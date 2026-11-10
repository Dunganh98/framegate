---
name: status
description: Show where the agentflow features of this repository stand (one line each, or the last progress of one feature) and offer the tag cleanup after a merged PR. Run only when the user invokes it.
argument-hint: "[feature-slug]"
disable-model-invocation: true
allowed-tools: Bash(agentflow status *) PowerShell(agentflow status *)
---
!`agentflow status $ARGUMENTS --brief`

Show the output above in a code block, unchanged, then at most two short lines:

- A feature waiting at a gate, running, interrupted or not running: "Continue it with
  `/agentflow:resume <f>`." One still in SPEC: "Continue the spec with
  `/agentflow:feature <f>`."
- A feature shown as `DONE, run files cleaned up; tags left: ...`: if the user says its PR is
  merged, offer
  `agentflow cleanup <f> --tags` (deletes the local spec-approved/tests-approved/spec-update/tests-update
  tags; the CI frame diff needs them until the merge) and run it only on their yes.

Do not start, approve or change anything else from here.
