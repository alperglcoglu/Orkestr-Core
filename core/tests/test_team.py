"""Configurable agent teams (PHASE4_IMPLEMENTATION.md §5, acceptance 6)."""

from __future__ import annotations

import pytest

from orkestr_core.domain import team as tm
from orkestr_core.domain.errors import OrkestrError


def row(role="backend-engineer", **kw):
    return {**tm.default_role(role), **kw}


def test_the_default_team_is_the_phase3_behavior():
    team = tm.default_team()
    assert len(team) == 10 and all(r["enabled"] and r["model"] is None and r["max_turns"] is None and r["instructions"] is None for r in team.values())
    assert tm.differences(team) == {}


@pytest.mark.parametrize("role", tm.REQUIRED_ROLES)
def test_required_roles_cannot_be_disabled(role):
    with pytest.raises(OrkestrError, match="required and cannot be disabled"):
        tm.apply_change(row(role), {"enabled": False}, 20)


def test_other_roles_can_be_disabled_and_enabled_again():
    off = tm.apply_change(row("ml-engineer"), {"enabled": False}, 20)
    assert not off["enabled"]
    assert "ml-engineer" not in tm.enabled_roles({**tm.default_team(), "ml-engineer": off})
    assert tm.apply_change(off, {"enabled": True}, 20)["enabled"]


@pytest.mark.parametrize("model", ["sonnet", "opus", "claude-sonnet-5-5", "claude-opus-5-5[1m]", "haiku"])
def test_model_names_and_aliases_are_accepted(model):
    assert tm.apply_change(row(), {"model": model}, 20)["model"] == model


@pytest.mark.parametrize("model", ["--dangerously-skip-permissions", "-p", "sonnet --bare", "a" * 65, "sonnet;rm", 5])
def test_a_model_value_can_never_become_a_flag(model):
    with pytest.raises(OrkestrError, match="model must be"):
        tm.apply_change(row(), {"model": model}, 20)


def test_max_turns_can_only_lower_the_definition_limit():
    assert tm.apply_change(row(), {"max_turns": 10}, 20)["max_turns"] == 10
    for bad in (21, 0, -1, True, "5"):
        with pytest.raises(OrkestrError):
            tm.apply_change(row(), {"max_turns": bad}, 20)
    assert tm.effective_max_turns(row(max_turns=10), 20) == 10
    assert tm.effective_max_turns(row(), 20) == 20


def test_instructions_are_limited_redacted_and_follow_the_role_prompt():
    r = tm.apply_change(row(), {"instructions": "  Prefer small modules. Key: sk-ant-api03-" + "x" * 40 + "  "}, 20)
    assert r["instructions"].startswith("Prefer small modules.") and "sk-ant-api03-xxxx" not in r["instructions"]
    prompt = tm.system_prompt("BASE ROLE PROMPT", r)
    assert prompt.startswith("BASE ROLE PROMPT") and "## Founder's instructions for this project" in prompt
    assert tm.system_prompt("BASE", row()) == "BASE"
    with pytest.raises(OrkestrError, match="limited to 2000"):
        tm.apply_change(row(), {"instructions": "x" * 2001}, 20)


def test_empty_strings_clear_model_and_instructions():
    r = tm.apply_change(row(model="opus", instructions="x"), {"model": "", "instructions": ""}, 20)
    assert r["model"] is None and r["instructions"] is None


def test_unknown_roles_and_fields_are_rejected():
    assert "unknown role" in tm.change_problems("cfo", {}, None)[0]
    assert "unknown field" in tm.change_problems("tech-lead", {"tools": "implement"}, None)[0]


def test_model_precedence_is_role_then_run_then_default():
    assert tm.effective_model(row(model="opus"), "sonnet") == "opus"
    assert tm.effective_model(row(), "sonnet") == "sonnet"
    assert tm.effective_model(row(), None) is None


def test_differences_show_only_changed_values():
    team = tm.default_team()
    team["qa-engineer"] = row("qa-engineer", model="opus")
    assert tm.differences(team) == {"qa-engineer": {"model": "opus"}}


# --- stored team (service and store) ------------------------------------------------------------


def test_a_team_change_is_stored_with_an_audit_event(tmp_path):
    from phase1_helpers import P

    p = P(tmp_path)
    row = p.project.set_team_role("qa-engineer", {"model": "opus", "max_turns": 5})
    assert row["model"] == "opus" and p.project.team()["qa-engineer"]["max_turns"] == 5
    ev = [e for e in p.history() if e["type"] == "team.update"][-1]
    assert ev["entity"] == "qa-engineer" and ev["actor"] == "founder"
    assert ev["data"] == {"model": {"from": None, "to": "opus"}, "max_turns": {"from": None, "to": 5}}


def test_a_rejected_team_change_stores_nothing(tmp_path):
    from phase1_helpers import P

    p = P(tmp_path)
    with pytest.raises(OrkestrError, match="cannot be disabled"):
        p.project.set_team_role("tech-lead", {"enabled": False})
    with pytest.raises(OrkestrError, match="only lower"):
        p.project.set_team_role("tech-lead", {"max_turns": 10_000})
    assert tm.differences(p.project.team()) == {}
    assert not [e for e in p.history() if e["type"] == "team.update"]
