// Meetings, decisions, reviews, and retrospectives (MASTER_SPEC §6, §11.3, §18).
// Each record is a JSON file (source of truth) plus a generated Markdown view.

import fs from 'node:fs';
import path from 'node:path';
import { AGENT_ROLES, OWNERS, OrkestrError, PREFIX, isId, now, paths, readJson } from './core.mjs';
import {
  TERMINAL,
  addEvidence,
  authorize,
  autonomyOf,
  createTasks,
  emit,
  addNote,
  getTask,
  maxRecoveryMeetings,
  nextId,
  recoverTask,
  requireFounder,
  requireMutable,
  transition,
} from './state.mjs';
import { renderDecision, renderMeeting, renderRetro, renderReview } from './render.mjs';

export const MEETING_OUTCOMES = Object.freeze(['DECIDED', 'ESCALATED', 'NO_DECISION']);
export const DECISION_STATES = Object.freeze(['PROPOSED', 'NEEDS_FOUNDER', 'APPROVED', 'REJECTED', 'SUPERSEDED']);
export const REVIEW_VERDICTS = Object.freeze(['PASS', 'CHANGES_REQUESTED', 'BLOCKED']);
export const SEVERITIES = Object.freeze(['critical', 'high', 'medium', 'low', 'info']);
export const FINDING_STATUS = Object.freeze(['confirmed', 'suspected']);
const PARTICIPANT_ROLES = Object.freeze([...AGENT_ROLES, 'orchestrator', 'founder']);

const str = (v) => typeof v === 'string' && v.trim().length > 0;
const strList = (v) => Array.isArray(v) && v.every((x) => typeof x === 'string');

function fail(kind, errors) {
  if (errors.length) throw new OrkestrError(`Invalid ${kind} draft:\n- ${errors.join('\n- ')}`);
}

function recordPaths(root, folder, id) {
  const dir = paths(root)[folder];
  return { json: path.join(dir, `${id}.json`), md: path.join(dir, `${id}.md`) };
}

function queueRecord(state, folder, record, markdown) {
  const p = recordPaths(state.root, folder, record.id);
  state.writes.push({ file: p.json, json: record }, { file: p.md, text: markdown });
}

export function loadDraft(root, file) {
  const full = path.resolve(root, file);
  const draft = readJson(full, root);
  if (!draft || typeof draft !== 'object' || Array.isArray(draft)) {
    throw new OrkestrError(`Draft ${file} must contain a JSON object.`);
  }
  return draft;
}

// ---------------------------------------------------------------------------
// Meetings

