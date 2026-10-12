"""Design choices the plan leaves open are settled by a team meeting before the tasks they block
start (phase2/FINDINGS.md, Run 4; PHASE4_IMPLEMENTATION.md §13)."""

from __future__ import annotations

import pytest

from conftest import PASSING_TEST, engine, implement, needs_node, plan_output, plan_task, review, tasks_by_id

pytestmark = needs_node

GOOD = {"src/a.js": "export const a = 1;\n", "tests/a.test.mjs": PASSING_TEST}
SCOPE = ("src/**", "tests/**")
QUESTION = "Who gets the leftover cents?"


def plan_with_decision(blocks=("#1",), **decision):
    out = plan_output(plan_task(scope=SCOPE))
    out["decisions_needed"] = [{"question": QUESTION, "options": ["First people", "The payer"], "blocks": list(blocks), **decision}]
    return out


def contribution(option):
    return {"position": "keep it simple", "preferred_option": option, "key_points": ["predictable"], "concerns": [], "high_severity_concern": False}


def synthesis(outcome="DECIDED", founder_required=False):
    return {
        "options": [{"id": "O1", "name": "First people"}, {"id": "O2", "name": "The payer"}],
        "outcome": outcome,
        "decision": (
            {"question": QUESTION, "selected_option": "O1" if outcome == "DECIDED" else None, "rationale": "predictable", "founder_required": founder_required}
            if outcome != "NO_DECISION"
            else None
        ),
    }


def meeting_script(fake, **kw):
    fake.add("contribution", contribution("O1"), role="backend-engineer")
    fake.add("contribution", contribution("O1"), role="tech-lead")
    fake.add("synthesis", synthesis(**kw))


def build_script(fake):
    fake.add("implement", implement(GOOD))
    fake.add("review", review("PASS"))


def test_a_routine_plan_decision_is_settled_before_the_task_starts(project, fake):
    fake.add("plan", plan_with_decision())
    meeting_script(fake)
    build_script(fake)

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED", run["status_reason"]
    kinds = [r.kind for r in fake.requests]
    assert kinds.index("synthesis") < kinds.index("implement"), "the meeting comes before the implementation"
    state = project.store.load()
    assert state.meetings["M-001"]["applies_to"] == ["T-001"]
    assert state.meetings["M-001"]["trigger"].startswith("A design choice the plan left open")
    assert state.decisions["D-001"]["approval_status"] == "APPROVED"
    assert "D-001: First people" in fake.calls("implement")[0].prompt
    assert len(fake.calls("synthesis")) == 1, "held once"


def test_a_founder_level_plan_decision_stops_the_run_until_decided(project, fake):
    fake.add("plan", plan_with_decision())
    meeting_script(fake, founder_required=True)

    run = engine(project, fake).run()

    assert run["status"] == "WAITING_FOUNDER" and "decide D-001" in run["status_reason"]
    assert not fake.calls("implement")
    again = engine(project, fake).run()
    assert again["status"] == "WAITING_FOUNDER" and not fake.calls("implement"), "no second meeting, still waiting"
    assert len(fake.calls("synthesis")) == 1

    project.decide_decision("D-001", "approve", note="ok")
    build_script(fake)
    done = engine(project, fake).run()
    assert done["id"] == run["id"] and done["status"] == "COMPLETED", done["status_reason"]


def test_no_decision_lets_the_tasks_go_ahead(project, fake):
    fake.add("plan", plan_with_decision())
    meeting_script(fake, outcome="NO_DECISION")
    build_script(fake)

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED", run["status_reason"]
    assert project.store.load().meetings["M-001"]["outcome"] == "NO_DECISION"


def test_a_meeting_interrupted_mid_way_is_held_again_on_resume(project, fake):
    fake.add("plan", plan_with_decision())

    def crash(req):
        raise KeyboardInterrupt

    fake.add("contribution", crash, role="backend-engineer")
    fake.add("contribution", contribution("O1"), role="tech-lead")
    with pytest.raises(KeyboardInterrupt):
        engine(project, fake).run()

    meeting_script(fake)
    build_script(fake)
    run = engine(project, fake).run()
    assert run["status"] == "COMPLETED", run["status_reason"]
    assert list(project.store.load().meetings) == ["M-001"]


