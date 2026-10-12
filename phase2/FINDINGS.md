# Phase 2 findings

## Run 1 — `split` bill-splitter CLI (2026-10-11)

Setup: Claude Code 2.1.296, Sonnet, Windows, bounded autonomy, sandbox without a Git remote.

### Results by criterion

| Criterion | Result |
| :- | :- |
| Completes the intended workflow | Yes. Both tasks COMPLETED, `node --test` (29 tests) passes, completion report produced. **But** the plan step wrote no PRD and imported no tasks; the meeting's action items became the whole plan |
| Artifacts consistent | Yes. `ork validate` passes, all references resolve |
| Meetings useful | Yes. One round, 2 agent calls, clear decision with rationale and rejected options; no needless second round. Differences in reasoning were not recorded as dissent |
| Tasks and dependencies | Dependencies respected. Tasks had no file scopes (meeting action items have no scope in the template) |
| Generated code verifiable | Code: yes (tests and CLI re-run green). Evidence: 5 of 7 recorded commands were not re-runnable (`;` chains, a description instead of a command, the internal scope check) |
| Access and budget | Good. 5 agent calls in total; no push; the push request created an approval request and the hook asked for confirmation |
| Missing or unreliable | See below |

### Problems and fixes

| # | Problem | Severity | Fix |
| :- | :- | :- | :- |
| 1 | A passed `ork scope check` counted as verification evidence, so a task could be completed without any real test | High | State tool: scope checks no longer satisfy the completion rule (test added) |
| 2 | Hook asked for confirmation on `ork approval request` because the quoted `--risks` text contained `;` (false positive) | Medium | Guard ignores separators inside quoted arguments, and the state-tool exemption now requires the command to start with `node …orkestr.mjs` (tests added) |
| 3 | Evidence commands not re-runnable | Medium | build/review skills and data model: one exact re-runnable command per record |
| 4 | Plan step produced no PRD and no tasks | Medium | plan skill: "Done when" checklist (PRD, at least one scoped task, validate) |
| 5 | Meeting action items had no file scope | Medium | meeting skill and template: scope for implementation items; overlapping work becomes a task note |
| 6 | Reviews done by the orchestrator instead of a QA agent | Medium | review skill: independent `qa-engineer` call required, otherwise `reviewers: ["orchestrator"]` and say so; scorecard warns |
| 7 | Implementer replaced the founder's README content | Low | build skill: edit existing files, never replace their whole content |
| 8 | Dissent not recorded for differing reasoning | Low | meeting skill: record material differences even under the same option; scorecard check made informational (word-level comparison gave a false warning) |
| 9 | `node --test plugins/orkestr/tests/` fails on Node 22+ | Low | README documents the glob form |

### Still open

- Fixes 3–8 are instructions, not code; they need a second run to confirm.
- Total agent calls and token usage are still not recorded by Orkestr.
- `ork init` refuses when `.orkestr/` already has files (for example a draft written before init); not seen in this run.

## Run 2 — same project, after the run 1 fixes (2026-10-11)

Scorecard: no ❌. PRD written, all 4 tasks scoped and COMPLETED, every changed file inside a task scope, two tasks built in parallel, 4 independent QA reviews, all 6 recorded commands re-ran green (42 tests). 11 agent calls in total.

| # | Problem | Severity | Fix |
| :- | :- | :- | :- |
| 10 | A DECIDED meeting had to create at least one task, so a decision that only steered existing tasks added a redundant task (and broke the 3-task cap) | Medium | Meeting drafts accept `applies_to` (open task IDs); the approved decision becomes a note on those tasks and no task is created (test added) |
| 11 | Scorecard warns that both participants took the same position | Info | Expected here: the meeting was forced by the test protocol. Kept as a warning because in real runs it signals an unnecessary meeting |

Fixes 3–8 from run 1 are confirmed by this run.

## Run 3 — same project, standalone core (2026-10-12)

Setup: `orkestr-core run --model sonnet --max-agent-calls 20` on a fresh sandbox (`orkestr-core init`, local Git, no remote), Claude Code 2.1.295 headless, bounded autonomy. No interactive session and no slash commands.

Before the run, three minimal calls verified the executor (fixtures in `core/tests/fixtures/claude-2.1.295/`): the subscription sign-in works with `--restricted`, structured output arrives in `structured_output`, `--max-turns` is honored (`error_max_turns`), and a disallowed `echo pwned > pwned.txt` was denied with no file written.

