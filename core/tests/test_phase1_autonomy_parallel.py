"""Ported from plugins/orkestr/tests/autonomy.test.mjs and parallel.test.mjs."""

from __future__ import annotations

import re

from orkestr_core.domain import records as rec
from orkestr_core.domain import state as st
from orkestr_core.domain.scope import file_in_scope, patterns_overlap, scope_problems, scopes_overlap
from orkestr_core.domain.validate import validate_state

from phase1_helpers import P, fail_four_times, meeting_draft, recovery_draft


def bounded(tmp_path):
    return P(tmp_path, autonomy="bounded")


# --- autonomy.test.mjs ------------------------------------------------------------------------


def test_bounded_an_imported_plan_is_authorized_at_once(tmp_path):
    p = bounded(tmp_path)
    p.plan()
    assert p.task("T-001")["status"] == "READY"
    assert p.task("T-002")["status"] == "BACKLOG"
    assert p.task("T-001")["authorized"] is True
    assert any(e["type"] == "task.authorize" and e["actor"] == "orchestrator" for e in p.history())
    p.fails(lambda s: st.approve_plan(s, by="founder"), "no unapproved plan tasks")
    p.validate_ok()


def test_bounded_restricted_tasks_still_need_a_founder_granted_approval(tmp_path):
    p = bounded(tmp_path)
    p.plan({"tasks": [{"title": "Deploy to production", "owner": "orchestrator", "acceptance_criteria": ["live"], "requires_approval": True}]})
    assert p.task("T-001")["status"] == "READY"
    p.fails(lambda s: st.transition(s, "T-001", "IN_PROGRESS"), "no approval request")
    p.do(lambda s: st.request_approval(s, action="deploy", category="deploy", reason="release", task_id="T-001"))
    p.fails(lambda s: st.transition(s, "T-001", "IN_PROGRESS"), "PENDING")
    p.fails(lambda s: st.decide_approval(s, "A-001", "grant", by="orchestrator"), "founder-only")


def test_bounded_a_decided_technical_meeting_takes_effect_automatically(tmp_path):
    p = bounded(tmp_path)
    r = p.meeting(meeting_draft())
    assert r["decision"]["approval_status"] == "APPROVED"
    d = p.read("decisions/D-001.json")
    assert d["decided_by"] == "orchestrator"
    assert p.task("T-001")["status"] == "READY"
    assert p.task("T-002")["status"] == "BACKLOG"


def test_bounded_founder_level_decisions_still_wait_for_the_founder(tmp_path):
    p = bounded(tmp_path)
    draft = meeting_draft()
    draft["decision"]["founder_required"] = True
    p.meeting(draft)
    assert p.read("decisions/D-001.json")["approval_status"] == "PROPOSED"
    assert p.task("T-001")["status"] == "BACKLOG"

    q = bounded(tmp_path / "q")
    q.meeting(meeting_draft(outcome="ESCALATED", decision={"question": "Change the product scope?", "selected_option": None, "rationale": "", "tradeoffs": [], "risks": []}, action_items=[]))
    assert q.read("decisions/D-001.json")["approval_status"] == "NEEDS_FOUNDER"


def test_bounded_after_3_fix_attempts_a_recovery_meeting_reopens_the_task(tmp_path):
    p = bounded(tmp_path)
    p.plan()
    fail_four_times(p, "T-001")
    t1 = p.task("T-001")
    assert t1["status"] == "FAILED"
    assert "Recovery meeting required" in t1["failure"]["next_action"]
    assert p.task("T-002")["status"] == "BLOCKED"

    p.meeting(recovery_draft("T-001"))
    t1 = p.task("T-001")
    assert (t1["status"], t1["fix_attempts"], t1["recovery_meetings"]) == ("READY", 0, 1)
    assert re.search(r"Recovery decision D-001: Mock the clock in tests", t1["notes"][-1]["text"])
    assert p.task("T-002")["status"] == "BACKLOG"
    assert p.read("meetings/M-001.json")["recovery_for"] == "T-001"
    p.validate_ok()


def test_bounded_when_the_recovery_budget_is_used_up_the_founder_is_informed(tmp_path):
    p = bounded(tmp_path)
    p.plan()
    fail_four_times(p, "T-001")
    p.meeting(recovery_draft("T-001"))
    fail_four_times(p, "T-001")
    t1 = p.task("T-001")
    assert t1["status"] == "FAILED"
    assert "Founder decision required" in t1["failure"]["next_action"]
    p.fails(lambda s: rec.record_meeting(s, recovery_draft("T-001")), "already used its 1 recovery meeting")
    assert "T-001 could not be fixed within limits" in p.report()


