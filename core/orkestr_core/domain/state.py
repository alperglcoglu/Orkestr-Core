"""Project, task, and approval rules, ported from plugins/orkestr/scripts/lib/state.mjs.

Implements MASTER_SPEC §5.2, §5.2.1, §5.3, §10, §12.1, §12.2. The in-memory shapes are the
Phase 1 JSON shapes, so the same rules apply whether the state came from SQLite or from files.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..constants import AUTONOMY_MODES, FOUNDER, OWNERS, PREFIX
from .errors import OrkestrError, format_id, is_id, non_empty, now, str_list
from .scope import file_in_scope, scope_problems, scopes_overlap

TASK_STATES = ("BACKLOG", "READY", "IN_PROGRESS", "BLOCKED", "REVIEW", "FAILED", "COMPLETED", "CANCELLED")
TERMINAL = ("COMPLETED", "CANCELLED")
TRANSITIONS = {
    "BACKLOG": ("READY", "BLOCKED", "CANCELLED"),
    "READY": ("IN_PROGRESS", "BLOCKED", "BACKLOG", "CANCELLED"),
    "IN_PROGRESS": ("REVIEW", "BLOCKED", "FAILED", "CANCELLED"),
    "REVIEW": ("COMPLETED", "IN_PROGRESS", "FAILED", "CANCELLED"),
    "BLOCKED": ("READY", "BACKLOG", "CANCELLED"),
    "FAILED": ("READY", "CANCELLED"),
    "COMPLETED": (),
    "CANCELLED": (),
}
AUTHORIZED_STATES = ("READY", "IN_PROGRESS", "REVIEW", "FAILED", "COMPLETED")
PROJECT_STATES = ("ACTIVE", "PAUSED", "CANCELLED", "COMPLETED")
APPROVAL_STATES = ("PENDING", "GRANTED", "REJECTED", "EXPIRED", "CANCELLED")
APPROVAL_CATEGORIES = (
    "destructive",
    "deploy",
    "publish",
    "git_push",
    "external_communication",
    "spending",
    "permission_change",
    "sensitive_data",
    "other",
)
EVIDENCE_TYPES = ("test", "check", "review", "manual")
EVIDENCE_RESULTS = ("passed", "failed", "not_run")
IMPACTS = ("low", "medium", "high")

# Scope checks are bookkeeping, not verification: they never satisfy the "passed check" rule.
SCOPE_CHECK_COMMAND = "orkestr scope check"


@dataclass
class State:
    """Everything one mutation may read or change. `events` collects new history events."""

    project: dict
    tasks: list[dict] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)
    meetings: dict[str, dict] = field(default_factory=dict)
    decisions: dict[str, dict] = field(default_factory=dict)
    reviews: dict[str, dict] = field(default_factory=dict)
    retros: dict[str, dict] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    changed_records: set[tuple[str, str]] = field(default_factory=set)
    # Markdown blocks to append to memory/lessons-learned.md after the change is committed.
    lesson_blocks: list[str] = field(default_factory=list)


def emit(state: State, type: str, actor: str = "orchestrator", entity=None, frm=None, to=None, summary: str = "", data=None):
    state.events.append(
        {"ts": now(), "type": type, "actor": actor, "entity": entity, "from": frm, "to": to, "summary": summary, "data": data}
    )


def next_id(state: State, kind: str) -> str:
    prefix = PREFIX[kind]
    counters = state.project["counters"]
    counters[prefix] = counters.get(prefix, 0) + 1
    return format_id(prefix, counters[prefix])


def autonomy_of(state: State) -> str:
    """Projects created before autonomy existed have no field and are treated as supervised."""
    return state.project.get("autonomy") or "supervised"


def max_recovery_meetings(state: State) -> int:
    return state.project["limits"].get("max_recovery_meetings", 1)


def max_parallel(state: State) -> int:
    """Projects created before parallel work existed keep the old sequential behavior."""
    return state.project["limits"].get("max_parallel_tasks", 1)


def require_mutable(state: State):
    s = state.project["status"]
    if s in ("CANCELLED", "COMPLETED"):
        raise OrkestrError(f"Project is {s}; its state can no longer be changed.", "E_PROJECT_CLOSED")


def require_founder(by):
    if by != FOUNDER:
        raise OrkestrError(
            'This is a founder-only action. Use "--by founder" only after the founder has explicitly approved it.',
            "E_FOUNDER_ONLY",
        )


def get_task(state: State, task_id: str) -> dict:
    for t in state.tasks:
        if t["id"] == task_id:
            return t
    raise OrkestrError(f"Unknown task: {task_id}", "E_NOT_FOUND")


def find_task(state: State, task_id: str) -> dict | None:
    return next((t for t in state.tasks if t["id"] == task_id), None)


def get_approval(state: State, approval_id: str) -> dict:
    for a in state.approvals:
        if a["id"] == approval_id:
            return a
    raise OrkestrError(f"Unknown approval: {approval_id}", "E_NOT_FOUND")


# ---------------------------------------------------------------------------
# Task creation


def create_tasks(state: State, items, source: dict, actor: str = "orchestrator", decision_id: str | None = None) -> list[dict]:
    """Validates and creates tasks. Items may reference each other with "#n" (1-based) and
    existing tasks by ID. Creates nothing when any item is invalid."""
    if not isinstance(items, list) or not items:
        raise OrkestrError("At least one task is required.")
    errors: list[str] = []
    existing = {t["id"] for t in state.tasks}
    counter = state.project["counters"].get(PREFIX["task"], 0)
    new_ids = [format_id(PREFIX["task"], counter + i + 1) for i in range(len(items))]

    normalized = []
    for i, item in enumerate(items):
        where = f"task #{i + 1}"
        if not isinstance(item, dict):
            errors.append(f"{where}: must be an object")
            normalized.append(None)
            continue
        if not non_empty(item.get("title")):
            errors.append(f'{where}: "title" is required')
        if item.get("owner") not in OWNERS:
            errors.append(f'{where}: "owner" must be one of {", ".join(OWNERS)} (got {item.get("owner")!r})')
        criteria = item.get("acceptance_criteria")
        if not str_list(criteria) or not [c for c in criteria if non_empty(c)]:
            errors.append(f'{where}: "acceptance_criteria" needs at least one non-empty string')
        deps = item.get("depends_on") if item.get("depends_on") is not None else []
        resolved: list[str] = []
        if not str_list(deps):
            errors.append(f'{where}: "depends_on" must be a list of strings')
        else:
            for d in deps:
                if d.startswith("#") and d[1:].isdigit():
                    n = int(d[1:])
                    if n < 1 or n > len(items):
                        errors.append(f'{where}: "{d}" does not refer to an item in this draft')
                    elif n == i + 1:
                        errors.append(f"{where}: a task cannot depend on itself")
                    else:
                        resolved.append(new_ids[n - 1])
                elif is_id(PREFIX["task"], d):
                    if d not in existing:
                        errors.append(f"{where}: unknown dependency {d}")
                    else:
                        resolved.append(d)
                else:
                    errors.append(f'{where}: invalid dependency "{d}" (use a task ID such as T-001 or "#n")')
        if "requires_approval" in item and not isinstance(item["requires_approval"], bool):
            errors.append(f'{where}: "requires_approval" must be true or false')
        if "description" in item and not isinstance(item["description"], str):
            errors.append(f'{where}: "description" must be a string')
        errors.extend(f"{where}: {p}" for p in scope_problems(item.get("scope")))
        verify = item.get("verify")
        if verify is not None and (not str_list(verify) or not all(non_empty(v) for v in verify)):
            errors.append(f'{where}: "verify" must be a list of exact test commands')
        normalized.append(
            {
                "title": item["title"].strip() if isinstance(item.get("title"), str) else item.get("title"),
                "description": item.get("description") or "",
                "owner": item.get("owner"),
                "depends_on": list(dict.fromkeys(resolved)),
                "acceptance_criteria": [c for c in criteria if non_empty(c)] if str_list(criteria) else [],
                "requires_approval": item.get("requires_approval") is True,
                "scope": [s.strip().replace("\\", "/") for s in item["scope"]] if str_list(item.get("scope")) else [],
                "verify": [v.strip() for v in verify] if str_list(verify) else None,
            }
        )

    if errors:
        raise OrkestrError("Invalid tasks:\n- " + "\n- ".join(errors))

    ts = now()
    created = []
    for i, n in enumerate(normalized):
        task = {
            "id": new_ids[i],
            "title": n["title"],
            "description": n["description"],
            "owner": n["owner"],
            "status": "BACKLOG",
            "depends_on": n["depends_on"],
            "acceptance_criteria": n["acceptance_criteria"],
            "scope": n["scope"],
            "requires_approval": n["requires_approval"],
            "approval_id": None,
            "authorized": False,
            "source": source,
            "decision_id": decision_id,
            "blocked_reason": None,
            "blocked_by_dependency": None,
            "failure": None,
            "fix_attempts": 0,
            "recovery_meetings": 0,
            "evidence": [],
            "notes": [],
            "created_at": ts,
            "updated_at": ts,
        }
        if n["verify"]:
            task["verify"] = n["verify"]
        created.append(task)

    cycle = find_cycle([*state.tasks, *created])
    if cycle:
        raise OrkestrError(f"Dependency cycle: {' -> '.join(cycle)}")

    state.project["counters"][PREFIX["task"]] = counter + len(items)
    state.tasks.extend(created)
    for t in created:
        emit(
            state,
            "task.create",
            actor=actor,
            entity=t["id"],
            to=t["status"],
            summary=t["title"],
            data={"owner": t["owner"], "depends_on": t["depends_on"], "source": t["source"], "decision_id": decision_id},
        )
    return created


def find_cycle(tasks: list[dict]) -> list[str] | None:
    by_id = {t["id"]: t for t in tasks}
    color: dict[str, int] = {}
    stack: list[str] = []

    def visit(tid: str):
        color[tid] = 1
        stack.append(tid)
        for d in by_id.get(tid, {}).get("depends_on", []):
            if d not in by_id:
                continue
            if color.get(d) == 1:
                return [*stack[stack.index(d):], d]
            if not color.get(d):
                c = visit(d)
                if c:
                    return c
        stack.pop()
        color[tid] = 2
        return None

    for t in tasks:
        if not color.get(t["id"]):
            c = visit(t["id"])
            if c:
                return c
    return None


# ---------------------------------------------------------------------------
# Guards


def _deps_completed(state: State, task: dict) -> bool:
    return all((find_task(state, d) or {}).get("status") == "COMPLETED" for d in task["depends_on"])


def readiness_problems(state: State, task: dict) -> list[str]:
    problems = []
    if not task["authorized"]:
        problems.append("task is not authorized (plan or decision approval, or founder authorization, is missing)")
    if state.project["status"] != "ACTIVE":
        problems.append(f"project is {state.project['status']}")
    open_deps = [d for d in task["depends_on"] if (find_task(state, d) or {}).get("status") != "COMPLETED"]
    if open_deps:
        problems.append(f"dependencies not completed: {', '.join(open_deps)}")
    return problems


def parallel_problems(state: State, task: dict, running: list[dict] | None = None) -> list[str]:
    if running is None:
        running = [t for t in state.tasks if t["status"] == "IN_PROGRESS" and t["id"] != task["id"]]
    problems = []
    if len(running) >= max_parallel(state):
        ids = ", ".join(t["id"] for t in running)
        problems.append(f"parallel limit reached ({len(running)}/{max_parallel(state)} tasks in progress: {ids})")
    for other in running:
        if scopes_overlap(task.get("scope") or [], other.get("scope") or []):
            why = (
                "one of them has no scope, so it runs exclusively"
                if not task.get("scope") or not other.get("scope")
                else "their file scopes overlap"
            )
            problems.append(f"conflicts with {other['id']} (in progress): {why}")
    return problems


def start_problems(state: State, task: dict, running: list[dict] | None = None) -> list[str]:
    problems = readiness_problems(state, task)
    problems.extend(parallel_problems(state, task, running))
    if task["requires_approval"]:
        a = next((x for x in state.approvals if x["id"] == task.get("approval_id")), None) if task.get("approval_id") else None
        if not a:
            problems.append("task requires approval but has no approval request")
        elif a["status"] != "GRANTED":
            problems.append(f"approval {a['id']} is {a['status']}, not GRANTED")
    return problems


def parallel_candidates(state: State) -> list[dict]:
    """READY tasks that can start now, together, without exceeding the limit or overlapping scopes."""
    chosen: list[dict] = []
    running = [t for t in state.tasks if t["status"] == "IN_PROGRESS"]
    for t in state.tasks:
        if t["status"] != "READY":
            continue
        if start_problems(state, t, [*running, *chosen]):
            continue
        chosen.append(t)
    return chosen


def scope_check(state: State, files: list[str], task_id: str | None = None) -> dict:
    """Attributes changed files to active tasks. With a task ID, every file must be inside that
    task's scope; without one, every file must belong to some IN_PROGRESS or REVIEW task."""
    if not isinstance(files, list) or not files:
        raise OrkestrError("Pass at least one --file (the files that actually changed).")
    active = [t for t in state.tasks if t["status"] in ("IN_PROGRESS", "REVIEW")]
    target = get_task(state, task_id) if task_id else None
    rows = []
    for f in files:
        owners = [t["id"] for t in active if file_in_scope(f, t.get("scope") or [])]
        problem = None
        if target and not file_in_scope(f, target.get("scope") or []):
            problem = f"outside {target['id']}'s scope"
        elif not target and not owners:
            problem = "not in the scope of any active task"
        rows.append({"file": f, "owners": owners, "problem": problem})
    violations = [r for r in rows if r["problem"]]
    if target:
        add_evidence(
            state,
            target["id"],
            type="check",
            result="failed" if violations else "passed",
            command=SCOPE_CHECK_COMMAND,
            summary=(
                f"Out-of-scope changes: {', '.join(v['file'] for v in violations)}"
                if violations
                else f"All {len(rows)} changed file(s) are inside the task scope"
            ),
        )
    return {"rows": rows, "violations": violations}