@pytest.mark.parametrize("blocks", [("#2",), ("T-001",), ("#0",)])
def test_a_decision_that_blocks_an_unknown_task_rejects_the_plan(project, fake, blocks):
    fake.add("plan", plan_with_decision(blocks=blocks), repeat=True)
    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER" and "decisions_needed[1] blocks" in run["status_reason"]
    assert not tasks_by_id(project)


def test_the_plan_prompt_offers_decisions_instead_of_founder_tasks(project, fake):
    fake.add("plan", plan_output(plan_task(scope=SCOPE)))
    build_script(fake)
    engine(project, fake).run()
    prompt = fake.calls("plan")[0].prompt
    assert "decisions_needed" in prompt and "Do not create founder-owned tasks for such choices" in prompt


# --- plan consistency (phase2/FINDINGS.md #20) ------------------------------------------------


def test_a_verify_command_naming_a_file_nobody_may_create_rejects_the_plan(project, fake):
    bad = plan_output(
        plan_task("Core", scope=("src/split.js",), verify=("node --test test/split.test.js",)),
        plan_task("Tests", scope=("test/split.test.js",), verify=("node --test test/split.test.js",), depends_on=("#1",)),
    )
    fake.add("plan", bad, repeat=True)
    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER"
    assert "task #1 verify command needs test/split.test.js" in run["status_reason"]
    assert "task #2" not in run["status_reason"], "the file is in task #2's own scope"
    retry = fake.calls("plan")[1].prompt
    assert "keep everything else in your plan as it was, including decisions_needed" in retry


def test_verify_files_in_a_dependency_scope_or_on_disk_are_fine(project, fake):
    from conftest import write_files

    write_files(project.root, {"tests/existing.test.mjs": PASSING_TEST})
    ok = plan_output(
        plan_task("Lib", scope=("src/**", "test/lib.test.js")),
        plan_task("CLI", scope=("bin/**",), verify=("node --test test/lib.test.js", "node --test tests/existing.test.mjs", "node --test tests/"), depends_on=("#1",)),
    )
    eng = engine(project, fake)
    assert not [p for p in eng._plan_problems(ok) if "verify command needs" in p]


# --- a decision can widen the scope of the tasks it steers (phase2/FINDINGS.md #21) -------------

SCOPE_Q = "test/a.test.mjs is outside T-001's scope but its verify command needs it. How should this be resolved?"


def scope_synthesis(changes, founder_required=False):
    return {
        "options": [{"id": "O1", "name": "Add the test file to T-001's scope"}, {"id": "O2", "name": "Drop the test"}],
        "outcome": "DECIDED",
        "decision": {"question": SCOPE_Q, "selected_option": "O1", "rationale": "tests belong with the module", "founder_required": founder_required, "scope_changes": changes},
    }


def run5_scenario(project, fake, changes, founder_required=False):
    """The test file exists but is outside the task's scope; the implementer changes it anyway,
    then raises the conflict as a design question."""
    from conftest import write_files

    write_files(project.root, {"test/a.test.mjs": PASSING_TEST + "\n// written before the run\n"})
    fake.add("plan", plan_output(plan_task(scope=("src/**",), verify=("node --test test/a.test.mjs",))))
    write_both = implement({"src/a.js": "export const a = 1;\n", "test/a.test.mjs": PASSING_TEST}, tests=())
    fake.add("implement", write_both)
    fake.add("implement", implement({}, tests=(), decision_needed={"question": SCOPE_Q, "options": ["Add the test file to T-001's scope", "Drop the test"]}))
    meeting_script_for(fake, scope_synthesis(changes, founder_required))


def meeting_script_for(fake, synth):
    fake.add("contribution", contribution("O1"), role="backend-engineer")
    fake.add("contribution", contribution("O1"), role="tech-lead")
    fake.add("synthesis", synth)


