---
name: meeting
description: Run a bounded, structured multi-agent meeting (2–4 relevant participants, at most 2 rounds) on a cross-functional question or a stuck task, record the decision with dissent, and turn action items into tasks. Routine decisions take effect automatically under bounded autonomy.
argument-hint: "<question or decision to resolve>"
disable-model-invocation: true
---

# /orkestr:meeting

Meeting topic: $ARGUMENTS

Read `${CLAUDE_PLUGIN_ROOT}/reference/policies.md` first and follow it. `ork <args>` means `node "${CLAUDE_PLUGIN_ROOT}/scripts/orkestr.mjs" <args>`, run from the project root.

**Subagent budget:** one call per participant per round. That is at most 4 participants × 2 rounds = 8, and usually fewer.

This procedure is used in two ways:
- by the founder, through `/orkestr:meeting`;
- **by the orchestrator itself**, from `/orkestr:run` or `/orkestr:build`. Orchestrator-initiated meetings need no founder request.

## 1. Is a meeting justified?

Hold a meeting only if one of these applies:
- the question affects several disciplines;
- agents give conflicting recommendations (for example on architecture);
- QA and a developer disagree about a bug's cause or fix;
- the decision has long-term architectural consequences;
- a significant security, performance, privacy, or scope risk has appeared;
- a task hit its fix-attempt limit (**recovery meeting**, see below);
- the founder explicitly asked for one.

First try direct coordination: send the involved agent one targeted follow-up that includes the other side's findings. If that resolves it, record the outcome as a task note (`ork task note`) and skip the meeting. For a simple, reversible decision, decide yourself and record an assumption.

**Recovery meeting.** When a task is FAILED and its `failure.next_action` says "Recovery meeting required":
- set `"recovery_for": "<task id>"` in the draft;
- the meeting may have no action items; its decision is the new approach for that task;
- give the participants the failing check output, the task's evidence and notes, and what was already tried;
- each task gets one recovery meeting. The tool enforces this.

## 2. Prepare

- Run `ork status`. A project must exist; if not, suggest `/orkestr:plan`.
- Check prior decisions: list `.orkestr/decisions/*.md` and read the ones on this topic. Do not reopen an approved decision without new evidence; if you do, state the new evidence.
- Write a short **objective**, the **context** (requirements, constraints, relevant decisions), and **why a meeting** is needed (the trigger).
- Choose **2–4 participants** whose expertise is needed, from `orkestr:product-manager`, `orkestr:system-architect`, `orkestr:tech-lead`, `orkestr:backend-engineer`, `orkestr:frontend-engineer`, `orkestr:ml-engineer`, `orkestr:data-engineer`, `orkestr:qa-engineer`, `orkestr:security-reviewer`, and `orkestr:researcher`. Do not invite everyone.
- Define the **evaluation criteria**. Always include cost (zero additional budget).

## 3. Round 1 — independent analysis

Call all participants **in one message, in parallel**, so they cannot see each other's answers. Give each one the same brief: objective, context, known options, criteria. Ask each to return:
- its position (preferred option);
- key points;
- concerns;
- evidence for its claims;
- anything it would need to change its mind.

## 4. Round 2 — only if needed

Hold round 2 only if a **material** disagreement remains and new information could resolve it. Send each participant a neutral summary of the other positions, and ask only for new evidence or a changed position. Never hold a third round; the state tool rejects it.

## 5. Decide honestly

- Compare the options against the criteria in a short evaluation.
- **DECIDED:** the evidence clearly supports one option. Record dissent; agreement is not required.
- **ESCALATED:** a material disagreement remains, or the decision is the founder's to make (strategy, cost, risk acceptance). Do not select an option.
- **NO_DECISION:** not enough information. Record the open questions.
- Never invent consensus. Never drop a material objection.
- For DECIDED, write **action items**: owner role, acceptance criteria, `depends_on` (use `"#n"` for other items), a file `scope` for implementation items (same rules as plan tasks), and `requires_approval` for restricted actions. If the decision only changes how existing open tasks are done, set `"applies_to": ["T-xxx", ...]` and leave `action_items` empty: the approved decision is added to those tasks as a note, and no overlapping task is created.
- Record **dissent** for every material difference in reasoning or conditions, even when participants picked the same option (for example "would choose O2 if a payer field is added").

## 6. Record

Write `.orkestr/drafts/meeting.json` (format: `${CLAUDE_PLUGIN_ROOT}/reference/templates/meeting-draft.json`) and run `ork meeting record .orkestr/drafts/meeting.json`. Fix validation errors and retry. This creates M-xxx, D-xxx, and BACKLOG tasks.

## 7. After the meeting

Set `"founder_required": true` in `decision` only for matters in the "consult the founder" list (policies §0): product goal or core UX, major scope, cost, irreversible actions, credentials or sensitive data.

- **Bounded autonomy, DECIDED, not `founder_required`:** the tool records the decision as APPROVED by the orchestrator, authorizes its tasks, and (for a recovery meeting) reopens the failed task. **Do not ask the founder.** Continue the work: if you came from `/orkestr:run` or `/orkestr:build`, return there.
- **`founder_required`, ESCALATED, or supervised mode:** the decision waits for the founder. Present:
  - the options and the recommendation;
  - the rationale and the dissent;
  - the risks;
  - the affected tasks.

  Then ask:
  - **DECIDED:** "Do you approve D-xxx?" If yes, run `ork decision approve D-xxx --by founder --note "<their words>"`.
  - **ESCALATED:** "Which option do you choose?" Then run `ork decision approve D-xxx --by founder --option <id> --note "..."`.
  - **Founder declines:** run `ork decision reject D-xxx --by founder --note "<reason>"`. Unstarted tasks are cancelled.

  Meanwhile, continue with other independent work.

Report the meeting ID, decision ID and status, task IDs, the number of rounds and agent calls used, and what is still open.
