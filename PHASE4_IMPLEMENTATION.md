# PHASE 4 IMPLEMENTATION — ORKESTR DASHBOARD, TEAMS, AND POLICIES

You are the implementation engineer for Phase 4. Read `MASTER_SPEC.md`, `PHASE3_IMPLEMENTATION.md`, `core/README.md`, and `phase2/FINDINGS.md` first. This document is the binding Phase 4 scope. If the documents disagree, report the conflict instead of choosing silently.

Status: **approved by the founder on 2026-10-12.** Where the implementation refines a detail, this document is updated to match; §13 lists those changes.

## 1. Objective

MASTER_SPEC §20 lists seven Phase 4 items. This phase implements the first three, on top of the Phase 3 core:

1. **Founder dashboard** with a task and dependency visualization and live execution monitoring.
2. **Configurable agent teams**: per project, which roles work, with which model and turn limit, and with which extra founder instructions.
3. **Richer permission policies**: founder-owned, versioned rules that tighten what agents may do and widen only the list of test commands.

Before any of these, step 0 closes the open Phase 3 items, because MASTER_SPEC §20 starts Phase 4 "only after the standalone core is stable".

Out of scope for this phase (later Phase 4 slices or Phase 5): additional tool integrations (MCP servers, web access for agents), business and operational agents, custom agent roles, remote or multi-user access, hosting, Docker, multi-project coordination, local-model executors. The core still executes no restricted action itself (PHASE3 §8).

## 2. Founder decisions (2026-10-12)

| Decision | Choice |
| :- | :- |
| Phase 4 scope (first slice) | Dashboard and visualization, configurable agent teams, richer permission policies |
| Dashboard technology | Plain HTML, CSS, and JavaScript (ES modules), served by the existing FastAPI app. No build step, no npm packages, no CDN |
| Phase 3 open items | Closed first, as step 0 of this phase |
| Order of work | This specification first; implementation only after approval |
| Plugin | Finished and released separately (`alperglcoglu/orkestr`). Phase 4 does not modify `plugins/orkestr/`; the core keeps reading its agent prompts and guard script |

## 3. Budget and dependency constraints

- The additional budget is still $0. No paid APIs, services, or hosting.
- **No new packages.** The pinned list in `core/pyproject.toml` (PHASE3 §3) stays unchanged. FastAPI serves the static files with `starlette.staticfiles`, which is already installed with FastAPI. The dashboard uses no JavaScript or CSS library, no web font, and no external URL. A test enforces this.
- Dashboard logic that can be tested without a browser lives in plain ES modules and is tested with `node --test`, which the plugin already uses. No JavaScript test framework is added.
- Every Phase 3 executor rule stays: no `--bare`, no `--fallback-model`, no permission-bypass flag. Team and policy settings can never add one; a test enforces this.
- **Billing caveat (unchanged):** starting a run from the dashboard uses Claude Code exactly like `orkestr-core run`. The dashboard says so next to the start button.

## 4. Step 0 — close the Phase 3 open items

| # | Item | Work |
| :- | :- | :- |
| 0.1 | Finding #13: "Checks that actually ran" lists scope checks | Split the report section into "Tests and checks run" and "Scope checks" in the core renderer (`core/orkestr_core/domain/render.py`). The plugin renderer is not changed (§2). Scope checks never count as test evidence (unchanged) |
| 0.2 | No `retro` command in the core | `orkestr-core retro` and `POST /retros`: one bounded agent call (`tech-lead`, `read_only` profile, `RetroOutput` schema already exists) over the run history, written as an `RT-###` record and mirrored to `.orkestr/`. Counts against the run's agent-call limit. Refused while a run is active |
| 0.3 | No live meeting has run | A live drill on a fresh `split` sandbox **with founder consent**: run with `max_fix_attempts: 1` and a task whose `verify` command fails until a design choice is made, so a recovery meeting is triggered for real. Recorded in `phase2/FINDINGS.md` as "Run 4". If no meeting happens, record that honestly and do not fake one |
| 0.4 | Usage-limit result shape unknown | Not forced (it would waste plan usage). If a run hits a real limit at any point in Phase 4, its JSON is saved as a redacted fixture and the matcher is tightened. Until then the Phase 3 safe-side rule stays |

## 5. Configurable agent teams

### 5.1 What can be configured

Per project, for each of the ten roles in `AGENT_ROLES`:

