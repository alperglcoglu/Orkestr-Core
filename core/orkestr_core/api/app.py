"""FastAPI app. Binds to 127.0.0.1 only and requires the per-project token in
`X-Orkestr-Token` on every data or action request (PHASE3_IMPLEMENTATION.md §9.2). No CORS
headers. It also serves the founder dashboard (PHASE4_IMPLEMENTATION.md §7): static files
that hold no project data, behind a strict Content-Security-Policy and a Host check."""

from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

from ..constants import LOCK_FILE
from ..domain import state as st
from ..domain.errors import OrkestrError
from ..domain.state import find_task
from ..service import Project
from ..workflow.lock import lock_holder_alive

HOST = "127.0.0.1"  # A different host needs a code change, not a flag.
DEFAULT_PORT = 8765
UI_DIR = Path(__file__).resolve().parent.parent / "ui"
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


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


class RetroBody(BaseModel):
    scope: str | None = None
    model: str | None = None


class NeedItem(BaseModel):
    kind: str
    id: str
    text: str


class ProjectInfo(BaseModel):
    name: str
    goal: str
    status: str
    status_reason: str | None
    autonomy: str
    managed_by: str
    limits: dict[str, Any]
    constraints: list[str]
    success_criteria: list[str]
    latest_run: Run | None
    active: bool
    usage: dict[str, Any]
    needs_founder: list[NeedItem]
    policy_version: int


class GraphNode(BaseModel):
    id: str
    title: str
    owner: str
    status: str
    depends_on: list[str]
    scope: list[str]
    blocked_reason: str | None


class Graph(BaseModel):
    nodes: list[GraphNode]
    can_start_now: list[str]


class Execution(BaseModel):
    id: int
    step_id: int | None
    role: str
    task_id: str | None
    executor: str
    model: str | None
    status: str
    started_at: str
    ended_at: str
    duration_ms: int
    num_turns: int | None
    input_tokens: int | None
    output_tokens: int | None
    reported_cost_usd: float | None
    error: str | None


class TestResult(BaseModel):
    id: int
    command: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool
    output_tail: str
    run_by: str
    at: str


class TeamRole(BaseModel):
    role: str
    enabled: bool
    model: str | None
    max_turns: int | None
    instructions: str | None
    definition_max_turns: int | None = None
    required: bool = False


class TeamChange(BaseModel):
    enabled: bool | None = None
    model: str | None = None
    max_turns: int | None = None
    instructions: str | None = None


class PolicyVersion(BaseModel):
    version: int
    policy: dict[str, Any]
    reason: str
    created_at: str
    created_by: str


class PolicyBody(BaseModel):
    policy: dict[str, Any]
    reason: str


class AutonomyBody(BaseModel):
    mode: str
    reason: str