def completion_problems(task: dict) -> list[str]:
    problems = []
    ev = task["evidence"]
    verification = [e for e in ev if e["type"] in ("test", "check", "manual") and e.get("command") != SCOPE_CHECK_COMMAND]
    if not any(e["result"] == "passed" for e in verification):
        problems.append("no passed test, check, or manual-verification evidence (a scope check does not count)")
    checks = [e for e in ev if e["type"] in ("test", "check") and e["result"] != "not_run"]
    if checks and checks[-1]["result"] == "failed":
        problems.append("the latest test/check failed")
    reviews = [e for e in ev if e["type"] == "review"]
    if not reviews:
        problems.append("no review evidence")
    elif reviews[-1]["result"] != "passed":
        problems.append("the latest review did not pass")
    return problems


# ---------------------------------------------------------------------------
# Transitions


def _set_status(state: State, task: dict, to: str, actor: str = "orchestrator", summary: str = "", data=None):
    frm = task["status"]
    task["status"] = to
    task["updated_at"] = now()
    emit(state, "task.transition", actor=actor, entity=task["id"], frm=frm, to=to, summary=summary or "", data=data)


def transition(
    state: State,
    task_id: str,
    to: str,
    reason: str | None = None,
    error: str | None = None,
    next_action: str | None = None,
    retry: bool = False,
    actor: str = "orchestrator",
) -> dict:
    require_mutable(state)
    task = get_task(state, task_id)
    if to not in TASK_STATES:
        raise OrkestrError(f'Unknown state "{to}". Valid states: {", ".join(TASK_STATES)}')
    frm = task["status"]
    if to not in TRANSITIONS[frm]:
        allowed = ", ".join(TRANSITIONS[frm]) if TRANSITIONS[frm] else "none (terminal state)"
        raise OrkestrError(
            f"Transition {frm} -> {to} is not allowed for {task_id}. Allowed from {frm}: {allowed}.", "E_TRANSITION"
        )
    if to == "READY":
        if frm == "FAILED" and not retry:
            raise OrkestrError(
                f"{task_id} is FAILED. Retrying needs an explicit founder decision: use --retry (resets the fix-attempt counter).",
                "E_RETRY_REQUIRED",
            )
        problems = readiness_problems(state, task)
        if problems:
            raise OrkestrError(f"{task_id} cannot become READY: {'; '.join(problems)}.", "E_GUARD")
    if to == "IN_PROGRESS":
        problems = start_problems(state, task)
        if problems:
            raise OrkestrError(f"{task_id} cannot start: {'; '.join(problems)}.", "E_GUARD")
    if to in ("BLOCKED", "CANCELLED") and not non_empty(reason):
        raise OrkestrError(f"A reason is required to move {task_id} to {to} (use --reason).")
    if to == "FAILED" and (not non_empty(error) or not non_empty(next_action)):
        raise OrkestrError(f"Moving {task_id} to FAILED requires --error and --next-action.")
    if to == "COMPLETED":
        problems = completion_problems(task)
        if problems:
            raise OrkestrError(f"{task_id} cannot be completed: {'; '.join(problems)}.", "E_GUARD")

    if to == "BLOCKED":
        task["blocked_reason"] = reason.strip()
        task["blocked_by_dependency"] = None
    if frm == "BLOCKED":
        task["blocked_reason"] = None
        task["blocked_by_dependency"] = None
    if to == "FAILED":
        task["failure"] = {"error": error.strip(), "next_action": next_action.strip()}
    if frm == "FAILED" and to == "READY":
        task["failure"] = None
        task["fix_attempts"] = 0

    summary = reason or error or ("founder retry; fix attempts reset" if retry else "")
    data = task["failure"] if to == "FAILED" else ({"retry": True} if retry else None)
    _set_status(state, task, to, actor=actor, summary=summary, data=data)
    if to in ("FAILED", "CANCELLED"):
        block_dependents(state, task_id)
    sync(state)
    return task