def test_an_approved_decision_widens_the_scope_and_the_task_completes(project, fake):
    run5_scenario(project, fake, [{"task_id": "T-001", "add": ["test/a.test.mjs"]}])
    fake.add("implement", implement({"src/a.js": "export const a = 2;\n"}, tests=()))
    fake.add("review", review("PASS"))

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED", run["status_reason"]
    t = tasks_by_id(project)["T-001"]
    assert t["scope"] == ["src/**", "test/a.test.mjs"]
    assert any("D-001 widened the scope: test/a.test.mjs" in n["text"] for n in t["notes"])
    assert [e for e in project.store.history() if e["type"] == "task.scope"][0]["entity"] == "T-001"
    assert project.store.load().decisions["D-001"]["scope_changes"] == [{"task_id": "T-001", "add": ["test/a.test.mjs"]}]
    assert "test/a.test.mjs" in fake.calls("implement")[-1].prompt, "the next implementer sees the wider scope"


def test_a_founder_level_scope_change_waits_for_the_founder(project, fake):
    run5_scenario(project, fake, [{"task_id": "T-001", "add": ["test/a.test.mjs"]}], founder_required=True)
    run = engine(project, fake).run()
    assert run["status"] == "WAITING_FOUNDER" and "decide D-001" in run["status_reason"]
    assert tasks_by_id(project)["T-001"]["scope"] == ["src/**"], "not applied before approval"
    project.decide_decision("D-001", "approve", note="ok")
    assert tasks_by_id(project)["T-001"]["scope"] == ["src/**", "test/a.test.mjs"]


def test_the_founder_cannot_approve_a_scope_change_over_a_protected_path(project, fake):
    from orkestr_core.domain.errors import OrkestrError

    run5_scenario(project, fake, [{"task_id": "T-001", "add": ["test/a.test.mjs"]}], founder_required=True)
    engine(project, fake).run()
    project.set_policy({"protected_paths": ["test/**"]}, "tests are mine")
    with pytest.raises(OrkestrError, match="protected paths"):
        project.decide_decision("D-001", "approve", note="ok")
    assert tasks_by_id(project)["T-001"]["scope"] == ["src/**"]


@pytest.mark.parametrize(
    "changes, why",
    [
        ([{"task_id": "T-404", "add": ["x/**"]}], "not a task this decision steers"),
        ([{"task_id": "T-001", "add": ["../outside/**"]}], 'must not contain ".."'),
        ([{"task_id": "T-001", "add": [".orkestr/**"]}], "must not include .orkestr"),
    ],
)
def test_invalid_scope_changes_are_sent_back_to_the_synthesis(project, fake, changes, why):
    run5_scenario(project, fake, changes)
    fake.add("synthesis", scope_synthesis([{"task_id": "T-001", "add": ["test/a.test.mjs"]}]))
    fake.add("implement", implement({"src/a.js": "export const a = 2;\n"}, tests=()))
    fake.add("review", review("PASS"))

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED", run["status_reason"]
    retry = fake.calls("synthesis")[1].prompt
    assert why in retry


def test_a_scope_change_over_a_protected_path_is_sent_back(project, fake):
    project.set_policy({"protected_paths": ["docs/**"]}, "docs are mine")
    run5_scenario(project, fake, [{"task_id": "T-001", "add": ["docs/**", "test/a.test.mjs"]}])
    fake.add("synthesis", scope_synthesis([{"task_id": "T-001", "add": ["test/a.test.mjs"]}]))
    fake.add("implement", implement({"src/a.js": "export const a = 2;\n"}, tests=()))
    fake.add("review", review("PASS"))

    run = engine(project, fake).run()

    assert run["status"] == "COMPLETED", run["status_reason"]
    assert "may touch protected paths docs/**" in fake.calls("synthesis")[1].prompt
    assert tasks_by_id(project)["T-001"]["scope"] == ["src/**", "test/a.test.mjs"]