def create_app(project: Project, engine_factory: Callable[[RunBody], Any] | None = None, port: int = DEFAULT_PORT) -> FastAPI:
    token = project.token()
    app = FastAPI(title="Orkestr core", version="0.2.0", description="Local API and dashboard of the Orkestr standalone core.")
    worker: dict[str, threading.Thread | None] = {"thread": None}
    allowed_hosts = {f"{HOST}:{port}", f"localhost:{port}"}

    @app.middleware("http")
    async def host_and_headers(request: Request, call_next):
        # DNS-rebinding defense in depth: a page on another name never reaches the core.
        if request.headers.get("host", "").lower() not in allowed_hosts:
            return JSONResponse({"detail": "unexpected Host header"}, status_code=400, headers=SECURITY_HEADERS)
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers[k] = v
        return response

    if UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    @app.get("/", include_in_schema=False)
    def dashboard():
        """The dashboard page holds no project data; it reads everything through the token-protected API."""
        return FileResponse(UI_DIR / "index.html", media_type="text/html")

    def auth(x_orkestr_token: str | None = Header(default=None)):
        if not x_orkestr_token or not secrets.compare_digest(x_orkestr_token, token):
            raise HTTPException(status_code=401, detail="missing or wrong X-Orkestr-Token")

    def guarded(fn):
        try:
            return fn()
        except OrkestrError as err:
            status = 404 if err.code == "E_NOT_FOUND" else 422 if err.code in ("E_TEAM", "E_POLICY", "E_USAGE") else 409
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

    @app.get("/retros", response_model=list[dict[str, Any]], dependencies=deps)
    def retros():
        return sorted(project.store.load().retros.values(), key=lambda r: r["id"])

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

    # -- Phase 4 reads (PHASE4_IMPLEMENTATION.md §7.3) ------------------------------------------

    @app.get("/project", response_model=ProjectInfo, dependencies=deps)
    def project_info():
        state = project.store.load()
        p = state.project
        run = project.latest_run()
        return {
            **{k: p.get(k) for k in ("name", "goal", "status", "status_reason", "managed_by", "limits")},
            "autonomy": st.autonomy_of(state),
            "constraints": p.get("constraints") or [],
            "success_criteria": p.get("success_criteria") or [],
            "latest_run": run,
            "active": busy(),
            "usage": project.usage(run["id"]) if run else project.usage(),
            "needs_founder": project.needs_founder(),
            "policy_version": project.policy()["version"],
        }

    @app.get("/graph", response_model=Graph, dependencies=deps)
    def graph():
        state = project.store.load()
        return {
            "nodes": [{**t, "scope": t.get("scope") or []} for t in state.tasks],
            "can_start_now": [t["id"] for t in st.parallel_candidates(state) if t["owner"] != "founder"],
        }

    @app.get("/meetings", response_model=list[dict[str, Any]], dependencies=deps)
    def meetings():
        return sorted(project.store.load().meetings.values(), key=lambda m: m["id"])

    @app.get("/decisions", response_model=list[dict[str, Any]], dependencies=deps)
    def decisions():
        return sorted(project.store.load().decisions.values(), key=lambda d: d["id"])

    @app.get("/runs/{run_id}/executions", response_model=list[Execution], dependencies=deps)
    def executions(run_id: int):
        return project.store.query(
            """SELECT id, step_id, role, task_id, executor, model, status, started_at, ended_at, duration_ms, num_turns,
                      input_tokens, output_tokens, reported_cost_usd, error FROM agent_execution WHERE run_id = ? ORDER BY id""",
            (run_id,),
        )

    @app.get("/tasks/{task_id}/tests", response_model=list[TestResult], dependencies=deps)
    def task_tests(task_id: str):
        rows = project.store.query("SELECT * FROM test_result WHERE task_id = ? ORDER BY id", (task_id,))
        return [{**r, "timed_out": bool(r["timed_out"])} for r in rows]

    def team_rows() -> list[dict]:
        from ..agents.definitions import load_agents
        from ..domain.team import REQUIRED_ROLES

        agents = load_agents()
        return [
            {**r, "definition_max_turns": agents[role].max_turns if role in agents else None, "required": role in REQUIRED_ROLES}
            for role, r in project.team().items()
        ]

    @app.get("/team", response_model=list[TeamRole], dependencies=deps)
    def team():
        return team_rows()

    @app.get("/policy", response_model=PolicyVersion, dependencies=deps)
    def policy():
        return project.policy()

    @app.get("/policy/versions", response_model=list[PolicyVersion], dependencies=deps)
    def policy_versions():
        return project.store.policy_versions()

    # -- Phase 4 founder actions ------------------------------------------------------------------

    @app.put("/team/{role}", response_model=TeamRole, dependencies=deps)
    def set_team_role(role: str, body: TeamChange):
        guarded(lambda: project.set_team_role(role, body.model_dump(exclude_unset=True)))
        return next(r for r in team_rows() if r["role"] == role)

    @app.put("/policy", response_model=PolicyVersion, dependencies=deps)
    def set_policy(body: PolicyBody):
        guarded(lambda: project.set_policy(body.policy, body.reason))
        return project.policy()

    @app.put("/project/autonomy", response_model=Message, dependencies=deps)
    def set_autonomy(body: AutonomyBody):
        guarded(lambda: project.set_autonomy(body.mode, body.reason))
        return {"message": f"Autonomy is now {body.mode}."}

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

    @app.post("/retros", response_model=Message, status_code=202, dependencies=deps)
    def start_retro(body: RetroBody | None = None):
        if engine_factory is None:
            raise HTTPException(status_code=501, detail="this server was started without an executor")
        if busy():
            raise HTTPException(status_code=409, detail="a run is already active")
        b = body or RetroBody()
        engine = engine_factory(RunBody(model=b.model))
        t = threading.Thread(target=lambda: engine.retro(b.scope), daemon=True, name="orkestr-retro")
        worker["thread"] = t
        t.start()
        return {"message": "retrospective started"}

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


def dashboard_url(project: Project, port: int = DEFAULT_PORT) -> str:
    """The token goes in the URL fragment: browsers never send a fragment to a server, so it
    stays out of requests and logs. The page moves it to sessionStorage and clears the address."""
    return f"http://{HOST}:{port}/#token={project.token()}"


def serve(project: Project, port: int = DEFAULT_PORT, open_dashboard: bool = False):
    import uvicorn

    from ..executor.claude_cli import ClaudeCliExecutor
    from ..workflow.engine import Engine

    def factory(body: RunBody):
        return Engine(project, ClaudeCliExecutor(), model=body.model, max_agent_calls=body.max_agent_calls)

    app = create_app(project, factory, port=port)
    if open_dashboard:
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(dashboard_url(project, port))).start()
        print(f"Orkestr dashboard on http://{HOST}:{port} (opening it in your browser). Stop with Ctrl+C.")
    else:
        print(f"Orkestr API on http://{HOST}:{port} — send the token from .orkestr/core-token in X-Orkestr-Token.")
    uvicorn.run(app, host=HOST, port=port, log_level="warning")
