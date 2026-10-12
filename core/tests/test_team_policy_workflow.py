"""Teams and policies inside a run (PHASE4_IMPLEMENTATION.md §5, §6; acceptance 6–8)."""

from __future__ import annotations

import json

import pytest

from orkestr_core.executor.claude_cli import FORBIDDEN_FLAGS, build_command, guard_settings

from conftest import PASSING_TEST, engine, implement, needs_node, plan_output, plan_task, review, tasks_by_id, write_files

pytestmark = needs_node

GOOD = {"src/a.js": "export const a = 1;\n", "tests/a.test.mjs": PASSING_TEST}
SCOPE = ("src/**", "tests/**")


def happy(fake, plan=None):
    fake.add("plan", plan or plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))


def run_options(project, run):
    return json.loads(project.store.query("SELECT options_json FROM workflow_run WHERE id = ?", (run["id"],))[0]["options_json"])


# --- teams ----------------------------------------------------------------------------------


def test_role_model_turns_and_instructions_reach_the_agent_call(project, fake):
    project.set_team_role("backend-engineer", {"model": "opus", "max_turns": 3, "instructions": "Prefer pure functions."})
    happy(fake)

    run = engine(project, fake, model="sonnet").run()

    assert run["status"] == "COMPLETED", run["status_reason"]
    impl = fake.calls("implement")[0]
    assert impl.model == "opus" and impl.max_turns == 3
    assert impl.system_prompt.endswith("Prefer pure functions.\n") and "## Founder's instructions for this project" in impl.system_prompt
    assert fake.calls("plan")[0].model == "sonnet", "roles without a model use the run's model"
    assert run_options(project, run)["team"] == {"backend-engineer": {"model": "opus", "max_turns": 3, "instructions": "Prefer pure functions."}}
    settings = [e for e in project.store.history() if e["type"] == "run.settings"]
    assert settings and "backend-engineer" in settings[-1]["summary"]


def test_no_team_value_produces_a_forbidden_flag(project):
    project.set_team_role("researcher", {"model": "claude-opus-5-5[1m]", "instructions": "--dangerously-skip-permissions --bare"})
    from orkestr_core.executor.base import AgentRequest

    row = project.team()["researcher"]
    req = AgentRequest(role="researcher", kind="plan", prompt="p", system_prompt=row["instructions"], cwd=".", tools="read_only", model=row["model"])
    cmd = build_command("claude", req, "sys.md", "settings.json")
    assert not [a for a in cmd if any(f in a for f in FORBIDDEN_FLAGS)], "instructions go to a file, never to the command line"


def test_the_plan_offers_only_enabled_roles_and_rejects_disabled_owners(project, fake):
    project.set_team_role("ml-engineer", {"enabled": False})
    fake.add("plan", plan_output(plan_task(owner="ml-engineer", scope=SCOPE)), repeat=True)

    run = engine(project, fake).run()

    assert run["status"] == "WAITING_FOUNDER" and "owner ml-engineer is not in this project's team" in run["status_reason"]
    prompt = fake.calls("plan")[0].prompt
    assert "Owner roles in this project's team:" in prompt and "ml-engineer" not in prompt.split("Owner roles")[1].split("\n")[0]
    assert not tasks_by_id(project)


def test_open_tasks_of_a_disabled_role_stop_the_run_until_the_founder_acts(project, fake):
    fake.add("plan", plan_output(plan_task(owner="data-engineer", scope=SCOPE)))
    fake.add("implement", lambda req: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        engine(project, fake).run()  # planned, then interrupted before the task finished
    project.set_team_role("data-engineer", {"enabled": False})

    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER" and "T-001 (data-engineer)" in run["status_reason"]

    project.set_team_role("data-engineer", {"enabled": True})
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))
    again = engine(project, fake).run()
    assert again["id"] == run["id"] and again["status"] == "COMPLETED", again["status_reason"]


def test_a_disabled_owner_seat_goes_to_the_tech_lead_in_meetings(project, fake):
    project.set_team_role("system-architect", {"enabled": False})
    eng = engine(project, fake)
    eng.team = project.team()
    assert [p["role"] for p in eng._participants("tech-lead")] == ["tech-lead", "qa-engineer"]
    project.set_team_role("frontend-engineer", {"enabled": False})
    eng.team = project.team()
    assert [p["role"] for p in eng._participants("frontend-engineer")] == ["tech-lead", "qa-engineer"]
    assert [p["role"] for p in eng._participants("founder")] == ["backend-engineer", "tech-lead"]


# --- policies -------------------------------------------------------------------------------