| Field | Meaning | Rule |
| :- | :- | :- |
| `enabled` | The role may own tasks and join meetings | Required roles cannot be disabled (§5.2) |
| `model` | Model alias or name for this role's calls | Matches `^[A-Za-z0-9][A-Za-z0-9._\-\[\]]{0,63}$`, so it can never be read as a flag. Precedence: role model, then the run's `--model`, then Claude Code's default |
| `max_turns` | Turn limit for this role | Integer from 1 up to the role definition's `maxTurns`. It can only lower the limit |
| `instructions` | Extra founder text for this role | At most 2,000 characters, redacted, appended to the role prompt under the heading "Founder's instructions for this project". It cannot change tool profiles, permissions, or limits, because those are set on the command line by the core |

Not configurable in this phase: tool profiles (set by the step kind), new roles (the role list is shared with the Phase 1 validator), and the role prompts themselves (they stay in `plugins/orkestr/agents/*.md`).

### 5.2 Required roles

`product-manager` (plans), `tech-lead` (fallback owner, meetings, retros), `qa-engineer` (reviews), and `security-reviewer` (security reviews) are always enabled. The engine changes accordingly:

- The plan prompt lists only enabled roles as owners. A plan that assigns a disabled role is rejected by `_plan_problems` and retried once, like other invalid plans.
- Meeting participants are chosen only from enabled roles; when the owner's role is disabled, `tech-lead` takes its seat.
- Existing tasks owned by a role that is later disabled are not reassigned silently. The run stops at `WAITING_FOUNDER` and names them, so the founder re-enables the role or edits the tasks.

### 5.3 Storage and audit

- Table `team_role` (migration `0002`): one row per role, defaults equal to Phase 3 behavior (all enabled, no overrides).
- Every change writes an audit event with the old and new values.
- The team in force is copied into `workflow_run.options_json` when a run starts. Changes made during a run apply from the next run, and the dashboard says so.
- The Phase 1 format has no team. `export` leaves the team out, and `export --release` warns that the plugin ignores it.

## 6. Permission policies

### 6.1 Principle

A policy can **only tighten** the Phase 3 rules, with one exception: it can add test command forms. It can never:

- remove an approval requirement in MASTER_SPEC §10.2 (`git push`, deploy, publish, deletes, paid services, permission changes, external messages);
- let the core or an agent carry out a restricted action;
- add tools, MCP servers, or network access to agents;
- raise a limit above the hard caps in §6.3;
- pass any flag forbidden by PHASE3 §3.

### 6.2 Policy fields

```json
{
  "test_commands": ["go test ./...", "cargo test"],
  "protected_paths": ["LICENSE", ".github/**", "db/migrations/**"],
  "approval_paths": ["src/billing/**"],
  "security_review_paths": ["src/auth/**"],
  "limits": { "max_agent_calls_per_run": 40, "max_fix_attempts": 2 }
}
```

| Field | Effect | Enforcement |
| :- | :- | :- |
| `test_commands` | Extra command prefixes the core may run as tests and agents may run with Bash | Added to `command_problem` and to the `Bash(<prefix> *)` allow rules. Each entry needs at least two words, no shell operators, and must not be a bare shell or interpreter (`sh`, `bash`, `pwsh`, `powershell`, `cmd`, `python`, `node`, `npx`, `deno`, `ruby`, `perl`) or start with `git`. The guard's destructive patterns are checked against each entry |
| `protected_paths` | Files no task may change | (1) A plan or meeting task whose scope overlaps a protected path is rejected. (2) After every implementation call, a changed protected file is a failed check, exactly like an out-of-scope file. (3) Defense in depth: `Edit(<glob>)` and `Write(<glob>)` deny rules in the agent settings file. Rule (2) is the boundary; (3) is only a guardrail |
| `approval_paths` | Tasks whose scope touches these paths need a founder approval before they start | The engine sets `requires_approval` on such tasks and creates the approval request, as in Phase 3 |
| `security_review_paths` | Paths that always trigger the security review | Added to the existing `SECURITY_TRIGGER` check |
| `limits` | Founder-chosen limits | Bounded by the hard caps in §6.3 |

Autonomy (`bounded` or `supervised`) stays a separate founder setting. Phase 4 adds a founder action to change it from the CLI and the dashboard; it was only settable at `init` before.

### 6.3 Hard caps

| Limit | Phase 3 default | Hard cap |
| :- | :- | :- |
| `max_agent_calls_per_run` | 30 | 200 |
| `max_agent_calls_per_task` | 8 | 20 |
| `max_fix_attempts` | 3 | 5 |
| `max_recovery_meetings` | 1 | 2 |
| `max_parallel_tasks` | 2 | 4 |
| `max_meeting_participants` | 4 | 6 |
| `max_discussion_rounds` | 2 | 3 |
| `max_reported_cost_usd_per_run` | off | any positive value |

