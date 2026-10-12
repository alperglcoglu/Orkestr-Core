"""Permission policies (PHASE4_IMPLEMENTATION.md §6, acceptance 7–8)."""

from __future__ import annotations

import pytest

from orkestr_core.domain import policy as pol

from conftest import needs_node


@pytest.mark.parametrize("prefix", ["go test", "go test ./...", "cargo test", "make test"])
def test_test_runner_prefixes_are_accepted(prefix):
    assert pol.test_command_problem(prefix) is None


@pytest.mark.parametrize(
    "prefix, why",
    [
        ("go", "at least two words"),
        ("python script.py", "can run any code"),
        ("bash run-tests.sh", "can run any code"),
        ("node test.js", "can run any code"),
        ("npx vitest run", "can run any code"),
        ("git status", "git commands"),
        ("go test; git push", "shell operators"),
        ("go test $(whoami)", "shell operators"),
        ("go test *", "shell operators"),
        ("go  test", "extra spaces"),
        ("", "non-empty"),
    ],
)
def test_test_command_prefixes_that_could_widen_access_are_rejected(prefix, why):
    assert why in (pol.test_command_problem(prefix) or "")


@needs_node
@pytest.mark.parametrize("prefix", ["npm publish now", "rm -rf build", "terraform apply now", "chmod 777 x"])
def test_the_guard_rules_are_applied_to_test_command_prefixes(prefix):
    assert "flagged as risky" in (pol.test_command_problem(prefix) or "")


def test_a_valid_policy_has_no_problems():
    policy = {
        "test_commands": ["go test ./..."],
        "protected_paths": ["LICENSE", ".github/**"],
        "approval_paths": ["src/billing/**"],
        "security_review_paths": ["src/auth/**"],
        "limits": {"max_agent_calls_per_run": 40, "max_fix_attempts": 2, "max_reported_cost_usd_per_run": 1.5},
    }
    assert pol.policy_problems(policy) == []


@pytest.mark.parametrize(
    "policy, why",
    [
        ({"tools": ["WebFetch"]}, "unknown field"),
        ({"approvals": {"git_push": False}}, "unknown field"),
        ({"protected_paths": "LICENSE"}, "must be a list"),
        ({"protected_paths": ["/etc/passwd"]}, "relative to the project root"),
        ({"approval_paths": ["../x"]}, 'must not contain ".."'),
        ({"protected_paths": [".orkestr/**"]}, "must not include .orkestr"),
        ({"limits": {"max_agent_calls_per_run": 201}}, "from 1 to 200"),
        ({"limits": {"max_parallel_tasks": 0}}, "from 1 to 4"),
        ({"limits": {"max_fix_attempts": True}}, "from 1 to 5"),
        ({"limits": {"max_reported_cost_usd_per_run": -1}}, "positive number"),
        ({"limits": {"max_turns": 5}}, "not a known limit"),
        ([], "JSON object"),
    ],
)
def test_policies_cannot_loosen_rules_or_exceed_the_hard_caps(policy, why):
    problems = pol.policy_problems(policy)
    assert any(why in p for p in problems), problems


def test_every_hard_cap_is_at_least_the_phase3_default():
    from orkestr_core.constants import CORE_LIMITS, DEFAULT_LIMITS

    for key, default in {**DEFAULT_LIMITS, **CORE_LIMITS}.items():
        assert pol.HARD_CAPS[key] >= default, key


def test_scope_helpers():
    policy = {"protected_paths": ["LICENSE", "db/migrations/**"], "approval_paths": ["src/billing/**"], "security_review_paths": ["src/auth/**"]}
    assert pol.protected_overlap(["src/**"], policy) == []
    assert pol.protected_overlap(["db/**"], policy) == ["db/migrations/**"]
    assert pol.protected_overlap([], policy) == ["LICENSE", "db/migrations/**"], "an unscoped task may touch anything"
    assert pol.protected_files(["LICENSE", "src/a.js", "db/migrations/001.sql"], policy) == ["LICENSE", "db/migrations/001.sql"]
    assert pol.needs_approval(["src/billing/**"], policy) and not pol.needs_approval(["src/ui/**"], policy)
    assert pol.needs_approval([], policy), "an unscoped task may touch approval paths"
    assert pol.security_review(["src/auth/login.js"], policy) and not pol.security_review(["src/ui/**"], policy)
    assert pol.protected_overlap(["src/**"], {}) == [] and not pol.needs_approval([], {})


# --- stored policy (service and store) ----------------------------------------------------------


def test_policy_versions_are_appended_with_a_reason_and_an_audit_event(tmp_path):
    from orkestr_core.domain.errors import OrkestrError
    from phase1_helpers import P

    p = P(tmp_path)
    assert p.project.policy()["version"] == 0
    v = p.project.set_policy({"protected_paths": ["LICENSE"]}, "keep the license")
    assert v == 1 and p.project.policy()["policy"]["protected_paths"] == ["LICENSE"]
    assert p.project.policy()["policy"]["limits"] == {}, "missing fields are filled in"
    with pytest.raises(OrkestrError, match="reason is required"):
        p.project.set_policy({}, " ")
    with pytest.raises(OrkestrError, match="Policy rejected"):
        p.project.set_policy({"limits": {"max_parallel_tasks": 9}}, "faster")
    assert [x["version"] for x in p.project.store.policy_versions()] == [0, 1]
    ev = [e for e in p.history() if e["type"] == "policy.update"]
    assert len(ev) == 1 and ev[0]["summary"] == "keep the license" and ev[0]["actor"] == "founder"


def test_the_founder_changes_autonomy_with_a_reason(tmp_path):
    from orkestr_core.domain.errors import OrkestrError
    from phase1_helpers import P

    p = P(tmp_path, autonomy="bounded")
    with pytest.raises(OrkestrError, match="reason is required"):
        p.project.set_autonomy("supervised", "")
    p.project.set_autonomy("supervised", "I want to review plans")
    assert p.project.store.load().project["autonomy"] == "supervised"
    ev = [e for e in p.history() if e["type"] == "project.autonomy"][-1]
    assert ev["to"] == "supervised" and ev["summary"] == "I want to review plans"
