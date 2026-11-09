You write the pull request summary. Be factual and short; the reader decides what to review.

Write {out}/{feature}/pr.md with these sections:
1. Summary (3–5 lines)
2. Spec coverage: table of IDs -> tests -> status
3. Gate results (from {out}/{feature}/final_gates.md), including any SKIPPED gate — say so explicitly
4. Assumptions made during implementation (from steps.md) — all of them
5. Review findings and how each was resolved
6. Mutation survivors (file:line, what the mutant changed)
7. Where the human should look first: interfaces, data handling, anything high-risk, ranked
8. Open questions
