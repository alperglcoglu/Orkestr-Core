# orkestr-core

The standalone orchestration core of Orkestr (Phase 3). It runs the Orkestr workflow
(plan → build → test → review → fix → meetings → completion report) as deterministic Python
code, and calls AI agents only for well-defined steps. Every agent answer is validated
against a schema before it changes anything, and the core runs the tests itself.

The binding scope is [`PHASE3_IMPLEMENTATION.md`](../PHASE3_IMPLEMENTATION.md).

> **Status:** implemented, tested with a scripted executor (134 tests), and validated live:
> one full run on the Phase 2 `split` project completed 3 tasks in 11 agent calls with no
> founder question (see `phase2/FINDINGS.md`, run 3).

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
- `POST /runs`, `/runs/{id}/pause|resume|cancel`, `/retros`.

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
```

## Known limitations

- Verified with Claude Code 2.1.295: the subscription sign-in works with `--restricted`,
  `--max-turns` is honored, and disallowed tool calls are denied and reported. The exact
  shape of a usage-limit error has not been observed yet. Until it is, any error that
  mentions a limit is treated as a usage limit, and the run stops.
- Only one project per database and one local founder. There are no user accounts.
- Meetings are held for recovery and for design questions raised by an implementer. Other
  MASTER_SPEC §6.1 triggers are not detected automatically yet.
- `retro` has been tested only with the fake executor so far.
