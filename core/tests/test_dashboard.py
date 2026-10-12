"""Dashboard API and serving rules (PHASE4_IMPLEMENTATION.md §7; acceptance 2, 3, 5)."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orkestr_core.api import app as api

from conftest import PASSING_TEST, engine, implement, needs_node, plan_output, plan_task, review
from phase1_helpers import P

BASE = "http://127.0.0.1:8765"
UI = Path(api.UI_DIR)


@pytest.fixture
def client(tmp_path):
    p = P(tmp_path, autonomy="bounded")
    return p, TestClient(api.create_app(p.project), base_url=BASE), {"X-Orkestr-Token": p.project.token()}


NEW_READS = ("/project", "/graph", "/meetings", "/decisions", "/retros", "/team", "/policy", "/policy/versions", "/runs/1/executions", "/tasks/T-001/tests")


def test_every_new_read_requires_the_token(client):
    p, c, auth = client
    for path in NEW_READS:
        assert c.get(path).status_code == 401, path
        assert c.get(path, headers=auth).status_code == 200, path
    for method, path, body in (
        ("put", "/team/qa-engineer", {"model": "opus"}),
        ("put", "/policy", {"policy": {}, "reason": "x"}),
        ("put", "/project/autonomy", {"mode": "supervised", "reason": "x"}),
    ):
        assert getattr(c, method)(path, json=body).status_code == 401, path


def test_the_dashboard_page_needs_no_token_but_holds_no_data(client):
    p, c, auth = client
    page = c.get("/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert p.project.token() not in page.text and "Test project" not in page.text
    assert c.get("/ui/app.js").status_code == 200
    assert c.get("/tasks").status_code == 401


def test_security_headers_are_on_every_response(client):
    p, c, auth = client
    for r in (c.get("/"), c.get("/ui/app.js"), c.get("/project", headers=auth), c.get("/project")):
        csp = r.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"


def test_requests_for_another_host_name_are_refused(client):
    p, c, auth = client
    assert c.get("/project", headers={**auth, "Host": "evil.example:8765"}).status_code == 400
    assert c.get("/", headers={"Host": "attacker.test"}).status_code == 400
    assert c.get("/project", headers={**auth, "Host": "localhost:8765"}).status_code == 200
    assert c.get("/project", headers={**auth, "Host": "127.0.0.1:9999"}).status_code == 400


def test_project_lists_what_needs_the_founder(client):
    p, c, auth = client
    p.add_task("Sign the contract", "founder")
    p.project.authorize_task("T-001")
    p.add_task("Explore", "researcher")
    info = c.get("/project", headers=auth).json()
    assert info["name"] == "Test project" and info["autonomy"] == "bounded" and info["policy_version"] == 0
    kinds = {(n["kind"], n["id"]) for n in info["needs_founder"]}
    assert ("founder_task", "T-001") in kinds and ("authorize", "T-002") in kinds


def test_graph_marks_tasks_that_can_start_now(client):
    p, c, auth = client
    p.plan()
    g = c.get("/graph", headers=auth).json()
    assert [n["id"] for n in g["nodes"]] == ["T-001", "T-002"] and g["nodes"][1]["depends_on"] == ["T-001"]
    assert g["can_start_now"] == ["T-001"]


def test_team_policy_and_autonomy_through_the_api(client):
    p, c, auth = client
    r = c.put("/team/qa-engineer", json={"model": "opus", "max_turns": 4}, headers=auth)
    assert r.status_code == 200 and r.json()["model"] == "opus" and r.json()["required"] is True and r.json()["definition_max_turns"]
    assert c.put("/team/tech-lead", json={"enabled": False}, headers=auth).status_code == 422
    assert c.put("/team/cfo", json={"enabled": True}, headers=auth).status_code == 422
    team = {t["role"]: t for t in c.get("/team", headers=auth).json()}
    assert team["qa-engineer"]["max_turns"] == 4 and team["researcher"]["required"] is False

    r = c.put("/policy", json={"policy": {"protected_paths": ["LICENSE"]}, "reason": "license"}, headers=auth)
    assert r.status_code == 200 and r.json()["version"] == 1
    bad = c.put("/policy", json={"policy": {"limits": {"max_parallel_tasks": 50}}, "reason": "go"}, headers=auth)
    assert bad.status_code == 422 and "from 1 to 4" in bad.json()["detail"]
    assert [v["version"] for v in c.get("/policy/versions", headers=auth).json()] == [0, 1]

    assert c.put("/project/autonomy", json={"mode": "supervised", "reason": "review"}, headers=auth).status_code == 200
    assert c.put("/project/autonomy", json={"mode": "chaos", "reason": "x"}, headers=auth).status_code == 409
    assert c.get("/project", headers=auth).json()["autonomy"] == "supervised"


@needs_node
def test_executions_and_test_results_after_a_run(tmp_path):
    from conftest import git_repo
    from orkestr_core.executor.fake import FakeExecutor
    from orkestr_core.service import Project

    git_repo(tmp_path)
    project = Project(tmp_path)
    project.init("Run", "x")
    fake = FakeExecutor()
    fake.add("plan", plan_output(plan_task(scope=("src/**", "tests/**"))))
    fake.add("implement", implement({"src/a.js": "1\n", "tests/a.test.mjs": PASSING_TEST}))
    fake.add("review", review("PASS"))
    c = TestClient(api.create_app(project, lambda body: engine(project, fake)), base_url=BASE)
    auth = {"X-Orkestr-Token": project.token()}
    assert c.post("/runs", json={}, headers=auth).status_code == 202
    for _ in range(200):
        runs = c.get("/runs", headers=auth).json()
        if runs and runs[0]["status"] != "RUNNING":
            break
        time.sleep(0.1)
    ex = c.get(f"/runs/{runs[0]['id']}/executions", headers=auth).json()
    assert [e["role"] for e in ex] == ["product-manager", "backend-engineer", "qa-engineer"]
    assert "output_json" not in ex[0]
    tests = c.get("/tasks/T-001/tests", headers=auth).json()
    assert tests and tests[0]["command"] == "node --test tests/" and tests[0]["timed_out"] is False


def test_the_openapi_document_lists_the_phase4_routes(client):
    p, c, auth = client
    paths = c.get("/openapi.json", headers=auth).json()["paths"]
    for path in ("/project", "/graph", "/team/{role}", "/policy", "/policy/versions", "/project/autonomy", "/runs/{run_id}/executions", "/tasks/{task_id}/tests", "/retros"):
        assert path in paths, path
    assert "/" not in paths


def test_the_dashboard_url_carries_the_token_in_the_fragment_only(tmp_path):
    p = P(tmp_path)
    url = api.dashboard_url(p.project, 8765)
    assert url == f"http://127.0.0.1:8765/#token={p.project.token()}"


# --- dashboard sources (no build step, no external resources, no HTML sinks) -------------------

UI_FILES = sorted(f for f in UI.rglob("*") if f.is_file())


def test_the_dashboard_files_exist():
    names = {f.name for f in UI_FILES}
    assert {"index.html", "app.css", "app.js"} <= names


@pytest.mark.parametrize("path", UI_FILES, ids=lambda f: f.name)
def test_no_external_resources(path):
    # The SVG namespace is an identifier, not a resource; nothing else may look like a URL.
    text = path.read_text(encoding="utf-8").replace("'http://www.w3.org/2000/svg'", "")
    assert not re.search(r"https?://|//[a-z0-9-]+\.[a-z]{2,}/", text, re.IGNORECASE), "the dashboard loads nothing from the network"
    assert "@import" not in text
    assert not re.search(r"url\((?!#|data:)", text), "url() may only point inside the page"


@pytest.mark.parametrize("path", [f for f in UI_FILES if f.suffix in (".js", ".html")], ids=lambda f: f.name)
def test_no_html_injection_sinks(path):
    text = path.read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout('", 'setTimeout("'):
        assert sink not in text, f"{path.name} uses {sink}"


def test_no_inline_scripts_styles_or_handlers():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline script"
    assert "<style" not in html and not re.search(r"\sstyle=", html)
    assert not re.search(r"\son[a-z]+=", html), "inline event handler"
