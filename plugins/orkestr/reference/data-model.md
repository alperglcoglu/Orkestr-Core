# Orkestr data model and draft formats

The binding definition is in `PHASE1_IMPLEMENTATION.md` §5. The state tool enforces it. This page tells the orchestrator how to write drafts.

## IDs and states

| Item | IDs | States |
| :- | :- | :- |
| Tasks | `T-001`, `T-002`, … | `BACKLOG` → `READY` → `IN_PROGRESS` → `REVIEW` → `COMPLETED`; plus `BLOCKED`, `FAILED`, `CANCELLED` |
| Meetings | `M-001`, … | — |
| Decisions | `D-001`, … | `PROPOSED`, `NEEDS_FOUNDER`, `APPROVED`, `REJECTED`, `SUPERSEDED` |
| Approvals | `A-001`, … | `PENDING`, `GRANTED`, `REJECTED`, `EXPIRED`, `CANCELLED` |
| Reviews | `R-001`, … | — |
| Retrospectives | `RT-001`, … | — |

The state tool assigns every ID. Never invent IDs in drafts, except references to existing tasks.

Allowed task transitions:

| From | To |
| :- | :- |
| `BACKLOG` | `READY` (automatic), `BLOCKED`, `CANCELLED` |
| `READY` | `IN_PROGRESS`, `BLOCKED`, `BACKLOG`, `CANCELLED` |
| `IN_PROGRESS` | `REVIEW`, `BLOCKED`, `FAILED`, `CANCELLED` |
| `REVIEW` | `COMPLETED`, `IN_PROGRESS`, `FAILED`, `CANCELLED` |
| `BLOCKED` | `READY`, `BACKLOG`, `CANCELLED` |
| `FAILED` | `READY` (`--retry`, founder only), `CANCELLED` |

Rules the tool enforces:

- A task becomes `READY` automatically once it is **authorized** (by plan approval, decision approval, or `task authorize`), all its dependencies are `COMPLETED`, and the project is `ACTIVE`.
- `IN_PROGRESS` also needs a `GRANTED` approval when `requires_approval` is true.
- `COMPLETED` needs passed test/check/manual evidence, no later failing test/check, and a passed latest review.
- When a task becomes `FAILED` or `CANCELLED`, its dependents become `BLOCKED` automatically.

Task owners (roles): `product-manager`, `system-architect`, `tech-lead`, `backend-engineer`, `frontend-engineer`, `ml-engineer`, `data-engineer`, `qa-engineer`, `security-reviewer`, `researcher`, `orchestrator`, `founder`.

## Plan draft → `ork plan import .orkestr/drafts/plan.json`

```json
{
  "summary": "One line about the plan",
  "tasks": [
    {
      "title": "Short imperative title",
      "owner": "backend-engineer",
      "description": "What and why, briefly",
      "acceptance_criteria": ["Testable statement"],
      "depends_on": [],
      "requires_approval": false
    }
  ]
}
```

`depends_on` may contain existing task IDs (`"T-003"`) or `"#n"`, the n-th task in this draft (1-based). Set `requires_approval: true` for tasks that involve a restricted action.

`scope` (recommended for implementation tasks) lists the path patterns the task may change, relative to the project root:
- `"src/api/**"`: everything below `src/api`;
- `"src/api"`: that file or directory;
- `"src/*.js"`: `.js` files directly in `src`.

Rules:
- Tasks with disjoint scopes may run in parallel (limit `max_parallel_tasks`, default 2).
- An empty or missing scope means the task runs alone.
- Overlap is judged conservatively, and matching is case-insensitive.
- Absolute paths, `..`, and `.orkestr/` are rejected.

Parallel commands:

```
ork next --parallel                               # READY tasks that can start together now (JSON list)
ork scope check T-001 --file src/api/a.js ...     # records a passed/failed check on T-001
ork scope check --file src/api/a.js --file x.md   # union check: every file must belong to an active task
```

## Meeting draft → `ork meeting record .orkestr/drafts/meeting.json`

See `templates/meeting-draft.json`.

- `participants`: 2–4 entries of `{role, responsibility}`.
- `rounds`: 1–2 entries, numbered from 1; every contribution `role` must be a participant.
- `outcome` is one of:
  - `DECIDED`: needs `decision.selected_option` (an option `id`), `decision.question`, `decision.rationale`, and at least one action item or `applies_to`;
  - `ESCALATED`: `decision.selected_option` must be null, at least 2 options;
  - `NO_DECISION`: no action items.
- `action_items` use the same fields as plan tasks. `depends_on` may use `"#n"` for other action items.

Autonomy-related meeting fields:

- `decision.founder_required` (optional, boolean): set it to true for founder-level matters (policies §0). Under bounded autonomy, a DECIDED meeting **without** this flag is auto-approved by the orchestrator and its tasks are authorized immediately.
- `applies_to` (optional, a list of open task IDs): the tasks a DECIDED meeting steers. With it, `action_items` may be empty; once the decision is approved it is added to each of those tasks as a note.
- `recovery_for` (optional, a task ID): marks a recovery meeting for a FAILED task that hit its fix-attempt limit. Each task can have one. Action items are optional. An approved decision reopens the task (READY, fix attempts reset) and adds the chosen approach as a task note. ESCALATED or NO_DECISION hands the task to the founder.

## Shared notes and assumptions

```
ork task note T-001 --author qa-engineer --text "test_list fails: expected 3, got 2 (tests/list.test.js:14)"
ork assumption add --text "Single-user app; no auth in MVP" --impact medium [--task T-001]
ork report    # completion report built from the records
```

Notes are the hand-off channel between roles. Assumptions are decisions made without asking the founder; they appear in `status` and `report`.

## Review draft → `ork review record .orkestr/drafts/review.json`

See `templates/review-draft.json`.

- `verdict`: `PASS`, `CHANGES_REQUESTED`, or `BLOCKED`.
- `checks_run`: only commands that actually ran, each with `result` `passed` or `failed`.
- `findings[].status`:
  - `confirmed`: reproduced or directly evidenced;
  - `suspected`: plausible but not verified.
- `findings[].severity`: `critical`, `high`, `medium`, `low`, or `info`.
- `PASS` is rejected if there is a confirmed `critical` or `high` finding.

## Retrospective draft → `ork retro record .orkestr/drafts/retro.json`

See `templates/retro-draft.json`. Every lesson needs `evidence` (task IDs, review IDs, history events). `proposed_changes` are proposals only.

## Evidence

```
ork evidence add T-001 --type test|check|review|manual --result passed|failed|not_run --summary "<what happened>" [--command "<exact command>"] [--ref R-001]
```

`--command` is required for an executed test or check. It must be one exact command that can be re-run as is from the project root (no `;` chains of separate checks, no descriptions). Scope checks are recorded by the tool and do not count as verification for completion. While a task is `IN_PROGRESS`, a failed test or check consumes a fix attempt.

## Prose documents (not managed by the state tool)

- `.orkestr/requirements/prd.md`: product requirements (template: `templates/prd.md`)
- `.orkestr/architecture/architecture.md`: optional architecture notes

Create them when needed. Never overwrite an existing one without asking the founder.
