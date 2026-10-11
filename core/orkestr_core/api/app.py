"""FastAPI app. Binds to 127.0.0.1 only and requires the per-project token in
`X-Orkestr-Token` on every request (PHASE3_IMPLEMENTATION.md §9.2). No CORS headers."""

from __future__ import annotations

import secrets
import threading
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from ..constants import LOCK_FILE
from ..domain.errors import OrkestrError
from ..domain.state import find_task
from ..service import Project
from ..workflow.lock import lock_holder_alive

HOST = "127.0.0.1"  # A different host needs a code change, not a flag.


class Message(BaseModel):
    message: str


class TextReport(BaseModel):
    markdown: str


class Evidence(BaseModel):
    type: str
    result: str
    command: str | None = None
    summary: str
    ref: str | None = None
    at: str
    test_result_id: int | None = None


class Note(BaseModel):
    author: str
    text: str
    at: str


class Task(BaseModel):
    id: str
    title: str
    description: str
    owner: str
    status: str
    depends_on: list[str]
    acceptance_criteria: list[str]
    scope: list[str]
    verify: list[str] = []
    requires_approval: bool
    approval_id: str | None
    authorized: bool
    source: dict[str, Any]
    decision_id: str | None
    blocked_reason: str | None
    failure: dict[str, Any] | None
    fix_attempts: int
    recovery_meetings: int
    evidence: list[Evidence]
    notes: list[Note]
    created_at: str
    updated_at: str


class Approval(BaseModel):
    id: str
    action: str
    category: str
    reason: str
    consequences: str
    risks: str
    requested_by: str
    task_id: str | None
    status: str
    decided_by: str | None
    decided_at: str | None
    decision_note: str | None
    execution_result: str | None
    created_at: str


class Run(BaseModel):
    id: int
    kind: str
    goal: str | None
    status: str
    status_reason: str | None
    control: str | None
    agent_calls: int
    report_path: str | None
    started_at: str
    ended_at: str | None


class Step(BaseModel):
    id: int
    seq: int
    kind: str
    task_id: str | None
    status: str
    error: str | None
    started_at: str | None
    ended_at: str | None


class Event(BaseModel):
    ts: str
    type: str
    actor: str | None
    entity: str | None
    from_: str | None = None
    to: str | None
    summary: str
    data: Any = None


class DecideBody(BaseModel):
    note: str | None = None
    option: str | None = None


class RunBody(BaseModel):
    goal: str | None = None
    max_agent_calls: int | None = None
    model: str | None = None


