# PHASE 3 IMPLEMENTATION — ORKESTR STANDALONE CORE

You are the implementation engineer for the standalone orchestration core. Read `MASTER_SPEC.md`, `PHASE1_IMPLEMENTATION.md`, and `phase2/FINDINGS.md` first. This document is the binding Phase 3 scope. If the documents disagree, report the conflict instead of choosing silently.

Status: **draft for founder approval.** No core code is written until the founder approves this document.

## 1. Objective

Build a local, standalone orchestration core that runs the Orkestr workflow (plan → build → test → review → fix → meetings → completion report) **without an interactive Claude Code session acting as the orchestrator**.

In Phase 1, the orchestrator was the main Claude Code session following skill instructions. Many rules were therefore instructions rather than code (Phase 2 findings 3–8). In Phase 3, the orchestrator is deterministic Python code. Model calls are limited to well-defined agent steps, and every agent output is validated against a schema before it changes state.

The core must:

- keep the product principles and the Phase 1 rules (task state machine, dependencies, limits, approvals, evidence, redaction);
- persist state in SQLite and stay able to exchange data with the Phase 1 `.orkestr/` format;
- execute agents through an interface, with Claude Code CLI as the first implementation;
- resume after any interruption from persisted checkpoints;
- expose a local HTTP API that the Phase 4 dashboard can use later.

Out of scope for Phase 3 (MASTER_SPEC §20, Phase 4–5): the dashboard UI, multi-project coordination, local-model executors, configurable agent teams, organization policies, remote access, hosting, Docker, or cloud deployment.

## 2. Founder decisions (2026-10-11)

| Decision | Choice |
| :- | :- |
| Agent executor | Claude Code CLI in headless mode (`claude -p`), using the founder's existing Claude Code access. Behind an interface, so a local-model executor can be added later |
| Language and stack | Python 3.12+ (developed on 3.13), FastAPI, stdlib `sqlite3` |
| Order of work | This specification first; implementation only after approval |

## 3. Budget and dependency constraints

