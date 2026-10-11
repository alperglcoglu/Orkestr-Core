"""Ported from plugins/orkestr/tests/state.test.mjs (acceptance criterion 2 of Phase 3).

Each test names the Phase 1 test it covers. File-level behaviors of the plugin (its own
lock file, corrupted JSON) map to the core's import, store, and run lock.
"""

from __future__ import annotations

import json

import pytest

from orkestr_core.domain import records as rec
from orkestr_core.domain import state as st
from orkestr_core.domain.errors import OrkestrError
from orkestr_core.domain.redact import find_secrets, redact_string
from orkestr_core.service import Project
from orkestr_core.workflow.lock import RunLock

from phase1_helpers import P, approved_plan, complete_t1, meeting_draft


# --- Initialization ---------------------------------------------------------------------------


def test_init_creates_the_data_files_and_leaves_unrelated_files_untouched(tmp_path):
    (tmp_path / "README.md").write_text("user file", encoding="utf-8")
    p = P(tmp_path, init=False)
    p.project.init("Demo", "Goal", constraints=["no paid services"], success=["tests pass"], autonomy="bounded")
    for f in ("project.json", "tasks.json", "approvals.json", "history.jsonl", "core.db"):
        assert (tmp_path / ".orkestr" / f).exists(), f
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "user file"
    proj = p.read("project.json")
    assert proj["status"] == "ACTIVE"
    assert {k: proj["limits"][k] for k in ("max_meeting_participants", "max_discussion_rounds", "max_fix_attempts", "max_recovery_meetings", "max_parallel_tasks")} == {
        "max_meeting_participants": 4,
        "max_discussion_rounds": 2,
        "max_fix_attempts": 3,
        "max_recovery_meetings": 1,
        "max_parallel_tasks": 2,
    }
    assert proj["autonomy"] == "bounded"
    assert proj["managed_by"] == "core"
    assert proj["constraints"] == ["no paid services"]
    assert p.history()[0]["type"] == "project.init"
    p.validate_ok()


