"""Meetings, decisions, reviews, and retrospectives, ported from records.mjs
(MASTER_SPEC §6, §11.3, §18). Records live in State dicts keyed by ID."""

from __future__ import annotations

from ..constants import AGENT_ROLES, OWNERS, PREFIX
from .errors import OrkestrError, is_id, non_empty, now, str_list
from .state import (
    TERMINAL,
    State,
    add_evidence,
    add_note,
    authorize,
    autonomy_of,
    create_tasks,
    emit,
    find_task,
    get_task,
    max_recovery_meetings,
    next_id,
    recover_task,
    require_founder,
    require_mutable,
    transition,
)

MEETING_OUTCOMES = ("DECIDED", "ESCALATED", "NO_DECISION")
DECISION_STATES = ("PROPOSED", "NEEDS_FOUNDER", "APPROVED", "REJECTED", "SUPERSEDED")
REVIEW_VERDICTS = ("PASS", "CHANGES_REQUESTED", "BLOCKED")
SEVERITIES = ("critical", "high", "medium", "low", "info")
FINDING_STATUS = ("confirmed", "suspected")
PARTICIPANT_ROLES = (*AGENT_ROLES, "orchestrator", "founder")


def _fail(kind: str, errors: list[str]):
    if errors:
        raise OrkestrError(f"Invalid {kind} draft:\n- " + "\n- ".join(errors))


def _put(state: State, folder: str, record: dict):
    getattr(state, folder)[record["id"]] = record
    state.changed_records.add((folder, record["id"]))


# ---------------------------------------------------------------------------
# Meetings


