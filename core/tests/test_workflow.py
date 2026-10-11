"""Workflow engine with the fake executor (PHASE3_IMPLEMENTATION.md §7, §11; acceptance 6–12)."""

from __future__ import annotations

import json
import threading

import pytest

from orkestr_core.domain import state as st
from orkestr_core.executor.base import AgentResult, CancelToken, ExecutorCapabilities, Usage
from orkestr_core.executor.fake import FakeExecutor
from orkestr_core.service import Project
from orkestr_core.workflow.engine import request_control

from conftest import (
    FAILING_TEST,
    PASSING_TEST,
    engine,
    git_repo,
    implement,
    needs_node,
    node_tool,
    plan_output,
    plan_task,
    review,
    tasks_by_id,
    write_files,
)

pytestmark = needs_node

GOOD = {"src/a.js": "export const a = 1;\n", "tests/a.test.mjs": PASSING_TEST}
BAD = {"src/a.js": "export const a = 0;\n", "tests/a.test.mjs": FAILING_TEST}
SCOPE = ("src/**", "tests/**")


def contribution(option=None, high=False):
    return {"position": "use the simpler approach", "preferred_option": option, "key_points": ["small change"], "concerns": [], "high_severity_concern": high}


def steps(project: Project, run_id: int) -> list[dict]:
    return project.store.query("SELECT kind, task_id, status, error FROM workflow_step WHERE run_id = ? ORDER BY seq", (run_id,))


# --- the happy path -------------------------------------------------------------------------


def test_full_run_from_goal_to_report(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED", run["status_reason"]
    t = tasks_by_id(project)["T-001"]
    assert t["status"] == "COMPLETED"
    tests = [e for e in t["evidence"] if e["type"] == "test"]
    assert tests and tests[-1]["result"] == "passed" and tests[-1]["command"] == "node --test tests/"
    assert tests[-1]["test_result_id"], "evidence points at the core's own test run"
    assert (project.dir / "requirements" / "prd.md").exists()
    report = (project.dir / run["report_path"]).read_text(encoding="utf-8")
    assert "Agent usage" in report and "Agent calls: 3" in report
    assert project.usage(run["id"])["calls"] == 3
    assert [s["kind"] for s in steps(project, run["id"])] == ["plan", "implement", "verify", "review"]
    r = node_tool(project.root, "validate")
    assert r.returncode == 0, r.stdout + r.stderr


def test_independent_tasks_are_built_in_one_parallel_wave(project, fake):
    fake.add("plan", plan_output(plan_task("API", scope=("src/api/**", "tests/api/**"), verify=("node --test tests/api/",)), plan_task("UI", owner="frontend-engineer", scope=("src/ui/**", "tests/ui/**"), verify=("node --test tests/ui/",))))
    started = threading.Barrier(2, timeout=10)

    def parallel(files):
        def run(req):
            started.wait()  # both calls must be in flight at the same time
            return implement(files)(req)

        return run

    fake.add("implement", parallel({"src/api/a.js": "1\n", "tests/api/a.test.mjs": PASSING_TEST}), task_id="T-001")
    fake.add("implement", parallel({"src/ui/b.js": "1\n", "tests/ui/b.test.mjs": PASSING_TEST}), task_id="T-002")
    fake.add("review", review("PASS"), repeat=True)

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED", run["status_reason"]
    kinds = [(s["kind"], s["task_id"]) for s in steps(project, run["id"])]
    assert kinds[1:3] == [("implement", "T-001"), ("implement", "T-002")]


# --- verification is the core's job --------------------------------------------------------


def test_agent_claims_are_not_evidence_and_the_fix_limit_leads_to_a_recovery_meeting(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    for _ in range(4):
        fake.add("implement", implement(BAD, notes=["all tests pass"]))
    fake.add("contribution", contribution(), repeat=True)
    fake.add("synthesis", {"options": [{"id": "A", "name": "Fix the assertion"}], "outcome": "DECIDED", "decision": {"question": "How?", "selected_option": "A", "rationale": "the test was wrong"}})
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED", run["status_reason"]
    t = tasks_by_id(project)["T-001"]
    assert t["recovery_meetings"] == 1
    assert any("Recovery decision D-001" in n["text"] for n in t["notes"])
    assert any("all tests pass" in n["text"] for n in t["notes"]), "the claim is kept as a note"
    failed = [e for e in t["evidence"] if e["type"] == "test" and e["result"] == "failed"]
    assert len(failed) == 4
    meeting = project.store.load().meetings["M-001"]
    assert meeting["recovery_for"] == "T-001" and len(meeting["rounds"]) == 1


def test_an_exhausted_recovery_waits_for_the_founder(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(BAD), repeat=True)
    fake.add("contribution", contribution(), repeat=True)
    fake.add("synthesis", {"options": [{"id": "A", "name": "Stop"}], "outcome": "NO_DECISION"})

    run = engine(project, fake).run()

    assert run["status"] == "WAITING_FOUNDER"
    assert "could not be recovered" in run["status_reason"]
    assert tasks_by_id(project)["T-001"]["status"] == "FAILED"


def test_out_of_scope_changes_are_a_failed_check(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement({**GOOD, "package.json": "{}\n"}))
    fake.add("implement", lambda req: ((project.root / "package.json").unlink(), implement(GOOD)(req))[1])
    fake.add("review", review("PASS"))

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED", run["status_reason"]
    t = tasks_by_id(project)["T-001"]
    scope_checks = [e for e in t["evidence"] if e["command"] == "orkestr scope check"]
    assert scope_checks[0]["result"] == "failed" and "package.json" in scope_checks[0]["summary"]
    assert t["fix_attempts"] == 1


def test_commands_outside_the_allowed_forms_are_never_run(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE, verify=())))
    fake.add("implement", implement(GOOD, tests=("node --test tests/; echo pwned",)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))

    run = engine(project, fake).run()

    t = tasks_by_id(project)["T-001"]
    assert run["status"] == "COMPLETED"
    assert any("Not run: `node --test tests/; echo pwned`" in n["text"] for n in t["notes"])
    ran = [r["command"] for r in project.store.query("SELECT command FROM test_result")]
    assert ran == ["node --test tests/"]


