---
name: retro
description: Run an evidence-based retrospective over the saved project records (tasks, evidence, reviews, meetings, history), record lessons with evidence, and propose — never apply — process changes.
argument-hint: "[scope, e.g. milestone 1]"
disable-model-invocation: true
---

# /orkestr:retro

Scope: $ARGUMENTS (if empty: everything since the last retrospective, or the whole project)

Read `${CLAUDE_PLUGIN_ROOT}/reference/policies.md` first and follow it. `ork <args>` means `node "${CLAUDE_PLUGIN_ROOT}/scripts/orkestr.mjs" <args>`, run from the project root. **This skill uses no subagents** unless the founder asks for one.

## Steps

1. **Gather evidence.** Run `ork status` and `ork validate`. Read:
   - `.orkestr/tasks.json`;
   - the relevant `.orkestr/reviews/*.md`, `.orkestr/meetings/*.md`, and `.orkestr/decisions/*.md`;
   - the relevant part of `.orkestr/history.jsonl` (state changes, failed checks, approvals);
   - any earlier `.orkestr/retros/*.md` and `.orkestr/memory/lessons-learned.md`.
2. **Evaluate**, citing IDs for every claim:
   - Which tasks succeeded and which failed or were cancelled, and why?
   - Was the decomposition appropriate (too many or too few tasks, wrong dependencies)?
   - Did meetings change or improve decisions, or were they unnecessary?
   - Which assumptions turned out wrong?
   - Which tests or reviews found important defects?
   - Did the architecture cause unnecessary complexity?
   - Were limits (fix attempts, meeting rounds) reached, and what did that reveal?
3. **Lessons:** only lessons backed by evidence (task IDs, review IDs, history events). Mark anything without evidence as an observation and leave it out of the lessons.
4. **Proposed changes** to agent instructions, workflows, or limits are **proposals only**. Never edit plugin files, policies, or permissions as part of a retrospective. Changes need founder review and testing first.
5. **Record.** Write `.orkestr/drafts/retro.json` (format: `${CLAUDE_PLUGIN_ROOT}/reference/templates/retro-draft.json`) and run `ork retro record .orkestr/drafts/retro.json`. This saves RT-xxx and appends the lessons to `.orkestr/memory/lessons-learned.md`.

## Report

Present:
- the retrospective ID;
- the main findings, with evidence;
- the lessons recorded;
- the proposed changes, for the founder to accept or reject.