export function validateMeetingDraft(draft, state) {
  const limits = state.project.limits;
  const e = [];
  const recoveryFor = draft.recovery_for ?? null;
  if (recoveryFor !== null) {
    const task = state.tasks.find((t) => t.id === recoveryFor);
    if (!task) e.push(`recovery_for: unknown task ${recoveryFor}`);
    else if (task.status !== 'FAILED') e.push(`recovery_for: ${recoveryFor} is ${task.status}, not FAILED`);
    else if ((task.recovery_meetings ?? 0) >= maxRecoveryMeetings(state)) {
      e.push(`recovery_for: ${recoveryFor} already used its ${maxRecoveryMeetings(state)} recovery meeting(s); the founder must decide`);
    }
  }
  const appliesTo = draft.applies_to ?? [];
  if (!Array.isArray(appliesTo) || !appliesTo.every((id) => typeof id === 'string')) e.push('"applies_to" must be a list of task IDs');
  else {
    for (const id of appliesTo) {
      const task = state.tasks.find((t) => t.id === id);
      if (!task) e.push(`applies_to: unknown task ${id}`);
      else if (TERMINAL.includes(task.status)) e.push(`applies_to: ${id} is ${task.status}; a decision can only steer open tasks`);
    }
  }
  if (draft.decision?.founder_required !== undefined && typeof draft.decision.founder_required !== 'boolean') {
    e.push('decision.founder_required must be true or false');
  }
  if (!str(draft.objective)) e.push('"objective" is required');
  if (!str(draft.context)) e.push('"context" is required');
  if (!str(draft.trigger)) e.push('"trigger" is required (why a meeting, not direct delegation)');

  const participants = Array.isArray(draft.participants) ? draft.participants : [];
  const roles = participants.map((p) => p?.role);
  if (participants.length < 2) e.push('a meeting needs at least 2 participants');
  if (participants.length > limits.max_meeting_participants) {
    e.push(`at most ${limits.max_meeting_participants} participants are allowed (got ${participants.length})`);
  }
  participants.forEach((p, i) => {
    if (!PARTICIPANT_ROLES.includes(p?.role)) e.push(`participants[${i}].role must be one of ${PARTICIPANT_ROLES.join(', ')}`);
    if (!str(p?.responsibility)) e.push(`participants[${i}].responsibility is required`);
  });
  if (new Set(roles).size !== roles.length) e.push('participant roles must be unique');

  const rounds = Array.isArray(draft.rounds) ? draft.rounds : [];
  if (rounds.length < 1) e.push('at least one discussion round is required');
  if (rounds.length > limits.max_discussion_rounds) {
    e.push(`at most ${limits.max_discussion_rounds} discussion rounds are allowed (got ${rounds.length})`);
  }
  rounds.forEach((r, i) => {
    if (r?.round !== i + 1) e.push(`rounds[${i}].round must be ${i + 1}`);
    const contributions = Array.isArray(r?.contributions) ? r.contributions : [];
    if (!contributions.length) e.push(`rounds[${i}] has no contributions`);
    contributions.forEach((c, j) => {
      if (!roles.includes(c?.role)) e.push(`rounds[${i}].contributions[${j}].role is not a participant`);
      if (!str(c?.position)) e.push(`rounds[${i}].contributions[${j}].position is required`);
    });
  });

  const options = Array.isArray(draft.options) ? draft.options : [];
  const optionIds = options.map((o) => o?.id);
  options.forEach((o, i) => {
    if (!str(o?.id) || !str(o?.name)) e.push(`options[${i}] needs "id" and "name"`);
  });
  if (new Set(optionIds).size !== optionIds.length) e.push('option ids must be unique');
  if (draft.criteria !== undefined && !strList(draft.criteria)) e.push('"criteria" must be a list of strings');

  if (!MEETING_OUTCOMES.includes(draft.outcome)) e.push(`"outcome" must be one of ${MEETING_OUTCOMES.join(', ')}`);
  const items = Array.isArray(draft.action_items) ? draft.action_items : [];
  if (draft.outcome === 'DECIDED') {
    const d = draft.decision;
    if (!d || !str(d.question) || !str(d.rationale)) e.push('DECIDED requires decision.question and decision.rationale');
    if (options.length < 1) e.push('DECIDED requires at least one option');
    if (d && !optionIds.includes(d.selected_option)) e.push('decision.selected_option must be one of the option ids');
    if (!items.length && recoveryFor === null && !(Array.isArray(appliesTo) && appliesTo.length)) {
      e.push('DECIDED requires at least one action item, or "applies_to" with the open tasks the decision steers');
    }
  }
  if (draft.outcome === 'ESCALATED') {
    const d = draft.decision;
    if (!d || !str(d.question)) e.push('ESCALATED requires decision.question');
    if (d && d.selected_option != null) e.push('ESCALATED must not select an option; the founder decides');
    if (options.length < 2) e.push('ESCALATED requires at least two options for the founder to choose from');
  }
  if (draft.outcome === 'NO_DECISION' && items.length) {
    e.push('NO_DECISION cannot create action items; add tasks manually or escalate instead');
  }
  return e;
}

