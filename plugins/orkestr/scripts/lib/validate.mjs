// Read-only validation of everything under .orkestr/ (MASTER_SPEC §19, PHASE1 §5).
// Never throws for bad data: every problem becomes an error or warning message.

import fs from 'node:fs';
import path from 'node:path';
import { AUTONOMY_MODES, LOCK_STALE_MS, OWNERS, PREFIX, findSecrets, isId, paths } from './core.mjs';
import {
  APPROVAL_CATEGORIES,
  APPROVAL_STATES,
  AUTHORIZED_STATES,
  EVIDENCE_RESULTS,
  EVIDENCE_TYPES,
  PROJECT_STATES,
  TASK_STATES,
  completionProblems,
  findCycle,
} from './state.mjs';
import { DECISION_STATES, MEETING_OUTCOMES, REVIEW_VERDICTS } from './records.mjs';
import { scopeProblems, scopesOverlap } from './scope.mjs';

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;
const isIso = (v) => typeof v === 'string' && ISO.test(v);
const str = (v) => typeof v === 'string' && v.trim().length > 0;

function tryRead(file, rel, errors) {
  if (!fs.existsSync(file)) {
    errors.push(`${rel}: missing`);
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^﻿/, ''));
  } catch (err) {
    errors.push(`${rel}: corrupted JSON (${err.message})`);
    return null;
  }
}

function walk(dir) {
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

function loadRecords(dir, prefix, folder, errors) {
  const records = [];
  if (!fs.existsSync(dir)) return records;
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith('.json')) continue;
    const rel = `${folder}/${name}`;
    const rec = tryRead(path.join(dir, name), rel, errors);
    if (!rec) continue;
    const expected = name.slice(0, -'.json'.length);
    if (rec.id !== expected) errors.push(`${rel}: id "${rec.id}" does not match the file name`);
    if (!isId(prefix, rec.id)) errors.push(`${rel}: invalid id format`);
    if (rec.schema_version !== 1) errors.push(`${rel}: schema_version must be 1`);
    if (!fs.existsSync(path.join(dir, `${expected}.md`))) errors.push(`${folder}/${expected}.md: generated view is missing`);
    records.push(rec);
  }
  return records;
}

