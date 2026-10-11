"""Helpers for the ported Phase 1 tests: a project managed by the core, driven through the
same domain operations the plugin's state tool exposes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from orkestr_core.domain import records as rec
from orkestr_core.domain import state as st
from orkestr_core.domain.errors import OrkestrError
from orkestr_core.service import Project

TWO_TASK_PLAN = {
    "summary": "API then UI",
    "tasks": [
        {"title": "Build API", "owner": "backend-engineer", "acceptance_criteria": ["GET /items returns 200"]},
        {"title": "Build UI", "owner": "frontend-engineer", "acceptance_criteria": ["List renders"], "depends_on": ["#1"]},
    ],
}


def meeting_draft(**overrides) -> dict:
    draft = {
        "objective": "Choose the API style",
        "context": "Need an API for the list view.",
        "trigger": "Affects backend, frontend, and QA.",
        "participants": [
            {"role": "tech-lead", "responsibility": "architecture"},
            {"role": "backend-engineer", "responsibility": "API implementation"},
            {"role": "frontend-engineer", "responsibility": "API consumer"},
        ],
        "rounds": [
            {
                "round": 1,
                "contributions": [
                    {"role": "tech-lead", "position": "REST", "key_points": ["simple"], "concerns": []},
                    {"role": "backend-engineer", "position": "REST", "key_points": [], "concerns": []},
                    {"role": "frontend-engineer", "position": "GraphQL", "key_points": ["flexible"], "concerns": ["overfetching"]},
                ],
            }
        ],
        "options": [
            {"id": "O1", "name": "REST", "summary": "Plain JSON endpoints"},
            {"id": "O2", "name": "GraphQL", "summary": "Single endpoint"},
        ],
        "criteria": ["simplicity", "cost"],
        "evaluation": "REST is simpler for an MVP.",
        "outcome": "DECIDED",
        "decision": {"question": "Which API style?", "selected_option": "O1", "rationale": "Simplest option", "tradeoffs": ["less flexible"], "risks": []},
        "dissent": [{"role": "frontend-engineer", "objection": "May need more endpoints later"}],
        "open_questions": [],
        "action_items": [
            {"title": "Define REST endpoints", "owner": "backend-engineer", "acceptance_criteria": ["OpenAPI file exists"]},
            {"title": "Consume endpoints", "owner": "frontend-engineer", "acceptance_criteria": ["List loads"], "depends_on": ["#1"]},
        ],
    }
    draft.update(overrides)
    return draft


def recovery_draft(task_id: str, **overrides) -> dict:
    base = {
        "objective": f"Unblock {task_id}",
        "trigger": "Fix-attempt limit reached",
        "recovery_for": task_id,
        "options": [
            {"id": "O1", "name": "Mock the clock in tests", "summary": "Tests depend on wall time"},
            {"id": "O2", "name": "Rewrite the scheduler", "summary": "Bigger change"},
        ],
        "decision": {"question": f"How do we fix {task_id}?", "selected_option": "O1", "rationale": "Root cause is time-dependent tests", "tradeoffs": [], "risks": []},
        "action_items": [],
    }
    base.update(overrides)
    return meeting_draft(**base)


class P:
    """A core-managed project with Phase 1 style helpers."""

    def __init__(self, root: Path, autonomy: str = "supervised", init: bool = True):
        self.root = root
        self.project = Project(root)
        if init:
            self.project.init("Test project", "Ship a small app", autonomy=autonomy)

    def do(self, fn):
        return self.project.mutate(fn)

    def fails(self, fn, pattern: str) -> OrkestrError:
        with pytest.raises(OrkestrError) as info:
            self.project.mutate(fn)
        assert re.search(pattern, info.value.message), info.value.message
        return info.value

    def task(self, tid: str) -> dict:
        return st.get_task(self.project.store.load(), tid)

    def read(self, rel: str):
        """A Phase 1 mirror file under .orkestr/."""
        return json.loads((self.project.dir / rel).read_text(encoding="utf-8"))

    def history(self) -> list[dict]:
        return self.project.store.history()

    def plan(self, draft=TWO_TASK_PLAN):
        return self.do(lambda s: st.import_plan(s, draft))

    def approve_plan(self):
        return self.do(lambda s: st.approve_plan(s, by="founder"))

    def move(self, tid, to, **kw):
        return self.do(lambda s: st.transition(s, tid, to, **kw))

    def evidence(self, tid, type, result, summary="ok", command="npm test", **kw):
        return self.do(lambda s: st.add_evidence(s, tid, type=type, result=result, command=command, summary=summary, **kw))

    def meeting(self, draft):
        return self.do(lambda s: rec.record_meeting(s, draft))

    def add_task(self, title, owner, criteria=("done",), **extra):
        item = {"title": title, "owner": owner, "acceptance_criteria": list(criteria), **extra}
        return self.do(lambda s: st.create_tasks(s, [item], {"type": "manual", "ref": None}))[0]

    def status(self) -> str:
        return self.project.status_text()

    def report(self) -> str:
        return self.project.report_text()

    def validate_ok(self):
        v = self.project.validate()
        assert not v["errors"], v["errors"]


def approved_plan(tmp_path: Path) -> P:
    p = P(tmp_path)
    p.plan()
    p.approve_plan()
    return p


def complete_t1(p: P):
    p.move("T-001", "IN_PROGRESS")
    p.evidence("T-001", "test", "passed", "all tests pass")
    p.move("T-001", "REVIEW")
    p.evidence("T-001", "review", "passed", "reviewed by QA", command=None)
    p.move("T-001", "COMPLETED")


def fail_four_times(p: P, tid: str) -> dict:
    p.move(tid, "IN_PROGRESS")
    last = None
    for i in range(4):
        last = p.evidence(tid, "test", "failed", f"still failing {i + 1}")
    return last