export function recordMeeting(state, draft) {
  requireMutable(state);
  fail('meeting', validateMeetingDraft(draft, state));
  const recoveryFor = draft.recovery_for ?? null;

  const meetingId = nextId(state, 'meeting');
  const hasDecision = draft.outcome === 'DECIDED' || draft.outcome === 'ESCALATED';
  const decisionId = hasDecision ? nextId(state, 'decision') : null;
  const items = Array.isArray(draft.action_items) ? draft.action_items : [];
  const tasks = items.length
    ? createTasks(state, items, { type: 'meeting', ref: meetingId }, { decisionId })
    : [];
  const date = now();
  const participants = draft.participants.map((p) => p.role);

  const meeting = {
    schema_version: 1,
    id: meetingId,
    date,
    project: state.project.name,
    objective: draft.objective,
    context: draft.context,
    trigger: draft.trigger,
    participants: draft.participants,
    rounds: draft.rounds,
    options: draft.options ?? [],
    criteria: draft.criteria ?? [],
    evaluation: draft.evaluation ?? '',
    outcome: draft.outcome,
    decision_id: decisionId,
    recovery_for: recoveryFor,
    dissent: draft.dissent ?? [],
    open_questions: draft.open_questions ?? [],
    task_ids: tasks.map((t) => t.id),
    applies_to: draft.applies_to ?? [],
  };
  queueRecord(state, 'meetings', meeting, renderMeeting(meeting));
  emit(state, {
    type: 'meeting.record',
    entity: meetingId,
    summary: draft.objective,
    data: { outcome: draft.outcome, participants, decision_id: decisionId, task_ids: meeting.task_ids },
  });

  let decision = null;
  if (hasDecision) {
    const d = draft.decision;
    decision = {
      schema_version: 1,
      id: decisionId,
      date,
      project: state.project.name,
      question: d.question,
      context: draft.context,
      options: draft.options ?? [],
      criteria: draft.criteria ?? [],
      selected_option: draft.outcome === 'DECIDED' ? d.selected_option : null,
      rationale: d.rationale ?? '',
      tradeoffs: d.tradeoffs ?? [],
      objections: draft.dissent ?? [],
      risks: d.risks ?? [],
      participants,
      approval_status: draft.outcome === 'DECIDED' ? 'PROPOSED' : 'NEEDS_FOUNDER',
      founder_required: d.founder_required === true,
      meeting_id: meetingId,
      recovery_for: recoveryFor,
      task_ids: meeting.task_ids,
      applies_to: meeting.applies_to,
      decided_by: null,
      decided_at: null,
      note: null,
    };
    emit(state, { type: 'decision.create', entity: decisionId, to: decision.approval_status, summary: d.question, data: { meeting_id: meetingId } });

    // Bounded autonomy: routine technical decisions take effect without the founder.
    if (decision.approval_status === 'PROPOSED' && !decision.founder_required && autonomyOf(state) === 'bounded') {
      decision.approval_status = 'APPROVED';
      decision.decided_by = 'orchestrator';
      decision.decided_at = date;
      decision.note = 'Auto-approved under bounded autonomy (routine technical decision).';
      emit(state, { type: 'decision.approve', actor: 'orchestrator', entity: decisionId, from: 'PROPOSED', to: 'APPROVED', summary: decision.note });
      authorize(state, tasks, { actor: 'orchestrator', via: `${decisionId} (bounded autonomy)` });
      noteAppliedDecision(state, decision, 'orchestrator');
    }
    queueRecord(state, 'decisions', decision, renderDecision(decision));
  }

  if (recoveryFor) {
    const task = getTask(state, recoveryFor);
    task.recovery_meetings = (task.recovery_meetings ?? 0) + 1;
    if (decision?.approval_status === 'APPROVED') {
      recoverTask(state, recoveryFor, { decisionId, approach: approachOf(decision), actor: 'orchestrator' });
    } else {
      task.failure.next_action =
        decision?.approval_status === 'PROPOSED'
          ? `Founder approval of ${decisionId} needed; approving it reopens ${recoveryFor}.`
          : `Founder decision required: recovery meeting ${meetingId} reached no decision.`;
    }
  }
  return { meeting, decision, tasks };
}

// A decision that steers existing tasks reaches them as task notes instead of new tasks.
function noteAppliedDecision(state, d, author) {
  for (const id of d.applies_to ?? []) {
    const task = state.tasks.find((t) => t.id === id);
    if (task && !TERMINAL.includes(task.status)) addNote(state, id, { author, text: `${d.id}: ${approachOf(d)}` });
  }
}

function approachOf(d) {
  const option = d.options.find((o) => o.id === d.selected_option);
  return `${option ? option.name : d.selected_option}${d.rationale ? ` — ${d.rationale}` : ''}`;
}

