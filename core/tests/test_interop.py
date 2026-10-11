"""Phase 1 import/export and hand-over (acceptance 4)."""

from __future__ import annotations

import json

import pytest

from orkestr_core.domain.errors import OrkestrError
from orkestr_core.service import Project

from conftest import needs_node, node_tool
from phase1_helpers import P, meeting_draft

pytestmark = needs_node


def plugin_project(root, autonomy="bounded"):
    def ok(*args):
        r = node_tool(root, *args)
        assert r.returncode == 0, r.stdout + r.stderr
        return r

    ok("init", "--name", "Legacy", "--goal", "Ship it", "--autonomy", autonomy)
    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {"title": "API", "owner": "backend-engineer", "acceptance_criteria": ["works"], "scope": ["src/**"]},
                    {"title": "UI", "owner": "frontend-engineer", "acceptance_criteria": ["works"], "depends_on": ["#1"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    ok("plan", "import", "plan.json")
    ok("task", "move", "T-001", "IN_PROGRESS")
    ok("evidence", "add", "T-001", "--type", "test", "--result", "passed", "--command", "npm test", "--summary", "ok")
    meeting = root / "m.json"
    meeting.write_text(json.dumps(meeting_draft()), encoding="utf-8")
    ok("meeting", "record", "m.json")
    return ok


def test_import_then_export_round_trip_passes_the_phase1_validator(tmp_path):
    plugin_project(tmp_path)
    before = json.loads((tmp_path / ".orkestr" / "tasks.json").read_text(encoding="utf-8"))
    p = Project(tmp_path)
    r = p.import_phase1()
    assert r["tasks"] == 4
    state = p.store.load()
    assert state.project["managed_by"] == "core"
    assert [t["id"] for t in state.tasks] == ["T-001", "T-002", "T-003", "T-004"]
    assert state.tasks[0]["evidence"][0]["command"] == "npm test"
    assert "M-001" in state.meetings and "D-001" in state.decisions
    after = json.loads((tmp_path / ".orkestr" / "tasks.json").read_text(encoding="utf-8"))
    assert after["tasks"] == before["tasks"], "the export reproduces the plugin's task records"
    v = node_tool(tmp_path, "validate")
    assert v.returncode == 0, v.stdout
    assert any(e["type"] == "core.import" for e in p.store.history())


def test_import_refuses_an_invalid_project_and_a_second_import(tmp_path):
    plugin_project(tmp_path)
    tasks_file = tmp_path / ".orkestr" / "tasks.json"
    doc = json.loads(tasks_file.read_text(encoding="utf-8"))
    doc["tasks"][1]["depends_on"] = ["T-404"]
    tasks_file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(OrkestrError, match="unknown dependency T-404"):
        Project(tmp_path).import_phase1()
    assert not (tmp_path / ".orkestr" / "core.db").exists()

    other = tmp_path / "other"
    other.mkdir()
    plugin_project(other)
    Project(other).import_phase1()
    with pytest.raises(OrkestrError, match="already manages"):
        Project(other).import_phase1()


def test_export_refuses_to_overwrite_files_changed_outside_the_core(tmp_path):
    p = P(tmp_path)
    tasks_file = tmp_path / ".orkestr" / "tasks.json"
    tasks_file.write_text(tasks_file.read_text(encoding="utf-8").replace('"tasks": []', '"tasks": [] '), encoding="utf-8")
    with pytest.raises(OrkestrError, match="changed outside orkestr-core"):
        p.plan()
    assert any("differs from the last export" in w for w in p.project.validate()["warnings"])
    p.project.export(force=True)
    assert not p.project.validate()["warnings"]


def test_the_plugin_refuses_writes_while_the_core_manages_the_project(tmp_path):
    ok = plugin_project(tmp_path)
    Project(tmp_path).import_phase1()
    r = node_tool(tmp_path, "task", "note", "T-001", "--author", "qa-engineer", "--text", "hi")
    assert r.returncode == 1
    assert "managed by orkestr-core" in r.stderr
    ok("status")  # reading still works


def test_release_hands_the_project_back_and_reimport_keeps_the_old_database(tmp_path):
    ok = plugin_project(tmp_path)
    p = Project(tmp_path)
    p.import_phase1()
    p.export(release=True)
    assert json.loads((tmp_path / ".orkestr" / "project.json").read_text(encoding="utf-8"))["managed_by"] == "plugin"
    ok("task", "note", "T-001", "--author", "qa-engineer", "--text", "back in the plugin")
    r = Project(tmp_path).import_phase1()
    assert r["backup"] and (tmp_path / ".orkestr" / r["backup"]).exists()
    notes = Project(tmp_path).store.load().tasks[0]["notes"]
    assert notes[-1]["text"] == "back in the plugin"


def test_core_projects_record_verify_commands_that_the_plugin_accepts(tmp_path):
    p = P(tmp_path, autonomy="bounded")
    from orkestr_core.domain import state as st

    p.plan({"tasks": [{"title": "API", "owner": "backend-engineer", "acceptance_criteria": ["works"], "scope": ["src/**"], "verify": ["node --test tests/"]}]})
    assert p.task("T-001")["verify"] == ["node --test tests/"]
    assert node_tool(tmp_path, "validate").returncode == 0
    with pytest.raises(OrkestrError, match="verify"):
        p.do(lambda s: st.create_tasks(s, [{"title": "x", "owner": "qa-engineer", "acceptance_criteria": ["y"], "verify": [""]}], {"type": "manual", "ref": None}))
