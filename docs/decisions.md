# Decisions

Standing decisions of the user, one line each. The merged principles: docs/plan.md, section 2. How each works: docs/reference.md.

1. Two human gates, forward only: spec and final. Test-review leftovers go to the final gate.
2. Between the gates never stop: assume (logged) or park; parked work is answered at the final gate.
3. The test reviewer checks the tests against the spec and triages review findings.
4. Answers go into the spec (`ANS-n`); re-approval moves the tag.
5. Mutation: mutmut through WSL on Windows; coverage and mutation are scoped to the task.
6. Driven from the chat: the user types requirements, answers and gate decisions only.
7. One branch per task, in the current checkout; dirty tracked files are refused.
8. Cleanup keeps `specs/<f>/` and the metrics row; a pull request only on the user's explicit OK.
9. Notifications: chat, Claude Code's input alert, Windows toast.
10. Epics (one task split into sub-features, run in parallel) come after single features run clean.
11. Manager-style gates: the human gives clear input and checks outcomes, not every line or test.
12. Questions are filtered by rule, not capped: only real decisions are asked.
13. One lean core plus language adapters, grown by modes; a project's stack stays in its setup.
14. One version for CLI and plugin, released as git tag `v<version>`.
15. Everything the human adds or changes happens at spec time; after "spec OK" the lock stays.
16. Deploy and CI files are never changed by a feature; the need becomes a pipeline follow-up.
17. Shared repositories: framegate's files live on a state branch that is never merged.
18. Gates judge only what the task changed; the whole suite runs once, at the final gate (2026-11-17).
19. A feature and a refactor start on a green tree (setup sees to it); a bug fix may start red (2026-11-17).
20. During a task `framegate.toml` changes only where no check gets weaker; the rest is setup (2026-11-17).
21. framegate updates itself only when no task is in progress on the machine (2026-11-17).
22. Replay: the user writes the candidates file; caps 25 USD and 3 hours per candidate; chat and driver on Sonnet (2026-11-17).
23. A replay never affects anything outside its run folder: deny rules and a guard hook, no prompts (2026-11-17).
24. Setup settles mutation with a trial of seconds: it runs, or the user switches it off (2026-11-17).
25. Code is the truth, docs are the plan, comments are no evidence; agents write few comments (2026-11-18).
26. Setup maps the folders with an agent (`framegate survey`), checked mechanically: every code file accounted for (2026-11-18).
27. A listed test that fails on this machine is not run here again; the summary offers a follow-up task, default skip (2026-11-18).
28. Sources by rank: code = what exists, the task = the requirement, docs = context only, comments = no source; program output is behaviour and may be specified and tested (2026-11-18). Replaces 25.
29. No agent of the loop writes a doc. Proposals: docs/ files are final-gate questions (default: later), README / CHANGELOG / handover lines are applied without a question (2026-11-18).
30. One docs step after the approved final gate applies them as its own commit; a failed step is reverted and never blocks the task (2026-11-18).
