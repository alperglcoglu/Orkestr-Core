"""`orkestr-core` command line (PHASE3_IMPLEMENTATION.md §9.1)."""

from __future__ import annotations

import json

from orkestr_core.cli import main
from orkestr_core.domain import state as st
from orkestr_core.service import Project


def run(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_init_status_validate_and_founder_actions(tmp_path, capsys):
    d = str(tmp_path)
    code, out, _ = run(capsys, "--project", d, "init", "--name", "Müşteri Portalı", "--goal", "Çalışan bir MVP", "--autonomy", "supervised")
    assert code == 0 and "Initialized" in out
    code, _, err = run(capsys, "--project", d, "init", "--name", "x", "--goal", "y")
    assert code == 1 and "refusing to overwrite" in err

    p = Project(tmp_path)
    p.mutate(lambda s: st.import_plan(s, {"tasks": [{"title": "API", "owner": "backend-engineer", "acceptance_criteria": ["ok"]}]}))
    code, out, _ = run(capsys, "--project", d, "approve", "plan")
    assert code == 0 and "authorized T-001" in out

    p.mutate(lambda s: st.request_approval(s, action="deploy", category="deploy", reason="release"))
    code, out, _ = run(capsys, "--project", d, "reject", "A-001", "--reason", "not yet")
    assert code == 0 and "REJECTED" in out
    code, _, err = run(capsys, "--project", d, "approve", "A-001")
    assert code == 1 and "only PENDING" in err

    code, out, _ = run(capsys, "--project", d, "status")
    assert code == 0 and "Müşteri Portalı" in out and "Managed by:** core" in out
    code, out, _ = run(capsys, "--project", d, "validate")
    assert code == 0 and "Validation passed" in out
    code, out, _ = run(capsys, "--project", d, "pause", "--reason", "lunch")
    assert code == 0 and p.store.load().project["status"] == "PAUSED"


def test_export_release_hands_back_to_the_plugin(tmp_path, capsys):
    d = str(tmp_path)
    run(capsys, "--project", d, "init", "--name", "x", "--goal", "y")
    code, out, _ = run(capsys, "--project", d, "export", "--release")
    assert code == 0 and "plugin manages this project again" in out
    assert json.loads((tmp_path / ".orkestr" / "project.json").read_text(encoding="utf-8"))["managed_by"] == "plugin"


def test_usage_errors_exit_with_2(tmp_path, capsys):
    run(capsys, "--project", str(tmp_path), "init", "--name", "x", "--goal", "y")
    code, _, err = run(capsys, "--project", str(tmp_path), "approve", "X-1")
    assert code == 2 and "Pass an approval" in err


def test_team_policy_and_autonomy_commands(tmp_path, capsys):
    d = str(tmp_path)
    run(capsys, "--project", d, "init", "--name", "x", "--goal", "y")
    notes = tmp_path / "qa.txt"
    notes.write_text("Check edge cases first.\n", encoding="utf-8")
    code, out, _ = run(capsys, "--project", d, "team", "set", "qa-engineer", "--model", "opus", "--max-turns", "5", "--instructions-file", str(notes))
    assert code == 0 and "on  qa-engineer  (model opus, max turns 5, instructions)" in out
    code, out, _ = run(capsys, "--project", d, "team", "set", "ml-engineer", "--disable")
    assert code == 0 and "off ml-engineer" in out
    code, _, err = run(capsys, "--project", d, "team", "set", "tech-lead", "--disable")
    assert code == 1 and "cannot be disabled" in err
    code, out, _ = run(capsys, "--project", d, "team", "set", "qa-engineer", "--model", "", "--max-turns", "0")
    assert code == 0 and "on  qa-engineer  (instructions)" in out

    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"protected_paths": ["LICENSE"], "limits": {"max_fix_attempts": 2}}), encoding="utf-8")
    code, out, _ = run(capsys, "--project", d, "policy", "set", str(policy), "--reason", "keep the license")
    assert code == 0 and "version 1" in out
    code, out, _ = run(capsys, "--project", d, "policy", "show")
    assert code == 0 and '"LICENSE"' in out and "Policy version 1 (keep the license)" in out
    policy.write_text(json.dumps({"limits": {"max_parallel_tasks": 99}}), encoding="utf-8")
    code, _, err = run(capsys, "--project", d, "policy", "set", str(policy), "--reason", "faster")
    assert code == 1 and "from 1 to 4" in err
    code, out, _ = run(capsys, "--project", d, "policy", "history")
    assert code == 0 and "v0" in out and "v1" in out and "v2" not in out

    code, out, _ = run(capsys, "--project", d, "autonomy", "supervised", "--reason", "review plans")
    assert code == 0 and Project(tmp_path).store.load().project["autonomy"] == "supervised"
