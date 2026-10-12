# orkestr-core

The standalone orchestration core of Orkestr. It runs the Orkestr workflow
(plan → build → test → review → fix → meetings → completion report) as deterministic Python
code, and calls AI agents only for well-defined steps. Every agent answer is validated
against a schema before it changes anything, and the core runs the tests itself. Phase 4
adds a founder dashboard, configurable agent teams, and permission policies.

The binding scopes are [`PHASE3_IMPLEMENTATION.md`](../PHASE3_IMPLEMENTATION.md) and
[`PHASE4_IMPLEMENTATION.md`](../PHASE4_IMPLEMENTATION.md).

> **Status:** Phase 3 implemented and validated live (`phase2/FINDINGS.md`, runs 3 and 4).
> Phase 4 implemented, tested with a scripted executor (259 Python and 18 dashboard tests),
> checked in a real browser, and used live once: a dashboard-started run with a team
> override and a protected path (`phase2/FINDINGS.md`, run 5).

## How it differs from the plugin

| | Plugin (Phase 1) | Core (Phase 3) |
| :- | :- | :- |
| Orchestrator | the Claude Code session following skill text | Python code (`orkestr_core/workflow/engine.py`) |
| State | JSON files in `.orkestr/` | SQLite `.orkestr/core.db`; JSON files are kept as read-only mirrors |
| Tests counted as evidence | whatever the session recorded | only commands the core ran itself |
| Changed files | reported by the agent | detected with Git and checked against task scopes |
| Agent-call and usage limits | instructions | enforced and recorded per call |
| Resume after a crash | skill procedure | checkpoints in `workflow_step` |
| Interface | slash commands | `orkestr-core` CLI and a local HTTP API |

The agent roles are shared: the core reads `plugins/orkestr/agents/*.md`.

## Install

You need Python 3.12 or newer, Git, Node.js 20 or newer (for the guard hook and parity tests),
and, for live runs, Claude Code 2.1.295 or newer.

From the repository root:

```bash
python -m venv core/.venv
core/.venv/Scripts/python -m pip install -e "core[dev]"
```

On macOS or Linux, use `core/.venv/bin/python` instead. Every dependency is pinned in
`pyproject.toml`: `fastapi`, `uvicorn`, and `pydantic` at runtime, and `pytest` and `httpx`
for tests. All of them are free and open source.

## Use

Run the commands from inside the project you want to manage, or pass `--project <dir>`. The
project must be a Git repository; it does not need a remote.

```bash
orkestr-core init --name "Split" --goal "CLI that splits a bill; node:test only"
orkestr-core run                       # until done or a founder decision is needed
orkestr-core status
orkestr-core report
```

| Command | What it does |
| :- | :- |
| `init --name --goal [--constraint] [--success] [--autonomy bounded\|supervised]` | Creates a core-managed project. Refuses to overwrite an existing one. |
| `import` | Hands a plugin (Phase 1) project to the core. The project must validate first. |
| `export [--release] [--force]` | Rewrites the JSON/Markdown mirrors. `--release` hands the project back to the plugin. |
| `run [goal] [--model M] [--max-agent-calls N]` | Runs or continues the workflow. Without a goal, the open run continues. |
| `pause`, `resume`, `cancel [--project-cancel --reason R]` | Run control. `cancel` stops running agent processes. Tasks keep their state. |
| `approve <A-###\|D-###\|plan> [--option O] [--note N]`, `reject <A-###\|D-###> --reason R` | Founder decisions. |
| `authorize <T-###>`, `retry <T-###>`, `approval-result <A-###> --result R` | Founder actions. |
| `status`, `report`, `validate` | Reports and validation. The report lists tests and scope checks separately. |
| `retro [--scope S] [--model M]` | One `tech-lead` agent call (read-only) that reviews the project history and records an `RT-###` retrospective. It is its own run and never continues or replaces a workflow run. |
| `serve [--port 8765]` | Starts the local API. |
| `dashboard [--port 8765] [--no-open]` | Starts the local API and opens the founder dashboard in your browser. |
| `team show`, `team set <role> [--enable\|--disable] [--model M] [--max-turns N] [--instructions-file F]` | Shows or changes the agent team. `--model ""` and `--max-turns 0` clear a value. |
| `policy show\|history`, `policy set <file.json> --reason R` | Shows or changes the permission policy. Every change is a new version. |
| `autonomy bounded\|supervised --reason R` | Changes the autonomy mode. |