def block_dependents(state: State, root_id: str):
    root = get_task(state, root_id)
    queue = [root_id]
    seen: set[str] = set()
    while queue:
        current = queue.pop(0)
        for t in state.tasks:
            if current not in t["depends_on"] or t["id"] in seen:
                continue
            seen.add(t["id"])
            queue.append(t["id"])
            if t["status"] in TERMINAL or t["status"] == "BLOCKED":
                continue
            t["blocked_reason"] = f"Dependency {root_id} is {root['status']}"
            t["blocked_by_dependency"] = root_id
            _set_status(state, t, "BLOCKED", actor="orkestr", summary=t["blocked_reason"])


def _broken_dependency(state: State, task: dict) -> str | None:
    for d in task["depends_on"]:
        dep = find_task(state, d)
        if not dep:
            return d
        if dep["status"] in ("FAILED", "CANCELLED"):
            return d
        if dep["status"] == "BLOCKED" and dep.get("blocked_by_dependency"):
            return d
    return None


def sync(state: State):
    """Applies automatic transitions until nothing changes (MASTER_SPEC §5.3)."""
    if state.project["status"] in ("CANCELLED", "COMPLETED"):
        return
    changed = True
    while changed:
        changed = False
        for t in state.tasks:
            if t["status"] == "BLOCKED" and t.get("blocked_by_dependency") and not _broken_dependency(state, t):
                t["blocked_reason"] = None
                t["blocked_by_dependency"] = None
                _set_status(state, t, "BACKLOG", actor="orkestr", summary="dependency recovered")
                changed = True
            if t["status"] == "BACKLOG" and t["authorized"] and state.project["status"] == "ACTIVE" and _deps_completed(state, t):
                _set_status(state, t, "READY", actor="orkestr", summary="authorized and dependencies completed")
                changed = True