// ---------------------------------------------------------------------------
// Decisions

export function loadDecision(root, id) {
  if (!isId(PREFIX.decision, id)) throw new OrkestrError(`Invalid decision ID: ${id}`);
  const file = recordPaths(root, 'decisions', id).json;
  if (!fs.existsSync(file)) throw new OrkestrError(`Unknown decision: ${id}`, 'E_NOT_FOUND');
  return readJson(file, root);
}

export function decideDecision(state, id, action, { by, option = null, note = null }) {
  requireMutable(state);
  requireFounder(by);
  const d = loadDecision(state.root, id);
  if (d.approval_status !== 'PROPOSED' && d.approval_status !== 'NEEDS_FOUNDER') {
    throw new OrkestrError(`${id} is ${d.approval_status}; only PROPOSED or NEEDS_FOUNDER decisions can be decided.`);
  }
  const from = d.approval_status;
  const tasks = state.tasks.filter((t) => t.decision_id === id);
  if (action === 'approve') {
    if (option !== null) {
      if (!d.options.some((o) => o.id === option)) throw new OrkestrError(`Unknown option "${option}" for ${id}.`);
      d.selected_option = option;
    }
    if (!d.selected_option) throw new OrkestrError(`${id} has no selected option; pass --option.`);
    d.approval_status = 'APPROVED';
  } else if (action === 'reject') {
    if (!str(note)) throw new OrkestrError('--note is required when rejecting a decision.');
    d.approval_status = 'REJECTED';
  } else {
    throw new OrkestrError(`Unknown decision action: ${action}`);
  }
  d.decided_by = by;
  d.decided_at = now();
  d.note = note;
  queueRecord(state, 'decisions', d, renderDecision(d));
  emit(state, { type: `decision.${action}`, actor: by, entity: id, from, to: d.approval_status, summary: note ?? '' });

  if (action === 'approve') {
    authorize(state, tasks, { actor: by, via: id });
    if (d.recovery_for) recoverTask(state, d.recovery_for, { decisionId: id, approach: approachOf(d), actor: by });
    noteAppliedDecision(state, d, 'founder');
  } else {
    for (const t of tasks) {
      if (TERMINAL.includes(t.status) || t.status === 'IN_PROGRESS' || t.status === 'REVIEW') continue;
      transition(state, t.id, 'CANCELLED', { actor: 'orkestr', reason: `Decision ${id} was rejected` });
    }
  }
  return { decision: d, tasks };
}

// ---------------------------------------------------------------------------
// Reviews

export function validateReviewDraft(draft, state) {
  const e = [];
  if (draft.task_id != null) {
    if (!state.tasks.some((t) => t.id === draft.task_id)) e.push(`unknown task_id ${draft.task_id}`);
  }
  if (!Array.isArray(draft.reviewers) || !draft.reviewers.length || !draft.reviewers.every((r) => OWNERS.includes(r))) {
    e.push(`"reviewers" must list roles from: ${OWNERS.join(', ')}`);
  }
  if (!str(draft.scope)) e.push('"scope" is required');
  if (!REVIEW_VERDICTS.includes(draft.verdict)) e.push(`"verdict" must be one of ${REVIEW_VERDICTS.join(', ')}`);
  const checks = draft.checks_run ?? [];
  if (!Array.isArray(checks)) e.push('"checks_run" must be a list');
  else {
    checks.forEach((c, i) => {
      if (!str(c?.command)) e.push(`checks_run[${i}].command is required (only list commands that actually ran)`);
      if (!['passed', 'failed'].includes(c?.result)) e.push(`checks_run[${i}].result must be passed or failed`);
    });
  }
  const findings = draft.findings ?? [];
  if (!Array.isArray(findings)) e.push('"findings" must be a list');
  else {
    findings.forEach((f, i) => {
      if (!str(f?.title)) e.push(`findings[${i}].title is required`);
      if (!SEVERITIES.includes(f?.severity)) e.push(`findings[${i}].severity must be one of ${SEVERITIES.join(', ')}`);
      if (!FINDING_STATUS.includes(f?.status)) e.push(`findings[${i}].status must be confirmed or suspected`);
      if (!str(f?.evidence)) e.push(`findings[${i}].evidence is required`);
    });
    const serious = findings.filter((f) => f?.status === 'confirmed' && ['critical', 'high'].includes(f?.severity));
    if (draft.verdict === 'PASS' && serious.length) e.push('verdict PASS is not allowed with confirmed critical/high findings');
  }
  if (draft.assumptions !== undefined && !strList(draft.assumptions)) e.push('"assumptions" must be a list of strings');
  return e;
}