- The additional budget is still $0. No paid APIs, subscriptions, databases, or cloud services.
- **Dependency change (explicit):** the Phase 1 "no packages" rule applies to the plugin and stays in force there. The core may use these free, open-source packages, with pinned versions in `core/pyproject.toml`, installed into a local virtual environment (`core/.venv`, git-ignored):
  - runtime: `fastapi`, `uvicorn`, `pydantic` (v2);
  - development only: `pytest`, `httpx` (for FastAPI's test client).

  Any other package needs a written reason in this document and the founder's approval.
- The core itself makes no network calls. The only outbound traffic is the model traffic of the `claude` subprocess, which belongs to Claude Code.
- The executor must **never** pass these flags:
  - `--bare`: it reads only `ANTHROPIC_API_KEY` and would switch the founder to API billing;
  - `--fallback-model`;
  - `--dangerously-skip-permissions`;
  - `--allow-dangerously-skip-permissions`;
  - `--permission-mode bypassPermissions`.

  A test enforces this.
- **Billing caveat (unchanged):** the core cannot tell how Claude Code is billed. With a subscription, usage counts against plan limits. With API-key billing, every call costs money. The documentation says so. Nobody may claim a zero-cost guarantee.
- **Machine-enforced usage limits** (new; in Phase 1 they were only instructions):
  - `max_agent_calls_per_run` (default 30);
  - `max_agent_calls_per_task` (default 8);
  - an optional `max_reported_cost_usd_per_run`, which is checked against the cost Claude Code reports and is **off by default**.

  When a limit is reached, the run stops, keeps its state, and asks the founder.

## 4. Repository layout

```text
core/
├── pyproject.toml              # package metadata, pinned dependencies, pytest config
├── README.md                   # install, usage, limitations (English)
├── orkestr_core/
│   ├── __init__.py
│   ├── constants.py            # data directory name, ID prefixes, defaults (single source)
│   ├── domain/                 # pure rules, no I/O
│   │   ├── models.py           # pydantic models for every entity and draft
│   │   ├── states.py           # task/decision/approval state machines
│   │   ├── rules.py            # guards: authorization, dependencies, evidence, limits
│   │   ├── scope.py            # scope pattern matching and overlap (port of scope.mjs)
│   │   └── redact.py           # secret redaction (port of the Phase 1 redactor)
│   ├── store/                  # SQLite persistence
│   │   ├── db.py               # connection, transactions, locking
│   │   ├── migrations/         # 0001_initial.sql, ...
│   │   └── repo.py             # repositories; the only module that writes SQL
│   ├── interop/                # Phase 1 format
│   │   ├── importer.py         # .orkestr/*.json → SQLite
│   │   └── exporter.py         # SQLite → .orkestr/*.json + generated .md views
│   ├── agents/
│   │   ├── definitions.py      # loads plugins/orkestr/agents/*.md (single source of role prompts)
│   │   ├── schemas/            # JSON Schemas for each structured agent output
│   │   └── prompts.py          # step prompts (plan, implement, review, meeting, synthesis)
│   ├── executor/
│   │   ├── base.py             # AgentExecutor protocol, AgentRequest, AgentResult
│   │   ├── claude_cli.py       # Claude Code CLI executor
│   │   └── fake.py             # scripted executor for tests
│   ├── workflow/
│   │   ├── engine.py           # resumable run loop, checkpoints, pause/resume/cancel
│   │   ├── steps.py            # plan, wave, implement, verify, review, fix, recovery, report
│   │   └── verify.py           # runs test commands itself; git-based changed-file detection
│   ├── api/
│   │   └── app.py              # FastAPI app (127.0.0.1 only)
│   └── cli.py                  # `orkestr-core` command
└── tests/                      # pytest suites (see §11)
```

The agent role prompts stay in `plugins/orkestr/agents/*.md`. The core reads them and does not copy them, so the plugin and the core share one set of roles (MASTER_SPEC §2.3).

## 5. Data model (SQLite)

### 5.1 Location and ownership

- The database is `<project>/.orkestr/core.db`. Project data stays inside the managed project, as in Phase 1. The core writes its own `.orkestr/.gitignore`, which ignores `core.db*`, `core-token`, and `core.lock`. It never edits the project's own `.gitignore`.
- SQLite settings: WAL mode, `foreign_keys = ON`, `busy_timeout = 5000`. A write happens in a single transaction, and so do its history events.
- A project is managed by **one owner at a time**: either the plugin (JSON files are the source of truth) or the core (`core.db` is the source of truth). `project.managed_by` records which one:
  - `orkestr-core import` hands the project from the plugin to the core;
  - `orkestr-core export --release` hands it back.

  While the core owns the project, the exporter keeps the JSON files and `.md` views up to date as **read-only mirrors**, so `/orkestr:status` and the Phase 1 validator still work. Each mirror has a header or field saying it is generated by the core.
- Before every import or export the core compares file hashes against its last sync. If someone changed the JSON files in between, the core refuses to overwrite them and reports the conflict.

### 5.2 Tables

IDs, enums, and field meanings are the Phase 1 ones (`PHASE1_IMPLEMENTATION.md` §5), and the state tool's validator rules still hold. Every table has `created_at` and `updated_at` columns (ISO 8601 UTC text) unless noted.

| Table | Key columns | Notes |
| :- | :- | :- |
| `schema_migrations` | `version`, `applied_at` | forward-only migrations; each runs in one transaction |
| `project` | `id` (always 1), `name`, `goal`, `status`, `status_reason`, `autonomy`, `managed_by`, `limits_json`, `counters_json` | single project per database (multi-project is Phase 5) |
| `project_list_item` | `kind` (`constraint`, `success_criterion`), `position`, `text` | |
| `assumption` | `id`, `text`, `impact`, `task_id`, `at` | |
| `task` | Phase 1 task fields except lists | `status` CHECK constraint; `fix_attempts`, `recovery_meetings` CHECKs |
| `task_dependency` | `task_id`, `depends_on_id` | FKs; cycle check in the domain layer before insert |
| `task_scope` | `task_id`, `position`, `pattern` | |
| `task_acceptance_criterion` | `task_id`, `position`, `text` | |
| `task_note` | `id`, `task_id`, `author`, `text`, `at` | |
| `evidence` | `id`, `task_id`, `type`, `result`, `command`, `summary`, `ref`, `test_result_id`, `at` | |
| `test_result` | `id`, `task_id`, `command`, `exit_code`, `duration_ms`, `output_tail` (redacted, ≤ 8 KB), `run_by` (`core` or an agent role), `at` | written only for commands the core executed itself |
| `meeting` | `id`, `objective`, `context`, `trigger`, `outcome`, `recovery_for`, `body_json` | `body_json` keeps the full validated draft |
| `meeting_participant` | `meeting_id`, `role`, `responsibility` | |
| `meeting_contribution` | `meeting_id`, `round`, `role`, `position`, `key_points_json`, `concerns_json`, `agent_execution_id` | |
| `decision` | Phase 1 decision fields | |
| `decision_task` | `decision_id`, `task_id`, `relation` (`created`, `applies_to`) | |
| `approval` | Phase 1 approval fields | only `PENDING` rows may change (enforced in `repo.py`) |
| `review` | `id`, `task_id`, `verdict`, `reviewers_json`, `scope`, `assumptions_json`, `agent_execution_id` | |
| `review_finding` | `review_id`, `title`, `severity`, `status`, `component`, `evidence`, `recommendation` | |
| `retro` | `id`, `body_json` | lessons are also appended to `memory/lessons-learned.md` |
| `artifact` | `path`, `kind` (`prd`, `architecture`, `lessons`, `other`), `sha256`, `updated_by` | prose files stay on disk; the table tracks them |
| `agent_definition` | `role`, `source_path`, `sha256`, `max_turns`, `tools_json` | refreshed from the plugin's agent files on every run |
| `agent_execution` | `id`, `run_id`, `step_id`, `role`, `task_id`, `executor`, `model`, `status`, `started_at`, `ended_at`, `duration_ms`, `num_turns`, `input_tokens`, `output_tokens`, `reported_cost_usd`, `session_id`, `error`, `output_json` (redacted) | one row per model call; the Phase 2 open item "agent calls and usage are not recorded" is closed here |
| `workflow_run` | `id`, `kind` (`run`, `plan`, `build`, `review`, `meeting`, `retro`), `goal`, `status` (`RUNNING`, `PAUSED`, `WAITING_FOUNDER`, `COMPLETED`, `FAILED`, `CANCELLED`), `status_reason`, `agent_calls`, `started_at`, `ended_at` | |
| `workflow_step` | `id`, `run_id`, `seq`, `kind`, `task_id`, `status` (`PENDING`, `RUNNING`, `DONE`, `FAILED`, `SKIPPED`), `attempt`, `input_json`, `result_json`, `error` | checkpoints for resume (§7.3) |
| `audit_event` | Phase 1 history fields (`ts`, `type`, `actor`, `entity`, `from`, `to`, `summary`, `data`) plus `run_id` | append-only: no UPDATE or DELETE statements exist for it (a test enforces this); mirrored to `history.jsonl` |

Organization, user, and tool-execution tables (MASTER_SPEC §16.1) are deferred. In Phase 3 there is one local founder, and Claude Code's `json` output does not list individual tool calls. They are added when Phase 4/5 needs them.

### 5.3 Redaction

Every string the core persists goes through `domain/redact.py`, whichever table it lands in. This includes agent output, test output, and errors. The Python redactor must redact everything the Phase 1 redactor redacts. A parity test runs both redactors on a shared corpus.

## 6. Agent execution

### 6.1 Interface

```python
class AgentExecutor(Protocol):
    name: str
    def capabilities(self) -> ExecutorCapabilities: ...
    def run(self, request: AgentRequest, cancel: CancelToken) -> AgentResult: ...

AgentRequest = {
  role, prompt, cwd, output_schema | None,
  tools: "read_only" | "implement" | "review",   # mapped to concrete tool lists by the executor
  scope: list[str],                                # informational for the agent; enforced by verify.py
  max_turns, timeout_s, model | None
}

AgentResult = {
  status: "ok" | "invalid_output" | "error" | "usage_limit" | "timeout" | "cancelled",
  output: dict | None,          # schema-validated structured output
  text: str,                    # redacted final text
  usage: { num_turns, input_tokens, output_tokens, reported_cost_usd },  # null fields when unknown
  session_id, error
}
```

`ExecutorCapabilities` reports what the executor can do: structured output, tool use, editing files, turn limits, and usage reporting. This is MASTER_SPEC §15 "model capability discovery", kept minimal. There is no model routing.

### 6.2 Claude Code CLI executor

- **Binary discovery:** in this order:
  1. `ORKESTR_CLAUDE_BIN`;
  2. the `core.claude_bin` setting;
  3. `claude` on `PATH`.

  On startup the executor runs `claude --version` and refuses versions older than the one this phase is validated with (2.1.295). On the development machine, the `claude` on `PATH` is v1.0.51 and is too old. The executor reports this instead of failing silently.
- **Invocation** (one subprocess per agent call, list arguments, no shell):

  ```
  claude -p <prompt>
    --output-format json
    --json-schema <schema>                 # when output_schema is set
    --agents <tmp file> --agent <role>     # role definition built from plugins/orkestr/agents/<role>.md
    --allowedTools <role tool list>
    --permission-mode dontAsk              # anything not allowed is denied; nobody is prompted
    --settings <tmp settings file>         # PreToolUse hook → plugins/orkestr/scripts/guard.mjs
    --no-session-persistence
    [--model <model>]
  ```

  The exact flag behavior (in particular `--json-schema` output placement, `--agent` with `--agents`, how `maxTurns` is applied, and how usage limits show up in the result) is verified against v2.1.295 in work-order step 4. The findings are recorded in `core/README.md`, and this section is corrected if needed.
- **Permissions:** headless runs have no human to answer a prompt. `dontAsk` makes every "ask" from the guard hook a denial. Restricted actions (MASTER_SPEC §10.2) are therefore never possible inside an agent call; they go through approval records (§8). Tool profiles:
  - `read_only`: Read, Grep, Glob;
  - `review`: `read_only` plus Bash limited to the project's test commands;
  - `implement`: `review` plus Edit and Write.

  No profile includes the `Agent` tool, web tools, or MCP tools. Web research for the researcher role is a separate, opt-in profile that is off by default.
- **Timeouts and cancellation:** the core enforces `timeout_s` (default 900) and kills the process tree on timeout or cancel. The result is `timeout` or `cancelled`, and the step stays resumable.
- **Usage limits:** if Claude Code reports a usage or rate limit, the run becomes `WAITING_FOUNDER` with the reason. It is never retried automatically and never routed to another provider.
- **Output handling:** structured output is validated with the step's JSON Schema and then with pydantic. If validation fails, the core retries once with the validation errors appended. After that the step fails as `invalid_output`.

### 6.3 Fake executor

`executor/fake.py` replays scripted results per `(role, step kind)`. It can also edit files in a temporary project, to simulate implementation. All workflow tests use it. No test calls a real model.

## 7. Workflow engine

### 7.1 Principle

Phase 1 workflow rules move from skill text into code. The engine decides what happens next. Agents only produce:

- plan drafts;
- implementation work (file edits plus a structured summary that names the test commands);
- review drafts;
- meeting contributions;
- meeting syntheses;
- retrospective drafts.

### 7.2 The `run` loop

1. **Init or load.** Create the project from the goal, or load it. Refuse when the core does not own the project (§5.1).
2. **Plan** (if there are no open tasks). One `product-manager` call (plus an optional `system-architect` call) produces the PRD text and a plan draft. The core writes the PRD only if it is absent, imports the tasks, and auto-authorizes them under bounded autonomy. The Phase 2 "done when" checklist is checked in code: a PRD exists, at least one scoped task exists, and validation passes.
3. **Wave.** Select up to `max_parallel_tasks` READY tasks with disjoint scopes. This is the Phase 1 `next --parallel` rule, ported.
4. **Implement.** Move each task to IN_PROGRESS and make one owner-role call with the `implement` profile. In a wave, the calls run concurrently in the same working tree; their scopes are disjoint.
5. **Verify** (sequential, by the core).
   - Changed files come from `git status --porcelain` and `git diff --name-only` against the snapshot taken before the wave. A file outside the task's scope is a failed check.
   - The test commands come from the task's `verify` list (new optional task field) and the implementer's summary. The core runs them itself, as list arguments with a timeout, from the project root, and records `test_result` and `evidence`.
   - Agent-reported results are recorded as notes only and never count as evidence. This turns Phase 2 findings 1 and 3 into code.
   - A command must match the project's allowed test-command patterns (default: `node --test …`, `npm test`, `python -m pytest …`, `pytest …`). Other commands become a founder question and are not run.
6. **Review.** One independent `qa-engineer` call with the `review` profile produces a review draft. It is recorded with the Phase 1 rules: PASS is rejected with a confirmed critical or high finding. `security-reviewer` is added when the task touches auth, secrets, input parsing, or dependencies (keyword and path rules in code).
7. **Complete or fix.** With passed evidence and a PASS review, the task goes REVIEW → COMPLETED. Otherwise the findings become a task note and the owner gets a fix call. Each failed verification consumes a fix attempt (limit 3), as in Phase 1.
8. **Recovery.** When a task reaches FAILED, the core holds one recovery meeting (§7.4). A decision reopens the task. ESCALATED or NO_DECISION, or a second failure, sets the run to `WAITING_FOUNDER`.
9. **Repeat** from step 3 until no READY tasks remain. Then write the completion report (the Phase 1 `report` content plus agent calls and reported usage) and set the run to `COMPLETED`.

Founder consultation cases are the MASTER_SPEC §10.1 list: restricted actions, founder-required decisions, exhausted recovery, limit reached, and conflicting files. In each case the run goes to `WAITING_FOUNDER` with a clear question, and continues once the founder answers through the CLI or the API.

### 7.3 Checkpoints and resume

- Every step is a `workflow_step` row. The engine:
  1. writes the step as `PENDING`;
  2. marks it `RUNNING`;
  3. executes it;
  4. writes the result and marks it `DONE`, in the same transaction as the state change it causes.
- On start, the engine looks for `RUNNING` runs whose process is gone. It detects this with a PID plus start-time lock file, `.orkestr/core.lock`. For a dead run's `RUNNING` step:
  - **plan, review, meeting:** re-execute the step. Agent output only changes state after validation, so a re-run is safe.
  - **implement:** re-run the owner call with a "resumed after interruption; inspect the working tree first" note. Fix attempts are not consumed.
  - **verify:** re-run. Commands are side-effect-free tests.
- **Pause:** finish the current step, then stop. Running agent calls finish; nothing new starts. The project becomes `PAUSED`, as in Phase 1.
- **Resume:** continue from the next step.
- **Cancel:** kill running agent processes and mark the run and its open steps `CANCELLED`. Tasks stay in their current state, with a note, and the founder decides on them.

### 7.4 Meetings

Phase 1 limits apply: 2–4 participants and at most 2 rounds.

1. Round 1 is one call per participant. These run in parallel, because they are read-only.
2. A second round happens only if positions conflict. The rule in code: participants chose different options, or a high-severity concern is unresolved.
3. A synthesis call (`tech-lead`, or `system-architect` for architecture questions) produces the meeting draft from the recorded contributions.
4. The draft is recorded through the same validation as Phase 1, including `applies_to`, `recovery_for`, and `founder_required`.

Meetings are held only on the MASTER_SPEC §6.1 triggers. The engine does not hold a meeting for routine work.

## 8. Approvals and safety

- The Phase 1 approval rules are unchanged: approval categories, a single change to a `PENDING` approval, and `GRANTED` before execution.
- In Phase 3 the core executes **no restricted action itself**. For example, it never runs `git push`, deploys, or publishes. When the workflow needs one, it creates an approval request and stops at `WAITING_FOUNDER`. In Phase 3, carrying out a granted action stays a founder action outside the core. The founder records the result with `approval result`.
- Founder-only commands are the Phase 1 list. The API and CLI expose them as founder actions (§9). There is one local founder in Phase 3 and no user accounts.
- Repository files, agent output, and test output are untrusted data. They are never interpreted as instructions to the core.

## 9. Interfaces

### 9.1 CLI: `orkestr-core`

```
orkestr-core init --name <n> --goal <g> [--autonomy bounded]
orkestr-core import [--project <dir>]        # Phase 1 .orkestr/ → core.db; sets managed_by=core
orkestr-core export [--release]              # mirrors JSON + .md; --release hands back to the plugin
orkestr-core run [<goal>] [--max-agent-calls N] [--model M]
orkestr-core pause | resume | cancel [--reason R]
orkestr-core status | report | validate
orkestr-core approve <A-###|D-###> | reject <A-###|D-###> --reason R
orkestr-core retry <T-###>
orkestr-core serve [--port 8765]             # API on 127.0.0.1 only
```

### 9.2 HTTP API (FastAPI)

- Binds to `127.0.0.1` only. A different host requires a code change, not a flag.
- Every request needs the header `X-Orkestr-Token`. The token is a random value created at `init` and stored in `.orkestr/core-token`, which is git-ignored and never logged. This blocks other local web pages and DNS-rebinding attacks from driving the core.
- No CORS headers.
- Endpoints:
  - Reads:
    - `GET /status`, `/report`;
    - `GET /tasks`, `/tasks/{id}`;
    - `GET /meetings/{id}`, `/decisions/{id}`, `/approvals`;
    - `GET /runs`, `/runs/{id}`, `/runs/{id}/steps`;
    - `GET /history?after=<ts>`.
  - Founder actions:
    - `POST /approvals/{id}/grant`, `/approvals/{id}/reject`;
    - `POST /decisions/{id}/approve`, `/decisions/{id}/reject`;
    - `POST /tasks/{id}/authorize`, `/tasks/{id}/retry`.
  - Runs:
    - `POST /runs` starts a run in a background worker thread, at most one active run;
    - `POST /runs/{id}/pause`, `/runs/{id}/resume`, `/runs/{id}/cancel`.
- Responses are pydantic models. FastAPI's generated OpenAPI document is the API contract for Phase 4.

## 10. Limits

The Phase 1 limits (`PHASE1_IMPLEMENTATION.md` §7) apply unchanged. Limits that were only instructions in Phase 1 become code:

| Limit | Default | Enforcement in Phase 3 |
| :- | :- | :- |
| Agent calls per run | 30 | engine counter; reaching it → `WAITING_FOUNDER` |
| Agent calls per task | 8 | engine counter; reaching it → task note + `WAITING_FOUNDER` |
| Agent turns | agent `maxTurns` | passed to Claude Code; the core also enforces `timeout_s` |
| Agent call timeout | 900 s | core kills the process tree |
| Test command timeout | 600 s | core kills the process tree; counts as a failed check |
| Parallel implementation calls | `max_parallel_tasks` (2) | wave selection |
| Concurrent runs | 1 | `core.lock` plus a DB check |
| Reported cost per run | off | optional; only Claude Code's reported figure, which is an estimate |

## 11. Testing

- Use `pytest`, run from `core/` with `python -m pytest`. Tests use the fake executor and temporary directories with a local Git repo. They make no network calls and no model calls.
- Port the behavior of all 70 Phase 1 tests to the core's domain and store layers. Name each ported test after the Phase 1 test it covers, so coverage can be compared.
- New suites:
  - store: migrations, constraints, transaction rollback on error, audit append-only, concurrent writers;
  - interop:
    - import → export round-trip produces files that pass the Phase 1 validator (`node plugins/orkestr/scripts/orkestr.mjs validate`, when Node is available);
    - hash-conflict refusal;
    - `managed_by` hand-over;
  - redaction parity with the Node redactor on a shared corpus;
  - executor:
    - command-line construction, including the forbidden flags and the tool profiles;
    - parsing of recorded Claude Code JSON results (fixtures captured in step 4);
    - timeout and cancel with a dummy subprocess;
  - workflow, with the fake executor:
    - full run from goal to report;
    - parallel wave with disjoint scopes;
    - out-of-scope file → failed check;
    - agent-claimed tests that do not count;
    - fix limit → recovery meeting → reopen;
    - recovery exhausted → `WAITING_FOUNDER`;
    - founder-required decision;
    - approval stop;
    - agent-call limit;
    - usage-limit result;
    - invalid output, retried once;
    - kill mid-step → resume;
    - pause, resume, cancel;
  - API:
    - token required;
    - binding host;
    - founder actions;
    - one active run;
    - OpenAPI document generated.
  - budget rules:
    - no network imports outside the executor's subprocess call;
    - dependencies match the pinned list;
    - no forbidden Claude flags in the source.
- The Phase 1 suite (`node --test "plugins/orkestr/tests/*.test.mjs"`) must still pass. Phase 3 changes the plugin only where §12 says so.
- **Live smoke test (manual, needs founder consent because it uses Claude Code usage):** run the Phase 2 `split` protocol through `orkestr-core run` instead of `/orkestr:run`, then score it with `phase2/scorecard.mjs`. The result is recorded in `phase2/FINDINGS.md` as "Run 3 — standalone core". Behavior that only a live run can show is reported as **not tested** until then.

## 12. Plugin changes allowed in Phase 3

Keep these minimal and backward-compatible:

- `project.managed_by` (optional; absent means `plugin`). The plugin's state tool refuses writes when it is `core` and points to `orkestr-core export --release`.
- Optional task field `verify` (a list of exact test commands). The plugin accepts and shows it; the core uses it.
- Validator support for both fields, with tests.

## 13. Acceptance criteria

| # | Criterion | Check |
| :- | :- | :- |
| 1 | Core initializes a project and refuses to overwrite one | CLI and store tests |
| 2 | Phase 1 rules hold in the core (states, dependencies, authorization, evidence, limits, approvals, meetings, reviews) | ported Phase 1 tests |
| 3 | State is persisted in SQLite with migrations, constraints, and an append-only audit log | store tests |
| 4 | Phase 1 projects can be imported, and exports pass the Phase 1 validator | interop round-trip test |
| 5 | Agents run through an executor interface; Claude Code CLI is the first implementation; no forbidden flags; no paid fallback | executor tests; budget-rule tests; step 4 verification notes |
| 6 | Every agent output is schema-validated before it changes state | workflow invalid-output tests |
| 7 | The core runs test commands itself; agent claims are not evidence | workflow tests |
| 8 | Changed files are checked against task scopes using Git | workflow out-of-scope test |
| 9 | Goal → plan → parallel build → verify → review → fix → report works end to end | fake-executor end-to-end test; live smoke test |
| 10 | Runs resume after a killed process; pause, resume, and cancel work | workflow resume tests |
| 11 | Agent calls, turns, tokens, and reported cost are recorded per call and per run, and limits stop the run | workflow limit tests; `report` contents |
| 12 | Restricted actions only happen through granted approvals; headless agents cannot perform them | approval-stop test; tool-profile tests |
| 13 | A local, token-protected HTTP API exposes state, founder actions, and run control | API tests |
| 14 | Install, usage, limitations, and known failures are documented | `core/README.md` |

## 14. Work order

1. **Skeleton.**
   - Create `core/pyproject.toml` with pinned versions and a venv setup note.
   - Write `constants.py`.
   - Write the domain models and rules, and port the Phase 1 rule tests first.
   - Run them.
2. **Store.**
   - Write migration `0001`, the repositories, and the transactions, with store tests.
   - Add redaction and the parity tests.
3. **Interop.**
   - Write the importer and exporter.
   - Make the round-trip test pass against the Phase 1 validator.
   - Make the plugin changes in §12, with Node tests.
4. **Executor.**
   - Write the interface and the fake executor.
   - Write the Claude CLI executor.
   - **With founder consent, make 1–2 minimal real calls** to capture JSON result fixtures and verify the flags in §6.2. Record the findings.
5. **Workflow engine.** Write the steps, checkpoints, resume, limits, and meetings, all tested with the fake executor.
6. **API and CLI.**
7. **Docs.** Write `core/README.md` (English) and update the root README and MASTER_SPEC §20 links.
8. **Live smoke test** on the `split` sandbox, with founder consent.
9. **Report honestly:**
   - files created;
   - commands;
   - how to install and run;
   - tests executed and their results;
   - what was not tested;
   - known limitations;
   - next steps.

Each step is a separate commit on a branch (not `main`). The whole phase goes to `main` through a pull request after the founder reviews it.

## 15. Assumptions and open questions

- **Assumption:** one project per core database and one local founder, matching the current single-user usage.
- **Assumption:** the managed project is a Git repository. Scope verification needs Git. Without Git, the core refuses to run implementation steps and explains why. It does not fall back to an unverified mode.
- **Open (resolved in step 4):** whether `maxTurns` from `--agents` is honored in print mode. If it is not, the core relies on `timeout_s` and reports the gap.
- **Open (resolved in step 4):** the exact JSON shape of usage-limit failures. Until it is known, any `is_error` result whose text mentions a limit is treated as `usage_limit` (the safe side: stop, do not retry).
- **Open (founder):** whether the plugin should eventually call the core's API instead of its own state tool. This is not part of Phase 3. It is evaluated after the live smoke test.