def authorize(state: State, tasks: list[dict], actor: str, via: str):
    for t in tasks:
        if t["authorized"]:
            continue
        t["authorized"] = True
        t["updated_at"] = now()
        emit(state, "task.authorize", actor=actor, entity=t["id"], summary=f"authorized via {via}")
    sync(state)


# ---------------------------------------------------------------------------
# Evidence and the fix-attempt limit (MASTER_SPEC §12.1)


def add_evidence(
    state: State,
    task_id: str,
    type: str,
    result: str,
    summary: str,
    command: str | None = None,
    ref: str | None = None,
    actor: str = "orchestrator",
    test_result_id: int | None = None,
) -> dict:
    require_mutable(state)
    task = get_task(state, task_id)
    if task["status"] in TERMINAL:
        raise OrkestrError(f"{task_id} is {task['status']}; evidence can no longer be added.")
    if type not in EVIDENCE_TYPES:
        raise OrkestrError(f"--type must be one of {', '.join(EVIDENCE_TYPES)}")
    if result not in EVIDENCE_RESULTS:
        raise OrkestrError(f"--result must be one of {', '.join(EVIDENCE_RESULTS)}")
    if not non_empty(summary):
        raise OrkestrError("--summary is required: describe what was checked and what happened.")
    if type in ("test", "check") and result != "not_run" and not non_empty(command):
        raise OrkestrError("--command is required for an executed test or check (record the command that actually ran).")
    entry = {"type": type, "result": result, "command": command, "summary": summary.strip(), "ref": ref, "at": now()}
    if test_result_id is not None:
        entry["test_result_id"] = test_result_id
    task["evidence"].append(entry)
    task["updated_at"] = now()
    emit(state, "task.evidence", actor=actor, entity=task_id, summary=f"{type} {result}: {entry['summary']}", data={"command": command, "ref": ref})

    limit = state.project["limits"]["max_fix_attempts"]
    outcome = {"task": task, "failed_task": False, "fix_attempts": task["fix_attempts"], "max": limit}
    if type in ("test", "check") and result == "failed" and task["status"] == "IN_PROGRESS":
        if task["fix_attempts"] >= limit:
            can_recover = autonomy_of(state) == "bounded" and task.get("recovery_meetings", 0) < max_recovery_meetings(state)
            transition(
                state,
                task_id,
                "FAILED",
                actor="orkestr",
                error=f"Checks still failing after {limit} fix attempts: {entry['summary']}",
                next_action=(
                    f'Recovery meeting required: hold a meeting with "recovery_for": "{task_id}" to choose a new approach (no founder approval needed).'
                    if can_recover
                    else "Founder decision required: retry with --retry, change the approach, or cancel the task."
                ),
            )
            outcome["failed_task"] = True
        else:
            task["fix_attempts"] += 1
        outcome["fix_attempts"] = task["fix_attempts"]
    return outcome