export function recordReview(state, draft) {
  requireMutable(state);
  fail('review', validateReviewDraft(draft, state));
  const id = nextId(state, 'review');
  const review = {
    schema_version: 1,
    id,
    date: now(),
    task_id: draft.task_id ?? null,
    reviewers: draft.reviewers,
    scope: draft.scope,
    verdict: draft.verdict,
    checks_run: draft.checks_run ?? [],
    findings: draft.findings ?? [],
    assumptions: draft.assumptions ?? [],
  };
  queueRecord(state, 'reviews', review, renderReview(review));
  emit(state, {
    type: 'review.record',
    entity: id,
    summary: `${review.verdict}: ${review.scope}`,
    data: { task_id: review.task_id, findings: review.findings.length },
  });
  if (review.task_id) {
    const task = getTask(state, review.task_id);
    if (!TERMINAL.includes(task.status)) {
      for (const c of review.checks_run) {
        addEvidence(state, task.id, { type: 'check', result: c.result, command: c.command, summary: c.summary || c.command, ref: id });
      }
      const confirmed = review.findings.filter((f) => f.status === 'confirmed').length;
      addEvidence(state, task.id, {
        type: 'review',
        result: review.verdict === 'PASS' ? 'passed' : 'failed',
        summary: `${review.verdict} (${confirmed} confirmed / ${review.findings.length - confirmed} suspected findings)`,
        ref: id,
      });
    }
  }
  return review;
}

// ---------------------------------------------------------------------------
// Retrospectives

export function validateRetroDraft(draft) {
  const e = [];
  if (!str(draft.scope)) e.push('"scope" is required');
  for (const key of ['went_well', 'went_badly', 'incorrect_assumptions', 'proposed_changes']) {
    if (draft[key] !== undefined && !strList(draft[key])) e.push(`"${key}" must be a list of strings`);
  }
  const lessons = draft.lessons ?? [];
  if (!Array.isArray(lessons)) e.push('"lessons" must be a list');
  else {
    lessons.forEach((l, i) => {
      if (!str(l?.lesson)) e.push(`lessons[${i}].lesson is required`);
      if (!str(l?.evidence)) e.push(`lessons[${i}].evidence is required (lessons must be backed by evidence)`);
    });
  }
  return e;
}

export function recordRetro(state, draft) {
  fail('retrospective', validateRetroDraft(draft));
  const id = nextId(state, 'retro');
  const retro = {
    schema_version: 1,
    id,
    date: now(),
    scope: draft.scope,
    went_well: draft.went_well ?? [],
    went_badly: draft.went_badly ?? [],
    incorrect_assumptions: draft.incorrect_assumptions ?? [],
    decomposition_assessment: draft.decomposition_assessment ?? '',
    meeting_assessment: draft.meeting_assessment ?? '',
    lessons: draft.lessons ?? [],
    proposed_changes: draft.proposed_changes ?? [],
  };
  queueRecord(state, 'retros', retro, renderRetro(retro));
  if (retro.lessons.length) {
    const file = path.join(paths(state.root).memory, 'lessons-learned.md');
    const existing = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '# Lessons learned\n\nAppended by orkestr retrospectives. Each lesson links to its evidence.\n';
    const block = retro.lessons.map((l) => `- ${l.lesson} — evidence: ${l.evidence} (${id})`).join('\n');
    state.writes.push({ file, text: `${existing.trimEnd()}\n\n## ${id} — ${retro.date.slice(0, 10)}\n\n${block}\n` });
  }
  emit(state, { type: 'retro.record', entity: id, summary: retro.scope, data: { lessons: retro.lessons.length } });
  return retro;
}
