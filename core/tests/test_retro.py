"""`orkestr-core retro` (PHASE4_IMPLEMENTATION.md §4, step 0.2)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from orkestr_core.api import app as api
from orkestr_core.executor.base import AgentResult
from orkestr_core.workflow.engine import retro_summary

from conftest import PASSING_TEST, engine, implement, needs_node, plan_output, plan_task, review

pytestmark = needs_node

GOOD = {"src/a.js": "export const a = 1;\n", "tests/a.test.mjs": PASSING_TEST}
SCOPE = ("src/**", "tests/**")


def retro_output(**overrides):
    return {
        "scope": "first release",
        "went_well": ["tests ran green on the first try"],
        "went_badly": [],
        "incorrect_assumptions": [],
        "decomposition_assessment": "one task was enough",
        "meeting_assessment": "no meetings were needed",
        "lessons": [{"lesson": "small scopes keep reviews short", "evidence": "T-001, R-001"}],
        "proposed_changes": ["none"],
        **overrides,
    }


def completed_run(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))
    run = engine(project, fake).run()
    assert run["status"] == "COMPLETED", run["status_reason"]
    return run


def test_a_retro_is_one_read_only_tech_lead_call_recorded_as_rt(project, fake):
    completed_run(project, fake)
    fake.add("retro", retro_output())

    run = engine(project, fake).retro()

    assert run["kind"] == "retro" and run["status"] == "COMPLETED", run["status_reason"]
    assert run["retro_id"] == "RT-001" and run["agent_calls"] == 1
    req = fake.calls("retro")[0]
    assert req.role == "tech-lead" and req.tools == "read_only"
    assert "T-001 [COMPLETED]" in req.prompt and "node --test tests/" in req.prompt
    retro = project.store.load().retros["RT-001"]
    assert retro["lessons"][0]["evidence"] == "T-001, R-001"
    assert (project.dir / "retros" / "RT-001.json").exists()
    assert "small scopes keep reviews short" in (project.dir / "retros" / "RT-001.md").read_text(encoding="utf-8")
    assert "RT-001" in (project.dir / "memory" / "lessons-learned.md").read_text(encoding="utf-8")


def test_a_retro_without_valid_output_fails_and_records_nothing(project, fake):
    completed_run(project, fake)
    fake.add("retro", {"scope": "x", "lessons": [{"lesson": "no evidence"}]}, repeat=True)

    run = engine(project, fake).retro()

    assert run["status"] == "FAILED" and run["retro_id"] is None
    assert run["agent_calls"] == 2, "one retry for invalid output, then stop"
    assert not project.store.load().retros


def test_a_retro_never_takes_over_an_open_workflow_run(project, fake):
    first = engine(project, fake).run()  # no plan scripted: planning fails
    assert first["status"] == "WAITING_FOUNDER"
    fake.add("retro", retro_output())
    assert engine(project, fake).retro()["status"] == "COMPLETED"
    assert project.latest_run()["id"] == first["id"]

    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))
    again = engine(project, fake).run()

    assert again["id"] == first["id"] and again["status"] == "COMPLETED", again["status_reason"]


def test_an_interrupted_retro_is_closed_not_resumed(project, fake):
    project.store.execute("INSERT INTO workflow_run (kind, status, options_json, started_at) VALUES ('retro', 'RUNNING', '{}', '2026-10-12T00:00:00Z')")
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))

    run = engine(project, fake).run()

    assert run["kind"] == "run" and run["status"] == "COMPLETED"
    old = project.store.query("SELECT status, status_reason FROM workflow_run WHERE kind = 'retro'")[0]
    assert old == {"status": "FAILED", "status_reason": "interrupted"}


def test_a_usage_limit_ends_the_retro_as_failed(project, fake):
    fake.add("retro", AgentResult(status="usage_limit", error="usage limit reached"))
    run = engine(project, fake).retro()
    assert run["status"] == "FAILED" and "usage limit" in run["status_reason"]


def test_the_summary_is_bounded_and_lists_empty_sections(project):
    state = project.store.load()
    history = [{"ts": "t", "type": "x", "entity": None, "summary": "y" * 500}] * 500
    text = retro_summary(state, history, project.usage())
    assert "Meetings:\n- (none)" in text
    assert len(text) <= 24_000 + len("\n(truncated)")


def test_the_api_starts_a_retro_in_the_background(project, fake):
    completed_run(project, fake)
    fake.add("retro", retro_output())
    c = TestClient(api.create_app(project, lambda body: engine(project, fake)), base_url="http://127.0.0.1:8765")
    auth = {"X-Orkestr-Token": project.token()}

    assert c.post("/retros", json={}).status_code == 401
    assert c.post("/retros", json={"scope": "first release"}, headers=auth).status_code == 202
    for _ in range(100):
        retros = c.get("/retros", headers=auth).json()
        if retros:
            break
        time.sleep(0.05)
    assert [r["id"] for r in retros] == ["RT-001"]
    runs = c.get("/runs", headers=auth).json()
    assert runs[0]["kind"] == "retro"
