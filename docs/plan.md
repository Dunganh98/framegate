# framegate: the plan

What framegate is for, the principles it follows, how a change flows through it, what is built
and what comes next.

This file is the plan. The code is the truth: where the two differ, the code is right and this
file needs a change. How each part works in detail is in [reference.md](reference.md).

## 1. Purpose and direction

**The problem.** A coding agent writes a change in minutes, but the result cannot be trusted
without reading every line. Rules written into a prompt are followed most of the time, not every
time. An agent that writes both the code and its tests can make them agree with each other
instead of with the requirement. Long conversations lose what was agreed at the start. So the
human either reads everything, and is the bottleneck, or reads nothing, and approves blind.

**The bet.** A human can hand over a whole change when four things hold:

- The requirement is fixed in a spec the human approved, before any code.
- The tests are written from that spec by a different agent than the one that writes the code,
  checked against the spec, and then frozen.
- Every rule that can be checked mechanically is a check the agent cannot switch off.
- The human decides twice, each time from one screen of plain words: the spec and the result.

Everything between those two decisions runs without a stop. Each agent works inside a frame:
the checks it must pass and the files it may not touch.

**The one metric.** Cost per accepted change, with escaped defects at or near zero. An escaped
defect is a bug found after the change was accepted. Cost is tracked, not optimized: verification
is never traded for speed or tokens.

**The direction.** Fewer human stops on low-risk work, smaller diffs, a shorter path for small
changes, and proof that comes closer to the real system. Each step is taken when measured runs
show the need for it (section 6), not before.

## 2. Principles

### Proof

1. **The approved spec is the source of truth for the agents.** Every requirement, edge case and
   invariant has an ID. A requirement without an ID does not exist. The human's answers go into
   the spec.
2. **Sources have a rank.** The code says what exists. The task and the approved spec say what
   is required. Docs are context only. Comments are no source. Program output (an error
   message, a log line) is behaviour and may be specified and tested.
3. **The verifier is independent of the implementer.** Tests are written from the spec and the
   stubs, checked against the spec by a test reviewer, then frozen. The implementer cannot
   change them. Checkers run on a stronger model than writers: a weak checker defeats the frame.
4. **Rules are checks, not prompts.** A rule that can be checked mechanically lives in a check,
   a hook or CI. Guideline text is only for what cannot be checked. The order of preference for
   any fix: a check, then a test or template, then a prompt.
5. **Hard gates stay authoritative.** Hooks give early feedback and are not airtight. The
   guarantee is the diff check against the tag set when the tests were approved, and CI. A new
   instruction never replaces a check.
6. **Gates judge only what the task changed.** The whole suite runs once, at the final gate. A
   feature and a refactor start on a green tree; a bug fix may start red. Debt that was there
   before the task (red tests, lint findings, formatting) is found at setup and never blamed on
   the task.
7. **A check that measured nothing is not a pass.** It is reported as not measured, and nothing
   that depends on it counts as proven.

### The human

8. **Two human gates, forward only: spec and final.** Everything the human adds or changes
   happens at spec time. After "spec OK" the spec is locked.
9. **Between the gates the run never stops.** An agent assumes from the spec and logs the
   assumption, or parks the work. Assumptions and parked work come to the final gate.
10. **The human is the manager.** They give clear input and check outcomes. Wherever they read
    or decide they get a brief: the decision and its default first, about one screen, plain
    words, detail on request. They own the structure (what each class is for and how the classes
    work together), not every line or test.
11. **Only real decisions are asked.** A question is asked when two or more reasonable options
    lead to different outcomes and nothing already settles it. Questions are filtered by this
    rule, not capped. An answered question is not asked again in the same task.
12. **A default answer changes nothing.** The default of every question is the option that
    leaves the code, the tests and the spec as they are. Taking defaults never starts rework.