def validate_meeting_draft(draft: dict, state: State) -> list[str]:
    limits = state.project["limits"]
    e: list[str] = []
    recovery_for = draft.get("recovery_for")
    if recovery_for is not None:
        task = find_task(state, recovery_for)
        if not task:
            e.append(f"recovery_for: unknown task {recovery_for}")
        elif task["status"] != "FAILED":
            e.append(f"recovery_for: {recovery_for} is {task['status']}, not FAILED")
        elif task.get("recovery_meetings", 0) >= max_recovery_meetings(state):
            e.append(f"recovery_for: {recovery_for} already used its {max_recovery_meetings(state)} recovery meeting(s); the founder must decide")
    applies_to = draft.get("applies_to") if draft.get("applies_to") is not None else []
    applies_ok = str_list(applies_to)
    if not applies_ok:
        e.append('"applies_to" must be a list of task IDs')
    else:
        for tid in applies_to:
            task = find_task(state, tid)
            if not task:
                e.append(f"applies_to: unknown task {tid}")
            elif task["status"] in TERMINAL:
                e.append(f"applies_to: {tid} is {task['status']}; a decision can only steer open tasks")
    decision = draft.get("decision") if isinstance(draft.get("decision"), dict) else None
    if decision and "founder_required" in decision and not isinstance(decision["founder_required"], bool):
        e.append("decision.founder_required must be true or false")
    if not non_empty(draft.get("objective")):
        e.append('"objective" is required')
    if not non_empty(draft.get("context")):
        e.append('"context" is required')
    if not non_empty(draft.get("trigger")):
        e.append('"trigger" is required (why a meeting, not direct delegation)')

    participants = draft.get("participants") if isinstance(draft.get("participants"), list) else []
    roles = [p.get("role") if isinstance(p, dict) else None for p in participants]
    if len(participants) < 2:
        e.append("a meeting needs at least 2 participants")
    if len(participants) > limits["max_meeting_participants"]:
        e.append(f"at most {limits['max_meeting_participants']} participants are allowed (got {len(participants)})")
    for i, p in enumerate(participants):
        p = p if isinstance(p, dict) else {}
        if p.get("role") not in PARTICIPANT_ROLES:
            e.append(f"participants[{i}].role must be one of {', '.join(PARTICIPANT_ROLES)}")
        if not non_empty(p.get("responsibility")):
            e.append(f"participants[{i}].responsibility is required")
    if len(set(roles)) != len(roles):
        e.append("participant roles must be unique")

    rounds = draft.get("rounds") if isinstance(draft.get("rounds"), list) else []
    if len(rounds) < 1:
        e.append("at least one discussion round is required")
    if len(rounds) > limits["max_discussion_rounds"]:
        e.append(f"at most {limits['max_discussion_rounds']} discussion rounds are allowed (got {len(rounds)})")
    for i, r in enumerate(rounds):
        r = r if isinstance(r, dict) else {}
        if r.get("round") != i + 1:
            e.append(f"rounds[{i}].round must be {i + 1}")
        contributions = r.get("contributions") if isinstance(r.get("contributions"), list) else []
        if not contributions:
            e.append(f"rounds[{i}] has no contributions")
        for j, c in enumerate(contributions):
            c = c if isinstance(c, dict) else {}
            if c.get("role") not in roles:
                e.append(f"rounds[{i}].contributions[{j}].role is not a participant")
            if not non_empty(c.get("position")):
                e.append(f"rounds[{i}].contributions[{j}].position is required")

    options = draft.get("options") if isinstance(draft.get("options"), list) else []
    option_ids = [o.get("id") if isinstance(o, dict) else None for o in options]
    for i, o in enumerate(options):
        o = o if isinstance(o, dict) else {}
        if not non_empty(o.get("id")) or not non_empty(o.get("name")):
            e.append(f'options[{i}] needs "id" and "name"')
    if len(set(option_ids)) != len(option_ids):
        e.append("option ids must be unique")
    if "criteria" in draft and not str_list(draft["criteria"]):
        e.append('"criteria" must be a list of strings')

    outcome = draft.get("outcome")
    if outcome not in MEETING_OUTCOMES:
        e.append(f'"outcome" must be one of {", ".join(MEETING_OUTCOMES)}')
    items = draft.get("action_items") if isinstance(draft.get("action_items"), list) else []
    if outcome == "DECIDED":
        d = decision
        if not d or not non_empty(d.get("question")) or not non_empty(d.get("rationale")):
            e.append("DECIDED requires decision.question and decision.rationale")
        if len(options) < 1:
            e.append("DECIDED requires at least one option")
        if d and d.get("selected_option") not in option_ids:
            e.append("decision.selected_option must be one of the option ids")
        if not items and recovery_for is None and not (applies_ok and applies_to):
            e.append('DECIDED requires at least one action item, or "applies_to" with the open tasks the decision steers')
    if outcome == "ESCALATED":
        d = decision
        if not d or not non_empty(d.get("question")):
            e.append("ESCALATED requires decision.question")
        if d and d.get("selected_option") is not None:
            e.append("ESCALATED must not select an option; the founder decides")
        if len(options) < 2:
            e.append("ESCALATED requires at least two options for the founder to choose from")
    if outcome == "NO_DECISION" and items:
        e.append("NO_DECISION cannot create action items; add tasks manually or escalate instead")
    return e


def _approach_of(d: dict) -> str:
    option = next((o for o in d["options"] if o.get("id") == d["selected_option"]), None)
    name = option["name"] if option else d["selected_option"]
    return f"{name}{(' — ' + d['rationale']) if d.get('rationale') else ''}"


def _note_applied_decision(state: State, d: dict, author: str):
    """A decision that steers existing tasks reaches them as task notes instead of new tasks."""
    for tid in d.get("applies_to") or []:
        task = find_task(state, tid)
        if task and task["status"] not in TERMINAL:
            add_note(state, tid, author=author, text=f"{d['id']}: {_approach_of(d)}")