export function validateStore(root) {
  const errors = [];
  const warnings = [];
  const p = paths(root);
  if (!fs.existsSync(p.dir)) {
    return { errors: ['.orkestr/ does not exist; run init first'], warnings, decisions: [] };
  }

  // project.json
  const project = tryRead(p.project, 'project.json', errors);
  const counters = project?.counters ?? {};
  if (project) {
    if (project.schema_version !== 1) errors.push('project.json: schema_version must be 1');
    if (!str(project.name)) errors.push('project.json: name is required');
    if (!str(project.goal)) errors.push('project.json: goal is required');
    if (!PROJECT_STATES.includes(project.status)) errors.push(`project.json: invalid status ${project.status}`);
    if (['PAUSED', 'CANCELLED'].includes(project.status) && !str(project.status_reason)) {
      errors.push(`project.json: status ${project.status} requires status_reason`);
    }
    const l = project.limits ?? {};
    for (const k of ['max_meeting_participants', 'max_discussion_rounds', 'max_fix_attempts']) {
      if (!Number.isInteger(l[k]) || l[k] < 1) errors.push(`project.json: limits.${k} must be a positive integer`);
    }
    for (const k of Object.values(PREFIX)) {
      if (!Number.isInteger(counters[k]) || counters[k] < 0) errors.push(`project.json: counters.${k} must be a non-negative integer`);
    }
    if (!isIso(project.created_at) || !isIso(project.updated_at)) errors.push('project.json: timestamps must be ISO 8601 UTC');
    if (project.autonomy !== undefined && !AUTONOMY_MODES.includes(project.autonomy)) {
      errors.push(`project.json: autonomy must be one of ${AUTONOMY_MODES.join(', ')}`);
    }
    if (project.managed_by !== undefined && !['plugin', 'core'].includes(project.managed_by)) {
      errors.push('project.json: managed_by must be plugin or core');
    }
    if (l.max_parallel_tasks !== undefined && (!Number.isInteger(l.max_parallel_tasks) || l.max_parallel_tasks < 1)) {
      errors.push('project.json: limits.max_parallel_tasks must be a positive integer');
    }
    if (l.max_recovery_meetings !== undefined && (!Number.isInteger(l.max_recovery_meetings) || l.max_recovery_meetings < 0)) {
      errors.push('project.json: limits.max_recovery_meetings must be a non-negative integer');
    }
    if (project.assumptions !== undefined) {
      if (!Array.isArray(project.assumptions) || !project.assumptions.every((a) => str(a?.text) && isIso(a?.at))) {
        errors.push('project.json: assumptions must be a list of {text, impact, task, at}');
      }
    }
  }
  const limits = project?.limits ?? {};

  // approvals.json
  const approvalsDoc = tryRead(p.approvals, 'approvals.json', errors);
  const approvals = Array.isArray(approvalsDoc?.approvals) ? approvalsDoc.approvals : [];
  if (approvalsDoc && !Array.isArray(approvalsDoc.approvals)) errors.push('approvals.json: "approvals" must be a list');
  const approvalIds = new Set();
  for (const a of approvals) {
    const where = `approvals.json ${a?.id ?? '(no id)'}`;
    if (!isId(PREFIX.approval, a?.id)) errors.push(`${where}: invalid id`);
    if (approvalIds.has(a?.id)) errors.push(`${where}: duplicate id`);
    approvalIds.add(a?.id);
    if (!APPROVAL_STATES.includes(a?.status)) errors.push(`${where}: invalid status ${a?.status}`);
    if (!APPROVAL_CATEGORIES.includes(a?.category)) errors.push(`${where}: invalid category ${a?.category}`);
    if (!str(a?.action) || !str(a?.reason)) errors.push(`${where}: action and reason are required`);
    if (a?.status !== 'PENDING' && (!str(a?.decided_by) || !isIso(a?.decided_at))) {
      errors.push(`${where}: decided approvals need decided_by and decided_at`);
    }
    if (a?.execution_result && a?.status !== 'GRANTED') errors.push(`${where}: only GRANTED approvals can have an execution result`);
    const n = Number(a?.id?.split('-')[1]);
    if (n > (counters.A ?? 0)) errors.push(`${where}: id is above counters.A`);
  }

  // tasks.json
  const tasksDoc = tryRead(p.tasks, 'tasks.json', errors);
  const tasks = Array.isArray(tasksDoc?.tasks) ? tasksDoc.tasks : [];
  if (tasksDoc && !Array.isArray(tasksDoc.tasks)) errors.push('tasks.json: "tasks" must be a list');
  const taskIds = new Set(tasks.map((t) => t?.id));
  const seen = new Set();
  for (const t of tasks) {
    const where = `tasks.json ${t?.id ?? '(no id)'}`;
    if (!isId(PREFIX.task, t?.id)) errors.push(`${where}: invalid id`);
    if (seen.has(t?.id)) errors.push(`${where}: duplicate id`);
    seen.add(t?.id);
    if (Number(t?.id?.split('-')[1]) > (counters.T ?? 0)) errors.push(`${where}: id is above counters.T`);
    if (!str(t?.title)) errors.push(`${where}: title is required`);
    if (!OWNERS.includes(t?.owner)) errors.push(`${where}: invalid owner ${t?.owner}`);
    if (!TASK_STATES.includes(t?.status)) errors.push(`${where}: invalid status ${t?.status}`);
    if (!Array.isArray(t?.acceptance_criteria) || !t.acceptance_criteria.some(str)) {
      errors.push(`${where}: at least one acceptance criterion is required`);
    }
    if (!Array.isArray(t?.depends_on)) errors.push(`${where}: depends_on must be a list`);
    for (const d of t?.depends_on ?? []) {
      if (d === t.id) errors.push(`${where}: depends on itself`);
      else if (!taskIds.has(d)) errors.push(`${where}: unknown dependency ${d}`);
    }
    if (typeof t?.authorized !== 'boolean') errors.push(`${where}: authorized must be true or false`);
    if (AUTHORIZED_STATES.includes(t?.status) && !t.authorized) errors.push(`${where}: ${t.status} but not authorized`);
    if (t?.status === 'BLOCKED' && !str(t.blocked_reason)) errors.push(`${where}: BLOCKED without blocked_reason`);
    if (t?.status === 'FAILED' && (!str(t.failure?.error) || !str(t.failure?.next_action))) {
      errors.push(`${where}: FAILED without failure.error and failure.next_action`);
    }
    if (!Number.isInteger(t?.fix_attempts) || t.fix_attempts < 0 || t.fix_attempts > (limits.max_fix_attempts ?? 3)) {
      errors.push(`${where}: fix_attempts must be between 0 and ${limits.max_fix_attempts ?? 3}`);
    }
    if (t?.requires_approval && ['IN_PROGRESS', 'REVIEW', 'COMPLETED'].includes(t.status)) {
      const a = approvals.find((x) => x.id === t.approval_id);
      if (!a || a.status !== 'GRANTED') errors.push(`${where}: ${t.status} without a GRANTED approval`);
    }
    if (t?.approval_id && !approvalIds.has(t.approval_id)) errors.push(`${where}: unknown approval ${t.approval_id}`);
    if (!Array.isArray(t?.evidence)) errors.push(`${where}: evidence must be a list`);
    for (const e of t?.evidence ?? []) {
      if (!EVIDENCE_TYPES.includes(e?.type) || !EVIDENCE_RESULTS.includes(e?.result) || !str(e?.summary) || !isIso(e?.at)) {
        errors.push(`${where}: invalid evidence entry`);
        break;
      }
    }
    if (t?.status === 'COMPLETED' && Array.isArray(t.evidence)) {
      const problems = completionProblems(t);
      if (problems.length) errors.push(`${where}: COMPLETED but ${problems.join('; ')}`);
    }
    if (!['plan', 'meeting', 'manual'].includes(t?.source?.type)) errors.push(`${where}: invalid source.type`);
    if (t?.recovery_meetings !== undefined && (!Number.isInteger(t.recovery_meetings) || t.recovery_meetings > (limits.max_recovery_meetings ?? 1))) {
      errors.push(`${where}: recovery_meetings exceeds the limit`);
    }
    if (t?.notes !== undefined && (!Array.isArray(t.notes) || !t.notes.every((n) => OWNERS.includes(n?.author) && str(n?.text)))) {
      errors.push(`${where}: notes must be a list of {author, text, at}`);
    }
    for (const p of scopeProblems(t?.scope)) errors.push(`${where}: ${p}`);
    if (t?.verify !== undefined && (!Array.isArray(t.verify) || !t.verify.every(str))) {
      errors.push(`${where}: verify must be a list of commands`);
    }
  }
  // Parallel-work invariants (MASTER_SPEC §5.3)
  const running = tasks.filter((t) => t?.status === 'IN_PROGRESS');
  const maxParallel = limits.max_parallel_tasks ?? 1;
  if (running.length > maxParallel) {
    errors.push(`tasks.json: ${running.length} tasks IN_PROGRESS but limits.max_parallel_tasks is ${maxParallel}`);
  }
  for (let i = 0; i < running.length; i++) {
    for (let j = i + 1; j < running.length; j++) {
      if (scopesOverlap(running[i].scope ?? [], running[j].scope ?? [])) {
        errors.push(`tasks.json: ${running[i].id} and ${running[j].id} are both IN_PROGRESS with overlapping scopes`);
      }
    }
  }
  const cycle = findCycle(tasks.filter((t) => Array.isArray(t?.depends_on)));
  if (cycle) errors.push(`tasks.json: dependency cycle ${cycle.join(' -> ')}`);

  // Records
  const meetings = loadRecords(p.meetings, PREFIX.meeting, 'meetings', errors);
  for (const m of meetings) {
    if (!MEETING_OUTCOMES.includes(m.outcome)) errors.push(`meetings/${m.id}.json: invalid outcome`);
    if ((m.participants?.length ?? 0) > (limits.max_meeting_participants ?? 4)) errors.push(`meetings/${m.id}.json: too many participants`);
    if ((m.rounds?.length ?? 0) > (limits.max_discussion_rounds ?? 2)) errors.push(`meetings/${m.id}.json: too many rounds`);
    for (const id of m.task_ids ?? []) if (!taskIds.has(id)) errors.push(`meetings/${m.id}.json: unknown task ${id}`);
  }
  const decisions = loadRecords(p.decisions, PREFIX.decision, 'decisions', errors);
  for (const d of decisions) {
    if (!DECISION_STATES.includes(d.approval_status)) errors.push(`decisions/${d.id}.json: invalid approval_status`);
    if (d.approval_status === 'APPROVED' && !d.selected_option) errors.push(`decisions/${d.id}.json: APPROVED without selected_option`);
    for (const id of d.task_ids ?? []) if (!taskIds.has(id)) errors.push(`decisions/${d.id}.json: unknown task ${id}`);
  }
  const reviews = loadRecords(p.reviews, PREFIX.review, 'reviews', errors);
  for (const r of reviews) {
    if (!REVIEW_VERDICTS.includes(r.verdict)) errors.push(`reviews/${r.id}.json: invalid verdict`);
    if (r.task_id && !taskIds.has(r.task_id)) errors.push(`reviews/${r.id}.json: unknown task ${r.task_id}`);
  }
  loadRecords(p.retros, PREFIX.retro, 'retros', errors);

  // history.jsonl
  if (!fs.existsSync(p.history)) errors.push('history.jsonl: missing');
  else {
    const lines = fs.readFileSync(p.history, 'utf8').split('\n').filter((l) => l.trim());
    lines.forEach((line, i) => {
      try {
        const ev = JSON.parse(line);
        if (!isIso(ev.ts) || !str(ev.type)) errors.push(`history.jsonl line ${i + 1}: needs ts and type`);
      } catch {
        errors.push(`history.jsonl line ${i + 1}: not valid JSON`);
      }
    });
  }

  // Leftovers from interrupted writes, stale locks, and secrets
  for (const file of walk(p.dir)) {
    const rel = path.relative(p.dir, file).split(path.sep).join('/');
    if (/\.tmp-\d+$/.test(rel)) warnings.push(`${rel}: leftover temporary file from an interrupted write`);
    if (rel === '.lock') {
      const age = Date.now() - fs.statSync(file).mtimeMs;
      if (age > LOCK_STALE_MS) warnings.push('.lock: stale lock file (older than 10 minutes)');
    }
    if (!/\.(json|jsonl|md|txt)$/.test(rel)) continue;
    const found = findSecrets(fs.readFileSync(file, 'utf8'));
    if (!found.length) continue;
    const message = `${rel}: possible secret (${found.join(', ')}) — remove it`;
    // Structured state is written by the tool and redacted, so a hit there is an error.
    // Prose and drafts are written by hand; flag them without blocking.
    if (/\.(json|jsonl)$/.test(rel) && !rel.startsWith('drafts/')) errors.push(message);
    else warnings.push(message);
  }

  return { errors, warnings, decisions };
}