13. **Docs are the owner's plan.** No agent of the loop writes a doc. It proposes; the owner
    decides at the final gate; one docs step after the approved gate applies what was approved,
    as its own commit, and never blocks the task.
14. **Nothing leaves the machine without an explicit OK.** A pull request is created only when
    the human says so. Deploy and CI files are never changed by a feature, bug fix or refactor;
    the need becomes a pipeline follow-up.

### Agents and state

15. **State lives in files, not conversations.** Each phase and each plan step is a fresh
    headless agent run. Nothing depends on a long context.
16. **The orchestrator is deterministic.** No model decides what runs next, and no model
    computes a number the human decides on: cost, coverage and the risk rating come from the
    run's records.
17. **Every human correction becomes data.** Rejections, answers, parked work and failed gates
    are logged. A retro turns recurring problems into proposals. A problem seen once goes to a
    backlog and is counted if it comes back, so one odd task does not become a permanent rule.

### Scope

18. **One lean core, language adapters, modes.** The test for where a thing goes: does it change
    how proof is produced, or only what one project depends on? A project's stack (databases,
    brokers, cloud services) is never installed or wrapped by framegate.
19. **Templates and role prompts are general.** Project facts live in the project's knowledge
    file. It grows with the runs, and the human picks what is learned.
20. **One orchestrator.** Other tools are sources of ideas, not dependencies.
21. **Every addition is wired.** For each one it is stated which phase it changes, what it reads
    and writes, which gate checks it and what it logs. An addition that is only a prompt is
    incomplete.
22. **A running task is not disturbed.** During a task the project's config changes only where
    no check gets weaker. framegate updates itself only when no task is in progress. The command
    line tool and the plugin share one version.
23. **Measure before changing.** A few runs show that something is broken, not that a threshold
    or a model choice is better. A roadmap item is adopted when its trigger is met.

## 3. How a change flows

```
spec (human) -> tests -> code -> review -> summary -> final gate (human) -> docs -> retro -> done
```

| Phase | Who | Ends with |
|---|---|---|
| Spec | The human and the chat, in a dialogue | The spec brief; the human says "spec OK"; the spec and the stubs are frozen |
| Tests | Test writer, mechanical checks, then the test reviewer | Tests that show the mode's first proof and match the spec; frozen. Open points go to the final gate |
| Implement | Implementer, one fresh run per plan step | The step gate is green and the step is committed, or the step is parked |
| Review | Reviewer, then the test reviewer sorts the findings, then fixes | Valid findings fixed or parked, invalid ones removed with a reason, structure fixed with every test green; then the final checks |
| Summary | Summary agent | The final brief, the detailed summary, the code map update |
| Final gate | The human | "ok", or answers by number; only the affected work is redone |
| Docs | Docs writer, once | The approved doc proposals applied as one commit |
| Retro | Retro agent | Numbered proposals and project facts; the human picks by number |

**The two gates.** At the spec gate the human checks that the task is understood, that the plan
is sensible, which decisions were made for them, and what will not be proven. At the final gate
they read how each step went, the numbers, what is proven and what to try by hand, and answer
the questions that are real decisions. A project can add a human stop after the tests; the
default is none.

**When work gets stuck.** A step that fails the same way twice goes to a diagnosis agent. A code
cause is retried at once. A test or spec cause waits for the human at the final gate while the
rest of the run goes on. Parked work is redone only when an answer makes a different outcome
possible.

**Modes.** A mode is the first proof of a kind of work. The rest of the flow is the same.

| Mode | First proof | Differs how |
|---|---|---|
| Feature | The new tests fail against the stubs | The default |
| Bug fix | Reproduction tests fail on today's code, for the reported reason | May start on a red tree |
| Refactor | Tests that pin today's behaviour pass now and after every step | A report shows which lines in scope no test runs, before any code moves |
| Pipeline | None: a deploy or CI change has no unit test | No spec, no tests, no agent run. The chat makes the changes from the follow-up list, runs one check, shows one brief; the human approves |