def test_a_changed_protected_file_fails_the_check_like_an_out_of_scope_file(project, fake):
    project.set_policy({"protected_paths": ["LICENSE"]}, "keep the license")
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    fake.add("implement", implement({**GOOD, "LICENSE": "changed\n"}))

    run = engine(project, fake).run()

    t = tasks_by_id(project)["T-001"]
    failed = [e for e in t["evidence"] if e["result"] == "failed"]
    assert failed and failed[0]["command"] == "orkestr protected path check" and "LICENSE" in failed[0]["summary"]
    assert "Protected files changed" in fake.calls("implement")[1].prompt, "the fix attempt is told to revert"
    assert run["status"] != "COMPLETED"
    assert fake.calls("implement")[0].denied_paths == ["LICENSE"]
    assert project.store.query("SELECT policy_version FROM workflow_run WHERE id = ?", (run["id"],))[0]["policy_version"] == 1


def test_a_plan_task_scoped_over_a_protected_path_is_rejected(project, fake):
    project.set_policy({"protected_paths": ["db/migrations/**"]}, "migrations are mine")
    fake.add("plan", plan_output(plan_task(scope=("db/**",))), repeat=True)
    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER" and "protected paths db/migrations/**" in run["status_reason"]
    assert "Protected paths no task may change" in fake.calls("plan")[0].prompt


def test_approval_paths_make_the_task_wait_for_the_founder(project, fake):
    project.set_policy({"approval_paths": ["src/**"]}, "billing code")
    happy(fake)

    run = engine(project, fake).run()

    assert run["status"] == "WAITING_FOUNDER" and "A-001" in run["status_reason"]
    assert not fake.calls("implement")
    a = project.store.load().approvals[0]
    assert a["task_id"] == "T-001" and a["action"].startswith("Change files under approval paths")

    project.decide_approval("A-001", "grant", "ok")
    again = engine(project, fake).run()
    assert again["status"] == "COMPLETED", again["status_reason"]
    assert len(project.store.load().approvals) == 1, "asked once"


def test_security_review_paths_add_the_security_reviewer(project, fake):
    project.set_policy({"security_review_paths": ["src/**"]}, "all source is sensitive")
    fake.add("plan", plan_output(plan_task("Add module", scope=SCOPE)))
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"), repeat=True)
    run = engine(project, fake).run()
    assert run["status"] == "COMPLETED", run["status_reason"]
    assert [r.role for r in fake.calls("review")] == ["qa-engineer", "security-reviewer"]


def test_policy_test_commands_are_run_and_allowed_to_agents(project, fake):
    write_files(project.root, {"package.json": '{"scripts": {"check": "node --test tests/"}}\n'})
    project.set_policy({"test_commands": ["npm run check"]}, "project test script")
    fake.add("plan", plan_output(plan_task(scope=SCOPE, verify=("npm run check",))))
    fake.add("implement", implement(GOOD, tests=()))
    fake.add("review", review("PASS"))
    run = engine(project, fake).run()
    assert run["status"] == "COMPLETED", run["status_reason"]
    rules = fake.calls("implement")[0].test_command_rules
    assert "npm run check *" in rules and "npm run check" in rules
    assert "npm run check …" in fake.calls("plan")[0].prompt
    t = tasks_by_id(project)["T-001"]
    assert [e["command"] for e in t["evidence"] if e["type"] == "test"] == ["npm run check"]


def test_policy_limits_replace_project_limits_and_revert_when_removed(project, fake):
    project.set_policy({"limits": {"max_fix_attempts": 2, "max_agent_calls_per_run": 40}}, "tighter")
    happy(fake)
    engine(project, fake).run()
    limits = project.store.load().project["limits"]
    assert limits["max_fix_attempts"] == 2 and limits["max_agent_calls_per_run"] == 40
    assert [e for e in project.store.history() if e["type"] == "project.limits"]

    project.set_policy({}, "back to defaults")
    happy(fake)
    engine(project, fake).run("next request")
    limits = project.store.load().project["limits"]
    assert limits["max_fix_attempts"] == 3 and limits["max_agent_calls_per_run"] == 30


def test_a_policy_cannot_lower_a_limit_below_what_tasks_already_used(project, fake):
    from orkestr_core.domain import state as st
    from orkestr_core.domain.errors import OrkestrError

    happy(fake)
    engine(project, fake).run()
    project.mutate(lambda s: st.get_task(s, "T-001").update(fix_attempts=2))
    with pytest.raises(OrkestrError, match="T-001 has 2 fix attempts"):
        project.set_policy({"limits": {"max_fix_attempts": 1}}, "stricter")


def test_settings_apply_when_a_run_starts_or_continues_never_mid_run(project, fake):
    happy(fake)
    first = engine(project, fake).run()
    assert project.store.query("SELECT policy_version FROM workflow_run WHERE id = ?", (first["id"],))[0]["policy_version"] == 0


def test_the_protected_paths_become_deny_rules_for_file_tools():
    s = guard_settings(["LICENSE", ".github/**"])
    assert s["permissions"]["deny"] == ["Edit(LICENSE)", "Write(LICENSE)", "Edit(.github/**)", "Write(.github/**)"]
    assert "permissions" not in guard_settings([])