# ---------------------------------------------------------------------------
# Approvals (MASTER_SPEC §10.3)


def request_approval(
    state: State,
    action: str,
    category: str,
    reason: str,
    consequences: str = "",
    risks: str = "",
    task_id: str | None = None,
    requested_by: str = "orchestrator",
) -> dict:
    require_mutable(state)
    if not non_empty(action):
        raise OrkestrError("--action is required.")
    if category not in APPROVAL_CATEGORIES:
        raise OrkestrError(f"--category must be one of {', '.join(APPROVAL_CATEGORIES)}")
    if not non_empty(reason):
        raise OrkestrError("--reason is required: why is this action needed?")
    task = None
    if task_id:
        task = get_task(state, task_id)
        if task["status"] in TERMINAL:
            raise OrkestrError(f"{task_id} is {task['status']}.")
        current = next((a for a in state.approvals if a["id"] == task.get("approval_id")), None) if task.get("approval_id") else None
        if current and current["status"] in ("PENDING", "GRANTED"):
            raise OrkestrError(f"{task_id} already has approval {current['id']} ({current['status']}).")
    approval = {
        "id": next_id(state, "approval"),
        "action": action.strip(),
        "category": category,
        "reason": reason.strip(),
        "consequences": consequences,
        "risks": risks,
        "requested_by": requested_by,
        "task_id": task_id,
        "status": "PENDING",
        "decided_by": None,
        "decided_at": None,
        "decision_note": None,
        "execution_result": None,
        "created_at": now(),
    }
    state.approvals.append(approval)
    if task:
        task["requires_approval"] = True
        task["approval_id"] = approval["id"]
        task["updated_at"] = now()
    emit(state, "approval.request", actor=requested_by, entity=approval["id"], to="PENDING", summary=approval["action"], data={"category": category, "task_id": task_id})
    return approval


