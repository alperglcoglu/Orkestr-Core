---
name: plan
description: Turn the founder's goal into a project plan — requirements, architecture notes, and tasks with owners, dependencies, and acceptance criteria — saved in .orkestr/. Under bounded autonomy the plan is authorized at once; under supervised autonomy it waits for founder approval. Use /orkestr:run to plan and execute in one go.
argument-hint: "[goal]"
disable-model-invocation: true
---

# /orkestr:plan

Founder input: $ARGUMENTS

Read `${CLAUDE_PLUGIN_ROOT}/reference/policies.md` first and follow it. `ork <args>` means `node "${CLAUDE_PLUGIN_ROOT}/scripts/orkestr.mjs" <args>`, run from the project root.

**Subagent budget for this run: at most 3 calls:** `orkestr:product-manager`, `orkestr:system-architect` (only when the system is non-trivial), and `orkestr:tech-lead`. Use `orkestr:researcher` instead of one of them only when a blocking factual question exists. For a small, clear goal, use fewer agents or plan yourself.

## Steps

1. **Check the state.** Run `ork status`.
   - **No project yet:** collect a name, the goal, constraints, and success criteria from the founder input. Ask a clarifying question only if the goal is too ambiguous to plan. Then run `ork init --name "<name>" --goal "<goal>" [--constraint "<c>"]... [--success "<s>"]...`. The tool refuses to overwrite an existing project.
   - **Project exists:** this is a re-plan. New tasks are added; existing tasks are not changed. Read the current status and the relevant decisions in `.orkestr/decisions/` first.
2. **Inspect the repository** briefly: structure, language, test tools, Git status if available. Note what you actually saw.
3. **Requirements.** For a non-trivial goal, ask `orkestr:product-manager` for scope and acceptance criteria. Give it the goal, constraints, and success criteria only.
   - Write `.orkestr/requirements/prd.md` from `${CLAUDE_PLUGIN_ROOT}/reference/templates/prd.md`.
   - If the file already exists, show the founder what would change and ask before overwriting.
4. **System design.** For a non-trivial system, ask `orkestr:system-architect` (with the PRD and your repository findings) for components, data flow, the data model, interface contracts, and a module/directory layout. Write it to `.orkestr/architecture/architecture.md`, following the same no-overwrite rule.
5. **Tasks.** Ask `orkestr:tech-lead` (with the PRD summary and the design) for the task breakdown: owner, dependencies, acceptance criteria, and a **file scope** per task. Disjoint scopes let independent tasks run in parallel. Shared files belong to one setup task that the others depend on.
6. **Shape the tasks yourself.** Check the breakdown before importing it:
   - Every implementation task should have a `scope`. Leave it empty only for work that truly must touch everything; such a task runs alone.
   - Keep the task count proportional to the goal.
   - Every task needs an owner role, testable acceptance criteria, and correct `depends_on`.
   - Set `requires_approval: true` for anything restricted (deploy, publish, `git push`, spending, external communication, permission changes).
7. **Import.** Write `.orkestr/drafts/plan.json` (format: `${CLAUDE_PLUGIN_ROOT}/reference/data-model.md`) and run `ork plan import .orkestr/drafts/plan.json`. Fix any validation errors and retry. Then run `ork validate`.
8. **Assumptions.** Record every assumption you made instead of asking with `ork assumption add --text "..." --impact low|medium|high`. Ask the founder during planning only for the cases listed in policies §0, for example a requirement contradiction that no reasonable assumption resolves.
9. **Approval depends on the autonomy mode** (shown by `ork status`):
   - **bounded** (default): the import already authorized the tasks; this plan is the internal working plan. Do not ask for approval. Summarize the plan (task table: ID, title, owner, depends on, needs approval) and the assumptions, then say that `/orkestr:run` executes it. If this procedure is being followed from `/orkestr:run`, simply continue there.
   - **supervised:** present the plan (goal, scope, task table, assumptions, risks, open questions) and ask: "Do you approve this plan?"
     - **Approved** (explicitly, in this conversation): run `ork plan approve --by founder --note "<founder's words>"`.
     - **Changes requested:** adjust. Tasks already imported can be cancelled with `ork task move <id> CANCELLED --reason "..."`, and new ones imported.
     - **No answer:** leave the tasks in BACKLOG.

## Done when

Do not end the plan step until all of these hold, even for a small goal:
- `.orkestr/requirements/prd.md` exists (write a short one yourself when you skip the product-manager);
- at least one task is imported, each implementation task with a `scope`;
- `ork validate` passes.

A meeting is not a substitute for the plan: its action items add to the plan, they do not replace it.

## Report

End with:
- what was created, with paths and task IDs;
- which subagents were used;
- what is assumed;
- what the founder needs to decide.
