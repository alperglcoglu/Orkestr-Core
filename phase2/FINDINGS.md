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
