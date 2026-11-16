---
name: status
description: Say where the framegate features of this repository stand (one or two lines each, with the one decision needed) and offer the tag cleanup after a merged PR. Run only when the user invokes it.
argument-hint: "[feature-slug]"
disable-model-invocation: true
allowed-tools: Bash(framegate status *) PowerShell(framegate status *)
---
!`framegate status $ARGUMENTS --brief`

Turn the output above into one or two lines per feature, in words: where it stands and the one
decision needed, with your default. No raw command lines; the output itself on request.

- A feature waiting at a gate, running, interrupted or not running: "Continue it with
  `/framegate:resume <f>`." One still in SPEC: "Continue the spec with
  `/framegate:feature <f>`" (a bug fix: `/framegate:bugfix <f>`; a refactor:
  `/framegate:refactor <f>`; the `Mode:` line of its requirements.md says which).
- A first line `framegate: the plugin is X, the command-line tool Y`: "framegate is not up
  to date here: `/framegate:update` brings it in line."
- A line with `pipeline follow-up: N open item(s)` (deploy / CI changes a task left to do,
  also after it was cleaned up or merged): "Do them with `/framegate:pipeline <f>`."
- A feature shown as `DONE, run files cleaned up; tags left: ...`: "Is its PR merged? Then I
  remove its tags (default: yes once merged)." On yes run `framegate cleanup <f> --tags`
  (deletes the local spec-approved / tests-approved / spec-update / tests-update tags; the CI
  frame diff needs them until the merge).

Do not start, approve or change anything else from here.