**Who does what.** The human writes requirements, answers questions and makes the gate
decisions, all in the chat. The chat drives the command line tool. The tool runs the agents and
the checks. Writers (tests, code, docs) run on the cheaper model; checkers (test reviewer,
reviewer, diagnosis, retro) on the stronger one.

## 4. Where things live

| Layer | Where | Holds |
|---|---|---|
| Core | this repository | Phases, gates, checks, hooks, briefs, retro, the chat skills |
| Adapters | this repository | How a language proves things: select tests by spec ID, coverage, mutation, stubs. Python, .NET, and a generic one driven by commands in the config |
| Modes | this repository | The first proof of a kind of work |
| Hosting templates | this repository | The CI job that runs the gates, and how a pull request is opened, per host |
| Project setup | the target repository | The config file (commands, protected paths, limits, extra checks), the project knowledge file, the code map |
| Presets | later | Settings shared by projects with the same stack |

Rules:

- `framegate install-tools` installs only what the workflow needs. Everything else is the
  project's dependency.
- A new built-in adapter is written only after the generic one fell short on a real project.
- The flow fits where "done" is an automated test that fails first and passes after. Where the
  proof is something else, the answer is a new mode with its own first proof, not a second tool.
- What only a person can judge (how a UI looks, how a game feels) stays a final-gate item under
  "Not proven".
- In a repository a team shares, framegate's own files live on a state branch that is never
  merged; the pull request carries the product only.

## 5. Status

Checked against the code on 2026-11-24.

| Capability | Status | Note |
|---|---|---|
| Command line tool and Claude Code plugin, driven from the chat | built | One version; a start check keeps both in line |
| Setup: project facts probed and confirmed in one table, folder map, setup check | built | Finds a red tree once, before the first task |
| The flow: spec, tests, implement, review, summary, final gate, docs, retro | built | |
| Frame: frozen spec and tests, write locks per phase, tag-based diff check | built | |
| Checks: first proof, traceability, test smells, lint, format, types, diff coverage, mutation, assertions that never ran, removed suppressions | built | Scoped to the task's own lines |
| Diagnosis, parking, provisional steps, redo of affected work only | built | |
| Usage-limit wait; failure output cut down to what matters | built | |
| Briefs at every human point; question filter; no-change defaults | built | |
| Structure: findings and fixes, structure report, file-size questions, code map | built | |
| Modes: feature, bug fix, refactor, pipeline | built | Refactor includes the scope coverage report |
| Mode: experiment (a metric with noise, a threshold with a tolerance) | planned | |
| Adapters: Python, .NET, generic | built | |
| Project knowledge, grown from retros by the human's pick | built | |
| Reference projects and ports | built | Port stubs for Python only |
| Source ranking, doc proposals, docs step | built | |
| Deferred-work list (every "later" is kept for later tasks) | built | |
| Pipeline follow-up for deploy and CI files | built | |
| State branch, publish, generated pull request description | built | Pull requests on GitHub and Azure DevOps |
| CI job that runs the final gates | partly | GitHub Actions template; Azure Pipelines missing |
| Run records and `framegate stats` (cost, time, tokens per run, check results, human decisions) | built | One event log and one metrics row per task |
| Tool-call log, test failures per spec ID, escaped-defect log | planned | The metric's quality half is not recorded yet |
| Assumptions logged with spec IDs and listed at the final gate | built | |
| Structured decision log with a check that every run logged | planned | Free-text step notes today |
| Retro with a backlog that counts repeats | built | |
| Retro panel (two reviewers and a synthesizer) | planned | One retro run today |
| Risk rating on every final brief, logged next to the human's decision | built | Computed from the run's records |
| Final gate passed without a person on low risk | partly | Built behind a switch, off by default; the never-automatic path list is empty by default |
| Simplify pass, fast lane | planned | |
| Before-PR flow | partly | Publish and description built; rebase, re-check, CI rules and risk tier in the text are not |
| Project's CI rules at the step gate | planned | |
| Setup and chat relays as commands instead of model turns | planned | |
| Security pass by trigger | planned | |
| Check on the real system | partly | Project checks run after the final checks, never blocking; an optional smoke task at setup. No harness with recorded data |
| Early stop on friction signals | partly | Diagnosis after repeated failure is built; scoring signals before that is not |
| Replay: real commits as measured tasks | built | Python projects only |
| Epics: one task split into sub-tasks run in parallel | planned | |
| Presets | planned | |
| Review panel with several lenses; parallel bug hunters with a vote | dropped | For now: the measured failures were not missed bugs |
| Third human gate after the tests, by default | dropped | Available as a setting |
| Non-Claude workers; a performance mode; a metrics dashboard export | dropped | Until a real need shows |

