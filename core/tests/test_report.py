"""Completion report details added in Phase 4 (PHASE4_IMPLEMENTATION.md §4, step 0.1)."""

from __future__ import annotations

import re

from orkestr_core.domain import state as st

from phase1_helpers import P


def test_scope_checks_are_listed_apart_from_tests(tmp_path):
    p = P(tmp_path, autonomy="bounded")
    p.plan()
    p.move("T-001", "IN_PROGRESS")
    p.do(lambda s: st.scope_check(s, ["src/api.js"], task_id="T-001"))
    p.evidence("T-001", "test", "passed", "12 passed")
    out = p.report()
    tests = re.search(r"## Tests and checks run\n\n([\s\S]*?)\n\n", out).group(1)
    assert "`npm test` → passed — 12 passed" in tests
    assert st.SCOPE_CHECK_COMMAND not in tests
    assert re.search(r"### Scope checks\n\n- T-001: (passed|failed) — ", out)


def test_a_report_without_scope_checks_has_no_scope_section(tmp_path):
    p = P(tmp_path, autonomy="bounded")
    p.plan()
    p.move("T-001", "IN_PROGRESS")
    p.evidence("T-001", "test", "passed", "12 passed")
    assert "### Scope checks" not in p.report()


def test_only_scope_checks_means_no_tests_recorded(tmp_path):
    p = P(tmp_path, autonomy="bounded")
    p.plan()
    p.move("T-001", "IN_PROGRESS")
    p.do(lambda s: st.scope_check(s, ["src/api.js"], task_id="T-001"))
    out = p.report()
    assert re.search(r"## Tests and checks run\n\n- \(no checks recorded\)", out)
    assert "### Scope checks" in out


def test_founder_tasks_are_listed_as_needing_the_founder(tmp_path):
    p = P(tmp_path, autonomy="bounded")
    p.add_task("Decide the leftover-cent policy", "founder")
    p.project.authorize_task("T-001")
    assert p.task("T-001")["status"] == "READY"
    assert re.search(r"## Needs the founder\n\n[\s\S]*- Your task T-001: Decide the leftover-cent policy", p.report())
    assert re.search(r"### Your tasks\n\n- T-001 \[READY\] Decide the leftover-cent policy", p.status())
