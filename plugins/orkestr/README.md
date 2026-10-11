# Orkestr — Claude Code plugin (Phase 1 MVP)

Orkestr helps you run a software project like a small "AI company". It plans, holds bounded and structured meetings, and keeps decision records. It builds tasks behind approval gates, reviews work based on evidence, and produces status reports and retrospectives.

All project state is stored as local files in a `.orkestr/` folder at the root of the project you manage.

## Requirements

| Requirement | Notes |
| :- | :- |
| Claude Code with plugin support | **Tested with 2.1.295** |
| Node.js 20+ | Tested with v20.11.1. The state tool and hook use only the Node standard library; no `npm install` needed |
| Windows, macOS, or Linux | Developed and tested on Windows 11 |

> **Note:** Older Claude Code installs (for example 1.0.x from npm) have no plugin support, so `claude plugin ...` commands will not work with them. Update with `npm i -g @anthropic-ai/claude-code` or use the version bundled with the desktop app.

## Cost notice (important)

The plugin adds no paid services, APIs, MCP servers, or packages, and its scripts never touch the network. Automated tests enforce these rules.

**Even so, zero cost cannot be guaranteed.** Model usage depends on the account you use with Claude Code:

- **Subscription plan (Pro/Max, etc.):** usage counts against your plan limits.
- **API key (pay as you go):** every agent call is billed.

To keep usage down:
- Every command runs only when you invoke it (`disable-model-invocation: true`).
- Each workflow has a capped agent-call budget.
- Agents cannot start other agents.

## Installation

### A) Try it temporarily (recommended first step; does not change your settings)

Clone the repository, then start Claude Code in the project you want to manage with the plugin directory:

```bash
git clone https://github.com/alperglcoglu/Orkestr.git
```

```bash
claude --plugin-dir "/path/to/Orkestr/plugins/orkestr"
```

The plugin is loaded for that session only.

### B) Permanent install (from GitHub)

These steps change your Claude Code user settings. Inside Claude Code:

```
/plugin marketplace add alperglcoglu/Orkestr
/plugin install orkestr@orkestr
```

### C) Permanent install (from a local clone)

Inside Claude Code:

```
/plugin marketplace add /path/to/Orkestr
/plugin install orkestr@orkestr
```

Or from the command line, with a plugin-capable `claude`:

```bash
claude plugin marketplace add "/path/to/Orkestr"
```

```bash
claude plugin install orkestr@orkestr
```

To uninstall:

```bash
claude plugin uninstall orkestr
```

After changes, run `/reload-plugins` in an open session.

### Verify the installation

From the repository root:

```bash
claude plugin validate plugins/orkestr --strict
```

```bash
claude --plugin-dir plugins/orkestr plugin details orkestr
```

The second command should list 7 skills, 10 agents, and 1 hook.

## Commands

| Command | What it does | Agent budget |
| :- | :- | :- |
| `/orkestr:run [goal]` | **Main command.** Drives everything from goal to completion report on its own: requirements, plan, tasks, implementation, tests, review, fixes, and meetings when needed. It only comes back to you in the cases listed under "When are you consulted?" | ≤ 30 / run |
| `/orkestr:plan [goal]` | Initializes the project (if needed), writes a PRD and a task plan, and records the tasks in `.orkestr/`. In `bounded` mode the plan becomes the internal working plan right away; in `supervised` mode it asks for your approval | ≤ 2 |
| `/orkestr:meeting <question>` | Holds a meeting with 2–4 relevant agents, at most 2 rounds; records the decision, dissenting views, and action items; action items become tasks | ≤ participants × rounds (≤ 8) |
| `/orkestr:build [T-id]` | Implements one authorized task; records real test runs as evidence; makes **at most 3 fix attempts**; resumes interrupted work where it left off | ≤ 4 |
| `/orkestr:review [T-id]` | Runs QA and, when needed, Security review; separates verified findings from suspected findings and assumptions; completes the task if the evidence is sufficient | ≤ 2 |
| `/orkestr:status [pause\|resume\|cancel]` | Reports status from the records: blockers, failed tasks, pending approvals, decisions waiting on you. Pauses, resumes, or cancels the project | 0 |
| `/orkestr:retro [scope]` | Writes an evidence-based retrospective; records lessons; only **proposes** process changes | 0 |

Specialist agents:
- `orkestr:product-manager`: requirements and acceptance criteria
- `orkestr:system-architect`: system design (components, data flow, data model, interface contracts, scalability and reliability, directory layout)
- `orkestr:tech-lead`: tasks, dependencies, and file scopes for parallel work
- `orkestr:ml-engineer`: AI and machine learning components. Used only when truly needed; non-ML or free local solutions are considered first. It does not add paid model APIs, asks for approval before downloading large models, and never reports unmeasured metrics.
- `orkestr:data-engineer`: data model, schemas, reversible migrations, data pipelines, analysis. Destructive data operations such as dropping tables require approval.
- `orkestr:backend-engineer`
- `orkestr:frontend-engineer`
- `orkestr:qa-engineer`
- `orkestr:security-reviewer`
- `orkestr:researcher`