def record_meeting(state: State, draft: dict) -> dict:
    require_mutable(state)
    _fail("meeting", validate_meeting_draft(draft, state))
    recovery_for = draft.get("recovery_for")

    meeting_id = next_id(state, "meeting")
    has_decision = draft["outcome"] in ("DECIDED", "ESCALATED")
    decision_id = next_id(state, "decision") if has_decision else None
    items = draft.get("action_items") if isinstance(draft.get("action_items"), list) else []
    tasks = create_tasks(state, items, {"type": "meeting", "ref": meeting_id}, decision_id=decision_id) if items else []
    date = now()
    participants = [p["role"] for p in draft["participants"]]

    meeting = {
        "schema_version": 1,
        "id": meeting_id,
        "date": date,
        "project": state.project["name"],
        "objective": draft["objective"],
        "context": draft["context"],
        "trigger": draft["trigger"],
        "participants": draft["participants"],
        "rounds": draft["rounds"],
        "options": draft.get("options") or [],
        "criteria": draft.get("criteria") or [],
        "evaluation": draft.get("evaluation") or "",
        "outcome": draft["outcome"],
        "decision_id": decision_id,
        "recovery_for": recovery_for,
        "dissent": draft.get("dissent") or [],
        "open_questions": draft.get("open_questions") or [],
        "task_ids": [t["id"] for t in tasks],
        "applies_to": draft.get("applies_to") or [],
    }
    _put(state, "meetings", meeting)
    emit(
        state,
        "meeting.record",
        entity=meeting_id,
        summary=draft["objective"],
        data={"outcome": draft["outcome"], "participants": participants, "decision_id": decision_id, "task_ids": meeting["task_ids"]},
    )

    decision = None
    if has_decision:
        d = draft["decision"]
        decision = {
            "schema_version": 1,
            "id": decision_id,
            "date": date,
            "project": state.project["name"],
            "question": d["question"],
            "context": draft["context"],
            "options": draft.get("options") or [],
            "criteria": draft.get("criteria") or [],
            "selected_option": d.get("selected_option") if draft["outcome"] == "DECIDED" else None,
            "rationale": d.get("rationale") or "",
            "tradeoffs": d.get("tradeoffs") or [],
            "objections": draft.get("dissent") or [],
            "risks": d.get("risks") or [],
            "participants": participants,
            "approval_status": "PROPOSED" if draft["outcome"] == "DECIDED" else "NEEDS_FOUNDER",
            "founder_required": d.get("founder_required") is True,
            "meeting_id": meeting_id,
            "recovery_for": recovery_for,
            "task_ids": meeting["task_ids"],
            "applies_to": meeting["applies_to"],
            "decided_by": None,
            "decided_at": None,
            "note": None,
        }
        emit(state, "decision.create", entity=decision_id, to=decision["approval_status"], summary=d["question"], data={"meeting_id": meeting_id})

        # Bounded autonomy: routine technical decisions take effect without the founder.
        if decision["approval_status"] == "PROPOSED" and not decision["founder_required"] and autonomy_of(state) == "bounded":
            decision["approval_status"] = "APPROVED"
            decision["decided_by"] = "orchestrator"
            decision["decided_at"] = date
            decision["note"] = "Auto-approved under bounded autonomy (routine technical decision)."
            emit(state, "decision.approve", actor="orchestrator", entity=decision_id, frm="PROPOSED", to="APPROVED", summary=decision["note"])
            authorize(state, tasks, actor="orchestrator", via=f"{decision_id} (bounded autonomy)")
            _note_applied_decision(state, decision, "orchestrator")
        _put(state, "decisions", decision)

    if recovery_for:
        task = get_task(state, recovery_for)
        task["recovery_meetings"] = task.get("recovery_meetings", 0) + 1
        if decision and decision["approval_status"] == "APPROVED":
            recover_task(state, recovery_for, decision_id, _approach_of(decision), actor="orchestrator")
        else:
            task["failure"]["next_action"] = (
                f"Founder approval of {decision_id} needed; approving it reopens {recovery_for}."
                if decision and decision["approval_status"] == "PROPOSED"
                else f"Founder decision required: recovery meeting {meeting_id} reached no decision."
            )
    return {"meeting": meeting, "decision": decision, "tasks": tasks}


# ---------------------------------------------------------------------------
# Decisions


def get_decision(state: State, decision_id: str) -> dict:
    if not is_id(PREFIX["decision"], decision_id):
        raise OrkestrError(f"Invalid decision ID: {decision_id}")
    d = state.decisions.get(decision_id)
    if not d:
        raise OrkestrError(f"Unknown decision: {decision_id}", "E_NOT_FOUND")
    return d