def test_review_changes_requested_sends_the_task_back_with_the_findings(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(GOOD), repeat=False)
    finding = {"title": "Missing edge case", "severity": "medium", "status": "confirmed", "evidence": "a.js:1", "recommendation": "handle zero"}
    fake.add("review", review("CHANGES_REQUESTED", [finding]))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED"
    t = tasks_by_id(project)["T-001"]
    assert any("Missing edge case" in n["text"] for n in t["notes"])
    fix_prompt = fake.calls("implement")[1].prompt
    assert "Missing edge case" in fix_prompt
    assert [r["verdict"] for r in project.store.load().reviews.values()] == ["CHANGES_REQUESTED", "PASS"]


def test_security_sensitive_tasks_get_a_second_independent_review(project, fake):
    fake.add("plan", plan_output(plan_task("Add login with password hashing", scope=SCOPE)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"), role="qa-engineer")
    fake.add("review", review("PASS"), role="security-reviewer")

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED"
    assert [c.role for c in fake.calls("review")] == ["qa-engineer", "security-reviewer"]


def test_a_qa_owned_task_is_reviewed_by_someone_else(project, fake):
    fake.add("plan", plan_output(plan_task(owner="qa-engineer", scope=SCOPE)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))
    engine(project, fake).run()
    assert fake.calls("review")[0].role == "tech-lead"


# --- schema validation and limits ------------------------------------------------------------


def test_invalid_output_is_retried_once_with_the_errors(project, fake):
    fake.add("plan", {"summary": "x"})  # missing prd_markdown and tasks
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED"
    plans = fake.calls("plan")
    assert len(plans) == 2 and "did not match the required JSON schema" in plans[1].prompt
    statuses = [r["status"] for r in project.store.query("SELECT status FROM agent_execution WHERE role = 'product-manager' ORDER BY id")]
    assert statuses == ["invalid_output", "ok"]


def test_a_plan_without_scopes_or_with_bad_commands_is_rejected(project, fake):
    fake.add("plan", plan_output(plan_task(scope=(), verify=("rm -rf /",))))
    fake.add("plan", plan_output(plan_task(scope=(), verify=())))
    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER"
    assert "planning failed" in run["status_reason"] and "file scope" in run["status_reason"]
    assert "rm -rf" in fake.calls("plan")[1].prompt
    assert project.store.load().tasks == []


def test_the_agent_call_limit_stops_the_run(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(BAD), repeat=True)
    run = engine(project, fake, max_agent_calls=3).run()
    assert run["status"] == "WAITING_FOUNDER"
    assert "agent-call limit reached (3/3" in run["status_reason"]
    assert run["agent_calls"] == 3


def test_the_per_task_call_limit_stops_the_run_with_a_note(project, fake):
    project.mutate(lambda s: s.project["limits"].update(max_agent_calls_per_task=2))
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(BAD), repeat=True)
    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER" and "T-001 reached its agent-call limit" in run["status_reason"]
    assert "Agent-call limit" in tasks_by_id(project)["T-001"]["notes"][-1]["text"]


def test_a_usage_limit_stops_without_retrying(project, fake):
    fake.add("plan", AgentResult(status="usage_limit", error="Claude usage limit reached"))
    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER" and "usage limit" in run["status_reason"]
    assert len(fake.calls()) == 1


def test_without_git_no_implementation_step_runs(tmp_path, fake):
    p = Project(tmp_path)
    p.init("No git", "x")
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    run = engine(p, fake).run()
    assert run["status"] == "FAILED" and "not a Git repository" in run["status_reason"]
    assert not fake.calls("implement")


# --- founder consultation ----------------------------------------------------------------------


def test_supervised_plans_wait_for_the_founder(tmp_path, fake):
    git_repo(tmp_path)
    p = Project(tmp_path)
    p.init("Supervised", "x", autonomy="supervised")
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    run = engine(p, fake).run()
    assert run["status"] == "WAITING_FOUNDER" and "authorize T-001" in run["status_reason"]
    p.approve_plan()
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))
    run2 = engine(p, fake).run()
    assert run2["id"] == run["id"] and run2["status"] == "COMPLETED"