## 6. Roadmap

In order. Each item is adopted when its trigger is met, on its own branch, and checked on a real
task or a replay round before the next one starts.

**1. The quality half of the metric.** A command to log an escaped defect against the task that
caused it, then the tool-call log and test failures per spec ID. It is cheap, and it comes first: without it "escaped defects near zero", the half of the metric
that matters most, is not measured.

**2. Risk-based approval, switched on.** A task with low risk, all checks green and no question
passes the final gate without a person. Listed paths (schema, deploy, CI, docs) always need one.
*Evidence:* most final gates in the replay rounds had 0 or 1 question. *Left to do:* compare the
logged rating with the human's decisions, give the path list its defaults, then turn it on.

**3. Simplify pass.** One agent shrinks the task's diff with the tests unchanged, before the
review. *Evidence:* the task's diff is often larger than the original author's (21 source lines
against 6, 18 against 5).

**4. Mechanical work as commands, not model turns.** Setup becomes two commands: one installs,
surveys and prints the confirmation table, one writes the files and runs the setup check. The
chat prints the engine's briefs as they are and does not rewrite them. *Evidence:* over six replay
runs the chat read as many tokens as all engine agents together, and setup alone took about 25
model turns per project.

**5. Fast lane.** A small bug fix with a reproduction skips the plan steps and mutation.
*Evidence:* a 6-line bug fix took 27 minutes.

**6. Before-PR flow and the project's CI rules.** Before the pull request: rebase on the latest
base, run the checks and the project's CI rules again, and write the pull request text from the
final summary with the risk rating. The project's CI rules are the hard gate before the pull
request. The same commands run at the step gate on the changed files, so the implementer learns
the rules from their output and from a summary generated from the CI config, not from guideline
text.

**7. A check on the real system.** Unit tests are the weakest proof where correctness shows only
on real data or a running system. Per task: cases that run the real system on recorded data,
each naming the spec IDs it proves, frozen with the tests, run at the final gate, with the
evidence linked in the brief. *Status of the decision:* probably. *Trigger:* a task whose
correctness cannot be judged from unit tests, and recorded data the run can reach.

**8. Security pass, by trigger only.** A separate look when the change puts values into SQL or
shell text, or touches a listed path. Not on every task.

**9. Early stop on friction.** Stop retrying before the attempt limit when signals say the spec
or the interface is wrong: the same tests failing across attempts, blocked writes to tests or
spec, questions on the same IDs. *Trigger:* steps that regularly use every attempt. Needs
item 1's logs.

**10. Structured decision log.** Assumptions, deviations and workarounds as rows, with a check
that every implementer run logged. The retro reads it: assumptions that recur on one topic mean the
spec template lacks a section. *Trigger:* together with item 1.

**11. Epics.** One big task is split into sub-tasks. The human approves the split, then all
sub-specs in one reply. Shared interfaces are frozen before any sub-task starts; each sub-task
has its own worktree and the files it may write; merges happen one at a time, with integration
checks after each; one final gate and one retro. *Trigger:* single tasks run clean end to end.