### 6.4 Storage, versions, and audit

- Table `policy_version` (migration `0002`): `version`, `policy_json`, `reason`, `created_at`, `created_by`. Rows are append-only (a trigger, like the audit log). The current policy is the newest row. Version 0 is the empty policy.
- Changing the policy needs a reason and is a founder action.
- A run uses the policy version in force when it starts and records that version. Changes apply from the next run.
- The policy lives in `core.db`, never in a file an agent can edit. Agents cannot reach the API token: `.orkestr/` edits are denied by the guard hook and caught by the scope check.

## 7. Founder dashboard

### 7.1 Serving and security

- `orkestr-core serve` keeps binding to `127.0.0.1` only. The dashboard is served from the same app at `GET /` and `GET /ui/*`. These static files contain no project data, so they need no token. Every data and action endpoint keeps requiring `X-Orkestr-Token`.
- New command `orkestr-core dashboard [--port 8765] [--no-open]`: starts the server and opens `http://127.0.0.1:<port>/#token=<token>` in the default browser (stdlib `webbrowser`). The token is in the URL fragment, so the browser never sends it to any server or writes it to server logs. The page moves it to `sessionStorage`, removes it from the address bar, and sends it as the header.
- Requests whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` are refused (DNS-rebinding defense in depth).
- Response headers on the dashboard: `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. No inline scripts or styles.
- All project text (task titles, agent output, test output, meeting text) is untrusted. It is inserted with `textContent` or as SVG text nodes only, never as HTML. A test scans the dashboard sources for `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, and `eval`.
- Destructive founder actions (reject, cancel) and run start ask for confirmation in the page.

### 7.2 Views

| View | Content |
| :- | :- |
| Overview | Project goal, status, autonomy; the active run with its current step; agent calls and reported cost against limits; a "Needs you" list (pending approvals, decisions waiting for approval, founder tasks, the `WAITING_FOUNDER` reason) with the matching action buttons |
| Tasks | Board with one column per state. A task drawer shows description, owner, scope, acceptance criteria, dependencies, `verify`, notes, evidence, test results with output tail, reviews, and failure. Founder actions: authorize, retry |
| Graph | Dependency graph as SVG with a layered layout computed in `graph.js` (longest-path layering, then barycenter ordering to reduce crossings). Nodes are colored by state. Selecting a node highlights what it waits for and what waits for it. Tasks that can run in the same wave are marked |
| Runs | Run list; for a run, the step timeline and the agent calls (role, model, status, turns, tokens, reported cost, duration). A live event feed polls `GET /history?after=<ts>` every 2 s while a run is active and every 15 s otherwise. Start (goal, model, call limit), pause, resume, cancel |
| Meetings and decisions | Lists and details: participants, contributions by round, dissent, the decision, the tasks it created or steered. Founder actions: approve, reject |
| Team | The §5 table, editable. Shows which values differ from the defaults and that changes apply from the next run |
| Policy | The current policy, its version history, and an editor that validates on the server before saving. Shows the hard caps |
| Report | The latest completion report and retro, as preformatted text (no Markdown renderer, so no HTML injection path) |

The dashboard is usable at a 1280 px wide window and readable at 768 px. It follows the operating system's light or dark setting.

### 7.3 API additions

Same rules as PHASE3 §9.2: token required, pydantic responses, and the OpenAPI document is the contract.

- Reads:
  - `GET /project` (structured status: goal, state, autonomy, limits, counters, active run);
  - `GET /graph` (tasks with dependencies, state, owner, scope, and computed waves);
  - `GET /meetings`, `GET /decisions`, `GET /retros`;
  - `GET /runs/{id}/executions` (agent calls without `output_json`);
  - `GET /tasks/{id}/tests` (test results with redacted output tails);
  - `GET /team`, `GET /policy`, `GET /policy/versions`.
- Founder actions:
  - `PUT /team/{role}`;
  - `PUT /policy` (body: policy and reason; returns the new version or the validation errors);
  - `PUT /project/autonomy` (body: mode and reason);
  - `POST /retros`.

CLI equivalents: `orkestr-core team show|set <role> [--enabled ...] [--model ...] [--max-turns ...] [--instructions-file ...]`, `orkestr-core policy show|set <file> --reason R|history`, `orkestr-core autonomy <mode> --reason R`, `orkestr-core retro`, `orkestr-core dashboard`.

### 7.4 Files

```text
core/orkestr_core/
  api/app.py                 # new routes, static mount, headers, Host check
  domain/team.py             # team rules and validation
  domain/policy.py           # policy schema, validation, hard caps
  store/migrations/0002_teams_policies.sql
  ui/
    index.html
    app.css
    app.js                   # routing, polling, rendering (textContent only)
    api.js                   # token handling and fetch wrapper
    graph.js                 # pure layout functions, no DOM
    format.js                # pure formatting helpers, no DOM