def test_a_restricted_action_stops_for_approval_and_continues_after_the_grant(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(GOOD, restricted_action={"action": "git push origin main", "category": "git_push", "reason": "publish"}))
    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER" and "A-001" in run["status_reason"]
    t = tasks_by_id(project)["T-001"]
    assert t["status"] == "BLOCKED" and t["approval_id"] == "A-001"

    project.decide_approval("A-001", "grant", "ok")
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))
    run2 = engine(project, fake).run()
    assert run2["status"] == "COMPLETED", run2["status_reason"]
    assert "A-001 was granted" in fake.calls("implement")[-1].prompt


def test_a_design_question_becomes_a_meeting_and_founder_matters_wait(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(GOOD, decision_needed={"question": "Charge users?", "options": ["Free", "Paid"]}))
    fake.add("contribution", contribution("O1"), role="backend-engineer")
    fake.add("contribution", contribution("O2"), role="tech-lead")
    fake.add("contribution", contribution("O1"), role="backend-engineer")
    fake.add("contribution", contribution("O1"), role="tech-lead")
    fake.add("synthesis", {"options": [{"id": "O1", "name": "Free"}, {"id": "O2", "name": "Paid"}], "outcome": "DECIDED", "decision": {"question": "Charge users?", "selected_option": "O1", "rationale": "MVP", "founder_required": True}})
    run = engine(project, fake).run()

    assert run["status"] == "WAITING_FOUNDER" and "decide D-001" in run["status_reason"]
    meeting = project.store.load().meetings["M-001"]
    assert len(meeting["rounds"]) == 2, "different preferred options lead to a second round"
    assert meeting["applies_to"] == ["T-001"]

    project.decide_decision("D-001", "approve", note="free for now")
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))
    run2 = engine(project, fake).run()
    assert run2["status"] == "COMPLETED"
    assert "D-001: Free" in fake.calls("implement")[-1].prompt


# --- interruption, pause, resume, cancel -------------------------------------------------------


