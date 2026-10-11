"""Read-only validation of an in-memory project, ported from validate.mjs (MASTER_SPEC §19).

Never raises for bad data: every problem becomes an error or warning message. File-level
checks (generated views, leftover temp files) belong to interop; this module checks content.
"""

from __future__ import annotations

import json
import re

from ..constants import AUTONOMY_MODES, MANAGED_BY, OWNERS, PREFIX
from .errors import is_id, non_empty
from .records import DECISION_STATES, MEETING_OUTCOMES, REVIEW_VERDICTS
from .redact import find_secrets
from .scope import scope_problems, scopes_overlap
from .state import (
    APPROVAL_CATEGORIES,
    APPROVAL_STATES,
    AUTHORIZED_STATES,
    EVIDENCE_RESULTS,
    EVIDENCE_TYPES,
    PROJECT_STATES,
    TASK_STATES,
    State,
    completion_problems,
    find_cycle,
)

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def _iso(v) -> bool:
    return isinstance(v, str) and ISO.match(v) is not None


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(id_value) -> int:
    try:
        return int(str(id_value).split("-")[1])
    except (IndexError, ValueError):
        return 0


def validate_state(state: State, history: list[dict]) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    project = state.project
    counters = project.get("counters") or {}

    if project.get("schema_version") != 1:
        errors.append("project.json: schema_version must be 1")
    if not non_empty(project.get("name")):
        errors.append("project.json: name is required")
    if not non_empty(project.get("goal")):
        errors.append("project.json: goal is required")
    if project.get("status") not in PROJECT_STATES:
        errors.append(f"project.json: invalid status {project.get('status')}")
    if project.get("status") in ("PAUSED", "CANCELLED") and not non_empty(project.get("status_reason")):
        errors.append(f"project.json: status {project['status']} requires status_reason")
    limits = project.get("limits") or {}
    for k in ("max_meeting_participants", "max_discussion_rounds", "max_fix_attempts"):
        if not _int(limits.get(k)) or limits[k] < 1:
            errors.append(f"project.json: limits.{k} must be a positive integer")
    for k in PREFIX.values():
        if not _int(counters.get(k)) or counters[k] < 0:
            errors.append(f"project.json: counters.{k} must be a non-negative integer")
    if not _iso(project.get("created_at")) or not _iso(project.get("updated_at")):
        errors.append("project.json: timestamps must be ISO 8601 UTC")
    if "autonomy" in project and project["autonomy"] not in AUTONOMY_MODES:
        errors.append(f"project.json: autonomy must be one of {', '.join(AUTONOMY_MODES)}")
    if "managed_by" in project and project["managed_by"] not in MANAGED_BY:
        errors.append(f"project.json: managed_by must be one of {', '.join(MANAGED_BY)}")
    if "max_parallel_tasks" in limits and (not _int(limits["max_parallel_tasks"]) or limits["max_parallel_tasks"] < 1):
        errors.append("project.json: limits.max_parallel_tasks must be a positive integer")
    if "max_recovery_meetings" in limits and (not _int(limits["max_recovery_meetings"]) or limits["max_recovery_meetings"] < 0):
        errors.append("project.json: limits.max_recovery_meetings must be a non-negative integer")
    for k in ("max_agent_calls_per_run", "max_agent_calls_per_task"):
        if k in limits and (not _int(limits[k]) or limits[k] < 1):
            errors.append(f"project.json: limits.{k} must be a positive integer")
    if "assumptions" in project:
        a = project["assumptions"]
        if not isinstance(a, list) or not all(isinstance(x, dict) and non_empty(x.get("text")) and _iso(x.get("at")) for x in a):
            errors.append("project.json: assumptions must be a list of {text, impact, task, at}")

    approvals = state.approvals
    approval_ids: set = set()
    for a in approvals:
        where = f"approvals.json {a.get('id') or '(no id)'}"
        if not is_id(PREFIX["approval"], a.get("id")):
            errors.append(f"{where}: invalid id")
        if a.get("id") in approval_ids:
            errors.append(f"{where}: duplicate id")
        approval_ids.add(a.get("id"))
        if a.get("status") not in APPROVAL_STATES:
            errors.append(f"{where}: invalid status {a.get('status')}")
        if a.get("category") not in APPROVAL_CATEGORIES:
            errors.append(f"{where}: invalid category {a.get('category')}")
        if not non_empty(a.get("action")) or not non_empty(a.get("reason")):
            errors.append(f"{where}: action and reason are required")
        if a.get("status") != "PENDING" and (not non_empty(a.get("decided_by")) or not _iso(a.get("decided_at"))):
            errors.append(f"{where}: decided approvals need decided_by and decided_at")
        if a.get("execution_result") and a.get("status") != "GRANTED":
            errors.append(f"{where}: only GRANTED approvals can have an execution result")
        if _num(a.get("id")) > counters.get("A", 0):
            errors.append(f"{where}: id is above counters.A")

    tasks = state.tasks
    task_ids = {t.get("id") for t in tasks}
    seen: set = set()
    max_fix = limits.get("max_fix_attempts", 3)
    for t in tasks:
        where = f"tasks.json {t.get('id') or '(no id)'}"
        if not is_id(PREFIX["task"], t.get("id")):
            errors.append(f"{where}: invalid id")
        if t.get("id") in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(t.get("id"))
        if _num(t.get("id")) > counters.get("T", 0):
            errors.append(f"{where}: id is above counters.T")
        if not non_empty(t.get("title")):
            errors.append(f"{where}: title is required")
        if t.get("owner") not in OWNERS:
            errors.append(f"{where}: invalid owner {t.get('owner')}")
        if t.get("status") not in TASK_STATES:
            errors.append(f"{where}: invalid status {t.get('status')}")
        ac = t.get("acceptance_criteria")
        if not isinstance(ac, list) or not any(non_empty(x) for x in ac):
            errors.append(f"{where}: at least one acceptance criterion is required")
        if not isinstance(t.get("depends_on"), list):
            errors.append(f"{where}: depends_on must be a list")
        for d in t.get("depends_on") or []:
            if d == t.get("id"):
                errors.append(f"{where}: depends on itself")
            elif d not in task_ids:
                errors.append(f"{where}: unknown dependency {d}")
        if not isinstance(t.get("authorized"), bool):
            errors.append(f"{where}: authorized must be true or false")
        if t.get("status") in AUTHORIZED_STATES and not t.get("authorized"):
            errors.append(f"{where}: {t['status']} but not authorized")
        if t.get("status") == "BLOCKED" and not non_empty(t.get("blocked_reason")):
            errors.append(f"{where}: BLOCKED without blocked_reason")
        failure = t.get("failure") or {}
        if t.get("status") == "FAILED" and (not non_empty(failure.get("error")) or not non_empty(failure.get("next_action"))):
            errors.append(f"{where}: FAILED without failure.error and failure.next_action")
        fa = t.get("fix_attempts")
        if not _int(fa) or fa < 0 or fa > max_fix:
            errors.append(f"{where}: fix_attempts must be between 0 and {max_fix}")
        if t.get("requires_approval") and t.get("status") in ("IN_PROGRESS", "REVIEW", "COMPLETED"):
            a = next((x for x in approvals if x.get("id") == t.get("approval_id")), None)
            if not a or a.get("status") != "GRANTED":
                errors.append(f"{where}: {t['status']} without a GRANTED approval")
        if t.get("approval_id") and t["approval_id"] not in approval_ids:
            errors.append(f"{where}: unknown approval {t['approval_id']}")
        if not isinstance(t.get("evidence"), list):
            errors.append(f"{where}: evidence must be a list")
        for e in t.get("evidence") or []:
            if e.get("type") not in EVIDENCE_TYPES or e.get("result") not in EVIDENCE_RESULTS or not non_empty(e.get("summary")) or not _iso(e.get("at")):
                errors.append(f"{where}: invalid evidence entry")
                break
        if t.get("status") == "COMPLETED" and isinstance(t.get("evidence"), list):
            problems = completion_problems(t)
            if problems:
                errors.append(f"{where}: COMPLETED but {'; '.join(problems)}")
        if (t.get("source") or {}).get("type") not in ("plan", "meeting", "manual"):
            errors.append(f"{where}: invalid source.type")
        rm = t.get("recovery_meetings")
        if rm is not None and (not _int(rm) or rm > limits.get("max_recovery_meetings", 1)):
            errors.append(f"{where}: recovery_meetings exceeds the limit")
        notes = t.get("notes")
        if notes is not None and (not isinstance(notes, list) or not all(n.get("author") in OWNERS and non_empty(n.get("text")) for n in notes)):
            errors.append(f"{where}: notes must be a list of {{author, text, at}}")
        errors.extend(f"{where}: {p}" for p in scope_problems(t.get("scope")))
        verify = t.get("verify")
        if verify is not None and (not isinstance(verify, list) or not all(non_empty(v) for v in verify)):
            errors.append(f"{where}: verify must be a list of commands")

    running = [t for t in tasks if t.get("status") == "IN_PROGRESS"]
    max_par = limits.get("max_parallel_tasks", 1)
    if len(running) > max_par:
        errors.append(f"tasks.json: {len(running)} tasks IN_PROGRESS but limits.max_parallel_tasks is {max_par}")
    for i in range(len(running)):
        for j in range(i + 1, len(running)):
            if scopes_overlap(running[i].get("scope") or [], running[j].get("scope") or []):
                errors.append(f"tasks.json: {running[i]['id']} and {running[j]['id']} are both IN_PROGRESS with overlapping scopes")
    cycle = find_cycle([t for t in tasks if isinstance(t.get("depends_on"), list)])
    if cycle:
        errors.append(f"tasks.json: dependency cycle {' -> '.join(cycle)}")

    def records(folder: str, prefix: str) -> list[dict]:
        out = []
        for rid, rec in getattr(state, folder).items():
            if rec.get("id") != rid:
                errors.append(f"{folder}/{rid}.json: id \"{rec.get('id')}\" does not match the file name")
            if not is_id(prefix, rec.get("id")):
                errors.append(f"{folder}/{rid}.json: invalid id format")
            if rec.get("schema_version") != 1:
                errors.append(f"{folder}/{rid}.json: schema_version must be 1")
            out.append(rec)
        return out

    for m in records("meetings", PREFIX["meeting"]):
        if m.get("outcome") not in MEETING_OUTCOMES:
            errors.append(f"meetings/{m['id']}.json: invalid outcome")
        if len(m.get("participants") or []) > limits.get("max_meeting_participants", 4):
            errors.append(f"meetings/{m['id']}.json: too many participants")
        if len(m.get("rounds") or []) > limits.get("max_discussion_rounds", 2):
            errors.append(f"meetings/{m['id']}.json: too many rounds")
        for tid in m.get("task_ids") or []:
            if tid not in task_ids:
                errors.append(f"meetings/{m['id']}.json: unknown task {tid}")
    for d in records("decisions", PREFIX["decision"]):
        if d.get("approval_status") not in DECISION_STATES:
            errors.append(f"decisions/{d['id']}.json: invalid approval_status")
        if d.get("approval_status") == "APPROVED" and not d.get("selected_option"):
            errors.append(f"decisions/{d['id']}.json: APPROVED without selected_option")
        for tid in d.get("task_ids") or []:
            if tid not in task_ids:
                errors.append(f"decisions/{d['id']}.json: unknown task {tid}")
    for r in records("reviews", PREFIX["review"]):
        if r.get("verdict") not in REVIEW_VERDICTS:
            errors.append(f"reviews/{r['id']}.json: invalid verdict")
        if r.get("task_id") and r["task_id"] not in task_ids:
            errors.append(f"reviews/{r['id']}.json: unknown task {r['task_id']}")
    records("retros", PREFIX["retro"])

    for i, ev in enumerate(history):
        if not _iso(ev.get("ts")) or not non_empty(ev.get("type")):
            errors.append(f"history.jsonl line {i + 1}: needs ts and type")

    # Everything the core stores is redacted, so any secret-looking value is an error.
    blob = json.dumps(
        {
            "project": project,
            "tasks": tasks,
            "approvals": approvals,
            "meetings": state.meetings,
            "decisions": state.decisions,
            "reviews": state.reviews,
            "retros": state.retros,
            "history": history,
        },
        ensure_ascii=False,
    )
    found = find_secrets(blob)
    if found:
        errors.append(f"stored state: possible secret ({', '.join(found)}) — remove it")

    return {"errors": errors, "warnings": warnings}
