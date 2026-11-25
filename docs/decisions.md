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
12. Questions are filtered by rule, not capped: only real decisions are asked. Narrowed by 33.
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
25. Code is the truth, docs are the plan, comments are no evidence; agents write few comments (2026-11-18). Replaced by 28 and 42.
26. Setup maps the folders with an agent (`framegate survey`), checked mechanically: every code file accounted for (2026-11-18).
27. A listed test that fails on this machine is not run here again; the summary offers a follow-up task, default skip (2026-11-18).
28. Sources by rank: code = what exists, the task = the requirement, docs = context only, comments = no source; program output is behaviour and may be specified and tested (2026-11-18). Replaces 25.
29. No agent of the loop writes a doc. Proposals: docs/ files are final-gate questions (default: later), README / CHANGELOG / handover lines are applied without a question (2026-11-18).
30. One docs step after the approved final gate applies them as its own commit; a failed step is reverted and never blocks the task (2026-11-18).
31. Templates and role texts are general; a project's facts go to its project knowledge.
32. The approved spec is the source of truth for the agents: an agent hands work back only when the spec is contradictory or impossible.
33. The spec dialogue asks about every new or changed public shape; a reply that only describes the task is not "ok". Narrows 12.
34. The same question keeps one id; a later one on the same thing replaces the earlier.
35. Formatting is counted separately from the task.
36. Frameworks a project declares but has not installed are found at setup and asked about in the spec dialogue.
37. A file over the size limit is a note in the final summary and one deferred entry, never a final-gate question.
38. A default answer changes nothing and never starts rework; a gate with nothing built or a standing repair objection has no default.
39. Doc proposals state exactly what was implemented: they are the owner's overview of how the system moved away from the docs. Extends 29.
40. A stale docstring or comment is code text, never a question.
41. Hard gates (lint, CI rules) are preferred over guideline text.
42. Comment blocks are at most 2 lines and describe the code's state. Extends 28.
43. Only two human gates, spec and final, also for critical logic. Confirms 1.
44. Escaped defects are the most critical metric.
45. Roadmap order: escaped-defect log, risk approval, simplify pass, commands instead of model turns, fast lane, before-PR flow with CI rules.
46. Risk-based approval starts strict and is loosened only with evidence: one setting at a time, backed by `framegate stats --risk`.
47. A merged task must be revertible exactly and alone: one pull request, one task, one merge commit.
48. No new docstring and no spec ID in source: a step gate check in framegate and a frozen count in each project's CI; existing ones may only be reworded or removed (2026-11-25). Extends 42.
49. A test that was green before the task and is red at its end is decided by the user, with no default; a test red before the task is a suggested follow-up (2026-11-25). Extends 38.
50. A template or role changes only for a rule that holds for any feature and any run; a project's gap goes to its project knowledge or a project check, also in retro proposals (2026-11-25). Extends 31.
51. framegate itself: one pull request at a time, in order; the full suite on GitHub Actions is green before the merge and before the tag; a red run is fixed first (2026-11-25).
52. Roadmap order from here: a replay candidate set with many hidden tests and probes, the decision graph per run, fast lane, before-PR flow (2026-11-25). Replaces the order in 45.
53. Every new error rule in a spec names its source (task, code, docs, asked); one without a source is a question to the user (2026-11-25).
54. A spec-phase stub is a signature that raises not-implemented: no docstring, no spec ID; the link from ID to symbol lives in the design and the test tags (2026-11-25). Extends 48.
55. framegate's own code is under the same ratchet as the projects (`ci/check_rules.py`); a release goes through `tools/release.py` (2026-11-25). Extends 51.