def test_a_killed_run_resumes_from_its_steps(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))

    def crash(req):
        write_files(project.root, {"src/a.js": "half\n"})
        raise KeyboardInterrupt  # stands in for a killed process

    fake.add("implement", crash)
    with pytest.raises(KeyboardInterrupt):
        engine(project, fake).run()
    first = project.store.query("SELECT * FROM workflow_run")[0]
    assert first["status"] == "RUNNING"
    (project.dir / "core.lock").write_text(json.dumps({"pid": 999999, "started_at": "x"}), encoding="utf-8")  # stale lock

    fake2 = FakeExecutor()
    fake2.add("implement", implement(GOOD))
    fake2.add("review", review("PASS"))
    run = engine(project, fake2).run()

    assert run["id"] == first["id"] and run["status"] == "COMPLETED", run["status_reason"]
    assert "interrupted" in fake2.calls("implement")[0].prompt
    assert not fake2.calls("plan"), "planning is not repeated"
    kinds = [(s["kind"], s["status"]) for s in steps(project, run["id"])]
    assert ("implement", "FAILED") in kinds
    assert tasks_by_id(project)["T-001"]["fix_attempts"] == 0, "an interruption costs no fix attempt"


def test_pause_stops_after_the_step_and_resume_continues(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))

    def pause_meanwhile(req):
        project.store.execute("UPDATE workflow_run SET control = 'pause' WHERE status = 'RUNNING'")
        return implement(GOOD)(req)

    fake.add("implement", pause_meanwhile)
    run = engine(project, fake).run()
    assert run["status"] == "PAUSED"
    assert project.store.load().project["status"] == "PAUSED"

    fake.add("review", review("PASS"))
    run2 = engine(project, fake).run()
    assert run2["id"] == run["id"] and run2["status"] == "COMPLETED"
    assert project.store.load().project["status"] == "ACTIVE"


class BlockingExecutor:
    """Waits inside the agent call until it is cancelled."""

    name = "blocking"

    def __init__(self):
        self.inner = FakeExecutor()
        self.started = threading.Event()

    def capabilities(self):
        return ExecutorCapabilities(True, True, True, True, True)

    def run(self, request, cancel: CancelToken):
        if request.kind != "implement":
            return self.inner.run(request, cancel)
        self.started.set()
        cancel.wait(30)
        return AgentResult(status="cancelled", error="cancelled")


def test_cancel_stops_a_running_agent_call(project):
    ex = BlockingExecutor()
    ex.inner.add("plan", plan_output(plan_task(scope=SCOPE)))
    result: dict = {}
    t = threading.Thread(target=lambda: result.update(run=engine(project, ex).run()))
    t.start()
    assert ex.started.wait(20)
    print(request_control(project, "cancel"))
    t.join(30)
    assert result["run"]["status"] == "CANCELLED"
    assert tasks_by_id(project)["T-001"]["status"] == "IN_PROGRESS", "tasks keep their state"


def test_pause_and_cancel_without_a_live_engine_apply_directly(project, fake):
    fake.add("plan", AgentResult(status="usage_limit", error="limit"))
    engine(project, fake).run()
    assert "paused" in request_control(project, "pause").lower()
    assert project.store.load().project["status"] == "PAUSED"
    assert "cancelled" in request_control(project, "cancel").lower()
    assert project.store.query("SELECT status FROM workflow_run")[0]["status"] == "CANCELLED"


def test_founder_owned_tasks_are_never_given_to_an_agent(project, fake):
    fake.add("plan", plan_output(plan_task("Sign the contract", owner="founder", scope=("docs/**",))))
    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER" and "T-001 is the founder's task" in run["status_reason"]
    assert not fake.calls("implement")


def test_the_optional_reported_cost_limit_stops_the_run(project, fake):
    project.mutate(lambda s: s.project["limits"].update(max_reported_cost_usd_per_run=0.05))
    fake.add("plan", AgentResult(status="ok", output=plan_output(plan_task(scope=SCOPE)), usage=Usage(reported_cost_usd=0.07)))
    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER" and "reported cost limit reached" in run["status_reason"]
    assert len(fake.calls()) == 1


def test_a_run_interrupted_during_planning_keeps_its_goal(project, fake):
    def crash(req):
        raise KeyboardInterrupt

    fake.add("plan", crash)
    with pytest.raises(KeyboardInterrupt):
        engine(project, fake).run("add a CSV export")
    (project.dir / "core.lock").unlink(missing_ok=True)
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))
    run = engine(project, fake).run()
    assert run["status"] == "COMPLETED"
    assert "add a CSV export" in fake.calls("plan")[-1].prompt
