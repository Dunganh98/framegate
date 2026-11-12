# Workflow
- Development follows the framegate workflow: spec -> tests -> implement -> review -> final
  summary -> retro. Human gates: spec and final; nothing stops in between.
- {specs}/<feature>/ is the source of truth. If code and spec disagree, the spec wins.
- Never edit tests to make them pass.
- Ambiguity while writing or reviewing tests: write the question to
  {out}/<feature>/questions.md and continue; the questions are answered at the final gate.
- Ambiguity while implementing: assume what fits the spec and log it in
  {out}/<feature>/steps.md (`- ASSUMPTION [<spec ID>]: ...`). Only if no reasonable assumption
  is possible, write the question to {out}/<feature>/park.md and stop (the step is parked).
- Read {out}/<feature>/answers.md if it exists — it contains answers to earlier questions.
- No new dependencies unless the spec's design.md lists them.