### Dashboard

```bash
orkestr-core dashboard
```

The dashboard opens at `http://127.0.0.1:8765/`. It shows what needs you (approvals,
decisions, your own tasks, failed tasks), the run in progress with its agent calls and
usage, the task board, the dependency graph, meetings and decisions, the team, the policy,
and the latest report and retrospective. Every founder action from the CLI is also a button.
While a run is active the page refreshes every 2 seconds, and every 15 seconds otherwise.

- The page holds no project data. It reads everything from the token-protected API.
- The browser receives the token in the address fragment (`#token=…`), which is never sent
  to a server. The page moves it into the tab's session storage and removes it from the
  address bar. Do not share the address the command opens.
- The page loads nothing from the network: no CDN, fonts, or analytics. A strict
  Content-Security-Policy forbids inline scripts. Project text, such as agent output, is
  always shown as text, never as HTML.
- The server answers only to `127.0.0.1:<port>` and `localhost:<port>`.
- It uses plain HTML, CSS, and JavaScript modules, with no build step and no npm packages.

### Agent team

Each role can be switched off and given its own model, a lower turn limit, and extra
instructions (at most 2,000 characters, added after the role prompt). `product-manager`,
`tech-lead`, `qa-engineer`, and `security-reviewer` cannot be switched off. Tool permissions
and limits are never part of a team.

The model is chosen in this order: the role's model, then the run's `--model`, then Claude
Code's default. A plan that gives work to a disabled role is rejected. A run with open tasks
owned by a disabled role stops and asks you.

### Permission policy

A policy can only tighten the rules, with one exception: it can add test commands.

```json
{
  "test_commands": ["go test ./..."],
  "protected_paths": ["LICENSE", ".github/**"],
  "approval_paths": ["src/billing/**"],
  "security_review_paths": ["src/auth/**"],
  "limits": { "max_agent_calls_per_run": 40, "max_fix_attempts": 2 }
}
```

- `test_commands`: extra command prefixes the core runs as tests and agents may run. Shells,
  bare interpreters (`python`, `node`, `npx`, and so on), `git`, shell operators, and
  anything the guard flags are refused.
- `protected_paths`: no task may change these files. The core's Git check after each call
  fails the task when one changed. Plans and meetings may not scope a task over them, and
  agents also get deny rules for them.
- `approval_paths`: a task whose scope touches these waits for your approval before it starts.
- `security_review_paths`: tasks touching these always get a security review.
- `limits`: within hard caps (200 calls per run, 20 per task, 5 fix attempts, 2 recovery
  meetings, 4 parallel tasks, 6 meeting participants, 3 discussion rounds). Once you have a
  policy, a limit left out of it falls back to its default.

No policy can remove the approvals for push, deploy, publish, deletes, spending, permission
changes, or external messages. A policy also cannot make the core carry out such an action
itself.

The team and the policy in force are taken whenever a run starts or continues, and are
recorded on the run.

### Founder consultation

A run ends as `WAITING_FOUNDER` when it needs you, and the reason is printed. The cases are:

- a restricted action (push, deploy, publish, and so on);
- a founder-level decision;
- a task that the recovery meeting could not save;
- a reached limit;
- a usage limit reported by Claude Code;
- a plan that needs approval in `supervised` mode.

Answer with the commands above, then `orkestr-core run` continues. The core never carries
out a restricted action itself. After you grant one, you perform it and record the result
with `approval-result`.

### HTTP API