def test_bounded_a_recovery_meeting_without_a_decision_hands_the_task_to_the_founder(tmp_path):
    p = bounded(tmp_path)
    p.plan()
    fail_four_times(p, "T-001")
    p.meeting(recovery_draft("T-001", outcome="ESCALATED", decision={"question": "How do we fix T-001?", "selected_option": None, "rationale": "", "tradeoffs": [], "risks": []}))
    t1 = p.task("T-001")
    assert (t1["status"], t1["recovery_meetings"]) == ("FAILED", 1)
    assert "Founder decision required" in t1["failure"]["next_action"]
    p.project.decide_decision("D-001", "approve", option="O2")
    assert p.task("T-001")["status"] == "READY"


def test_recovery_meetings_are_only_for_failed_tasks(tmp_path):
    p = bounded(tmp_path)
    p.plan()
    p.fails(lambda s: rec.record_meeting(s, recovery_draft("T-001")), "T-001 is READY, not FAILED")


def test_supervised_a_recovery_decision_waits_for_the_founder(tmp_path):
    p = P(tmp_path)
    p.plan()
    p.approve_plan()
    fail_four_times(p, "T-001")
    assert "Founder decision required" in p.task("T-001")["failure"]["next_action"]


def test_changing_autonomy_is_founder_only_old_projects_count_as_supervised(tmp_path):
    p = bounded(tmp_path)
    p.fails(lambda s: st.set_autonomy(s, "supervised", by="orchestrator"), "founder-only")
    p.fails(lambda s: st.set_autonomy(s, "wild", by="founder"), "Autonomy must be one of")
    p.do(lambda s: st.set_autonomy(s, "supervised", by="founder"))
    assert p.read("project.json")["autonomy"] == "supervised"

    old = bounded(tmp_path / "old")

    def legacy(s):
        s.project.pop("autonomy")
        s.project["limits"].pop("max_recovery_meetings")

    old.do(legacy)
    old.validate_ok()
    old.plan()
    assert old.task("T-001")["status"] == "BACKLOG"
    assert "Autonomy:** supervised" in old.status()


def test_assumptions_and_hand_off_notes_are_shared_records_shown_in_status_and_report(tmp_path):
    p = bounded(tmp_path)
    p.plan()
    p.do(lambda s: st.add_assumption(s, "Single-user app; no authentication in the MVP", impact="medium"))
    p.do(lambda s: st.add_assumption(s, "Node 20 is the runtime", task_id="T-001"))
    p.fails(lambda s: st.add_assumption(s, "x", impact="huge"), "--impact")
    p.do(lambda s: st.add_note(s, "T-001", author="qa-engineer", text="Test test_list fails: expected 3 items, got 2"))
    p.fails(lambda s: st.add_note(s, "T-001", author="intern", text="x"), "--author")
    assert p.task("T-001")["notes"][0]["author"] == "qa-engineer"
    assert re.search(r"Assumptions made without asking[\s\S]*\[medium\] Single-user app", p.status())
    types = [e["type"] for e in p.history()]
    assert "task.note" in types and "assumption.record" in types


def test_completion_report_lists_work_done_checks_that_ran_assumptions_and_open_items(tmp_path):
    p = bounded(tmp_path)
    p.plan()
    p.do(lambda s: st.add_assumption(s, "SQLite is acceptable", impact="low"))
    p.move("T-001", "IN_PROGRESS")
    p.evidence("T-001", "test", "passed", "12 passed")
    p.move("T-001", "REVIEW")
    p.evidence("T-001", "review", "passed", "QA ok", command=None)
    p.move("T-001", "COMPLETED")
    p.evidence("T-002", "test", "not_run", "no UI test runner yet", command=None)
    out = p.report()
    assert re.search(r"## What was done[\s\S]*T-001 — Build API", out)
    assert re.search(r"## Tests and checks run[\s\S]*`npm test` → passed — 12 passed", out)
    assert re.search(r"### Not run[\s\S]*T-002: not run — no UI test runner yet", out)
    assert re.search(r"## Assumptions[\s\S]*SQLite is acceptable", out)
    assert re.search(r"## Unresolved[\s\S]*T-002 READY", out)
    assert re.search(r"## Needs the founder[\s\S]*Nothing\.", out)


def test_a_decided_meeting_can_steer_existing_tasks_via_applies_to(tmp_path):
    p = bounded(tmp_path)
    p.plan()
    p.meeting(meeting_draft(action_items=[], applies_to=["T-001"]))
    assert len(p.read("tasks.json")["tasks"]) == 2
    assert p.read("decisions/D-001.json")["approval_status"] == "APPROVED"
    assert p.task("T-001")["notes"][-1]["text"].startswith("D-001: ")
    p.validate_ok()
    p.fails(lambda s: rec.record_meeting(s, meeting_draft(action_items=[])), 'at least one action item, or "applies_to"')
    p.fails(lambda s: rec.record_meeting(s, meeting_draft(action_items=[], applies_to=["T-099"])), "unknown task T-099")