def test_init_refuses_to_overwrite_an_existing_project_or_an_unknown_orkestr_folder(tmp_path):
    p = P(tmp_path)
    before = (tmp_path / ".orkestr" / "project.json").read_text(encoding="utf-8")
    with pytest.raises(OrkestrError, match="refusing to overwrite"):
        Project(tmp_path).init("Other", "Other")
    assert (tmp_path / ".orkestr" / "project.json").read_text(encoding="utf-8") == before

    q = tmp_path / "q"
    (q / ".orkestr").mkdir(parents=True)
    (q / ".orkestr" / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(OrkestrError, match="exists but has no project"):
        Project(q).init("X", "Y")
    assert (q / ".orkestr" / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_commands_fail_clearly_without_a_project(tmp_path):
    with pytest.raises(OrkestrError, match="No orkestr-core database"):
        Project(tmp_path).store.load()


# --- Planning, dependencies, authorization --------------------------------------------------


def test_plan_import_validates_owners_criteria_dependencies_and_cycles_atomically(tmp_path):
    p = P(tmp_path)
    bad = {
        "tasks": [
            {"title": "A", "owner": "wizard", "acceptance_criteria": []},
            {"title": "B", "owner": "qa-engineer", "acceptance_criteria": ["x"], "depends_on": ["T-999"]},
            {"title": "C", "owner": "qa-engineer", "acceptance_criteria": ["x"], "depends_on": ["#3"]},
        ]
    }
    err = p.fails(lambda s: st.import_plan(s, bad), "owner")
    for pattern in ("acceptance_criteria", "unknown dependency T-999", "cannot depend on itself"):
        assert pattern in err.message
    assert p.read("tasks.json")["tasks"] == []
    assert p.read("project.json")["counters"]["T"] == 0

    cycle = {
        "tasks": [
            {"title": "A", "owner": "qa-engineer", "acceptance_criteria": ["x"], "depends_on": ["#2"]},
            {"title": "B", "owner": "qa-engineer", "acceptance_criteria": ["x"], "depends_on": ["#1"]},
        ]
    }
    p.fails(lambda s: st.import_plan(s, cycle), "Dependency cycle")
    assert p.read("tasks.json")["tasks"] == []


def test_imported_plan_tasks_stay_in_backlog_until_the_founder_approves_the_plan(tmp_path):
    p = P(tmp_path)
    p.plan()
    assert p.task("T-001")["status"] == "BACKLOG"
    assert p.task("T-001")["authorized"] is False
    assert p.task("T-002")["depends_on"] == ["T-001"]
    p.fails(lambda s: st.transition(s, "T-001", "READY"), "not authorized")
    p.fails(lambda s: st.approve_plan(s, by="agent"), "founder-only")
    p.approve_plan()
    assert p.task("T-001")["status"] == "READY"
    assert p.task("T-002")["status"] == "BACKLOG"
    assert any(e["type"] == "plan.approve" and e["actor"] == "founder" for e in p.history())


def test_manual_tasks_need_individual_founder_authorization(tmp_path):
    p = P(tmp_path)
    p.add_task("Write docs", "orchestrator", ["README exists"])
    assert p.task("T-001")["source"]["type"] == "manual"
    assert p.task("T-001")["status"] == "BACKLOG"
    p.fails(lambda s: st.authorize_task(s, "T-001", by="orchestrator"), "founder-only")
    p.project.authorize_task("T-001")
    assert p.task("T-001")["status"] == "READY"


# --- State machine and completion -----------------------------------------------------------


def test_illegal_transitions_and_missing_reasons_are_rejected(tmp_path):
    p = approved_plan(tmp_path)
    p.fails(lambda s: st.transition(s, "T-001", "COMPLETED"), "not allowed")
    p.fails(lambda s: st.transition(s, "T-001", "DONE"), "Unknown state")
    p.fails(lambda s: st.transition(s, "T-001", "BLOCKED"), "reason is required")
    p.move("T-001", "IN_PROGRESS")
    p.fails(lambda s: st.transition(s, "T-001", "FAILED", error="x"), "--next-action")
    p.fails(lambda s: st.transition(s, "T-002", "IN_PROGRESS"), "not allowed|cannot start")


def test_completion_requires_passing_verification_and_a_passing_review(tmp_path):
    p = approved_plan(tmp_path)
    p.move("T-001", "IN_PROGRESS")
    p.move("T-001", "REVIEW")
    err = p.fails(lambda s: st.transition(s, "T-001", "COMPLETED"), "no passed test")
    assert "no review evidence" in err.message
    p.fails(lambda s: st.add_evidence(s, "T-001", type="test", result="passed", summary="ok"), "--command is required")
    p.evidence("T-001", "test", "passed")
    p.fails(lambda s: st.transition(s, "T-001", "COMPLETED"), "no review evidence")
    p.evidence("T-001", "review", "passed", "looks good", command=None)
    p.move("T-001", "COMPLETED")
    assert p.task("T-002")["status"] == "READY"
    p.fails(lambda s: st.transition(s, "T-001", "REVIEW"), "terminal state")
    p.fails(lambda s: st.add_evidence(s, "T-001", type="manual", result="passed", summary="x"), "COMPLETED")


def test_a_later_failing_check_prevents_completion(tmp_path):
    p = approved_plan(tmp_path)
    p.move("T-001", "IN_PROGRESS")
    p.evidence("T-001", "test", "passed")
    p.move("T-001", "REVIEW")
    p.evidence("T-001", "check", "failed", "lint errors", command="npm run lint")
    p.evidence("T-001", "review", "passed", command=None)
    p.fails(lambda s: st.transition(s, "T-001", "COMPLETED"), "latest test/check failed")


def test_fix_attempts_are_limited_to_3_the_next_failure_fails_the_task_and_blocks_dependents(tmp_path):
    p = approved_plan(tmp_path)
    p.move("T-001", "IN_PROGRESS")
    for i in range(1, 4):
        p.evidence("T-001", "test", "failed", "still failing")
        assert p.task("T-001")["fix_attempts"] == i
        assert p.task("T-001")["status"] == "IN_PROGRESS"
    last = p.evidence("T-001", "test", "failed", "still failing")
    assert last["failed_task"] is True
    t1 = p.task("T-001")
    assert t1["status"] == "FAILED"
    assert "Founder decision required" in t1["failure"]["next_action"]
    t2 = p.task("T-002")
    assert t2["status"] == "BLOCKED" and t2["blocked_by_dependency"] == "T-001"

    p.fails(lambda s: st.transition(s, "T-001", "READY"), "--retry")
    p.project.retry_task("T-001")
    assert p.task("T-001")["fix_attempts"] == 0
    assert p.task("T-001")["failure"] is None
    assert p.task("T-002")["status"] == "BACKLOG"
    p.validate_ok()


def test_review_rework_keeps_the_fix_attempt_counter(tmp_path):
    p = approved_plan(tmp_path)
    p.move("T-001", "IN_PROGRESS")
    p.evidence("T-001", "test", "failed", "x")
    p.move("T-001", "REVIEW")
    p.move("T-001", "IN_PROGRESS")
    assert p.task("T-001")["fix_attempts"] == 1


def test_cancelling_a_task_blocks_transitive_dependents_with_a_reason(tmp_path):
    p = P(tmp_path)
    p.plan(
        {
            "tasks": [
                {"title": "A", "owner": "tech-lead", "acceptance_criteria": ["x"]},
                {"title": "B", "owner": "backend-engineer", "acceptance_criteria": ["x"], "depends_on": ["#1"]},
                {"title": "C", "owner": "qa-engineer", "acceptance_criteria": ["x"], "depends_on": ["#2"]},
            ]
        }
    )
    p.approve_plan()
    p.move("T-001", "CANCELLED", reason="scope cut")
    assert p.task("T-002")["status"] == "BLOCKED"
    assert p.task("T-003")["status"] == "BLOCKED"
    assert "T-001 is CANCELLED" in p.task("T-003")["blocked_reason"]


# --- Meetings and decisions -------------------------------------------------------------------


def test_meeting_limits_at_most_4_participants_and_2_rounds(tmp_path):
    p = P(tmp_path)
    five = meeting_draft(participants=[{"role": r, "responsibility": "x"} for r in ("tech-lead", "backend-engineer", "frontend-engineer", "qa-engineer", "security-reviewer")])
    p.fails(lambda s: rec.record_meeting(s, five), "at most 4 participants")
    base = meeting_draft()
    three = meeting_draft(rounds=[{**base["rounds"][0], "round": n} for n in (1, 2, 3)])
    p.fails(lambda s: rec.record_meeting(s, three), "at most 2 discussion rounds")
    p.fails(lambda s: rec.record_meeting(s, meeting_draft(action_items=[])), "at least one action item")
    assert p.read("project.json")["counters"]["M"] == 0


def test_a_decided_meeting_produces_a_meeting_record_a_decision_and_linked_tasks(tmp_path):
    p = P(tmp_path)
    r = p.meeting(meeting_draft())
    assert r["meeting"]["id"] == "M-001" and r["decision"]["approval_status"] == "PROPOSED"
    m = p.read("meetings/M-001.json")
    d = p.read("decisions/D-001.json")
    assert m["task_ids"] == ["T-001", "T-002"]
    assert d["selected_option"] == "O1"
    assert d["objections"][0]["role"] == "frontend-engineer"
    assert (tmp_path / ".orkestr" / "meetings" / "M-001.md").exists()
    assert "Generated by orkestr" in (tmp_path / ".orkestr" / "decisions" / "D-001.md").read_text(encoding="utf-8")
    assert p.task("T-001")["decision_id"] == "D-001"
    assert p.task("T-001")["status"] == "BACKLOG"
    assert p.task("T-002")["depends_on"] == ["T-001"]
    p.fails(lambda s: rec.decide_decision(s, "D-001", "approve", by="agent"), "founder-only")
    p.project.decide_decision("D-001", "approve", note="go")
    assert p.read("decisions/D-001.json")["approval_status"] == "APPROVED"
    assert p.task("T-001")["status"] == "READY"
    p.fails(lambda s: rec.decide_decision(s, "D-001", "approve", by="founder"), "APPROVED")
    p.validate_ok()


def test_rejecting_a_decision_cancels_its_unstarted_tasks(tmp_path):
    p = P(tmp_path)
    p.meeting(meeting_draft())
    p.fails(lambda s: rec.decide_decision(s, "D-001", "reject", by="founder"), "--note is required")
    p.project.decide_decision("D-001", "reject", note="not now")
    assert p.task("T-001")["status"] == "CANCELLED"
    assert p.task("T-002")["status"] == "CANCELLED"


def test_an_escalated_meeting_waits_for_the_founder_to_pick_an_option(tmp_path):
    p = P(tmp_path)
    p.meeting(meeting_draft(outcome="ESCALATED", decision={"question": "REST or GraphQL?", "selected_option": None, "rationale": "", "tradeoffs": [], "risks": []}, action_items=[]))
    assert p.read("decisions/D-001.json")["approval_status"] == "NEEDS_FOUNDER"
    p.fails(lambda s: rec.decide_decision(s, "D-001", "approve", by="founder"), "--option")
    p.fails(lambda s: rec.decide_decision(s, "D-001", "approve", by="founder", option="O9"), "Unknown option")
    p.project.decide_decision("D-001", "approve", option="O2")
    assert p.read("decisions/D-001.json")["selected_option"] == "O2"


def test_no_decision_meetings_cannot_create_tasks(tmp_path):
    p = P(tmp_path)
    p.fails(lambda s: rec.record_meeting(s, meeting_draft(outcome="NO_DECISION", decision=None)), "NO_DECISION cannot create action items")
    p.meeting(meeting_draft(outcome="NO_DECISION", decision=None, action_items=[]))
    assert p.read("meetings/M-001.json")["decision_id"] is None


# --- Approvals ----------------------------------------------------------------------------------


def test_a_task_that_requires_approval_cannot_start_until_the_founder_grants_it(tmp_path):
    p = P(tmp_path)
    p.plan({"tasks": [{"title": "Deploy to production", "owner": "orchestrator", "acceptance_criteria": ["site is live"], "requires_approval": True}]})
    p.approve_plan()
    p.fails(lambda s: st.transition(s, "T-001", "IN_PROGRESS"), "no approval request")
    p.fails(lambda s: st.request_approval(s, action="deploy", category="free-money", reason="x"), "--category")
    p.do(lambda s: st.request_approval(s, action="Deploy v1 to production", category="deploy", reason="release", task_id="T-001"))
    p.fails(lambda s: st.transition(s, "T-001", "IN_PROGRESS"), "A-001 is PENDING")
    p.fails(lambda s: st.decide_approval(s, "A-001", "grant", by="orchestrator"), "founder-only")
    p.project.decide_approval("A-001", "grant", "ok")
    p.move("T-001", "IN_PROGRESS")
    p.project.record_approval_result("A-001", "deployed build 42")
    a = p.read("approvals.json")["approvals"][0]
    assert (a["status"], a["decided_by"], a["execution_result"]) == ("GRANTED", "founder", "deployed build 42")
    types = [e["type"] for e in p.history()]
    for t in ("approval.request", "approval.grant", "approval.result"):
        assert t in types


def test_a_rejected_approval_never_becomes_granted_and_blocks_its_task(tmp_path):
    p = P(tmp_path)
    p.add_task("Push release", "orchestrator", ["tag pushed"], requires_approval=True)
    p.project.authorize_task("T-001")
    p.do(lambda s: st.request_approval(s, action="git push origin main", category="git_push", reason="publish", task_id="T-001"))
    p.project.decide_approval("A-001", "reject", "not yet")
    p.fails(lambda s: st.decide_approval(s, "A-001", "grant", by="founder"), "only PENDING")
    assert p.read("approvals.json")["approvals"][0]["status"] == "REJECTED"
    assert p.task("T-001")["status"] == "BLOCKED"
    assert "A-001 was rejected" in p.task("T-001")["blocked_reason"]
    p.fails(lambda s: st.record_approval_result(s, "A-001", "x"), "only GRANTED")


# --- Pause, resume, cancel ------------------------------------------------------------------------


def test_pause_blocks_starting_work_resume_restores_it(tmp_path):
    p = approved_plan(tmp_path)
    p.fails(lambda s: st.set_project_status(s, "pause"), "--reason")
    p.do(lambda s: st.set_project_status(s, "pause", reason="waiting for feedback"))
    p.fails(lambda s: st.transition(s, "T-001", "IN_PROGRESS"), "project is PAUSED")
    assert "PAUSED — waiting for feedback" in p.status()
    p.do(lambda s: st.set_project_status(s, "resume"))
    p.move("T-001", "IN_PROGRESS")


def test_project_cancel_is_founder_only_cancels_open_work_and_freezes_the_state(tmp_path):
    p = approved_plan(tmp_path)
    p.do(lambda s: st.request_approval(s, action="send email", category="external_communication", reason="x"))
    p.fails(lambda s: st.set_project_status(s, "cancel", reason="stop"), "founder-only")
    p.project.cancel_project("stop")
    assert p.read("project.json")["status"] == "CANCELLED"
    assert all(t["status"] == "CANCELLED" for t in p.read("tasks.json")["tasks"])
    assert p.read("approvals.json")["approvals"][0]["status"] == "CANCELLED"
    p.fails(lambda s: st.transition(s, "T-001", "READY"), "CANCELLED")
    p.validate_ok()


def test_next_returns_an_interrupted_in_progress_task_before_ready_ones(tmp_path):
    p = approved_plan(tmp_path)
    assert st.next_task(p.project.store.load())["id"] == "T-001"
    p.move("T-001", "IN_PROGRESS")
    nxt = st.next_task(p.project.store.load())
    assert (nxt["id"], nxt["status"]) == ("T-001", "IN_PROGRESS")


def test_project_complete_requires_all_tasks_closed(tmp_path):
    p = approved_plan(tmp_path)
    p.fails(lambda s: st.set_project_status(s, "complete"), "Open tasks remain")
    complete_t1(p)
    p.move("T-002", "CANCELLED", reason="descoped")
    p.do(lambda s: st.set_project_status(s, "complete"))
    assert p.read("project.json")["status"] == "COMPLETED"


# --- Reviews and retrospectives -------------------------------------------------------------------


def test_review_records_separate_confirmed_from_suspected_findings_and_add_evidence(tmp_path):
    p = approved_plan(tmp_path)
    p.move("T-001", "IN_PROGRESS")
    p.evidence("T-001", "test", "passed")
    p.move("T-001", "REVIEW")

    def finding(status, severity):
        return {"title": "SQL injection", "severity": severity, "status": status, "component": "api", "evidence": "string concat in query", "recommendation": "use parameters"}

    bad = {"task_id": "T-001", "reviewers": ["security-reviewer"], "scope": "API", "verdict": "PASS", "findings": [finding("confirmed", "high")]}
    p.fails(lambda s: rec.record_review(s, bad), "PASS is not allowed")
    good = {
        "task_id": "T-001",
        "reviewers": ["qa-engineer", "security-reviewer"],
        "scope": "API",
        "verdict": "PASS",
        "checks_run": [{"command": "npm test", "result": "passed", "summary": "12 passed"}],
        "findings": [finding("suspected", "high")],
        "assumptions": ["No auth in MVP"],
    }
    p.do(lambda s: rec.record_review(s, good))
    md = (tmp_path / ".orkestr" / "reviews" / "R-001.md").read_text(encoding="utf-8")
    assert "Suspected findings (not verified)" in md and "SQL injection" in md
    ev = [f"{e['type']}:{e['result']}:{e.get('ref') or ''}" for e in p.task("T-001")["evidence"]]
    assert "check:passed:R-001" in ev and "review:passed:R-001" in ev
    p.move("T-001", "COMPLETED")


def test_retrospective_lessons_need_evidence_and_are_appended_to_memory(tmp_path):
    p = P(tmp_path)
    p.fails(lambda s: rec.record_retro(s, {"scope": "M1", "lessons": [{"lesson": "x"}]}), "evidence is required")
    p.do(
        lambda s: rec.record_retro(
            s,
            {
                "scope": "Milestone 1",
                "went_well": ["plan was small"],
                "went_badly": ["flaky test"],
                "lessons": [{"lesson": "Pin test seeds", "evidence": "T-001 failed twice with random data"}],
                "proposed_changes": ["Add seed to QA agent instructions"],
            },
        )
    )
    assert (tmp_path / ".orkestr" / "retros" / "RT-001.md").exists()
    lessons = (tmp_path / ".orkestr" / "memory" / "lessons-learned.md").read_text(encoding="utf-8")
    assert "Pin test seeds" in lessons and "RT-001" in lessons


# --- Status ---------------------------------------------------------------------------------------


def test_status_lists_blockers_failures_pending_approvals_and_waiting_decisions(tmp_path):
    p = approved_plan(tmp_path)
    p.move("T-001", "IN_PROGRESS")
    p.move("T-001", "FAILED", error="build broke", next_action="ask founder")
    p.do(lambda s: st.request_approval(s, action="buy a domain", category="spending", reason="launch"))
    p.meeting(meeting_draft())
    out = p.status()
    import re

    assert re.search(r"## Blocked[\s\S]*T-002[\s\S]*Dependency T-001 is FAILED", out)
    assert re.search(r"## Failed[\s\S]*build broke[\s\S]*ask founder", out)
    assert re.search(r"Pending approvals[\s\S]*A-001 \[spending\] buy a domain", out)
    assert re.search(r"Decisions waiting for the founder[\s\S]*D-001 \[PROPOSED\]", out)
    assert re.search(r"Tasks waiting for authorization[\s\S]*T-003", out)
    assert re.search(r"Validation[\s\S]*OK", out)
    assert "%" not in out


# --- Validation, recovery, and secrets ------------------------------------------------------------


def test_validate_and_status_report_corrupted_state_without_crashing(tmp_path):
    """Phase 1 files are the core's import input; corrupted ones are refused with a clear message."""
    from conftest import node_tool, HAS_NODE

    if not HAS_NODE:
        pytest.skip("Node.js is not installed")
    assert node_tool(tmp_path, "init", "--name", "Legacy", "--goal", "g").returncode == 0
    (tmp_path / ".orkestr" / "tasks.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(OrkestrError, match="Corrupted JSON in .orkestr/tasks.json"):
        Project(tmp_path).import_phase1()
    assert not (tmp_path / ".orkestr" / "core.db").exists()


def test_validate_detects_broken_references_and_illegal_states_written_by_hand(tmp_path):
    p = approved_plan(tmp_path)
    from orkestr_core.domain.validate import validate_state

    state = p.project.store.load()
    state.tasks[0]["status"] = "COMPLETED"
    state.tasks[1]["depends_on"] = ["T-404"]
    errors = validate_state(state, p.history())["errors"]
    assert any("T-001: COMPLETED but" in e for e in errors)
    assert any("unknown dependency T-404" in e for e in errors)


def test_an_active_lock_blocks_writers_a_stale_lock_is_replaced(tmp_path):
    p = approved_plan(tmp_path)
    lock_file = tmp_path / ".orkestr" / "core.lock"
    with RunLock(lock_file):
        with pytest.raises(OrkestrError, match="Another orkestr-core run is active"):
            RunLock(lock_file).acquire()
    lock_file.write_text(json.dumps({"pid": 999999, "started_at": "2026-01-01T00:00:00.000Z"}), encoding="utf-8")
    with RunLock(lock_file):
        assert json.loads(lock_file.read_text(encoding="utf-8"))["pid"] != 999999
    assert not lock_file.exists()


def test_leftover_temp_files_from_an_interrupted_write_are_reported(tmp_path):
    from conftest import node_tool, HAS_NODE

    if not HAS_NODE:
        pytest.skip("Node.js is not installed")
    assert node_tool(tmp_path, "init", "--name", "Legacy", "--goal", "g").returncode == 0
    (tmp_path / ".orkestr" / "tasks.json.tmp-1234").write_text("{}", encoding="utf-8")
    with pytest.raises(OrkestrError, match="leftover temporary file"):
        Project(tmp_path).import_phase1()


def test_secrets_are_redacted_before_writing_and_detected_by_validate(tmp_path):
    p = approved_plan(tmp_path)
    token = "ghp_" + "a1B2" * 9
    p.move("T-001", "BLOCKED", reason=f"token={token} expired")
    raw = (tmp_path / ".orkestr" / "tasks.json").read_text(encoding="utf-8") + (tmp_path / ".orkestr" / "history.jsonl").read_text(encoding="utf-8")
    assert token not in raw and "[REDACTED]" in raw
    db_dump = "\n".join(p.project.store.query("SELECT blocked_reason FROM task WHERE id = 'T-001'")[0].values())
    assert token not in db_dump
    p.validate_ok()

    from orkestr_core.domain.validate import validate_state

    state = p.project.store.load()
    state.project["goal"] = "password: hunter2hunter2"
    assert any("possible secret" in e for e in validate_state(state, [])["errors"])


def test_every_state_change_is_recorded_in_history(tmp_path):
    p = approved_plan(tmp_path)
    complete_t1(p)
    transitions = [f"{e['from']}>{e['to']}" for e in p.history() if e["type"] == "task.transition" and e["entity"] == "T-001"]
    assert transitions == ["BACKLOG>READY", "READY>IN_PROGRESS", "IN_PROGRESS>REVIEW", "REVIEW>COMPLETED"]
    for e in p.history():
        assert e["ts"] and e["type"] and "actor" in e and "summary" in e


def test_redaction_helpers_cover_common_secret_formats():
    samples = [
        "sk-ant-" + "x" * 30,
        "AKIA" + "A" * 16,
        "Bearer " + "abc.def-" * 3,
        'api_key = "abcdef123456"',
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
    ]
    for s in samples:
        out = redact_string(s)
        assert "[REDACTED]" in out, s
        assert find_secrets(out) == [], out
        assert find_secrets(s), s
    assert redact_string("token budget is 4 meetings") == "token budget is 4 meetings"


def test_unicode_turkish_project_names_are_stored_and_reported_correctly(tmp_path):
    p = Project(tmp_path)
    p.init("Müşteri Portalı", "Çalışan bir MVP")
    assert json.loads((tmp_path / ".orkestr" / "project.json").read_text(encoding="utf-8"))["name"] == "Müşteri Portalı"
    assert "Müşteri Portalı" in p.status_text()

