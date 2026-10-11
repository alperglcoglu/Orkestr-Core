---
name: build
description: Implement one authorized task with a bounded build/fix loop (at most 3 fix attempts), real checks recorded as evidence, approval gates for restricted actions, and resume support for interrupted work.
argument-hint: "[T-id]"
disable-model-invocation: true
---

# /orkestr:build

Task: $ARGUMENTS (if empty, pick the next task)

Read `${CLAUDE_PLUGIN_ROOT}/reference/policies.md` first and follow it. `ork <args>` means `node "${CLAUDE_PLUGIN_ROOT}/scripts/orkestr.mjs" <args>`, run from the project root.

**Subagent budget:** 1 implementation call plus at most 1 call per fix attempt (at most 3). That is at most 4 per fix budget, plus one recovery meeting if needed.

## 1. Select the task

1. Run `ork status`. Stop and report if:
   - there are validation errors;
   - the project is not ACTIVE (if PAUSED, tell the founder to resume via `/orkestr:status resume`).
2. If a task ID was given, run `ork task show <id>`. Otherwise run `ork next`. An interrupted IN_PROGRESS task comes first, then the lowest READY task. If the result is `null`, report that nothing is ready and why (unauthorized, blocked, waiting for dependencies), and stop.
3. Only READY or IN_PROGRESS tasks can be built. For any other state, explain it and stop.

## 2. Gate

- **READY task with `requires_approval: true` and no GRANTED approval:**
  1. Run `ork approval request --action "<exact restricted action>" --category <category> --reason "..." --consequences "..." --risks "..." --task <id>`.
  2. Ask the founder and stop here.
  3. If the founder says yes, run `ork approval grant <A-id> --by founder --note "..."` and continue.
- **READY task:** run `ork task move <id> IN_PROGRESS`.
- **IN_PROGRESS task (resume):** do not restart from scratch.
  1. Read its `evidence` and `fix_attempts` from `ork task show <id>`.
  2. Inspect the working tree (`git status` and `git diff` when Git is available; otherwise the files named in earlier evidence).
  3. Continue from what is already there. Never discard partial work.

## 3. Implement (smallest correct change)

- Respect repository discipline: inspect first, follow existing conventions, preserve the founder's changes, and touch only what the task needs.
- If the owner is `backend-engineer`, `frontend-engineer`, `ml-engineer`, `data-engineer`, or `qa-engineer`, delegate **once** to `orkestr:<owner>`. Give it:
  - the task JSON (title, description, acceptance criteria);
  - the task's **notes** (QA or security findings, recovery decisions, hand-offs from earlier tasks);
  - the relevant decision summaries and file paths;
  - the instruction to report changed files, the checks it ran, and a hand-off.
- Tell the implementer the task's **file scope** (`scope`) and that it must change nothing outside it. An empty scope means the task runs alone and may touch any file.
- Afterwards, record the agent's hand-off with `ork task note <id> --author <owner> --text "<changed files; what QA should check>"`.
- Run `ork scope check <id> --file <changed file>...` with the files that actually changed (`git status --porcelain` when Git is available, otherwise the agent's list). A violation is recorded as a failed check and must be fixed in the next attempt.
- If the owner is `orchestrator`, or the change is tiny, implement it yourself.
- Never perform a restricted action (deploy, publish, `git push`, spending, deletions, permission changes, external communication) without a GRANTED approval for exactly that action.

## 4. Verify with real checks

- Run the relevant tests and checks **yourself**, even if an agent says it ran them. Record each one with its real result:

  `ork evidence add <id> --type test|check --result passed|failed --command "<exact command>" --summary "<key output>"`

- If the project has no automated tests, say so:
  - Record `--type test --result not_run --summary "<why>"`.
  - Add a focused test if that fits the task.
  - Use `--type manual --result passed` only for a manual check you actually performed, and describe it precisely.

## 5. Bounded fix loop

- **Do not ask the founder for permission to fix a failing test.** After a failing check, the tool reports how many fix attempts remain. For each attempt:
  1. Analyse the failure output.
  2. Send it to the responsible agent (or fix it yourself) for one focused fix.
  3. Re-run the checks.
  4. Record the result.
- If the tool reports the task is now **FAILED** (limit reached), make no further fixes. Read `ork task show <id>`:
  - **`failure.next_action` says "Recovery meeting required"** (bounded autonomy): hold a recovery meeting with `${CLAUDE_PLUGIN_ROOT}/skills/meeting/SKILL.md` and `"recovery_for": "<id>"`. If the meeting decides, the tool reopens the task with a fresh fix budget; continue at step 2 with the decision's approach (it is in the task notes). If it cannot decide, the task is handed to the founder.
  - **It says "Founder decision required":** stop work on this task. Report the error and the options (founder retry with `--retry`, a different approach, or cancellation).
- Never move a FAILED task back to READY yourself with `--retry`; that is a founder action.

## 6. Hand over to review

When the checks pass, run `ork task move <id> REVIEW`.
- **Bounded autonomy:** continue directly with the review procedure (`${CLAUDE_PLUGIN_ROOT}/skills/review/SKILL.md`).
- **Supervised:** recommend `/orkestr:review <id>`. If the work cannot continue, use `ork task move <id> BLOCKED --reason "..."`. For an unrecoverable error, use `ork task move <id> FAILED --error "..." --next-action "..."`.

## Report

- task ID and final state;
- files changed;
- checks run, each with its exact command and result;
- fix attempts used;
- what was not tested and why;
- approvals requested or used;
- next step.