def decide_approval(state: State, approval_id: str, decision: str, by: str, note: str | None = None) -> dict:
    require_mutable(state)
    require_founder(by)
    a = get_approval(state, approval_id)
    target = {"grant": "GRANTED", "reject": "REJECTED", "expire": "EXPIRED", "cancel": "CANCELLED"}.get(decision)
    if not target:
        raise OrkestrError(f"Unknown approval decision: {decision}")
    if a["status"] != "PENDING":
        raise OrkestrError(f"{approval_id} is {a['status']}; only PENDING approvals can change, and only once.", "E_APPROVAL_CLOSED")
    a["status"] = target
    a["decided_by"] = by
    a["decided_at"] = now()
    a["decision_note"] = note
    emit(state, f"approval.{decision}", actor=by, entity=approval_id, frm="PENDING", to=target, summary=note or a["action"])
    if target == "REJECTED" and a.get("task_id"):
        task = get_task(state, a["task_id"])
        if "BLOCKED" in TRANSITIONS[task["status"]]:
            transition(state, task["id"], "BLOCKED", actor="orkestr", reason=f"Approval {approval_id} was rejected")
    return a


def record_approval_result(state: State, approval_id: str, result: str, actor: str = "orchestrator") -> dict:
    a = get_approval(state, approval_id)
    if a["status"] != "GRANTED":
        raise OrkestrError(f"{approval_id} is {a['status']}; only GRANTED approvals have an execution result.")
    if a.get("execution_result"):
        raise OrkestrError(f"{approval_id} already has an execution result.")
    if not non_empty(result):
        raise OrkestrError("--result is required.")
    a["execution_result"] = result.strip()
    emit(state, "approval.result", actor=actor, entity=approval_id, summary=a["execution_result"])
    return a


