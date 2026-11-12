---
name: status
description: Say where the agentflow features of this repository stand (one or two lines each, with the one decision needed) and offer the tag cleanup after a merged PR. Run only when the user invokes it.
argument-hint: "[feature-slug]"
disable-model-invocation: true
allowed-tools: Bash(agentflow status *) PowerShell(agentflow status *)
---
!`agentflow status $ARGUMENTS --brief`

Turn the output above into one or two lines per feature, in words: where it stands and the one
decision needed, with your default. No raw command lines; the output itself on request.

- A feature waiting at a gate, running, interrupted or not running: "Continue it with
  `/agentflow:resume <f>`." One still in SPEC: "Continue the spec with
  `/agentflow:feature <f>`" (a bug fix: `/agentflow:bugfix <f>`; a refactor:
  `/agentflow:refactor <f>`; the `Mode:` line of its requirements.md says which).
- A feature shown as `DONE, run files cleaned up; tags left: ...`: "Is its PR merged? Then I
  remove its tags (default: yes once merged)." On yes run `agentflow cleanup <f> --tags`
  (deletes the local spec-approved / tests-approved / spec-update / tests-update tags; the CI
  frame diff needs them until the merge).

Do not start, approve or change anything else from here.
