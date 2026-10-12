"""The run loop (PHASE3_IMPLEMENTATION.md §7).

The engine is state-driven: every iteration reloads the project and picks the next action
from the task states, so a run that was killed continues where the records say it stopped.
Each action is a `workflow_step` row; agent calls are `agent_execution` rows.
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from ..agents import prompts
from ..agents.definitions import AgentDefinition, load_agents
from ..agents.schemas import OUTPUTS, schema_for
from ..constants import AGENT_ROLES, AGENT_TIMEOUT_S, CORE_LIMITS, DEFAULT_LIMITS, LOCK_FILE, REPORTS_DIR, TEST_TIMEOUT_S
from ..domain import policy as pol
from ..domain import records as rec
from ..domain import state as st
from ..domain import team as tm
from ..domain.errors import OrkestrError, now
from ..domain.redact import redact
from ..domain.scope import file_in_scope
from ..executor.base import AgentExecutor, AgentRequest, AgentResult, CancelToken
from ..interop.files import write_atomic
from ..service import Project
from . import verify as vf
from .lock import RunLock, lock_holder_alive

SECURITY_TRIGGER = re.compile(
    r"auth|login|password|secret|token|credential|crypt|permission|session|upload|sql|inject|"
    r"dependenc|package\.json|requirements\.txt|pyproject",
    re.IGNORECASE,
)
APPROVAL_WAIT = "Waiting for approval "
PROTECTED_CHECK = "orkestr protected path check"
PATH_APPROVAL = "Change files under approval paths"


class StopRun(Exception):
    """Ends the loop with a run status (WAITING_FOUNDER, PAUSED, CANCELLED, FAILED)."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass
class CallOutcome:
    result: AgentResult
    output: dict | None
    error: str | None
    execution_id: int