# ---------------------------------------------------------------------------
# Project lifecycle (MASTER_SPEC §12.2)


def set_project_status(state: State, action: str, reason: str | None = None, by: str | None = None):
    p = state.project
    frm = p["status"]
    if action == "pause":
        if frm != "ACTIVE":
            raise OrkestrError(f"Only an ACTIVE project can be paused (project is {frm}).")
        if not non_empty(reason):
            raise OrkestrError("--reason is required to pause.")
        p["status"] = "PAUSED"
        p["status_reason"] = reason.strip()
    elif action == "resume":
        if frm != "PAUSED":
            raise OrkestrError(f"Only a PAUSED project can be resumed (project is {frm}).")
        p["status"] = "ACTIVE"
        p["status_reason"] = None
    elif action == "cancel":
        require_founder(by)
        if frm not in ("ACTIVE", "PAUSED"):
            raise OrkestrError(f"Project is {frm}; it cannot be cancelled.")
        if not non_empty(reason):
            raise OrkestrError("--reason is required to cancel.")
        for t in state.tasks:
            if t["status"] not in TERMINAL:
                _set_status(state, t, "CANCELLED", actor="orkestr", summary="project cancelled")
        for a in state.approvals:
            if a["status"] == "PENDING":
                a["status"] = "CANCELLED"
                a["decided_by"] = by
                a["decided_at"] = now()
                a["decision_note"] = "project cancelled"
                emit(state, "approval.cancel", actor="orkestr", entity=a["id"], frm="PENDING", to="CANCELLED", summary="project cancelled")
        p["status"] = "CANCELLED"
        p["status_reason"] = reason.strip()
    elif action == "complete":
        if frm != "ACTIVE":
            raise OrkestrError(f"Only an ACTIVE project can be completed (project is {frm}).")
        open_ids = [t["id"] for t in state.tasks if t["status"] not in TERMINAL]
        if open_ids:
            raise OrkestrError(f"Open tasks remain: {', '.join(open_ids)}.")
        if not any(t["status"] == "COMPLETED" for t in state.tasks):
            raise OrkestrError("No task has been completed.")
        p["status"] = "COMPLETED"
        p["status_reason"] = reason
    else:
        raise OrkestrError(f"Unknown project action: {action}")
    emit(state, "project.status", actor=by or "orchestrator", entity="project", frm=frm, to=p["status"], summary=reason or "")
    sync(state)


