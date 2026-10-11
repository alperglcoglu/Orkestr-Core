---
name: run
description: Autonomous mode. Take a goal (or continue the current project) and run the whole loop — requirements, plan, tasks, implementation, tests, review, fixes, and meetings when needed — until the work is done or a founder decision is genuinely required, then give a short completion report.
argument-hint: "[goal]  (empty = continue the current project)"
disable-model-invocation: true
---

# /orkestr:run

Goal: $ARGUMENTS

Read `${CLAUDE_PLUGIN_ROOT}/reference/policies.md` first and follow it, especially section 0 (Autonomy). `ork <args>` means `node "${CLAUDE_PLUGIN_ROOT}/scripts/orkestr.mjs" <args>`, run from the project root.

You are running the company. Work until the goal is done or a stop condition below applies. **Do not ask the founder about routine choices.** Make a reasonable assumption, record it with `ork assumption add --text "..." --impact low|medium|high [--task T-xxx]`, and continue.

**Run budget: at most 30 subagent calls per run** (the per-step budgets in the procedures below also apply). If the budget runs out, stop cleanly; the next `/orkestr:run` resumes from the saved state.

## 1. Start or resume

1. Run `ork status`.
   - **No project:** run `ork init --name "<short name>" --goal "<goal>" [--constraint "..."]... [--success "..."]...`. New projects use bounded autonomy.
   - **Project exists:** read the status and continue from it. The goal argument, if given, adds scope; treat it as a re-plan.
2. If the project's autonomy is `supervised`, plans and decisions need the founder. Follow that mode and say so in the report. Never change the autonomy mode yourself; it is a founder-only action.
3. If validation reports errors, or the project is PAUSED or CANCELLED, stop and report.

## 2. Plan (when there is no open work for the goal)

Follow `${CLAUDE_PLUGIN_ROOT}/skills/plan/SKILL.md` steps 1–8:
- product-manager for requirements;
- system-architect for the system design (components, contracts, directory layout) when the system is non-trivial;
- tech-lead for tasks, dependencies, and file scopes that allow parallel work;
- `ork plan import`.

Under bounded autonomy the import authorizes the tasks immediately. Do not wait for plan approval. Record significant assumptions.

## 3. Work loop (in waves)

Repeat:

1. **Pick a wave.**
   1. Resume any task that is already IN_PROGRESS first (`ork task list --status IN_PROGRESS`).
   2. Then run `ork next --parallel`. It returns the READY tasks that can start together now, without exceeding the parallel limit (default 2) or overlapping file scopes. Tasks without a scope always run alone.
   3. If nothing is in progress and the list is empty, read `ork status`. If every task is closed, go to section 4. Otherwise work out why nothing is actionable (waiting for the founder, blocked, failed) and apply the stop conditions.
2. **Build the wave.** For each task in the wave, apply sections 1–3 of `${CLAUDE_PLUGIN_ROOT}/skills/build/SKILL.md` (gate, `ork task move <id> IN_PROGRESS`, brief):
   - **Call all implementer agents of the wave in one message, in parallel.** Tell each agent its task's `scope` and that it must not change files outside it. Shared files (`package.json`, lockfiles, shared configuration) may change only in the task whose scope includes them.
   - Wait until every agent of the wave has returned.
   - **Scope check:** for each task, run `ork scope check <id> --file <changed file>...`. Use the agent's reported changed files, and `git status --porcelain` when Git is available. Without a task ID, `ork scope check --file ...` checks that every changed file belongs to some active task. An out-of-scope change counts as a failed check: undo or move it in the next fix attempt.
   - **Checks:** run each task's tests and checks **one task at a time**, after the whole wave has finished writing. Running tests while another agent is still editing gives false results. Record evidence per task.
   - **Fix loop:** fixes for different failing tasks of the same wave may again run in parallel (their scopes are disjoint). Each task keeps its own fix-attempt limit.
   - When a task's checks pass, move it to REVIEW.
3. **Review.** Apply `${CLAUDE_PLUGIN_ROOT}/skills/review/SKILL.md` to each task in REVIEW. Reviews are read-only, so the reviews of a wave may run in parallel.
   - **PASS:** the task becomes COMPLETED. Dependents become READY automatically.
   - **CHANGES_REQUESTED:**
     1. Record the reviewers' findings on the task: `ork task note <id> --author qa-engineer|security-reviewer --text "<finding and evidence>"`.
     2. The task returns to IN_PROGRESS.
     3. Go back to step 2. The developer receives the notes, and the fix-attempt counter continues.
4. **Recovery.** If a task became FAILED and its `failure.next_action` says "Recovery meeting required":
   1. Hold a recovery meeting with `${CLAUDE_PLUGIN_ROOT}/skills/meeting/SKILL.md`, with `"recovery_for": "<id>"`.
   2. Invite only the relevant roles: the task owner, qa-engineer, tech-lead, and security-reviewer if relevant.
   3. Give them the failing output and the evidence history.
   4. If the meeting decides, the tool reopens the task and the loop continues.
   5. If it cannot decide, the task goes to the founder. Skip it and continue with other independent work.
5. **Other automatic meetings.** Start a meeting yourself when:
   - agents give conflicting architecture recommendations;
   - QA and a developer disagree about a bug's cause or fix;
   - several viable technical options have long-term consequences;
   - a significant security, performance, or scope risk appears.

   First try direct coordination: send the involved agent one targeted follow-up that includes the other agent's findings. Meet only if that does not resolve it. Mark `founder_required: true` only for decisions in the "consult the founder" list (policies §0).
6. **Restricted actions** (`git push`, deploy, publish, spending, external communication, permission changes, destructive operations, sensitive data):
   1. Create an approval request.
   2. Do not perform the action.
   3. Continue with other work that does not depend on it.

Parallel work rules (MASTER_SPEC §5.3):
- The state tool refuses to start a task whose scope overlaps a running task, or beyond the parallel limit. Do not work around a refusal; wait for the next wave.
- Read-only work (independent meeting analysis, QA and security review) may always run in parallel.

## Stop conditions

Leave the loop and report when any of these holds:
- all tasks are closed;
- nothing is actionable without the founder (pending approvals, decisions marked `NEEDS_FOUNDER` or `founder_required`, FAILED tasks whose next action says "Founder decision required");
- the project is paused or cancelled;
- validation errors;
- a usage, capability, or permission limit (do not try to work around a permission);
- the run's subagent budget is exhausted.

The state on disk is always consistent, so `/orkestr:run` can resume later.

## 4. Completion report

Run `ork report`. Give the founder a **short** summary in their language:

1. What was done.
2. Which tests and checks actually ran, and their results. Never claim a check that is not in the evidence.
3. Which decisions rest on assumptions.
4. What could not be solved.
5. What the founder needs to do or decide, if anything.

Also mention how many subagent calls and meetings the run used.
