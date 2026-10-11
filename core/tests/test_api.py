"""Local HTTP API (PHASE3_IMPLEMENTATION.md §9.2, acceptance 13)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from orkestr_core.api import app as api
from orkestr_core.domain import state as st

from conftest import PASSING_TEST, engine, implement, needs_node, plan_output, plan_task, review
from phase1_helpers import P


@pytest.fixture
def client(tmp_path):
    p = P(tmp_path, autonomy="bounded")
    return p, TestClient(api.create_app(p.project)), {"X-Orkestr-Token": p.project.token()}


def test_every_endpoint_requires_the_token(client):
    p, c, auth = client
    for path in ("/status", "/report", "/tasks", "/approvals", "/runs", "/history"):
        assert c.get(path).status_code == 401, path
        assert c.get(path, headers={"X-Orkestr-Token": "wrong"}).status_code == 401, path
        assert c.get(path, headers=auth).status_code == 200, path
    assert c.post("/approvals/A-001/grant").status_code == 401
    assert "access-control-allow-origin" not in c.get("/status", headers={**auth, "Origin": "http://evil.example"}).headers


def test_the_token_file_is_private_and_git_ignored(tmp_path):
    p = P(tmp_path)
    token = p.project.token()
    assert len(token) >= 32
    gitignore = (tmp_path / ".orkestr" / ".gitignore").read_text(encoding="utf-8")
    assert "core-token" in gitignore and "core.db*" in gitignore
    assert token not in "".join(f.read_text(encoding="utf-8") for f in (tmp_path / ".orkestr").glob("*.json"))


def test_reads_return_tasks_status_and_history(client):
    p, c, auth = client
    p.plan()
    tasks = c.get("/tasks", headers=auth).json()
    assert [t["id"] for t in tasks] == ["T-001", "T-002"] and tasks[0]["status"] == "READY"
    assert c.get("/tasks/T-002", headers=auth).json()["depends_on"] == ["T-001"]
    assert c.get("/tasks/T-404", headers=auth).status_code == 404
    assert "# Orkestr status" in c.get("/status", headers=auth).json()["markdown"]
    history = c.get("/history", headers=auth).json()
    assert history[0]["type"] == "project.init"
    later = c.get("/history", params={"after": history[-1]["ts"]}, headers=auth).json()
    assert len(later) < len(history)


def test_founder_actions_follow_the_rules(client):
    p, c, auth = client
    p.do(lambda s: st.request_approval(s, action="git push", category="git_push", reason="release"))
    assert c.post("/approvals/A-001/grant", json={"note": "ok"}, headers=auth).json()["message"] == "A-001 is now GRANTED."
    again = c.post("/approvals/A-001/reject", headers=auth)
    assert again.status_code == 409 and "only PENDING" in again.json()["detail"]
    assert c.post("/approvals/A-404/grant", headers=auth).status_code == 404
    p.add_task("Docs", "product-manager", ["written"])
    assert c.post("/tasks/T-001/authorize", headers=auth).status_code == 200
    assert p.task("T-001")["status"] == "READY"
    assert c.post("/tasks/T-001/retry", headers=auth).status_code == 409


def test_the_openapi_document_is_the_phase4_contract(client):
    _, c, _ = client
    spec = c.get("/openapi.json").json()
    for path in ("/tasks", "/runs", "/runs/{run_id}/steps", "/approvals/{approval_id}/grant", "/decisions/{decision_id}/approve"):
        assert path in spec["paths"], path


def test_the_server_binds_to_localhost_only(tmp_path, monkeypatch):
    seen = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr("orkestr_core.executor.claude_cli.ClaudeCliExecutor.__init__", lambda self, *a, **k: None)
    api.serve(P(tmp_path).project, port=9999)
    assert seen["host"] == "127.0.0.1" and api.HOST == "127.0.0.1"


@needs_node
def test_runs_start_in_the_background_and_only_one_at_a_time(tmp_path):
    from conftest import git_repo
    from orkestr_core.executor.fake import FakeExecutor
    from orkestr_core.service import Project

    git_repo(tmp_path)
    project = Project(tmp_path)
    project.init("API run", "x")
    fake = FakeExecutor()
    fake.add("plan", plan_output(plan_task(scope=("src/**", "tests/**"))))
    fake.add("implement", implement({"src/a.js": "1\n", "tests/a.test.mjs": PASSING_TEST}))
    fake.add("review", review("PASS"))
    c = TestClient(api.create_app(project, lambda body: engine(project, fake)))
    auth = {"X-Orkestr-Token": project.token()}
    assert c.post("/runs", json={}, headers=auth).status_code == 202
    second = c.post("/runs", json={}, headers=auth)
    assert second.status_code in (202, 409)
    for _ in range(200):
        runs = c.get("/runs", headers=auth).json()
        if runs and runs[0]["status"] != "RUNNING":
            break
        time.sleep(0.1)
    assert runs[0]["status"] == "COMPLETED", runs[0]
    steps = c.get(f"/runs/{runs[0]['id']}/steps", headers=auth).json()
    assert [s["kind"] for s in steps] == ["plan", "implement", "verify", "review"]
