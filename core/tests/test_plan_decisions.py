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