Scorecard (`--rerun`): no ❌. One ⚠️, "no meetings held", which is expected: the core holds meetings only on real triggers, and none occurred.

| Criterion | Result |
| :- | :- |
| Completes the intended workflow | Yes. PRD written, 3 scoped tasks planned and COMPLETED, completion report produced, no founder question |
| Artifacts consistent | Yes. `ork validate` passes on the core's mirror files; references resolve |
| Meetings useful | Not exercised (no trigger) |
| Tasks and dependencies | Dependencies respected; every changed file inside a task scope; the tasks formed a chain, so no parallel wave ran |
| Generated code verifiable | Every test command was run by the core itself and re-ran green |
| Access and budget | No remote, no npm dependencies, no approvals needed. 11 agent calls, 62 turns, about 363k input and 21k output tokens; Claude Code's own estimate is $0.57 (plan usage under a subscription) |

Observations:

| # | Observation | Severity | Action |
| :- | :- | :- | :- |
| 12 | The security review (triggered by `package.json` in the task scope) found a confirmed bug: names such as `__proto__` corrupted results. The fix call resolved it and both reviews passed | Positive | None; the independent second review paid for itself |
| 13 | "Checks that actually ran" in the report also lists `orkestr scope check` lines, which are bookkeeping, not tests | Low | Open; the same in the plugin's report. Separate them in both renderers |
| 14 | The run used 11 calls against an estimate of 10–15 for the plugin protocol; the planned 3-task cap was respected | Info | — |

## Run 4 — standalone core, meeting drill (2026-10-12)

Setup: as run 3 (`orkestr-core init`, local Git, no remote, `--model sonnet`), Claude Code 2.1.296, bounded autonomy, `max_fix_attempts: 1`. The goal was the `split` goal, plus one constraint that left a real design question open on purpose: who gets the leftover cents (first people, the payer, or round up). The constraint said the team must decide it. Purpose: exercise a meeting live (PHASE4_IMPLEMENTATION.md §4, step 0.3).

**First attempt** (1 agent call): the planner turned the open question into a founder-owned task (`T-001 Decide leftover-cent policy`, the other tasks depending on it), and the run stopped at `WAITING_FOUNDER`. No meeting could happen: the core had no way for a plan to say "the team must decide this first".

**Second attempt** on a fresh sandbox, after fixes 15 and 16 (11 agent calls, then 1 for `retro`): scorecard (`--rerun`) has no ❌. The planner listed the question in `decisions_needed`. `M-001` (backend-engineer, tech-lead) agreed on O1 in one round, and `D-001` was auto-approved as a routine decision. The decision reached T-001 and T-002 as task notes before they started. All 3 tasks completed with no fix attempt, and 4 independent reviews passed (one was a security review). Every recorded command re-ran green. Usage: 62 turns, about 387k input and 24k output tokens; Claude Code's estimate is $0.67 (plan usage under a subscription). `orkestr-core retro` then recorded `RT-001` with 5 evidence-backed lessons.

| # | Problem | Severity | Fix |
| :- | :- | :- | :- |
| 15 | A plan could express an open design choice only as a founder task, so no meeting happened and the founder got a routine decision | High | Plan output `decisions_needed`; the engine holds a team meeting before the blocked tasks start; the plan prompt reserves founder tasks for work only the founder can do (tests added) |
| 16 | The run waited for a founder task while the report's "Needs the founder" said "Nothing." | Medium | Report and status list founder-owned READY/IN_PROGRESS tasks (test added) |
| 17 | Implementers believed Bash was denied entirely (their heredoc file writes were denied, correctly) and never ran the allowed test commands, so they wrote tests blind. The core's own runs still verified everything. `RT-001` found the same problem on its own | Medium | Implement prompt: write files with Write/Edit; Bash only for the listed test command forms, one command at a time |
| 18 | Reviews passed with "suspected" findings that nobody triaged (R-001, R-002, R-004); found by `RT-001` | Low | Open. Candidate for a later step: turn suspected findings on a PASS review into task notes |
| 19 | The planner's `context` for the decision already recommended an option, which may anchor the meeting | Info | Watch in later runs |

Still not exercised live: a recovery meeting (no task failed even with `max_fix_attempts: 1`), a decision that needs the founder, and a second discussion round.
