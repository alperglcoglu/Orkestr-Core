---
name: status
description: Show the project status from saved .orkestr/ artifacts (blockers, failed tasks, pending approvals, waiting decisions, interrupted work, validation problems), and pause, resume, or cancel the project.
argument-hint: "[pause <reason> | resume | cancel <reason>]"
disable-model-invocation: true
---

# /orkestr:status

Arguments: $ARGUMENTS

Read `${CLAUDE_PLUGIN_ROOT}/reference/policies.md` first and follow it. `ork <args>` means `node "${CLAUDE_PLUGIN_ROOT}/scripts/orkestr.mjs" <args>`, run from the project root. **This skill uses no subagents.**

## No arguments: report

1. Run `ork status` and read its full output.
2. Present it to the founder in the founder's language, faithfully and briefly:
   - project state;
   - task counts;
   - **needs founder attention** (pending approvals, decisions waiting, tasks awaiting authorization);
   - blocked tasks with their reasons;
   - failed tasks with error and next action;
   - in-progress tasks (an IN_PROGRESS task that no session is working on was interrupted, and `/orkestr:build <id>` resumes it);
   - tasks in review;
   - tasks ready to start;
   - validation problems.
3. Mention the autonomy mode and the assumptions recorded so far. For a final summary, use `ork report`.
4. Do not add information that is not in the artifacts. Do not estimate percentages or completion dates.
5. End with the single most useful next command. Usually this is `/orkestr:run` to continue autonomously; otherwise a specific founder decision or approval.

## `pause <reason>`

Run `ork project pause --reason "<reason>"`. Explain that no task can start until the project is resumed, and that in-progress tasks are kept as they are.

## `resume`

Run `ork project resume`, then show the status.

## `cancel <reason>`

Cancelling is irreversible:
- every open task becomes CANCELLED;
- every pending approval becomes CANCELLED.

First explain this and ask the founder to confirm explicitly in chat. Only after a clear yes, run `ork project cancel --reason "<reason>" --by founder`. Without a clear yes, do nothing.

If `ork` reports errors (for example corrupted files), show them as they are and stop. Do not attempt repairs without the founder.