The CEO and Project Manager roles are not separate agents; the main session running the skills (the orchestrator) takes on those roles.

Orkestr talks to you in the language you write in. Technical identifiers, commands, file names, and state names stay as they are.

## Typical flow (autonomous)

```
/orkestr:run A simple to-do list app; free tools only; with tests
```

Orkestr carries out these steps on its own:

1. The **Product Manager** writes the requirements.
2. The **System Architect** designs the system, and the **Tech Lead** breaks it into dependent tasks.
3. The plan becomes the internal working plan immediately; no approval is awaited.
4. For each task:
   - a developer agent implements it;
   - tests are **actually** run;
   - QA (and Security when needed) reviews it;
   - findings are written to the task as notes and go back to the developer.
5. If a test fails, no permission is asked; up to 3 fix attempts are made.
6. If it is still unresolved, a **recovery meeting** is held automatically with the relevant agents. If a decision comes out of it, work resumes where it stopped.
7. Technical assumptions are recorded; you are not asked.
8. At the end you get a short **completion report** (`ork report`):
   - what was done;
   - which tests actually ran, and their results;
   - which decisions rest on assumptions;
   - what could not be resolved;
   - what is expected from you.

If the session is interrupted, run `/orkestr:run` again; it continues from the records. The other commands are for running the steps by hand one at a time.

### Parallel work

Independent tasks are built at the same time:

- The Tech Lead gives each task a **file scope** (`scope`, e.g. `src/api/**`).
- Tasks with non-overlapping scopes run in parallel in the same **wave**; the default limit is 2 tasks at once.
- A task without a scope runs alone.
- Shared files such as `package.json` belong to a single task's scope; other tasks depend on that task.
- When the agents finish, each task's changed files are checked against its scope with `ork scope check`. Out-of-scope changes count as a failed check and get fixed.
- Tests run task by task, sequentially, after the wave finishes writing; reviews may run in parallel.
- The state tool rejects a start that overlaps or exceeds the limit.

### When are you consulted?

Only in these cases:
- a contradiction in the requirements that a reasonable assumption cannot resolve;
- a change to the product goal, the core user experience, or a significant scope decision;
- an irreversible operation or one that risks data loss;
- external or paid services, or any extra cost;
- real credentials, sensitive data, or privilege escalation;
- `git push`, production deployment, or public release without explicit authorization;
- fix limits and the recovery meeting have been exhausted.

### Autonomy mode

| Mode | Behavior |
| :- | :- |
| `bounded` (default for new projects) | The autonomous behavior above |
| `supervised` | Plans and meeting decisions wait for your approval. Older projects without an `autonomy` field are treated as this mode |

Only you can change the mode; the hook asks for confirmation on this command as well:

```bash
node plugins/orkestr/scripts/orkestr.mjs project autonomy supervised --by founder
```

## Approvals and safety

The following never happen without **your explicit approval**:
- `git push`;
- production deployment;
- public release;
- external communication;
- spending;
- permission changes;
- access to sensitive data beyond what was authorized;
- destructive deletions.

Approvals are kept as records such as `A-001`. A rejected approval never turns into an approval.

"Founder commands" create your approval record and are run only after you say "yes" in chat:
- plan approval and decision approval (in `supervised` mode or for `founder_required` decisions);
- task authorization;
- retry;
- autonomy mode change;
- project cancellation.

In `bounded` mode, routine plans and decisions do not need these commands.

Protection is layered; no single layer is a security boundary on its own:

1. Claude Code's own permission system.
2. The state tool: it rejects starting unapproved tasks, completing without evidence, exceeding limits, and automatic retries.
3. The `PreToolUse` hook (`scripts/guard.mjs`): it asks for confirmation (`ask`) on the risky commands above and on hand edits to `.orkestr/` state files.

> **About the hook:** While the plugin is enabled, the hook runs in **all of your sessions**. For example, `git push` or `rm -rf` will ask for confirmation even in work unrelated to Orkestr. The hook looks at the command text and may miss unusual spellings. Whether an `ask` decision still prompts in `bypassPermissions` mode has **not been verified**.

Credentials, tokens, and passwords are not written to logs or records. The state tool masks common secret patterns as `[REDACTED]` before writing, and the `validate` command reports suspicious values. This is a safety net, not a guarantee.

## Limits

| Limit | Value | Enforced by |
| :- | :- | :- |
| Meeting participants | at most 4 | state tool |
| Meeting discussion rounds | at most 2 | state tool |
| Build/fix attempts | at most 3; the next failed check sets the task to `FAILED` | state tool |
| Agent turns | `maxTurns` per agent (12–30) | Claude Code |
| Nested agent calls | none (agents have no `Agent` tool) | Claude Code |
| Parallel implementation tasks | at most 2 at once, only with non-overlapping file scopes | state tool |
| Recovery meeting | 1 per task (`bounded`); if it yields a decision, the task reopens with 3 fresh attempts | state tool |
| Retries beyond that | only with your `--retry` approval | state tool and hook |
| Agent calls per `/orkestr:run` | ≤ 30 (by instruction, not enforced by code) | skill |