# --- parallel.test.mjs -------------------------------------------------------------------------


def task(title, owner, scope=None, **extra):
    t = {"title": title, "owner": owner, "acceptance_criteria": [f"{title} works"], **extra}
    if scope is not None:
        t["scope"] = scope
    return t


def three_independent(tmp_path):
    p = bounded(tmp_path)
    p.plan({"tasks": [task("API", "backend-engineer", ["src/api/**", "tests/api/**"]), task("UI", "frontend-engineer", ["src/ui/**", "tests/ui/**"]), task("CLI", "backend-engineer", ["src/cli/**"])]})
    return p


def test_scope_patterns_overlap_is_conservative_matching_follows_glob_rules():
    assert patterns_overlap("src/api/**", "src/api/users.js")
    assert patterns_overlap("src/api", "src/api/users.js")
    assert patterns_overlap("src/**", "src/ui/x.js")
    assert patterns_overlap("**/*.js", "docs/a.md")
    assert patterns_overlap("SRC/Api/**", "src/api/x.js")
    assert not patterns_overlap("src/api/**", "src/ui/**")
    assert not patterns_overlap("src/a.js", "src/a.json")
    assert scopes_overlap([], ["src/**"])

    assert file_in_scope("src/api/v1/users.js", ["src/api/**"])
    assert file_in_scope("src\\api\\users.js", ["src/api/**"])
    assert file_in_scope("src/api/users.js", ["src/api"])
    assert file_in_scope("src/main.js", ["src/*.js"])
    assert not file_in_scope("src/lib/main.js", ["src/*.js"])
    assert not file_in_scope("src/ui/app.js", ["src/api/**"])

    assert scope_problems(["src/**"]) == []
    for bad in (["../outside"], ["/etc/passwd"], ["C:\\x"], [".orkestr/tasks.json"]):
        assert len(scope_problems(bad)) == 1, bad


def test_two_tasks_with_disjoint_scopes_run_in_parallel_the_limit_stops_a_third(tmp_path):
    p = three_independent(tmp_path)
    p.move("T-001", "IN_PROGRESS")
    p.move("T-002", "IN_PROGRESS")
    p.fails(lambda s: st.transition(s, "T-003", "IN_PROGRESS"), r"parallel limit reached \(2/2")
    assert "scope: src/api/**, tests/api/**" in p.status()
    p.validate_ok()


def test_overlapping_scopes_cannot_run_at_the_same_time(tmp_path):
    p = bounded(tmp_path)
    p.plan({"tasks": [task("Users API", "backend-engineer", ["src/api/**"]), task("Auth middleware", "backend-engineer", ["src/api/auth.js"])]})
    p.move("T-001", "IN_PROGRESS")
    p.fails(lambda s: st.transition(s, "T-002", "IN_PROGRESS"), r"conflicts with T-001 \(in progress\): their file scopes overlap")


def test_a_task_without_a_scope_is_exclusive_in_both_directions(tmp_path):
    p = bounded(tmp_path)
    p.plan({"tasks": [task("Refactor everything", "tech-lead"), task("UI", "frontend-engineer", ["src/ui/**"])]})
    p.move("T-001", "IN_PROGRESS")
    p.fails(lambda s: st.transition(s, "T-002", "IN_PROGRESS"), "runs exclusively")

    q = bounded(tmp_path / "q")
    q.plan({"tasks": [task("UI", "frontend-engineer", ["src/ui/**"]), task("Refactor everything", "tech-lead", [])]})
    q.move("T-001", "IN_PROGRESS")
    q.fails(lambda s: st.transition(s, "T-002", "IN_PROGRESS"), "runs exclusively")


def test_next_parallel_returns_a_conflict_free_batch_within_the_limit(tmp_path):
    p = bounded(tmp_path)
    p.plan(
        {
            "tasks": [
                task("API", "backend-engineer", ["src/api/**"]),
                task("API auth", "backend-engineer", ["src/api/auth/**"]),
                task("UI", "frontend-engineer", ["src/ui/**"]),
                task("Docs", "product-manager", ["docs/**"]),
            ]
        }
    )
    assert [t["id"] for t in st.parallel_candidates(p.project.store.load())] == ["T-001", "T-003"]
    p.move("T-001", "IN_PROGRESS")
    assert [t["id"] for t in st.parallel_candidates(p.project.store.load())] == ["T-003"]