def decide_decision(state: State, decision_id: str, action: str, by: str, option: str | None = None, note: str | None = None) -> dict:
    require_mutable(state)
    require_founder(by)
    d = get_decision(state, decision_id)
    if d["approval_status"] not in ("PROPOSED", "NEEDS_FOUNDER"):
        raise OrkestrError(f"{decision_id} is {d['approval_status']}; only PROPOSED or NEEDS_FOUNDER decisions can be decided.")
    frm = d["approval_status"]
    tasks = [t for t in state.tasks if t.get("decision_id") == decision_id]
    if action == "approve":
        if option is not None:
            if not any(o.get("id") == option for o in d["options"]):
                raise OrkestrError(f'Unknown option "{option}" for {decision_id}.')
            d["selected_option"] = option
        if not d.get("selected_option"):
            raise OrkestrError(f"{decision_id} has no selected option; pass --option.")
        d["approval_status"] = "APPROVED"
    elif action == "reject":
        if not non_empty(note):
            raise OrkestrError("--note is required when rejecting a decision.")
        d["approval_status"] = "REJECTED"
    else:
        raise OrkestrError(f"Unknown decision action: {action}")
    d["decided_by"] = by
    d["decided_at"] = now()
    d["note"] = note
    state.changed_records.add(("decisions", decision_id))
    emit(state, f"decision.{action}", actor=by, entity=decision_id, frm=frm, to=d["approval_status"], summary=note or "")

    if action == "approve":
        authorize(state, tasks, actor=by, via=decision_id)
        if d.get("recovery_for"):
            recover_task(state, d["recovery_for"], decision_id, _approach_of(d), actor=by)
        _note_applied_decision(state, d, "founder")
    else:
        for t in tasks:
            if t["status"] in TERMINAL or t["status"] in ("IN_PROGRESS", "REVIEW"):
                continue
            transition(state, t["id"], "CANCELLED", actor="orkestr", reason=f"Decision {decision_id} was rejected")
    return {"decision": d, "tasks": tasks}


# ---------------------------------------------------------------------------
# Reviews


def validate_review_draft(draft: dict, state: State) -> list[str]:
    e: list[str] = []
    if draft.get("task_id") is not None and not find_task(state, draft["task_id"]):
        e.append(f"unknown task_id {draft['task_id']}")
    reviewers = draft.get("reviewers")
    if not isinstance(reviewers, list) or not reviewers or not all(r in OWNERS for r in reviewers):
        e.append(f'"reviewers" must list roles from: {", ".join(OWNERS)}')
    if not non_empty(draft.get("scope")):
        e.append('"scope" is required')
    if draft.get("verdict") not in REVIEW_VERDICTS:
        e.append(f'"verdict" must be one of {", ".join(REVIEW_VERDICTS)}')
    checks = draft.get("checks_run") if draft.get("checks_run") is not None else []
    if not isinstance(checks, list):
        e.append('"checks_run" must be a list')
    else:
        for i, c in enumerate(checks):
            c = c if isinstance(c, dict) else {}
            if not non_empty(c.get("command")):
                e.append(f"checks_run[{i}].command is required (only list commands that actually ran)")
            if c.get("result") not in ("passed", "failed"):
                e.append(f"checks_run[{i}].result must be passed or failed")
    findings = draft.get("findings") if draft.get("findings") is not None else []
    if not isinstance(findings, list):
        e.append('"findings" must be a list')
    else:
        for i, f in enumerate(findings):
            f = f if isinstance(f, dict) else {}
            if not non_empty(f.get("title")):
                e.append(f"findings[{i}].title is required")
            if f.get("severity") not in SEVERITIES:
                e.append(f"findings[{i}].severity must be one of {', '.join(SEVERITIES)}")
            if f.get("status") not in FINDING_STATUS:
                e.append(f"findings[{i}].status must be confirmed or suspected")
            if not non_empty(f.get("evidence")):
                e.append(f"findings[{i}].evidence is required")
        serious = [f for f in findings if isinstance(f, dict) and f.get("status") == "confirmed" and f.get("severity") in ("critical", "high")]
        if draft.get("verdict") == "PASS" and serious:
            e.append("verdict PASS is not allowed with confirmed critical/high findings")
    if "assumptions" in draft and not str_list(draft["assumptions"]):
        e.append('"assumptions" must be a list of strings')
    return e