## Project data (`.orkestr/`)

```text
.orkestr/
├── project.json      project record, limits, ID counters
├── tasks.json        tasks (T-001 …)
├── approvals.json    approvals (A-001 …)
├── history.jsonl     append-only execution history
├── requirements/     prd.md
├── architecture/     architecture.md (optional)
├── meetings/         M-001.json + M-001.md
├── decisions/        D-001.json + D-001.md
├── reviews/          R-001.json + R-001.md
├── retros/           RT-001.json + RT-001.md
├── memory/           lessons-learned.md
└── drafts/           temporary input files written by the orchestrator
```

- JSON files are the source of truth. The `.md` files next to them are generated; do not edit them by hand.
- State changes only through the state tool. You can also use the tool directly:

  ```bash
  node plugins/orkestr/scripts/orkestr.mjs help
  ```

- If you use Git, you may want to add `.orkestr/drafts/` to your `.gitignore`.

### Task states

`BACKLOG → READY → IN_PROGRESS → REVIEW → COMPLETED`; plus `BLOCKED`, `FAILED`, and `CANCELLED`.

- A task becomes `READY` automatically once it is authorized and its dependencies are complete.
- If a task becomes `FAILED` or `CANCELLED`, the tasks that depend on it automatically become `BLOCKED`.

### Pause, resume, cancel, and recovery

- `/orkestr:status pause <reason>`: no new tasks can start.
- `/orkestr:status resume`: the project continues where it left off.
- `/orkestr:status cancel <reason>`: irreversible; open tasks and pending approvals are cancelled. Your explicit approval is requested first.
- Interrupted work: a task left in `IN_PROGRESS` shows up in the status report. `/orkestr:build T-xxx` resumes it from its evidence and the working tree; it does not start over.
- Files are written atomically, and a lock file prevents two writers at the same time. Corrupt or inconsistent state is reported by `validate` and workflows stop; no automatic repair is done.

## Tests

Run from the repository root:

```bash
node --test plugins/orkestr/tests/
```

Last run: **70 tests, 70 passed** (Windows 11, Node 20.11.1). Autonomy tests are in `tests/autonomy.test.mjs`, parallel-work tests in `tests/parallel.test.mjs`.

Coverage:
- project initialization and refusing to overwrite;
- plan import and rejecting circular dependencies;
- authorization;
- all state transition rules;
- dependency blocking and unblocking;
- the 3-fix-attempt limit;
- the retry gate;
- completion evidence rules;
- meeting limits;
- the meeting → decision → task chain;
- decision rejection;
- approval gates;
- pause, resume, and cancel;
- lock and temp-file recovery;
- secret masking;
- status report contents;
- hook decisions;
- plugin structure;
- budget rules;
- template validity.

## Renaming (Orkestr → another name)

1. Change the `name` field in `plugins/orkestr/.claude-plugin/plugin.json`. If needed, also update the folder name and `.claude-plugin/marketplace.json`.
2. Replace the `orkestr:` prefix in skill, agent, and reference files with the new name. The name-check test in `tests/plugin.test.mjs` catches any leftover references.
3. The data folder name is defined only in the `DATA_DIR_NAME` constant in `scripts/lib/core.mjs`.
4. Run the tests and `claude plugin validate` again.

## Known limitations

- **No live model test yet.** An end-to-end scenario following the skills through a real Claude session has not been run. The state tool, the hook, and plugin loading are verified automatically; whether the model follows the skill instructions correctly **requires manual verification**.
- Agent-call budgets in the skills are limited by instruction, not enforced by code. Meeting and fix limits are enforced by the state tool.
- Founder approval is protected by the `--by founder` flag and the hook's confirmation prompt. The state tool cannot verify on its own that a human actually approved the command.
- The hook looks at the command text and may miss unusual command forms.
- Secret masking is pattern-based and cannot catch every secret.
- In parallel work, agents staying inside their own scope is ensured **by instruction**. `ork scope check` catches violations afterwards but does not prevent the writes. Without Git, the list of changed files relies on the agent's report. Full isolation with a separate git worktree per task is left for a later phase.
- Scope overlap is computed conservatively; it may sometimes serialize two tasks that would not actually conflict (the safe side).
- The `/orkestr:run` loop has **not been tested live** end to end in a Claude session. The state tool's autonomy rules (automatic authorization, automatic decision approval, recovery meeting, budget limits) are verified by automated tests.
- Setting the `founder_required` flag correctly depends on the orchestrator's judgment; the tool cannot tell by itself which decisions are product-level.