def create_app(project: Project, engine_factory: Callable[[RunBody], Any] | None = None) -> FastAPI:
    token = project.token()
    app = FastAPI(title="Orkestr core", version="0.1.0", description="Local API of the Orkestr standalone core.")
    worker: dict[str, threading.Thread | None] = {"thread": None}

    def auth(x_orkestr_token: str | None = Header(default=None)):
        if not x_orkestr_token or not secrets.compare_digest(x_orkestr_token, token):
            raise HTTPException(status_code=401, detail="missing or wrong X-Orkestr-Token")

    def guarded(fn):
        try:
            return fn()
        except OrkestrError as err:
            status = 404 if err.code == "E_NOT_FOUND" else 409
            raise HTTPException(status_code=status, detail=err.message) from None

    deps = [Depends(auth)]

    @app.get("/status", response_model=TextReport, dependencies=deps)
    def status():
        return {"markdown": project.status_text()}

    @app.get("/report", response_model=TextReport, dependencies=deps)
    def report():
        return {"markdown": project.report_text()}

    @app.get("/tasks", response_model=list[Task], dependencies=deps)
    def tasks():
        return project.store.load().tasks

    @app.get("/tasks/{task_id}", response_model=Task, dependencies=deps)
    def task(task_id: str):
        t = find_task(project.store.load(), task_id)
        if not t:
            raise HTTPException(status_code=404, detail=f"Unknown task: {task_id}")
        return t

    @app.get("/meetings/{meeting_id}", response_model=dict[str, Any], dependencies=deps)
    def meeting(meeting_id: str):
        m = project.store.load().meetings.get(meeting_id)
        if not m:
            raise HTTPException(status_code=404, detail=f"Unknown meeting: {meeting_id}")
        return m

    @app.get("/decisions/{decision_id}", response_model=dict[str, Any], dependencies=deps)
    def decision(decision_id: str):
        d = project.store.load().decisions.get(decision_id)
        if not d:
            raise HTTPException(status_code=404, detail=f"Unknown decision: {decision_id}")
        return d

    @app.get("/approvals", response_model=list[Approval], dependencies=deps)
    def approvals():
        return project.store.load().approvals

    @app.get("/runs", response_model=list[Run], dependencies=deps)
    def runs():
        return project.store.query("SELECT * FROM workflow_run ORDER BY id DESC")

    @app.get("/runs/{run_id}", response_model=Run, dependencies=deps)
    def run(run_id: int):
        rows = project.store.query("SELECT * FROM workflow_run WHERE id = ?", (run_id,))
        if not rows:
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
        return rows[0]

    @app.get("/runs/{run_id}/steps", response_model=list[Step], dependencies=deps)
    def steps(run_id: int):
        return project.store.query("SELECT * FROM workflow_step WHERE run_id = ? ORDER BY seq", (run_id,))

    @app.get("/history", response_model=list[Event], dependencies=deps)
    def history(after: str | None = Query(default=None)):
        return [{**e, "from_": e["from"]} for e in project.store.history(after=after)]

    @app.post("/approvals/{approval_id}/grant", response_model=Message, dependencies=deps)
    def grant(approval_id: str, body: DecideBody | None = None):
        a = guarded(lambda: project.decide_approval(approval_id, "grant", (body or DecideBody()).note))
        return {"message": f"{a['id']} is now {a['status']}."}

    @app.post("/approvals/{approval_id}/reject", response_model=Message, dependencies=deps)
    def reject(approval_id: str, body: DecideBody | None = None):
        a = guarded(lambda: project.decide_approval(approval_id, "reject", (body or DecideBody()).note))
        return {"message": f"{a['id']} is now {a['status']}."}

    @app.post("/decisions/{decision_id}/approve", response_model=Message, dependencies=deps)
    def approve_decision(decision_id: str, body: DecideBody | None = None):
        b = body or DecideBody()
        r = guarded(lambda: project.decide_decision(decision_id, "approve", option=b.option, note=b.note))
        return {"message": f"{decision_id} is now {r['decision']['approval_status']}."}

    @app.post("/decisions/{decision_id}/reject", response_model=Message, dependencies=deps)
    def reject_decision(decision_id: str, body: DecideBody):
        r = guarded(lambda: project.decide_decision(decision_id, "reject", note=body.note))
        return {"message": f"{decision_id} is now {r['decision']['approval_status']}."}

    @app.post("/tasks/{task_id}/authorize", response_model=Message, dependencies=deps)
    def authorize(task_id: str):
        guarded(lambda: project.authorize_task(task_id))
        return {"message": f"Authorized {task_id}."}

    @app.post("/tasks/{task_id}/retry", response_model=Message, dependencies=deps)
    def retry(task_id: str):
        guarded(lambda: project.retry_task(task_id))
        return {"message": f"{task_id} is READY again."}

    def busy() -> bool:
        t = worker["thread"]
        return bool(t and t.is_alive()) or lock_holder_alive(project.dir / LOCK_FILE)

    @app.post("/runs", response_model=Message, status_code=202, dependencies=deps)
    def start_run(body: RunBody | None = None):
        if engine_factory is None:
            raise HTTPException(status_code=501, detail="this server was started without an executor")
        if busy():
            raise HTTPException(status_code=409, detail="a run is already active")
        b = body or RunBody()
        engine = engine_factory(b)
        t = threading.Thread(target=lambda: engine.run(b.goal), daemon=True, name="orkestr-run")
        worker["thread"] = t
        t.start()
        return {"message": "run started"}

    def control(run_id: int, action: str):
        from ..workflow.engine import request_control

        rows = project.store.query("SELECT id FROM workflow_run WHERE id = ?", (run_id,))
        if not rows:
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
        return {"message": guarded(lambda: request_control(project, action))}

    @app.post("/runs/{run_id}/pause", response_model=Message, dependencies=deps)
    def pause(run_id: int):
        return control(run_id, "pause")

    @app.post("/runs/{run_id}/cancel", response_model=Message, dependencies=deps)
    def cancel(run_id: int):
        return control(run_id, "cancel")

    @app.post("/runs/{run_id}/resume", response_model=Message, status_code=202, dependencies=deps)
    def resume(run_id: int):
        from ..domain import state as st

        if busy():
            raise HTTPException(status_code=409, detail="a run is already active")
        if project.store.load().project["status"] == "PAUSED":
            guarded(lambda: project.mutate(lambda s: st.set_project_status(s, "resume")))
        return start_run(RunBody())

    return app


def serve(project: Project, port: int = 8765):
    import uvicorn

    from ..executor.claude_cli import ClaudeCliExecutor
    from ..workflow.engine import Engine

    def factory(body: RunBody):
        return Engine(project, ClaudeCliExecutor(), model=body.model, max_agent_calls=body.max_agent_calls)

    print(f"Orkestr API on http://{HOST}:{port} — send the token from .orkestr/core-token in X-Orkestr-Token.")
    uvicorn.run(create_app(project, factory), host=HOST, port=port, log_level="warning")
