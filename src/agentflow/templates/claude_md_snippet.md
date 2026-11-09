# Workflow
- Development follows the agentflow workflow: spec -> tests -> implement -> review -> PR -> retro.
- {specs}/<feature>/ is the source of truth. If code and spec disagree, the spec wins.
- Never edit tests to make them pass. If a test or the spec looks wrong, write the problem to
  {out}/<feature>/questions.md and stop.
- If a requirement is ambiguous, do not guess: write the question to {out}/<feature>/questions.md and stop.
- Read {out}/<feature>/answers.md if it exists — it contains answers to earlier questions.
- No new dependencies unless the spec's design.md lists them.