def test_scope_check_records_a_failed_check_for_out_of_scope_changes(tmp_path):
    p = three_independent(tmp_path)
    p.move("T-001", "IN_PROGRESS")
    p.move("T-002", "IN_PROGRESS")
    good = p.do(lambda s: st.scope_check(s, ["src/api/users.js", "tests\\api\\users.test.js"], "T-001"))
    assert good["violations"] == []
    bad = p.do(lambda s: st.scope_check(s, ["src/api/users.js", "src/ui/app.js", "package.json"], "T-001"))
    rows = {r["file"]: r for r in bad["rows"]}
    assert rows["src/ui/app.js"]["owners"] == ["T-002"] and rows["src/ui/app.js"]["problem"] == "outside T-001's scope"
    assert rows["package.json"]["owners"] == [] and rows["package.json"]["problem"] == "outside T-001's scope"
    t1 = p.task("T-001")
    assert t1["fix_attempts"] == 1
    assert (t1["evidence"][-1]["command"], t1["evidence"][-1]["result"]) == ("orkestr scope check", "failed")


def test_scope_check_without_a_task_attributes_every_changed_file_or_reports_it(tmp_path):
    p = three_independent(tmp_path)
    p.move("T-001", "IN_PROGRESS")
    p.move("T-002", "IN_PROGRESS")
    r = st.scope_check(p.project.store.load(), ["src/api/a.js", "src/ui/b.js", "README.md"])
    rows = {x["file"]: x for x in r["rows"]}
    assert rows["src/api/a.js"]["owners"] == ["T-001"] and rows["src/api/a.js"]["problem"] is None
    assert rows["src/ui/b.js"]["owners"] == ["T-002"]
    assert rows["README.md"]["problem"] == "not in the scope of any active task"
    assert p.task("T-001")["evidence"] == []


def test_rework_from_review_waits_if_an_overlapping_task_started_meanwhile(tmp_path):
    p = bounded(tmp_path)
    p.plan({"tasks": [task("API", "backend-engineer", ["src/api/**"]), task("API v2", "backend-engineer", ["src/api/v2/**"])]})
    p.move("T-001", "IN_PROGRESS")
    p.move("T-001", "REVIEW")
    p.move("T-002", "IN_PROGRESS")
    p.fails(lambda s: st.transition(s, "T-001", "IN_PROGRESS"), "conflicts with T-002")


def test_invalid_scopes_are_rejected_at_import_manual_tasks_accept_scope(tmp_path):
    p = bounded(tmp_path)
    p.fails(lambda s: st.import_plan(s, {"tasks": [task("X", "backend-engineer", ["../../etc"])]}), r'must not contain "\.\."')
    p.add_task("Docs", "product-manager", ["written"], scope=["docs/**"])
    assert p.task("T-001")["scope"] == ["docs/**"]


def test_legacy_projects_without_max_parallel_tasks_stay_sequential_validate_catches_overlaps(tmp_path):
    p = three_independent(tmp_path)
    p.do(lambda s: s.project["limits"].pop("max_parallel_tasks"))
    p.move("T-001", "IN_PROGRESS")
    p.fails(lambda s: st.transition(s, "T-002", "IN_PROGRESS"), r"parallel limit reached \(1/1")

    state = p.project.store.load()
    state.tasks[1]["status"] = "IN_PROGRESS"
    state.tasks[1]["scope"] = ["src/api/x.js"]
    errors = validate_state(state, [])["errors"]
    assert any("2 tasks IN_PROGRESS but limits.max_parallel_tasks is 1" in e for e in errors)
    assert any("T-001 and T-002 are both IN_PROGRESS with overlapping scopes" in e for e in errors)


def test_system_architect_ml_engineer_and_data_engineer_are_valid_owners_devops_is_not(tmp_path):
    p = bounded(tmp_path)
    p.add_task("System design", "system-architect", ["architecture.md written"], scope=["docs/architecture/**"])
    p.add_task("Ranking model", "ml-engineer", ["F1 >= baseline"], scope=["src/ml/**"])
    p.add_task("Schema and seed data", "data-engineer", ["migration reversible"], scope=["db/**"])
    assert [p.task(t)["owner"] for t in ("T-001", "T-002", "T-003")] == ["system-architect", "ml-engineer", "data-engineer"]
    p.fails(lambda s: st.create_tasks(s, [task("Deploy", "devops-engineer")], {"type": "manual", "ref": None}), "owner")


def test_a_passed_scope_check_alone_is_not_verification_for_completion(tmp_path):
    p = three_independent(tmp_path)
    p.move("T-001", "IN_PROGRESS")
    p.do(lambda s: st.scope_check(s, ["src/api/users.js"], "T-001"))
    p.move("T-001", "REVIEW")
    p.do(lambda s: rec.record_review(s, {"task_id": "T-001", "reviewers": ["qa-engineer"], "scope": "API", "verdict": "PASS", "checks_run": [], "findings": [], "assumptions": []}))
    p.fails(lambda s: st.transition(s, "T-001", "COMPLETED"), "scope check does not count")