**When a project needs it:** the experiment mode, the Azure Pipelines template, presets (after
two set-up projects show what repeats), the retro panel (when the single retro's proposals keep
missing the cause).

**Not planned:** a review panel or voting bug hunters (reopen when escaped defects show the
reviewer misses a class of bugs); feature flags, gradual release and roll back (the project's
deployment; inside a task, one commit per step already allows a revert); non-Claude workers
(reopen only if usage limits become the main bottleneck).

## 7. How we measure and improve

**Replay rounds.** A replay takes a real commit, turns it into a task, and lets framegate
deliver it in a sandbox cut before that commit. The commit's own tests are hidden and score the
result. Each candidate has a cost and a time cap, and nothing leaves its run folder. A round
gives verdicts, cost, time, final-gate rounds and the retro proposals of every run, so two
framegate versions can be compared on the same tasks. A replay does not measure the spec
dialogue or the human's time at the gates; those are measured on real tasks.

**The retro loop.** After every task the retro proposes fixes, each marked as the project's or
framegate's, in the order of principle 4. The human picks by number. Picks go to the backlog
with a status, and a repeat is counted against its row. Project fixes are applied in the
project; framegate fixes in this repository, outside any task. Guideline text should shrink as
checks grow.

**Reading the numbers.** Per task: cost, agent and check time, failed step gates, final-gate
rounds, questions, assumptions, parked work, risk rating.

- Rejections and parked work falling: the frame is learning, and a gate can be loosened.
- Flat or rising: the retro proposals miss the real cause. Read the reasons yourself.
- Many questions per task: the spec template lacks a section.

**The trust curve.** How much the human reads goes down in stages, each entered on evidence.

| Stage | Enter when | What changes |
|---|---|---|
| 1 | Setup is done | Small, low-risk tasks. Read the brief and all the code. Log every correction |
| 2 | About three tasks with few corrections | Read the brief, the assumptions, the mutation survivors and the high-risk code only |
| 3 | The logged risk rating agrees with your own decisions | Low-risk tasks pass the final gate without you (roadmap 2) |
| 4 | Single tasks run clean | Epics (roadmap 11) |

The spec approval is never automated. It is a one-way door: a wrong spec is built exactly as
written.

## 8. Limits

- **Conformance, not correctness.** The frame proves the code matches the spec. A wrong spec is
  implemented perfectly. Checking the spec brief stays the human's most important job.
- **Structure is judged only in part.** The structure report and the reviewer catch size and
  obvious mixes of concepts. The code map makes the rest visible; the human still judges it.
- **Unit tests miss what only the real system shows:** data leakage, normalization and windowing
  in machine-learning code; look and feel. Until roadmap 7 these are "Not proven" items to try
  by hand.
- **Hooks are not airtight.** A write hidden inside a shell command can get past them. The diff
  check is the guarantee.
- **Less measured, less proven.** A project without a measured diff coverage or mutation score
  is never rated low risk.
- **Replay** covers Python projects only, and no setting is tuned from a handful of runs.
- **The flow needs a test that can fail first.** Work without one needs its own mode or stays
  with a person.

## 9. Where the detail is

| File | Holds |
|---|---|
| [reference.md](reference.md) | How everything works: commands, phases, gates, briefs, modes, config, retro, hooks |
| [decisions.md](decisions.md) | The owner's standing decisions, one line each |
| [replay.md](replay.md) | `framegate replay`: the candidates file, what happens per candidate, the results |
| [replay-candidates.md](replay-candidates.md) | Why replay exists and the rules for building a candidate |
| [adapters.md](adapters.md) | The language adapters and their measured timings |
| [plugin.md](plugin.md) | The Claude Code plugin: layout, install, hooks, how the skills drive the tool |

The earlier text of this file (the original setup guide with its scripts, and the plans appended
to it) is in the git history.
