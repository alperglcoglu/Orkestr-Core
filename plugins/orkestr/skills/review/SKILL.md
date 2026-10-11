---
name: review
description: Review a task's changes with QA (and Security when relevant), separating confirmed findings from suspected ones and assumptions, record the review, and complete the task only when the evidence allows it.
argument-hint: "[T-id | scope]"
disable-model-invocation: true
---

# /orkestr:review

Review target: $ARGUMENTS (a task ID such as T-003, or a scope description; if empty, review the first task in REVIEW)

Read `${CLAUDE_PLUGIN_ROOT}/reference/policies.md` first and follow it. `ork <args>` means `node "${CLAUDE_PLUGIN_ROOT}/scripts/orkestr.mjs" <args>`, run from the project root.

**Subagent budget: at most 2 calls**, `orkestr:qa-engineer` and, when relevant, `orkestr:security-reviewer`. They may run in parallel.

## Steps

1. **Select the target.** Run `ork status`.
   - For a task review, run `ork task show <id>`. A task review normally targets a task in REVIEW.
   - For a scope review (no task), the result is recorded without changing any task.
2. **Collect the change.** Use `git diff` / `git status` when Git is available. Otherwise use the files listed in the task evidence and the build report. Note what you could not determine.
3. **Choose reviewers.**
   - `qa-engineer`: always, for a task review. The review must come from a **separate `orkestr:qa-engineer` call**, not from the orchestrator and not from the agent that wrote the code. If you cannot call it (budget or usage limit), record the review with `"reviewers": ["orchestrator"]` and say in the report that the review was not independent.
   - `security-reviewer`: when the change touches authentication or authorization, user input, file, shell, or network operations, secrets or configuration, or dependencies — or when the founder asks.
   - Give each reviewer the task, its acceptance criteria, the changed files or diff, and the instruction to return its standard report.
4. **Verify the reviewers' claims yourself:**
   - `confirmed` only when reproduced or directly evidenced (file and line, or command output);
   - otherwise `suspected`;
   - never upgrade a suspected finding without evidence.

   `checks_run` lists only commands that actually ran, with their real results, one exact re-runnable command per entry (no `;` chains, no descriptions). Re-run a key check yourself when an agent's claim matters for the verdict.
5. **Choose the verdict:**
   - `PASS`: acceptance criteria met, and no confirmed critical or high finding;
   - `CHANGES_REQUESTED`: fixable problems;
   - `BLOCKED`: cannot be judged, or needs a founder decision.
6. **Record.** Write `.orkestr/drafts/review.json` (format: `${CLAUDE_PLUGIN_ROOT}/reference/templates/review-draft.json`) and run `ork review record .orkestr/drafts/review.json`. For a task review this adds check and review evidence to the task.
7. **Update the task:**
   - **PASS:** run `ork task move <id> COMPLETED`. The tool refuses unless there is passing verification evidence, no later failing check, and a passing review. If it refuses, report why; do not work around it.
   - **CHANGES_REQUESTED:**
     1. Record each confirmed finding for the developer with `ork task note <id> --author qa-engineer|security-reviewer --text "<finding, evidence, expected fix>"`.
     2. Run `ork task move <id> IN_PROGRESS`.
     3. Under bounded autonomy, continue directly with `${CLAUDE_PLUGIN_ROOT}/skills/build/SKILL.md` (resume). The developer gets the notes, and the fix-attempt counter continues. Under supervised autonomy, recommend `/orkestr:build <id>`.
   - **QA and developer disagree** about the cause or the fix: first send the developer one follow-up that includes QA's evidence. If that does not settle it, hold a meeting (`${CLAUDE_PLUGIN_ROOT}/skills/meeting/SKILL.md`).
   - **BLOCKED:** run `ork task move <id> BLOCKED --reason "..."`.

## Report

Use three clearly separated lists:
- **Verified findings**;
- **Suspected findings (not verified)**;
- **Assumptions**.

Also include:
- the checks run, with their results;
- the verdict and the review ID;
- the task's new state;
- what the founder should decide, if anything.