`orkestr-core serve` listens on `127.0.0.1` only. Every request needs the header
`X-Orkestr-Token` with the value from `.orkestr/core-token`. That file is created at `init`,
is git-ignored, and is never logged. The OpenAPI document is at `/openapi.json`, and
`/docs` shows it in a browser. Main endpoints:

- `GET /status`, `/report`, `/tasks`, `/tasks/{id}`, `/approvals`, `/retros`, `/runs`, `/runs/{id}/steps`, `/history?after=<ts>`;
- `POST /approvals/{id}/grant|reject`, `/decisions/{id}/approve|reject`, `/tasks/{id}/authorize|retry`;
- `POST /runs`, `/runs/{id}/pause|resume|cancel`, `/retros`;
- Phase 4 reads: `GET /project`, `/graph`, `/meetings`, `/decisions`, `/runs/{id}/executions`, `/tasks/{id}/tests`, `/team`, `/policy`, `/policy/versions`;
- Phase 4 founder actions: `PUT /team/{role}`, `/policy`, `/project/autonomy`.

## Cost and safety

- The core makes no network calls. Model traffic belongs to the `claude` subprocess.
- **Billing:** the core cannot tell how Claude Code is billed. With a subscription, runs count
  against your plan limits. With API-key billing (for example when `ANTHROPIC_API_KEY` is
  set), every call costs money. The "reported cost" in reports is Claude Code's own estimate.
  Nobody can promise zero cost.
- **Limits:** 30 agent calls per run and 8 per task by default (`project.limits`). There is
  also an optional `max_reported_cost_usd_per_run`. Reaching a limit stops the run and keeps
  its state. A usage limit from Claude Code is never retried automatically, and no other
  provider is ever used.
- **Agent permissions:** agents run headless with `--permission-mode dontAsk` and a tool
  allowlist per step:
  - read-only for planning and meetings;
  - read plus test commands for reviews;
  - read, edit, write, and test commands for implementation.

  Bash is limited to the test command forms `node --test …`, `npm test`,
  `python -m pytest …`, and `pytest …`. The plugin's guard hook is also active. No agent gets
  the Agent, web, or MCP tools. Calls run with `--restricted`, which ignores settings files
  from the repository and your user profile and confines file tools to the project.
- **The core never passes** `--bare` (it would switch to API-key billing),
  `--fallback-model`, or any permission-bypass flag. A test checks this.
- Every stored string is redacted for common secret formats.

## Develop

```bash
cd core
.venv/Scripts/python -m pytest
```

The suite uses a scripted executor and never calls a model. The tests that compare against
the Node state tool, and those that run `node --test` in sample projects, are skipped when
Node.js is missing. The plugin's own suite still passes:

```bash
node --test plugins/orkestr/tests/
node --test core/tests/ui/
```

The second command tests the dashboard's layout and formatting modules. Node 20 does not
expand globs in `node --test`, so pass the directory.

## Known limitations

- Verified with Claude Code 2.1.295: the subscription sign-in works with `--restricted`,
  `--max-turns` is honored, and disallowed tool calls are denied and reported. The exact
  shape of a usage-limit error has not been observed yet. Until it is, any error that
  mentions a limit is treated as a usage limit, and the run stops.
- Only one project per database and one local founder. There are no user accounts.
- Meetings are held:
  - for design choices the plan leaves open;
  - for design questions an implementer raises;
  - for recovery.

  Run 4 exercised a plan-time meeting live. A recovery meeting, a decision that needs the
  founder, and a second discussion round have not run live yet. Other MASTER_SPEC §6.1
  triggers are not detected automatically.
- Reviews can pass while they still list "suspected" findings. Nobody triages those yet
  (finding 18).
- Protected-path deny rules for agents have not been verified against a live Claude Code
  call. The core's Git check after each call is the enforcement either way.
- A meeting decision can widen the scope of the tasks it steers (finding 21); this has not
  been seen in a live run yet.
- The dashboard serves one project, the one `dashboard` was started in, for one local
  founder. Live updates use polling.