def recover_task(state: State, task_id: str, decision_id: str, approach: str, actor: str):
    """Reopens a FAILED task after an approved recovery decision (MASTER_SPEC §12.1)."""
    task = get_task(state, task_id)
    if task["status"] != "FAILED":
        return None
    transition(state, task_id, "READY", retry=True, actor=actor, reason=f"recovery decision {decision_id}")
    add_note(state, task_id, author=actor, text=f"Recovery decision {decision_id}: {approach}")
    return task


def add_note(state: State, task_id: str, author: str, text: str) -> dict:
    require_mutable(state)
    task = get_task(state, task_id)
    if author not in OWNERS:
        raise OrkestrError(f"--author must be one of {', '.join(OWNERS)}")
    if not non_empty(text):
        raise OrkestrError("--text is required.")
    task.setdefault("notes", []).append({"author": author, "text": text.strip(), "at": now()})
    task["updated_at"] = now()
    emit(state, "task.note", actor=author, entity=task_id, summary=text.strip())
    return task


def add_assumption(state: State, text: str, task_id: str | None = None, impact: str = "low", actor: str = "orchestrator") -> dict:
    require_mutable(state)
    if not non_empty(text):
        raise OrkestrError("--text is required.")
    if impact not in IMPACTS:
        raise OrkestrError(f"--impact must be one of {', '.join(IMPACTS)}")
    if task_id:
        get_task(state, task_id)
    entry = {"text": text.strip(), "task": task_id, "impact": impact, "at": now()}
    state.project.setdefault("assumptions", []).append(entry)
    emit(state, "assumption.record", actor=actor, entity=task_id or "project", summary=entry["text"], data={"impact": impact})
    return entry


def set_autonomy(state: State, mode: str, by: str):
    require_mutable(state)
    require_founder(by)
    if mode not in AUTONOMY_MODES:
        raise OrkestrError(f"Autonomy must be one of {', '.join(AUTONOMY_MODES)}")
    frm = autonomy_of(state)
    state.project["autonomy"] = mode
    emit(state, "project.autonomy", actor=by, entity="project", frm=frm, to=mode)


def import_plan(state: State, draft: dict) -> list[dict]:
    """`plan import`: creates plan tasks; bounded autonomy authorizes them at once."""
    require_mutable(state)
    tasks = create_tasks(state, draft.get("tasks"), {"type": "plan", "ref": None})
    emit(state, "plan.import", entity="plan", summary=draft.get("summary") or "", data={"task_ids": [t["id"] for t in tasks]})
    if autonomy_of(state) == "bounded":
        authorize(state, tasks, actor="orchestrator", via="plan (bounded autonomy)")
    return tasks


def approve_plan(state: State, by: str, note: str | None = None) -> list[str]:
    require_mutable(state)
    require_founder(by)
    pending = [t for t in state.tasks if t["source"]["type"] == "plan" and not t["authorized"] and t["status"] == "BACKLOG"]
    if not pending:
        raise OrkestrError("There are no unapproved plan tasks.")
    emit(state, "plan.approve", actor=by, entity="plan", summary=note or "", data={"task_ids": [t["id"] for t in pending]})
    authorize(state, pending, actor=by, via="plan approval")
    return [t["id"] for t in pending]


def authorize_task(state: State, task_id: str, by: str):
    require_mutable(state)
    require_founder(by)
    t = get_task(state, task_id)
    if t["authorized"]:
        raise OrkestrError(f"{task_id} is already authorized.")
    authorize(state, [t], actor=by, via="founder authorization")


def next_task(state: State) -> dict | None:
    """An interrupted IN_PROGRESS task first, then the lowest READY task."""
    return next((t for t in state.tasks if t["status"] == "IN_PROGRESS"), None) or next(
        (t for t in state.tasks if t["status"] == "READY"), None
    )