def record_review(state: State, draft: dict, check_evidence: bool = True) -> dict:
    """Records a review. With check_evidence=False the listed checks_run are shown in the
    review but not added as task evidence again (the core already recorded them when it
    ran the commands itself)."""
    require_mutable(state)
    _fail("review", validate_review_draft(draft, state))
    review_id = next_id(state, "review")
    review = {
        "schema_version": 1,
        "id": review_id,
        "date": now(),
        "task_id": draft.get("task_id"),
        "reviewers": draft["reviewers"],
        "scope": draft["scope"],
        "verdict": draft["verdict"],
        "checks_run": draft.get("checks_run") or [],
        "findings": draft.get("findings") or [],
        "assumptions": draft.get("assumptions") or [],
    }
    _put(state, "reviews", review)
    emit(
        state,
        "review.record",
        entity=review_id,
        summary=f"{review['verdict']}: {review['scope']}",
        data={"task_id": review["task_id"], "findings": len(review["findings"])},
    )
    if review["task_id"]:
        task = get_task(state, review["task_id"])
        if task["status"] not in TERMINAL:
            if check_evidence:
                for c in review["checks_run"]:
                    add_evidence(state, task["id"], type="check", result=c["result"], command=c["command"], summary=c.get("summary") or c["command"], ref=review_id)
            confirmed = len([f for f in review["findings"] if f["status"] == "confirmed"])
            add_evidence(
                state,
                task["id"],
                type="review",
                result="passed" if review["verdict"] == "PASS" else "failed",
                summary=f"{review['verdict']} ({confirmed} confirmed / {len(review['findings']) - confirmed} suspected findings)",
                ref=review_id,
            )
    return review


# ---------------------------------------------------------------------------
# Retrospectives


def validate_retro_draft(draft: dict) -> list[str]:
    e: list[str] = []
    if not non_empty(draft.get("scope")):
        e.append('"scope" is required')
    for key in ("went_well", "went_badly", "incorrect_assumptions", "proposed_changes"):
        if key in draft and not str_list(draft[key]):
            e.append(f'"{key}" must be a list of strings')
    lessons = draft.get("lessons") if draft.get("lessons") is not None else []
    if not isinstance(lessons, list):
        e.append('"lessons" must be a list')
    else:
        for i, l in enumerate(lessons):
            l = l if isinstance(l, dict) else {}
            if not non_empty(l.get("lesson")):
                e.append(f"lessons[{i}].lesson is required")
            if not non_empty(l.get("evidence")):
                e.append(f"lessons[{i}].evidence is required (lessons must be backed by evidence)")
    return e


def record_retro(state: State, draft: dict) -> dict:
    _fail("retrospective", validate_retro_draft(draft))
    retro_id = next_id(state, "retro")
    retro = {
        "schema_version": 1,
        "id": retro_id,
        "date": now(),
        "scope": draft["scope"],
        "went_well": draft.get("went_well") or [],
        "went_badly": draft.get("went_badly") or [],
        "incorrect_assumptions": draft.get("incorrect_assumptions") or [],
        "decomposition_assessment": draft.get("decomposition_assessment") or "",
        "meeting_assessment": draft.get("meeting_assessment") or "",
        "lessons": draft.get("lessons") or [],
        "proposed_changes": draft.get("proposed_changes") or [],
    }
    _put(state, "retros", retro)
    if retro["lessons"]:
        block = "\n".join(f"- {l['lesson']} — evidence: {l['evidence']} ({retro_id})" for l in retro["lessons"])
        state.lesson_blocks.append(f"## {retro_id} — {retro['date'][:10]}\n\n{block}\n")
    emit(state, "retro.record", entity=retro_id, summary=retro["scope"], data={"lessons": len(retro["lessons"])})
    return retro