class Engine:
    def __init__(
        self,
        project: Project,
        executor: AgentExecutor,
        agents: dict[str, AgentDefinition] | None = None,
        model: str | None = None,
        max_agent_calls: int | None = None,
        log: Callable[[str], None] | None = None,
        test_timeout_s: int = TEST_TIMEOUT_S,
        agent_timeout_s: int = AGENT_TIMEOUT_S,
    ):
        self.project = project
        self.store = project.store
        self.root = project.root
        self.executor = executor
        self.agents = agents or load_agents()
        self.model = model
        self.max_agent_calls_override = max_agent_calls
        self.log = log or (lambda msg: None)
        self.test_timeout_s = test_timeout_s
        self.agent_timeout_s = agent_timeout_s
        self.cancel = CancelToken()
        self.run_id: int | None = None
        self._calls_lock = threading.Lock()
        # Replaced by _load_settings when a run starts or continues.
        self.team = tm.default_team()
        self.policy = pol.empty_policy()
        self.policy_version = 0

    # ------------------------------------------------------------------
    # Run lifecycle

    def run(self, goal: str | None = None) -> dict:
        with RunLock(self.project.dir / LOCK_FILE):
            self.cancel = CancelToken()
            self._sync_agent_definitions()
            self.run_id = self._open_run(goal)
            goal = goal or self.store.query("SELECT goal FROM workflow_run WHERE id = ?", (self.run_id,))[0]["goal"]
            watcher = threading.Thread(target=self._watch_control, daemon=True)
            watcher.start()
            try:
                try:
                    self._load_settings()
                    self._loop(goal)
                    status, reason = self._final_status()
                except StopRun as stop:
                    status, reason = stop.status, stop.reason
                except OrkestrError as err:
                    status, reason = "FAILED", err.message
                except Exception as err:  # noqa: BLE001 - recorded on the run, then re-raised
                    self._close_run("FAILED", f"unexpected error: {err}", self._write_report())
                    raise
            finally:
                self.cancel.cancel()  # stops the watcher
            # An interruption (Ctrl+C, a killed process) skips this: the run stays RUNNING and
            # the next `orkestr-core run` continues it from the recorded steps.
            self._close_run(status, reason, self._write_report())
            self.log(f"Run {self.run_id} {status}{': ' + reason if reason else ''}")
            return self.store.query("SELECT * FROM workflow_run WHERE id = ?", (self.run_id,))[0]

    def _close_interrupted(self):
        """Any RUNNING run belongs to a process that is gone (we hold the lock now). Workflow
        runs are continued; other kinds (a retro) are closed as failed."""
        for r in self.store.query("SELECT id, kind FROM workflow_run WHERE status = 'RUNNING'"):
            self.store.execute(
                "UPDATE workflow_step SET status = 'FAILED', error = 'interrupted', ended_at = ? WHERE run_id = ? AND status IN ('RUNNING', 'PENDING')",
                (now(), r["id"]),
            )
            if r["kind"] == "run":
                self.log(f"Run {r['id']} was interrupted; continuing it.")
            else:
                self.store.execute("UPDATE workflow_run SET status = 'FAILED', status_reason = 'interrupted', ended_at = ? WHERE id = ?", (now(), r["id"]))

    def _open_run(self, goal: str | None) -> int:
        self._close_interrupted()
        latest = self.store.query(
            "SELECT * FROM workflow_run WHERE kind = 'run' AND status IN ('RUNNING', 'PAUSED', 'WAITING_FOUNDER') ORDER BY id DESC LIMIT 1"
        )
        options = {"model": self.model, "max_agent_calls": self.max_agent_calls_override}
        if latest and not goal:
            run_id = latest[0]["id"]
            self.store.execute(
                "UPDATE workflow_run SET status = 'RUNNING', status_reason = NULL, control = NULL, ended_at = NULL WHERE id = ?",
                (run_id,),
            )
            state = self.store.load()
            if state.project["status"] == "PAUSED":
                self.project.mutate(lambda s: st.set_project_status(s, "resume"), run_id=run_id)
            return run_id
        for r in latest:
            self.store.execute("UPDATE workflow_run SET status = 'CANCELLED', status_reason = 'superseded by a new run', ended_at = ? WHERE id = ?", (now(), r["id"]))
        return self.store.execute(
            "INSERT INTO workflow_run (kind, goal, status, options_json, started_at) VALUES ('run', ?, 'RUNNING', ?, ?)",
            (redact(goal) if goal else None, json.dumps(options), now()),
        )

    def _load_settings(self):
        """Team and policy in force from now until this invocation ends (PHASE4_IMPLEMENTATION.md
        §5.3, §6.4). Taken whenever a run starts or continues, never in the middle of a step, and
        recorded on the run with a history event."""
        self.team = self.store.team()
        p = self.store.policy()
        self.policy, self.policy_version = p["policy"], p["version"]
        options = json.loads(self.store.query("SELECT options_json FROM workflow_run WHERE id = ?", (self.run_id,))[0]["options_json"] or "{}")
        options["team"] = tm.differences(self.team)
        self.store.execute("UPDATE workflow_run SET options_json = ?, policy_version = ? WHERE id = ?", (json.dumps(options), self.policy_version, self.run_id))
        self.project.mutate(
            lambda s: st.emit(s, "run.settings", entity=f"run {self.run_id}", summary=f"policy v{self.policy_version}, team changes: {', '.join(options['team']) or 'none'}", data={"policy_version": self.policy_version, "team": options["team"]}),
            run_id=self.run_id,
            export=False,
        )
        if self.policy_version >= 1:
            self._apply_policy_limits()

    def _apply_policy_limits(self):
        """Policy limits replace the project limits; a limit left out of the policy is the
        Phase 3 default again (PHASE4_IMPLEMENTATION.md §13)."""
        state = self.store.load()
        current = state.project["limits"]
        wanted = {**current, **DEFAULT_LIMITS, **CORE_LIMITS, **(self.policy.get("limits") or {})}
        if "max_reported_cost_usd_per_run" not in (self.policy.get("limits") or {}):
            wanted.pop("max_reported_cost_usd_per_run", None)
        if wanted == current:
            return
        conflicts = pol.limit_conflicts(state.tasks, wanted)
        if conflicts:
            raise StopRun("WAITING_FOUNDER", f"policy v{self.policy_version} limits conflict with existing tasks: {'; '.join(conflicts)}")

        def apply(s):
            s.project["limits"] = wanted
            st.emit(s, "project.limits", entity="project", summary=f"Limits from policy v{self.policy_version}", data={"from": current, "to": wanted})

        self.project.mutate(apply, run_id=self.run_id)

    def retro(self, scope: str | None = None) -> dict:
        """One bounded retrospective call over the project history (PHASE4_IMPLEMENTATION.md §4,
        step 0.2). It is its own run (kind `retro`) and never continues or supersedes a workflow run.
        It ends COMPLETED, FAILED, or CANCELLED, so `run` never resumes it."""
        with RunLock(self.project.dir / LOCK_FILE):
            self.cancel = CancelToken()
            self._sync_agent_definitions()
            self._close_interrupted()
            options = {"model": self.model, "max_agent_calls": self.max_agent_calls_override}
            self.run_id = self.store.execute(
                "INSERT INTO workflow_run (kind, goal, status, options_json, started_at) VALUES ('retro', ?, 'RUNNING', ?, ?)",
                (redact(scope) if scope else None, json.dumps(options), now()),
            )
            watcher = threading.Thread(target=self._watch_control, daemon=True)
            watcher.start()
            retro_id = None
            try:
                try:
                    self._load_settings()
                    retro_id = self._retro(scope)
                    status, reason = "COMPLETED", None
                except StopRun as stop:
                    status = "CANCELLED" if stop.status == "CANCELLED" else "FAILED"
                    reason = stop.reason
                except OrkestrError as err:
                    status, reason = "FAILED", err.message
                except Exception as err:  # noqa: BLE001 - recorded on the run, then re-raised
                    self._close_run("FAILED", f"unexpected error: {err}", None)
                    raise
            finally:
                self.cancel.cancel()
            self._close_run(status, reason, None)
            self.log(f"Retro run {self.run_id} {status}{': ' + reason if reason else ''}")
            return {**self.store.query("SELECT * FROM workflow_run WHERE id = ?", (self.run_id,))[0], "retro_id": retro_id}

    def _retro(self, scope: str | None) -> str:
        state = self.store.load()
        scope = scope or f"{state.project['name']}: {state.project['goal']}"
        with self.step("retro", input_data={"scope": scope}) as s:
            self._reserve_calls([None])
            summary = retro_summary(state, self.store.history(), self.project.usage())
            req = self._request("retro", "tech-lead", prompts.retro_prompt(f"Scope: {scope}\n\n{summary}"), "read_only", None)
            out = self._execute(s["id"], req)
            if out.output is None:
                raise StopRun("FAILED", f"retrospective failed: {out.error}")
            draft = {**out.output, "scope": out.output.get("scope") or scope}
            retro = self.project.mutate(lambda s_: rec.record_retro(s_, draft), run_id=self.run_id)
            s["result"] = {"retro_id": retro["id"]}
            self.log(f"Recorded {retro['id']} with {len(retro['lessons'])} lesson(s)")
            return retro["id"]

    def _close_run(self, status: str, reason: str | None, report: str | None):
        self.store.execute(
            "UPDATE workflow_run SET status = ?, status_reason = ?, control = NULL, report_path = ?, ended_at = ? WHERE id = ?",
            (status, redact(reason) if reason else None, report, now(), self.run_id),
        )

    def _watch_control(self):
        """Polls the run's control flag so `cancel` stops running agent processes."""
        while not self.cancel.wait(1.0):
            try:
                rows = self.store.query("SELECT control FROM workflow_run WHERE id = ?", (self.run_id,))
            except Exception:  # noqa: BLE001 - the watcher must never crash the run
                continue
            if rows and rows[0]["control"] == "cancel":
                self.cancel.cancel()
                return

    def _check_control(self):
        row = self.store.query("SELECT control FROM workflow_run WHERE id = ?", (self.run_id,))[0]
        if row["control"] == "cancel" or self.cancel.cancelled:
            raise StopRun("CANCELLED", "cancelled by the founder")
        if row["control"] == "pause":
            state = self.store.load()
            if state.project["status"] == "ACTIVE":
                self.project.mutate(lambda s: st.set_project_status(s, "pause", reason="paused by the founder"), run_id=self.run_id)
            raise StopRun("PAUSED", "paused by the founder")

    def _sync_agent_definitions(self):
        for a in self.agents.values():
            self.store.execute(
                """INSERT INTO agent_definition VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(role) DO UPDATE SET source_path=excluded.source_path, sha256=excluded.sha256,
                     max_turns=excluded.max_turns, tools_json=excluded.tools_json, updated_at=excluded.updated_at""",
                (a.role, a.source_path, a.sha256, a.max_turns, json.dumps(list(a.tools)), now()),
            )

    # ------------------------------------------------------------------
    # Steps and agent calls

    def _start_step(self, kind: str, task_id: str | None, input_data: dict | None) -> int:
        seq = self.store.query("SELECT coalesce(max(seq), 0) + 1 AS n FROM workflow_step WHERE run_id = ?", (self.run_id,))[0]["n"]
        return self.store.execute(
            "INSERT INTO workflow_step (run_id, seq, kind, task_id, status, input_json, started_at) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?)",
            (self.run_id, seq, kind, task_id, json.dumps(redact(input_data or {})), now()),
        )

    def _end_step(self, step_id: int, status: str, result=None, error: str | None = None):
        self.store.execute(
            "UPDATE workflow_step SET status = ?, result_json = ?, error = ?, ended_at = ? WHERE id = ?",
            (status, json.dumps(redact(result)) if result is not None else None, redact(error) if error else None, now(), step_id),
        )

    @staticmethod
    def _error_text(err: BaseException) -> str:
        return f"{err.status}: {err.reason}" if isinstance(err, StopRun) else str(err)

    @contextmanager
    def step(self, kind: str, task_id: str | None = None, input_data: dict | None = None):
        holder: dict = {"id": self._start_step(kind, task_id, input_data), "result": None}
        try:
            yield holder
        except Exception as err:
            self._end_step(holder["id"], "FAILED", error=self._error_text(err))
            raise
        # An interruption (KeyboardInterrupt, SystemExit) leaves the step RUNNING on purpose:
        # the next run marks it interrupted and resumes it.
        else:
            self._end_step(holder["id"], "DONE", result=holder["result"])

    def _limit(self, key: str) -> int:
        if key == "max_agent_calls_per_run" and self.max_agent_calls_override:
            return self.max_agent_calls_override
        limits = self.store.load().project["limits"]
        return limits.get(key, CORE_LIMITS[key])

    def _reserve_calls(self, task_ids: list[str | None]):
        """Checks the per-run and per-task agent-call limits before calls are made."""
        used = self.store.query("SELECT agent_calls FROM workflow_run WHERE id = ?", (self.run_id,))[0]["agent_calls"]
        limit = self._limit("max_agent_calls_per_run")
        if used + len(task_ids) > limit:
            raise StopRun("WAITING_FOUNDER", f"agent-call limit reached ({used}/{limit} this run); raise it with --max-agent-calls or continue later")
        cost_limit = self.store.load().project["limits"].get("max_reported_cost_usd_per_run")
        if cost_limit:
            spent = self.store.query("SELECT coalesce(sum(reported_cost_usd), 0) AS c FROM agent_execution WHERE run_id = ?", (self.run_id,))[0]["c"]
            if spent >= cost_limit:
                raise StopRun("WAITING_FOUNDER", f"reported cost limit reached (${spent:.2f} of ${cost_limit:.2f}, as estimated by Claude Code)")
        per_task = self._limit("max_agent_calls_per_task")
        for tid in {t for t in task_ids if t}:
            n = self.store.query("SELECT count(*) AS n FROM agent_execution WHERE task_id = ?", (tid,))[0]["n"]
            if n + task_ids.count(tid) > per_task:
                self.project.mutate(
                    lambda s: st.add_note(s, tid, author="orchestrator", text=f"Agent-call limit for this task reached ({n}/{per_task}); the founder decides how to continue."),
                    run_id=self.run_id,
                )
                raise StopRun("WAITING_FOUNDER", f"{tid} reached its agent-call limit ({n}/{per_task})")

    def _request(self, kind: str, role: str, prompt: str, profile: str, task: dict | None) -> AgentRequest:
        agent = self.agents[role]
        row = self.team.get(role) or tm.default_role(role)
        return AgentRequest(
            role=role,
            kind=kind,
            prompt=prompt,
            system_prompt=tm.system_prompt(agent.prompt, row),
            cwd=str(self.root),
            tools=profile,  # type: ignore[arg-type]
            output_schema=schema_for(kind),
            scope=list(task.get("scope") or []) if task else [],
            max_turns=tm.effective_max_turns(row, agent.max_turns),
            timeout_s=self.agent_timeout_s,
            model=tm.effective_model(row, self.model),
            test_command_rules=vf.permission_rules(self._test_commands()),
            task_id=task["id"] if task else None,
            denied_paths=list(self.policy.get("protected_paths") or []),
        )

    def _test_commands(self) -> list[str]:
        return list(self.policy.get("test_commands") or [])

    def _enabled(self, role: str) -> bool:
        return (self.team.get(role) or tm.default_role(role))["enabled"]

    def _record_execution(self, step_id: int, req: AgentRequest, result: AgentResult, started: str, duration_ms: int) -> int:
        with self._calls_lock:
            exec_id = self.store.execute(
                """INSERT INTO agent_execution (run_id, step_id, role, task_id, executor, model, status, started_at, ended_at,
                     duration_ms, num_turns, input_tokens, output_tokens, reported_cost_usd, session_id, error, output_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.run_id,
                    step_id,
                    req.role,
                    req.task_id,
                    self.executor.name,
                    result.model or req.model,
                    result.status,
                    started,
                    now(),
                    duration_ms,
                    result.usage.num_turns,
                    result.usage.input_tokens,
                    result.usage.output_tokens,
                    result.usage.reported_cost_usd,
                    result.session_id,
                    redact(result.error) if result.error else None,
                    json.dumps(redact(result.output)) if result.output is not None else None,
                ),
            )
            self.store.execute("UPDATE workflow_run SET agent_calls = agent_calls + 1 WHERE id = ?", (self.run_id,))
        return exec_id

    def _execute(self, step_id: int, req: AgentRequest) -> CallOutcome:
        """One agent call plus at most one retry when the output does not match the schema."""
        model = OUTPUTS[req.kind]
        attempt_req = req
        for attempt in (1, 2):
            started, t0 = now(), time.monotonic()
            result = self.executor.run(attempt_req, self.cancel)
            output, error = None, None
            if result.status == "ok":
                try:
                    if result.output is None:
                        raise ValueError("the agent returned no JSON object")
                    output = model.model_validate(result.output).model_dump()
                except (ValidationError, ValueError) as err:
                    error = str(err)
                    result.status = "invalid_output"
                    result.error = error[:2000]
            else:
                error = result.error or result.status
            exec_id = self._record_execution(step_id, attempt_req, result, started, int((time.monotonic() - t0) * 1000))
            if result.status == "usage_limit":
                raise StopRun("WAITING_FOUNDER", f"Claude Code reported a usage limit: {result.error}. Continue later with `orkestr-core run`.")
            if result.status == "cancelled":
                raise StopRun("CANCELLED", "cancelled by the founder")
            if result.status != "invalid_output" or attempt == 2:
                return CallOutcome(result, output, error, exec_id)
            self._reserve_calls([req.task_id])
            attempt_req = AgentRequest(
                **{
                    **req.__dict__,
                    "prompt": req.prompt
                    + f"\n\nYour previous answer did not match the required JSON schema:\n{error[:1500]}\nReturn only a corrected JSON object.",
                }
            )
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------
    # The loop

    def _loop(self, goal: str | None):
        planned = bool(self.store.query("SELECT 1 FROM workflow_step WHERE run_id = ? AND kind = 'plan' AND status = 'DONE'", (self.run_id,)))
        while True:
            self._check_control()
            state = self.store.load()
            status = state.project["status"]
            if status == "PAUSED":
                raise StopRun("PAUSED", state.project.get("status_reason") or "project paused")
            if status in ("CANCELLED", "COMPLETED"):
                return

            if self._unblock_approved(state):
                continue
            open_tasks = [t for t in state.tasks if t["status"] not in st.TERMINAL]
            if not planned and (not state.tasks or (goal and not open_tasks)):
                self._plan(goal)
                planned = True
                continue
            if self._plan_decisions(state):
                continue
            self._check_team(state)

            pending_verify = self._pending_verify(state)
            if pending_verify:
                self._verify_wave(pending_verify["tasks"], pending_verify["snapshot"], pending_verify["outputs"])
                continue
            in_progress = [t for t in state.tasks if t["status"] == "IN_PROGRESS"]
            if in_progress:
                self._wave(in_progress, start=False)
                continue
            review = [t for t in state.tasks if t["status"] == "REVIEW"]
            if review:
                self._review(review[0])
                continue
            recovery = [t for t in state.tasks if self._needs_recovery(state, t)]
            if recovery:
                self._recovery_meeting(recovery[0])
                continue
            if self._request_path_approvals(state):
                continue
            wave = [t for t in st.parallel_candidates(state) if t["owner"] != "founder"]
            if wave:
                self._wave(wave, start=True)
                continue
            return

    def _final_status(self) -> tuple[str, str | None]:
        state = self.store.load()
        if state.project["status"] == "CANCELLED":
            return "CANCELLED", state.project.get("status_reason")
        needs = []
        needs += [f"approve or reject {a['id']} ({a['action']})" for a in state.approvals if a["status"] == "PENDING"]
        needs += [f"decide {d['id']} ({d['question']})" for d in state.decisions.values() if d["approval_status"] in ("PROPOSED", "NEEDS_FOUNDER")]
        needs += [f"{t['id']} FAILED: {t['failure']['next_action']}" for t in state.tasks if t["status"] == "FAILED"]
        needs += [f"{t['id']} BLOCKED: {t['blocked_reason']}" for t in state.tasks if t["status"] == "BLOCKED"]
        needs += [f"authorize {t['id']}" for t in state.tasks if not t["authorized"] and t["status"] not in st.TERMINAL]
        needs += [f"{t['id']} is the founder's task ({t['title']})" for t in state.tasks if t["owner"] == "founder" and t["status"] == "READY"]
        if needs:
            return "WAITING_FOUNDER", "; ".join(needs)
        if any(t["status"] not in st.TERMINAL for t in state.tasks):
            return "WAITING_FOUNDER", "open tasks remain but none can start"
        return "COMPLETED", None

    # ------------------------------------------------------------------
    # Plan

    def _plan(self, goal: str | None):
        state = self.store.load()
        prd = self.project.dir / "requirements" / "prd.md"
        project = dict(state.project)
        if goal:
            project["goal"] = f"{state.project['goal']}\nCurrent request: {goal}"
        role = "product-manager"
        with self.step("plan", input_data={"goal": goal}) as s:
            self._reserve_calls([None])
            owners = [r for r in tm.enabled_roles(self.team) if r in self.agents]
            prompt = prompts.plan_prompt(project, prd.exists(), vf.command_forms(self._test_commands()), owners, self.policy.get("protected_paths"))
            req = self._request("plan", role, prompt, "read_only", None)
            out = self._execute(s["id"], req)
            if out.output is None:
                raise StopRun("WAITING_FOUNDER", f"planning failed: {out.error}")
            problems = self._plan_problems(out.output)
            if problems:
                self._reserve_calls([None])
                req.prompt += "\n\nYour plan was rejected:\n- " + "\n- ".join(problems) + "\nReturn a corrected plan."
                out = self._execute(s["id"], req)
                problems = self._plan_problems(out.output) if out.output else [out.error or "no output"]
                if problems:
                    raise StopRun("WAITING_FOUNDER", "planning failed: " + "; ".join(problems))
            plan = out.output
            if not prd.exists():
                write_atomic(prd, plan["prd_markdown"].rstrip() + "\n")
                self.store.record_artifact("requirements/prd.md", "prd", prd.read_text(encoding="utf-8"), role)
            tasks = self.project.mutate(lambda s_: st.import_plan(s_, {"summary": plan["summary"], "tasks": plan["tasks"]}), run_id=self.run_id)
            decisions = [
                {
                    "key": f"plan-{s['id']}-{i + 1}",
                    "question": d["question"],
                    "options": d["options"],
                    "context": d.get("context") or "",
                    "task_ids": [tasks[int(ref[1:]) - 1]["id"] for ref in dict.fromkeys(d["blocks"])],
                }
                for i, d in enumerate(plan.get("decisions_needed") or [])
            ]
            s["result"] = {"task_ids": [t["id"] for t in tasks], "decisions": decisions}
            self.log(f"Planned {len(tasks)} task(s): {', '.join(t['id'] for t in tasks)}")
            for d in decisions:
                self.log(f"Open design choice before {', '.join(d['task_ids'])}: {d['question']}")

    def _plan_problems(self, plan: dict) -> list[str]:
        problems = []
        if not plan.get("prd_markdown", "").strip():
            problems.append("prd_markdown is empty")
        tasks = plan.get("tasks") or []
        if not any(t.get("scope") for t in tasks):
            problems.append("at least one task needs a file scope")
        for i, t in enumerate(tasks):
            for c in t.get("verify") or []:
                p = vf.command_problem(c, self._test_commands())
                if p:
                    problems.append(f"task #{i + 1} verify command {c!r}: {p}")
            if t.get("owner") in AGENT_ROLES and not self._enabled(t["owner"]):
                problems.append(f"task #{i + 1} owner {t['owner']} is not in this project's team (enabled: {', '.join(tm.enabled_roles(self.team))})")
            hit = pol.protected_overlap(t["scope"], self.policy) if t.get("scope") else []
            if hit and t.get("owner") != "founder":
                problems.append(f"task #{i + 1} scope may touch protected paths {', '.join(hit)}; narrow the scope")
        for i, d in enumerate(plan.get("decisions_needed") or []):
            for ref in d.get("blocks") or []:
                if not re.fullmatch(r"#[1-9]\d*", ref) or int(ref[1:]) > len(tasks):
                    problems.append(f"decisions_needed[{i + 1}] blocks {ref!r}: use \"#n\" for a task of this plan (1–{len(tasks)})")
        try:
            st.create_tasks(self.store.load(), tasks, {"type": "plan", "ref": None})  # a throwaway copy
        except OrkestrError as err:
            problems.append(err.message)
        return problems

    # ------------------------------------------------------------------
    # Implementation waves

    def _wave(self, tasks: list[dict], start: bool):
        vf.require_git(self.root)
        ids = [t["id"] for t in tasks]
        if start:
            def begin(s):
                for tid in ids:
                    st.transition(s, tid, "IN_PROGRESS")
            self.project.mutate(begin, run_id=self.run_id)
        state = self.store.load()
        tasks = [st.get_task(state, tid) for tid in ids]
        resumed = {t["id"]: self._interrupted_snapshot(t["id"]) for t in tasks}
        before = next((snap for snap in resumed.values() if snap is not None), None)
        if before is None:
            before = vf.snapshot(self.root)
        self._reserve_calls(ids)
        self.log(f"Implementing {', '.join(ids)}")

        requests: dict[str, AgentRequest] = {}
        for t in tasks:
            fix = self._fix_context(state, t)
            prompt = prompts.implement_prompt(t, fix, resumed[t["id"]] is not None, vf.command_forms(self._test_commands()))
            role = t["owner"] if t["owner"] in self.agents else "tech-lead"  # orchestrator-owned work goes to the tech lead
            requests[t["id"]] = self._request("implement", role, prompt, "implement", t)

        steps = {
            t["id"]: self._start_step("implement", t["id"], {"snapshot": before, "resumed": resumed[t["id"]] is not None})
            for t in tasks
        }
        outcomes: dict[str, CallOutcome] = {}
        errors: dict[str, BaseException] = {}
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = {tid: pool.submit(self._execute, steps[tid], requests[tid]) for tid in ids}
            for tid, fut in futures.items():
                try:
                    outcomes[tid] = fut.result()
                except BaseException as err:  # noqa: BLE001 - re-raised below, after every call has finished
                    errors[tid] = err
        interrupt = next((e for e in errors.values() if not isinstance(e, Exception)), None)
        if interrupt:
            raise interrupt  # steps stay RUNNING; the next run resumes them
        for tid in ids:
            if tid in errors:
                self._end_step(steps[tid], "FAILED", error=self._error_text(errors[tid]))
            else:
                o = outcomes[tid]
                self._end_step(steps[tid], "DONE", result={"status": o.result.status, "output": o.output, "error": o.error, "denials": o.result.denials})
        stops = [e for e in errors.values() if isinstance(e, StopRun)]
        if stops:
            raise stops[0]
        if errors:
            raise next(iter(errors.values()))

        verify_tasks = []
        for tid in ids:
            o = outcomes[tid]
            if o.output and o.output.get("restricted_action"):
                self._request_approval(tid, o.output["restricted_action"])
                continue
            if o.output and o.output.get("decision_needed"):
                self._decision_meeting(tid, o.output["decision_needed"])
                continue
            verify_tasks.append(tid)
        if verify_tasks:
            outputs = {tid: {**(outcomes[tid].output or {"_error": outcomes[tid].error}), "_denied": outcomes[tid].result.denials} for tid in verify_tasks}
            self._verify_wave(verify_tasks, before, outputs, wave_ids=ids)

    def _interrupted_snapshot(self, task_id: str) -> dict | None:
        rows = self.store.query(
            "SELECT status, error, input_json FROM workflow_step WHERE task_id = ? AND kind = 'implement' ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        if rows and rows[0]["status"] == "FAILED" and rows[0]["error"] == "interrupted":
            return json.loads(rows[0]["input_json"]).get("snapshot") or {}
        return None

    def _pending_verify(self, state: st.State) -> dict | None:
        """An implement step finished but its verify step never ran (the process died in between)."""
        tasks, snapshot, outputs = [], None, {}
        for t in state.tasks:
            if t["status"] != "IN_PROGRESS":
                continue
            rows = self.store.query(
                "SELECT kind, status, input_json, result_json FROM workflow_step WHERE task_id = ? AND kind IN ('implement', 'verify') ORDER BY id DESC LIMIT 1",
                (t["id"],),
            )
            if rows and rows[0]["kind"] == "implement" and rows[0]["status"] == "DONE":
                result = json.loads(rows[0]["result_json"] or "{}")
                output = result.get("output")
                if output and (output.get("restricted_action") or output.get("decision_needed")):
                    continue
                tasks.append(t["id"])
                snapshot = snapshot or json.loads(rows[0]["input_json"]).get("snapshot") or {}
                outputs[t["id"]] = output or {"_error": result.get("error")}
        return {"tasks": tasks, "snapshot": snapshot, "outputs": outputs} if tasks else None

    def _fix_context(self, state: st.State, task: dict) -> dict | None:
        failures = [e for e in task["evidence"] if e["result"] == "failed"]
        if not failures and not task["fix_attempts"]:
            return None
        recent = [f"- {e['type']} `{e.get('command') or ''}`: {e['summary']}" for e in failures[-4:]]
        results = self.store.query("SELECT command, output_tail FROM test_result WHERE task_id = ? ORDER BY id DESC LIMIT 2", (task["id"],))
        for r in results:
            recent.append(f"Output of `{r['command']}` (tail):\n{r['output_tail'][-1500:]}")
        return {"attempt": task["fix_attempts"], "max": state.project["limits"]["max_fix_attempts"], "details": "\n".join(recent)}

    def _verify_wave(self, task_ids: list[str], before: dict, outputs: dict[str, dict], wave_ids: list[str] | None = None):
        changed, reverted = vf.changed_since(self.root, before)
        state = self.store.load()
        wave = [st.get_task(state, tid) for tid in (wave_ids or task_ids)]
        unowned = [f for f in changed if not any(file_in_scope(f, t.get("scope") or []) for t in wave)]
        for tid in task_ids:
            self._check_control()
            with self.step("verify", task_id=tid, input_data={"changed": changed, "reverted": reverted}) as s:
                task = st.get_task(self.store.load(), tid)
                if task["status"] != "IN_PROGRESS":
                    s["result"] = {"skipped": task["status"]}
                    continue
                output = outputs.get(tid) or {}
                mine = [f for f in changed if file_in_scope(f, task.get("scope") or [])] if task.get("scope") else list(changed)
                files = sorted(set(mine) | set(unowned))
                notes = [f"Reverted: {', '.join(reverted)}"] if reverted else []
                if output.get("_error"):
                    self._evidence(tid, "check", "failed", "agent call", f"The implementation call did not return a valid result: {str(output['_error'])[:300]}")
                    s["result"] = {"agent_error": output["_error"]}
                    continue
                for n in output.get("notes") or []:
                    notes.append(n)
                if output.get("_denied"):
                    notes.append("Denied tool calls (not allowed for agents): " + "; ".join(output["_denied"][:10]))
                claimed = set(output.get("changed_files") or [])
                missing = sorted(f for f in claimed if f.replace("\\", "/") not in changed)
                if missing:
                    notes.append(f"Reported as changed but not changed on disk: {', '.join(missing)}")
                if notes:
                    self.project.mutate(lambda s_: st.add_note(s_, tid, author=task["owner"], text=" | ".join(notes)[:4000]), run_id=self.run_id)
                protected = pol.protected_files(files, self.policy)
                if protected:
                    self._evidence(tid, "check", "failed", PROTECTED_CHECK, f"Protected files changed (policy v{self.policy_version}): {', '.join(protected)}; revert them")
                    self.log(f"{tid}: protected files changed {', '.join(protected)}")
                    s["result"] = {"protected": protected}
                    continue
                if files:
                    r = self.project.mutate(lambda s_: st.scope_check(s_, files, tid), run_id=self.run_id)
                    if r["violations"]:
                        self.log(f"{tid}: out-of-scope changes {', '.join(v['file'] for v in r['violations'])}")
                        s["result"] = {"scope_violations": [v["file"] for v in r["violations"]]}
                        continue
                commands = list(dict.fromkeys([*(task.get("verify") or []), *(output.get("test_commands") or [])]))
                extra = self._test_commands()
                runnable = [c for c in commands if not vf.command_problem(c, extra)]
                rejected = [f"`{c}`: {vf.command_problem(c, extra)}" for c in commands if vf.command_problem(c, extra)]
                if rejected:
                    self.project.mutate(lambda s_: st.add_note(s_, tid, author="orchestrator", text="Not run: " + "; ".join(rejected)), run_id=self.run_id)
                if not runnable:
                    self._evidence(tid, "check", "failed", "verification", "No runnable verification command was given (allowed forms: " + ", ".join(vf.command_forms(extra)) + ")")
                    s["result"] = {"commands": []}
                    continue
                ran = []
                for c in runnable:
                    res = vf.run_test_command(self.root, c, self.test_timeout_s, self.cancel, extra)
                    trid = self.store.record_test_result(tid, c, res.exit_code, res.duration_ms, res.timed_out, res.output_tail, "core")
                    summary = (
                        f"exit {res.exit_code} in {res.duration_ms} ms"
                        if not res.error
                        else res.error
                    ) + ("" if res.passed else f"; tail: {res.output_tail[-300:].strip()}")
                    outcome = self._evidence(tid, "test", "passed" if res.passed else "failed", c, summary, test_result_id=trid)
                    ran.append({"command": c, "result": "passed" if res.passed else "failed"})
                    if not res.passed:
                        break
                s["result"] = {"commands": ran}
                task = st.get_task(self.store.load(), tid)
                if task["status"] == "IN_PROGRESS" and ran and all(r["result"] == "passed" for r in ran):
                    self.project.mutate(lambda s_: st.transition(s_, tid, "REVIEW"), run_id=self.run_id)
                    self.log(f"{tid}: checks passed; ready for review")
                elif task["status"] == "FAILED":
                    self.log(f"{tid}: FAILED after the fix-attempt limit")

    def _evidence(self, tid: str, type_: str, result: str, command: str, summary: str, test_result_id: int | None = None):
        return self.project.mutate(
            lambda s: st.add_evidence(s, tid, type=type_, result=result, command=command, summary=summary, test_result_id=test_result_id),
            run_id=self.run_id,
        )

    # ------------------------------------------------------------------
    # Review

    def _review(self, task: dict):
        tid = task["id"]
        reviews = [e for e in task["evidence"] if e["type"] == "review"]
        last_check = max((i for i, e in enumerate(task["evidence"]) if e["type"] in ("test", "check")), default=-1)
        last_review = max((i for i, e in enumerate(task["evidence"]) if e["type"] == "review"), default=-1)
        if reviews and last_review > last_check:
            # Already reviewed since the last checks: act on that verdict.
            self._after_review(tid, reviews[-1]["result"] == "passed", None)
            return
        reviewers = ["qa-engineer" if task["owner"] != "qa-engineer" else "tech-lead"]
        text = " ".join([task["title"], task.get("description") or "", *(task.get("scope") or [])])
        if (SECURITY_TRIGGER.search(text) or pol.security_review(task.get("scope"), self.policy)) and task["owner"] != "security-reviewer":
            reviewers.append("security-reviewer")
        checks = []
        for e in task["evidence"][last_review + 1 :]:
            if e["type"] == "test" and e["result"] != "not_run":
                checks.append({"command": e["command"], "result": e["result"], "summary": e["summary"]})
        changed = self._last_changed(tid)
        all_passed = True
        findings_notes = []
        for role in reviewers:
            self._check_control()
            with self.step("review", task_id=tid, input_data={"role": role}) as s:
                self._reserve_calls([tid])
                req = self._request("review", role, prompts.review_prompt(task, role, checks, changed), "review", task)
                out = self._execute(s["id"], req)
                if out.output is None:
                    draft = {"task_id": tid, "reviewers": [role], "scope": task["title"], "verdict": "BLOCKED", "checks_run": checks,
                             "findings": [{"title": "Review did not complete", "severity": "medium", "status": "confirmed", "evidence": (out.error or "no output")[:500]}],
                             "assumptions": []}
                else:
                    draft = {"task_id": tid, "reviewers": [role], "scope": task["title"], "verdict": out.output["verdict"], "checks_run": checks,
                             "findings": out.output["findings"], "assumptions": out.output["assumptions"]}
                try:
                    review = self.project.mutate(lambda s_: rec.record_review(s_, draft, check_evidence=False), run_id=self.run_id)
                except OrkestrError as err:
                    draft["verdict"] = "CHANGES_REQUESTED"
                    draft["findings"].append({"title": "Invalid review verdict", "severity": "medium", "status": "confirmed", "evidence": err.message[:500]})
                    review = self.project.mutate(lambda s_: rec.record_review(s_, draft, check_evidence=False), run_id=self.run_id)
                self.store.execute("UPDATE review SET agent_execution_id = ? WHERE id = ?", (out.execution_id, review["id"]))
                s["result"] = {"review": review["id"], "verdict": review["verdict"]}
                self.log(f"{tid}: {role} review {review['id']} {review['verdict']}")
                if review["verdict"] != "PASS":
                    all_passed = False
                    for f in review["findings"]:
                        findings_notes.append(f"[{f['severity']}/{f['status']}] {f['title']}: {f.get('recommendation') or f['evidence']}")
                    break
        self._after_review(tid, all_passed, findings_notes)

    def _after_review(self, tid: str, passed: bool, findings: list[str] | None):
        def apply(s):
            task = st.get_task(s, tid)
            if passed:
                st.transition(s, tid, "COMPLETED")
                return "COMPLETED"
            if findings:
                st.add_note(s, tid, author="qa-engineer", text="Review findings: " + " | ".join(findings)[:4000])
            if st.start_problems(s, task, [t for t in s.tasks if t["status"] == "IN_PROGRESS"]):
                return "WAIT"
            st.transition(s, tid, "IN_PROGRESS")
            return "IN_PROGRESS"

        try:
            outcome = self.project.mutate(apply, run_id=self.run_id)
        except OrkestrError as err:
            raise StopRun("WAITING_FOUNDER", f"{tid} cannot be completed: {err.message}") from None
        if outcome == "WAIT":
            raise StopRun("WAITING_FOUNDER", f"{tid} needs rework but cannot restart now")
        self.log(f"{tid}: {outcome}")

    def _last_changed(self, tid: str) -> list[str]:
        rows = self.store.query("SELECT input_json FROM workflow_step WHERE task_id = ? AND kind = 'verify' ORDER BY id DESC LIMIT 1", (tid,))
        return json.loads(rows[0]["input_json"]).get("changed", []) if rows else []

    # ------------------------------------------------------------------
    # Meetings

    def _needs_recovery(self, state: st.State, task: dict) -> bool:
        return (
            task["status"] == "FAILED"
            and st.autonomy_of(state) == "bounded"
            and task.get("recovery_meetings", 0) < st.max_recovery_meetings(state)
            and (task.get("failure") or {}).get("next_action", "").startswith("Recovery meeting required")
        )

    def _participants(self, owner: str) -> list[dict]:
        """The implementer's seat and a lead, both from the enabled team. A disabled or non-agent
        owner's seat goes to the tech lead (PHASE4_IMPLEMENTATION.md §5.2)."""
        if owner not in self.agents or not self._enabled(owner):
            owner = "backend-engineer" if owner not in AGENT_ROLES and self._enabled("backend-engineer") else "tech-lead"
        lead = "tech-lead" if owner != "tech-lead" else ("system-architect" if self._enabled("system-architect") else "qa-engineer")
        return [
            {"role": owner, "responsibility": "the implementer of the task"},
            {"role": lead, "responsibility": "technical direction and trade-offs"},
        ]

    def _meeting(self, meeting: dict, task_ids: list[str], recovery_for: str | None, key: str | None = None) -> dict:
        """Runs rounds and the synthesis, then records the meeting. Returns the record result."""
        limits = self.store.load().project["limits"]
        participants = meeting["participants"]
        contributions: list[dict] = []
        input_data = {"objective": meeting["objective"], **({"key": key} if key else {})}
        with self.step("meeting", task_id=task_ids[0] if task_ids else None, input_data=input_data) as s:
            rounds = []
            for round_no in range(1, limits["max_discussion_rounds"] + 1):
                self._check_control()
                self._reserve_calls([None] * len(participants))
                reqs = [
                    self._request(
                        "contribution",
                        p["role"],
                        prompts.contribution_prompt(meeting, p["role"], p["responsibility"], round_no, [c for c in contributions if c["round"] == 1]),
                        "read_only",
                        None,
                    )
                    for p in participants
                ]
                with ThreadPoolExecutor(max_workers=len(reqs)) as pool:
                    outs = list(pool.map(lambda r: self._execute(s["id"], r), reqs))
                this_round = []
                for p, o in zip(participants, outs):
                    out = o.output or {"position": f"(no contribution: {o.error})", "key_points": [], "concerns": [], "preferred_option": None, "high_severity_concern": False}
                    this_round.append({"round": round_no, "role": p["role"], **out, "agent_execution_id": o.execution_id})
                contributions += this_round
                rounds.append(
                    {
                        "round": round_no,
                        "contributions": [
                            {"role": c["role"], "position": c["position"], "key_points": c["key_points"], "concerns": c["concerns"]} for c in this_round
                        ],
                    }
                )
                preferred = {c["preferred_option"] for c in this_round if c.get("preferred_option")}
                if len(preferred) <= 1 and not any(c.get("high_severity_concern") for c in this_round):
                    break
            lead = participants[1]["role"]
            self._reserve_calls([None])
            synth_req = self._request("synthesis", lead, prompts.synthesis_prompt(meeting, contributions, task_ids, recovery_for is not None), "read_only", None)
            for attempt in (1, 2):
                out = self._execute(s["id"], synth_req)
                if out.output is None:
                    raise StopRun("WAITING_FOUNDER", f"meeting synthesis failed: {out.error}")
                syn = out.output
                draft = {
                    "objective": meeting["objective"],
                    "context": meeting["context"],
                    "trigger": meeting["trigger"],
                    "participants": participants,
                    "rounds": rounds,
                    "options": syn["options"],
                    "criteria": syn["criteria"],
                    "evaluation": syn["evaluation"],
                    "outcome": syn["outcome"],
                    "decision": syn["decision"],
                    "dissent": syn["dissent"],
                    "open_questions": syn["open_questions"],
                    "action_items": [{k: v for k, v in a.items() if not (k == "verify" and not v)} for a in syn["action_items"]],
                }
                if recovery_for:
                    draft["recovery_for"] = recovery_for
                elif task_ids:
                    draft["applies_to"] = task_ids
                try:
                    self._check_action_items(draft["action_items"])
                    result = self.project.mutate(lambda s_: rec.record_meeting(s_, draft), run_id=self.run_id)
                    break
                except OrkestrError as err:
                    if attempt == 2:
                        raise StopRun("WAITING_FOUNDER", f"meeting could not be recorded: {err.message}") from None
                    self._reserve_calls([None])
                    synth_req.prompt += f"\n\nThe draft was rejected by the state rules:\n{err.message}\nReturn a corrected synthesis."
            s["result"] = {"meeting": result["meeting"]["id"], "decision": (result["decision"] or {}).get("id")}
        d = result["decision"]
        self.log(f"Meeting {result['meeting']['id']}: {result['meeting']['outcome']}" + (f", {d['id']} {d['approval_status']}" if d else ""))
        if d and d["approval_status"] in ("PROPOSED", "NEEDS_FOUNDER"):
            raise StopRun("WAITING_FOUNDER", f"decide {d['id']}: {d['question']}")
        return result

    def _recovery_meeting(self, task: dict):
        meeting = {
            "objective": f"Choose a new approach for {task['id']} ({task['title']})",
            "context": f"{task['id']} failed after the fix-attempt limit: {(task.get('failure') or {}).get('error')}",
            "trigger": "Recovery meeting after the fix-attempt limit (MASTER_SPEC §12.1)",
            "participants": self._participants(task["owner"]),
            "options": [],
        }
        result = self._meeting(meeting, [task["id"]], recovery_for=task["id"])
        if st.get_task(self.store.load(), task["id"])["status"] == "FAILED":
            raise StopRun("WAITING_FOUNDER", f"{task['id']} could not be recovered by meeting {result['meeting']['id']}")

    def _decision_meeting(self, tid: str, need: dict):
        task = st.get_task(self.store.load(), tid)
        meeting = {
            "objective": need["question"],
            "context": f"Raised by {task['owner']} while implementing {tid} ({task['title']}).",
            "trigger": "A design choice blocks the implementer and has trade-offs (MASTER_SPEC §6.1)",
            "participants": self._participants(task["owner"]),
            "options": [{"id": f"O{i + 1}", "name": o, "summary": ""} for i, o in enumerate(need["options"])],
        }
        self._meeting(meeting, [tid], recovery_for=None)

    def _plan_decisions(self, state: st.State) -> bool:
        """Design choices the plan left open are settled in a meeting before the tasks they block
        start. Each one is held once per run (found again by its key after a restart). A decision
        that waits for the founder stops the run; no decision or a rejected one lets the tasks go
        ahead, and an implementer can still raise the choice again with decision_needed."""
        rows = self.store.query("SELECT result_json FROM workflow_step WHERE run_id = ? AND kind = 'plan' AND status = 'DONE' ORDER BY id", (self.run_id,))
        held = {}
        for m in self.store.query("SELECT input_json, result_json FROM workflow_step WHERE run_id = ? AND kind = 'meeting' AND status = 'DONE'", (self.run_id,)):
            key = json.loads(m["input_json"] or "{}").get("key")
            if key:
                held[key] = json.loads(m["result_json"] or "{}")
        for row in rows:
            for need in json.loads(row["result_json"] or "{}").get("decisions") or []:
                open_ids = [tid for tid in need["task_ids"] if (t := st.find_task(state, tid)) and t["status"] not in st.TERMINAL]
                if not open_ids:
                    continue
                if need["key"] not in held:
                    self._plan_decision_meeting(need, open_ids, state)
                    return True
                d = state.decisions.get(held[need["key"]].get("decision") or "")
                if d and d["approval_status"] in ("PROPOSED", "NEEDS_FOUNDER"):
                    raise StopRun("WAITING_FOUNDER", f"decide {d['id']}: {d['question']}")
        return False

    def _plan_decision_meeting(self, need: dict, task_ids: list[str], state: st.State):
        owner = st.get_task(state, task_ids[0])["owner"]
        meeting = {
            "objective": need["question"],
            "context": (need["context"] + " " if need["context"] else "") + f"The plan left this open; {', '.join(task_ids)} wait for it.",
            "trigger": "A design choice the plan left open blocks tasks (MASTER_SPEC §6.1)",
            "participants": self._participants(owner),
            "options": [{"id": f"O{i + 1}", "name": o, "summary": ""} for i, o in enumerate(need["options"])],
        }
        self._meeting(meeting, task_ids, recovery_for=None, key=need["key"])

    # ------------------------------------------------------------------
    # Approvals

    def _request_approval(self, tid: str, action: dict):
        def apply(s):
            a = st.request_approval(s, action=action["action"], category=action["category"], reason=action["reason"], task_id=tid, requested_by=st.get_task(s, tid)["owner"])
            st.transition(s, tid, "BLOCKED", reason=f"{APPROVAL_WAIT}{a['id']}")
            return a

        a = self.project.mutate(apply, run_id=self.run_id)
        self.log(f"{tid}: needs founder approval {a['id']} ({a['action']})")

    def _check_action_items(self, items: list[dict]):
        """Meeting action items follow the same team and policy rules as plan tasks."""
        problems = []
        for i, a in enumerate(items):
            if a.get("owner") in AGENT_ROLES and not self._enabled(a["owner"]):
                problems.append(f"action item {i + 1} owner {a['owner']} is not in this project's team")
            hit = pol.protected_overlap(a["scope"], self.policy) if a.get("scope") else []
            if hit and a.get("owner") != "founder":
                problems.append(f"action item {i + 1} scope may touch protected paths {', '.join(hit)}")
        if problems:
            raise OrkestrError("; ".join(problems))

    def _check_team(self, state: st.State):
        """Open tasks owned by a disabled role are not reassigned silently (§5.2)."""
        orphans = [t for t in state.tasks if t["status"] not in st.TERMINAL and t["owner"] in AGENT_ROLES and not self._enabled(t["owner"])]
        if orphans:
            names = ", ".join(f"{t['id']} ({t['owner']})" for t in orphans)
            raise StopRun("WAITING_FOUNDER", f"tasks owned by roles that are disabled in the team: {names}; re-enable the role or change the tasks")

    def _request_path_approvals(self, state: st.State) -> bool:
        """READY tasks whose scope touches the policy's approval paths ask the founder first (§6.2)."""
        asked = False
        for t in state.tasks:
            if t["status"] != "READY" or t["owner"] == "founder" or not pol.needs_approval(t.get("scope"), self.policy):
                continue
            if any(a.get("task_id") == t["id"] and a["action"].startswith(PATH_APPROVAL) for a in state.approvals):
                continue  # already asked; a granted one lets the task start, a rejected one keeps it blocked
            paths = ", ".join(self.policy["approval_paths"])
            self._request_approval(
                t["id"],
                {"action": f"{PATH_APPROVAL} ({paths}) for {t['id']}: {t['title']}", "category": "other", "reason": f"Policy v{self.policy_version} requires founder approval for changes under {paths}"},
            )
            asked = True
        return asked

    def _unblock_approved(self, state: st.State) -> bool:
        ready = []
        for t in state.tasks:
            reason = t.get("blocked_reason") or ""
            if t["status"] == "BLOCKED" and reason.startswith(APPROVAL_WAIT):
                a = next((x for x in state.approvals if x["id"] == t.get("approval_id")), None)
                if a and a["status"] == "GRANTED" and not st.readiness_problems(state, t):
                    ready.append((t["id"], a["id"]))
        if not ready:
            return False

        def apply(s):
            for tid, aid in ready:
                st.transition(s, tid, "READY", reason=f"{aid} granted")
                st.add_note(s, tid, author="orchestrator", text=f"{aid} was granted. The founder carries out the approved action; continue with the rest of the task.")

        with self.step("unblock", input_data={"tasks": [t for t, _ in ready]}):
            self.project.mutate(apply, run_id=self.run_id)
        return True

    # ------------------------------------------------------------------
    # Report

    def _write_report(self) -> str | None:
        try:
            text = self.project.report_text(run_id=self.run_id)
        except Exception:  # noqa: BLE001 - a failed report must not hide the run result
            return None
        rel = f"{REPORTS_DIR}/run-{self.run_id:03d}.md"
        write_atomic(self.project.dir / rel, text)
        self.store.record_artifact(rel, "report", text, "orchestrator")
        return rel


RETRO_HISTORY_EVENTS = 80
RETRO_SUMMARY_CHARS = 24_000


def retro_summary(state: st.State, history: list[dict], usage: dict) -> str:
    """The evidence a retrospective works from: records and recent history, never file contents."""
    p = state.project
    lines = [f"Project: {p['name']} — status {p['status']}", f"Goal: {p['goal']}", "", "Tasks:"]
    for t in state.tasks:
        lines.append(f"- {t['id']} [{t['status']}] {t['title']} (owner {t['owner']}, fix attempts {t['fix_attempts']}, recovery meetings {t.get('recovery_meetings', 0)})")
        for e in t["evidence"]:
            lines.append(f"  - evidence {e['type']}/{e['result']}: {e.get('command') or ''} — {e['summary']}"[:400])
        if t.get("failure"):
            lines.append(f"  - failure: {(t['failure'] or {}).get('error')}"[:400])
    sections = {
        "Reviews": [f"- {r['id']} on {r.get('task_id')}: {r.get('verdict')} ({len(r.get('findings') or [])} finding(s))" for r in state.reviews.values()],
        "Meetings": [f"- {m['id']} ({m.get('trigger')}): {m.get('objective')} → {m.get('outcome')}" for m in state.meetings.values()],
        "Decisions": [f"- {d['id']} [{d['approval_status']}] {d['question']}" for d in state.decisions.values()],
        "Assumptions": [f"- [{a.get('impact')}] {a['text']}" for a in p.get("assumptions") or []],
    }
    for title, items in sections.items():
        lines += ["", f"{title}:", *(items or ["- (none)"])]
    lines += [
        "",
        f"Agent usage: {usage['calls']} call(s), {usage['failed']} failed, {usage['num_turns']} turn(s), "
        f"{usage['input_tokens']} input and {usage['output_tokens']} output token(s)",
        "",
        f"History (last {RETRO_HISTORY_EVENTS} events):",
    ]
    lines += [f"- {h['ts']} {h['type']} {h.get('entity') or ''} {h.get('summary') or ''}"[:300] for h in history[-RETRO_HISTORY_EVENTS:]]
    text = "\n".join(lines)
    return text if len(text) <= RETRO_SUMMARY_CHARS else text[:RETRO_SUMMARY_CHARS] + "\n(truncated)"


def request_control(project: Project, action: str, reason: str | None = None) -> str:
    """pause/cancel from another process. Without a live engine, the change is applied directly."""
    rows = project.store.query("SELECT * FROM workflow_run WHERE status IN ('RUNNING', 'PAUSED', 'WAITING_FOUNDER') ORDER BY id DESC LIMIT 1")
    alive = lock_holder_alive(project.dir / LOCK_FILE)
    if action == "pause":
        if rows and alive and rows[0]["status"] == "RUNNING":
            project.store.execute("UPDATE workflow_run SET control = 'pause' WHERE id = ?", (rows[0]["id"],))
            return f"Run {rows[0]['id']} will pause after its current step."
        project.mutate(lambda s: st.set_project_status(s, "pause", reason=reason or "paused by the founder"))
        if rows:
            project.store.execute("UPDATE workflow_run SET status = 'PAUSED', status_reason = ? WHERE id = ?", (reason or "paused by the founder", rows[0]["id"]))
        return "Project paused."
    if action == "cancel":
        if rows and alive and rows[0]["status"] == "RUNNING":
            project.store.execute("UPDATE workflow_run SET control = 'cancel' WHERE id = ?", (rows[0]["id"],))
            return f"Run {rows[0]['id']} is being cancelled; running agent calls are stopped."
        if not rows:
            raise OrkestrError("There is no open run to cancel. To cancel the whole project, use `orkestr-core cancel --project`.")
        project.store.execute(
            "UPDATE workflow_run SET status = 'CANCELLED', status_reason = ?, ended_at = ? WHERE id = ?",
            (reason or "cancelled by the founder", now(), rows[0]["id"]),
        )
        project.store.execute("UPDATE workflow_step SET status = 'SKIPPED', ended_at = ? WHERE run_id = ? AND status IN ('PENDING', 'RUNNING')", (now(), rows[0]["id"]))
        return f"Run {rows[0]['id']} cancelled. Tasks keep their current state."
    raise OrkestrError(f"Unknown control action: {action}")