core/tests/
  test_team.py  test_policy.py  test_dashboard.py
  ui/graph.test.mjs  ui/format.test.mjs
```

## 8. Data model changes (migration `0002`)

- `team_role(role PRIMARY KEY, enabled, model, max_turns, instructions, updated_at)`, with `CHECK`s for the rules in §5.1. Required roles are checked by a trigger as well as in code.
- `policy_version(version PRIMARY KEY, policy_json, reason, created_at, created_by)`, append-only by trigger.
- `workflow_run.policy_version` (nullable for old runs). The team snapshot goes into the existing `options_json`.
- Forward-only, like `0001`. A Phase 3 database is upgraded on first open, and a test covers that upgrade with a `0001`-only database.

## 9. Testing

- `pytest` from `core/`, fake executor only, no network or model calls (unchanged).
- New suites:
  - team: required roles, `max_turns` only lowers, model pattern, precedence, plan rejection for disabled owners, meeting seats, snapshot per run, no forbidden flag from any team value;
  - policy: every rejection rule in §6.2, hard caps, append-only versions, protected path changed → failed check, protected path in a plan scope → rejected plan, `approval_paths` → approval stop, extra test command runs and its permission rule is generated, policy version recorded per run;
  - API: every new route, token required, `Host` check, dashboard served without a token but data routes still refused, security headers present;
  - dashboard sources: no external URL, no `innerHTML`-style sinks, no inline script or style;
  - migration `0001` → `0002` on a Phase 3 database;
  - step 0: renderer split, retro command;
  - `node --test core/tests/ui/*.test.mjs`: layering and ordering on chains, diamonds, cycles (rejected, as in the state tool), and wide graphs; formatting helpers.
- All Phase 3 tests and the plugin suite (`node --test "plugins/orkestr/tests/*.test.mjs"`) must still pass.
- **Browser check (manual, no model use):** with a fake-executor fixture project, open every view in a real browser (the Claude desktop browser pane or Chrome), check light and dark mode, 1280 px and 768 px widths, and that untrusted text containing `<img src=x onerror=…>` shows as text. Record the result in the PR.
- **Live check (with founder consent):** step 0.3, plus one run on the `split` sandbox started and monitored from the dashboard, with a team override (for example `qa-engineer` on a different model) and a policy with one protected path. Recorded in `phase2/FINDINGS.md`.

## 10. Acceptance criteria

| # | Criterion | Check |
| :- | :- | :- |
| 1 | Phase 3 open items closed or honestly reported (§4) | renderer and retro tests; Run 4 in `phase2/FINDINGS.md` |
| 2 | The founder can see project state, tasks, the dependency graph, runs, agent calls, meetings, decisions, and reports in the dashboard | browser check; API tests |
| 3 | Every founder action available in the CLI is available in the dashboard and needs the token | API tests; browser check |
| 4 | A running run is visible live, and can be paused, resumed, and cancelled from the dashboard | browser check; live check |
| 5 | Untrusted text cannot run as code in the dashboard; no external resources are loaded | source tests; CSP test; browser check |
| 6 | Teams can enable roles and set model, turn limit, and instructions within the §5 rules, with audit and per-run snapshots | team tests |
| 7 | Policies tighten rules as specified and cannot loosen §10.2 approvals or exceed hard caps | policy tests |
| 8 | Protected paths are enforced by the core's own Git check, not only by agent permissions | policy tests; live check |
| 9 | Phase 3 databases upgrade to the new schema without data loss | migration test |
| 10 | No new packages; no forbidden flags; core makes no network calls | budget-rule tests |
| 11 | Install, usage, limitations, and known failures are documented | `core/README.md` |

## 11. Work order

Each step is a separate commit on a branch (not `main`). The phase goes to `main` through a pull request after the founder reviews it.

1. **Step 0.** Renderer split (0.1) and `retro` (0.2), with tests.
2. **Schema.** Migration `0002`, repositories, upgrade test.
3. **Teams.** `domain/team.py`, engine changes, CLI, tests.
4. **Policies.** `domain/policy.py`, verify and engine changes, executor deny rules, CLI, tests.
5. **API.** New routes, static mount, headers, `Host` check, tests.
6. **Dashboard.** `graph.js` and `format.js` with node tests first, then the views. Browser check with a fixture project.
7. **Docs.** `core/README.md` (dashboard, teams, policies, limitations), root README, MASTER_SPEC §20 link.
8. **Live checks with founder consent.** Step 0.3 and the dashboard-driven run. Record findings.
9. **Report honestly:** files, commands, tests and results, what was not tested, limitations, next steps.

## 12. Assumptions and open questions

- **Assumption:** still one project per core database and one local founder. The dashboard serves one project, the one `serve` was started in.
- **Assumption:** polling is enough for live monitoring at this scale. No WebSocket or server-sent events are added unless the browser check shows polling is not enough.
- **Open (verified in step 8):** whether `permissions.deny` rules from `--settings` apply under `--restricted` and `dontAsk`. The core's own Git check is the enforcement either way; the result decides only whether the guardrail is documented as working.
- **Open (founder, later slice):** custom roles, additional tool integrations, and business agents. Each needs evidence from real use first (MASTER_SPEC §20).
- **Open (founder, from Phase 3):** whether the plugin should call the core's API instead of its own state tool.
- **Repositories:** the core is developed in `alperglcoglu/Orkestr-Core` (remote `origin`). The plugin is released from `alperglcoglu/orkestr` and is not changed in this phase.

## 13. Changes made during implementation

Any refinement of this document during implementation is listed here with its reason, as in PHASE3 §16.

| Section | Change | Reason |
| :- | :- | :- |
| §4 0.1 | Only the core renderer is changed; the plugin renderer keeps the old heading | Founder decision (2026-10-12): the plugin is finished and released separately |
| §4 0.2 | A retro is its own run (`kind = 'retro'`). It ends COMPLETED, FAILED, or CANCELLED, never continues or supersedes a workflow run, and `status` keeps showing the latest workflow run | A retro must not take over an open run that waits for the founder |
| §4 0.3 | The report's "Needs the founder" and the status view's new "Your tasks" list founder-owned tasks that are READY or IN_PROGRESS | Run 4 (first attempt): the run stopped for a founder task while the report said "Nothing." |
| §4 0.3 | The plan output gains `decisions_needed` (question, options, the `#n` tasks that wait). The engine holds a team meeting on each one before those tasks start; a decision that needs the founder stops the run, while no decision or a rejected one lets the tasks go ahead. The plan prompt tells the planner not to create founder-owned tasks for design choices | Run 4 (first attempt): with no other way to express an open design choice, the planner turned it into a founder task, so no meeting could happen. Founder decision (2026-10-12) |
| §5.3, §6.4 | The team and policy are taken each time a run **starts or continues** (`run`, `resume`, `POST /runs`), never in the middle of one invocation, and recorded on the run (`options_json.team`, `policy_version`) with a `run.settings` history event | A run that waits for the founder is continued, not restarted; otherwise re-enabling a role would only take effect after re-planning |
| §6.2 limits | Once a policy version 1 or later exists, its limits are written into the project limits when a run starts or continues, with a `project.limits` event. A limit left out of the policy is the Phase 3 default again. Projects without a policy keep their own limits. A policy that lowers `max_fix_attempts` or `max_recovery_meetings` below what a task already used is rejected | The domain rules read the project limits, and the Phase 1 validator checks tasks against them |
| §6.2 protected paths | The plan and meeting check applies to tasks that have a scope. Unscoped tasks are still caught by the Git check after the call | Rejecting every unscoped item (for example research) whenever a policy exists would block normal plans |
| §5.2 | Open tasks owned by a disabled role stop the run before any work on them continues, including interrupted work | Found by a test: resuming an interrupted task bypassed the check |
| §8 | Required roles are enforced by a `CHECK` constraint instead of a trigger | Simpler, and it covers inserts and updates alike |
| §3, §7.1 | Static files are served with `fastapi.staticfiles.StaticFiles`, not `starlette.staticfiles` | The budget-rule test allows imports from the approved packages only; FastAPI re-exports the same class |
| §7.3 | `GET /policy` also returns the hard caps and the always-allowed test commands | The dashboard shows them without keeping a second copy in JavaScript |
| §7.4 | Added `ui/dom.js` (the only place that creates elements; text only) and `ui/package.json` (`"type": "module"`, so `node --test` loads the `.js` modules) | Safe element building in one place; Node 20 treats `.js` as CommonJS otherwise |
| §9 | The node tests run with `node --test core/tests/ui/` | Node 20 does not expand globs in `node --test` |
