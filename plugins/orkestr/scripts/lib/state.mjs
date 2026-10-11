// Project, task, and approval state: loading, saving, transitions, dependencies, and limits.
// All rules here implement MASTER_SPEC §5.2, §5.2.1, §5.3, §10, §12.1, §12.2.

import fs from 'node:fs';
import {
  AUTONOMY_MODES,
  OWNERS,
  OrkestrError,
  PREFIX,
  appendHistory,
  display,
  formatId,
  isId,
  now,
  paths,
  readJson,
  redactString,
  writeJsonAtomic,
  writeTextAtomic,
} from './core.mjs';
import { fileInScope, scopeProblems, scopesOverlap } from './scope.mjs';

export const TASK_STATES = Object.freeze([
  'BACKLOG',
  'READY',
  'IN_PROGRESS',
  'BLOCKED',
  'REVIEW',
  'FAILED',
  'COMPLETED',
  'CANCELLED',
]);
export const TERMINAL = Object.freeze(['COMPLETED', 'CANCELLED']);
export const TRANSITIONS = Object.freeze({
  BACKLOG: ['READY', 'BLOCKED', 'CANCELLED'],
  READY: ['IN_PROGRESS', 'BLOCKED', 'BACKLOG', 'CANCELLED'],
  IN_PROGRESS: ['REVIEW', 'BLOCKED', 'FAILED', 'CANCELLED'],
  REVIEW: ['COMPLETED', 'IN_PROGRESS', 'FAILED', 'CANCELLED'],
  BLOCKED: ['READY', 'BACKLOG', 'CANCELLED'],
  FAILED: ['READY', 'CANCELLED'],
  COMPLETED: [],
  CANCELLED: [],
});
export const AUTHORIZED_STATES = Object.freeze(['READY', 'IN_PROGRESS', 'REVIEW', 'FAILED', 'COMPLETED']);
export const PROJECT_STATES = Object.freeze(['ACTIVE', 'PAUSED', 'CANCELLED', 'COMPLETED']);
export const APPROVAL_STATES = Object.freeze(['PENDING', 'GRANTED', 'REJECTED', 'EXPIRED', 'CANCELLED']);
export const APPROVAL_CATEGORIES = Object.freeze([
  'destructive',
  'deploy',
  'publish',
  'git_push',
  'external_communication',
  'spending',
  'permission_change',
  'sensitive_data',
  'other',
]);
export const EVIDENCE_TYPES = Object.freeze(['test', 'check', 'review', 'manual']);
export const EVIDENCE_RESULTS = Object.freeze(['passed', 'failed', 'not_run']);
export const FOUNDER = 'founder';

// ---------------------------------------------------------------------------
// Load / save

export function loadState(root) {
  const p = paths(root);
  if (!fs.existsSync(p.project)) {
    throw new OrkestrError(
      `No orkestr project found (expected ${display(p.project, root)}). Run "init" first.`,
      'E_NO_PROJECT',
    );
  }
  const project = readJson(p.project, root);
  const tasksDoc = readJson(p.tasks, root);
  const approvalsDoc = readJson(p.approvals, root);
  if (!project || typeof project !== 'object' || !project.counters) {
    throw new OrkestrError(`Corrupted project record: ${display(p.project, root)}`, 'E_CORRUPT');
  }
  if (!Array.isArray(tasksDoc?.tasks)) {
    throw new OrkestrError(`Corrupted task list: ${display(p.tasks, root)}`, 'E_CORRUPT');
  }
  if (!Array.isArray(approvalsDoc?.approvals)) {
    throw new OrkestrError(`Corrupted approval list: ${display(p.approvals, root)}`, 'E_CORRUPT');
  }
  return {
    root,
    project,
    tasks: tasksDoc.tasks,
    approvals: approvalsDoc.approvals,
    events: [],
    writes: [],
  };
}

export function saveState(state) {
  const p = paths(state.root);
  state.project.updated_at = now();
  // Records first, then the core state, then history. A crash leaves at worst a record
  // that the validator reports as unreferenced, never a task pointing at a missing record.
  for (const w of state.writes) {
    if (w.json !== undefined) writeJsonAtomic(w.file, w.json);
    else writeTextAtomic(w.file, redactString(w.text));
  }
  writeJsonAtomic(p.tasks, { schema_version: 1, tasks: state.tasks });
  writeJsonAtomic(p.approvals, { schema_version: 1, approvals: state.approvals });
  writeJsonAtomic(p.project, state.project);
  appendHistory(state.root, state.events);
}

export function emit(state, { type, actor = 'orchestrator', entity = null, from = null, to = null, summary = '', data = null }) {
  state.events.push({ ts: now(), type, actor, entity, from, to, summary, data });
}

export function nextId(state, kind) {
  const prefix = PREFIX[kind];
  const counters = state.project.counters;
  counters[prefix] = (counters[prefix] ?? 0) + 1;
  return formatId(prefix, counters[prefix]);
}

/** Projects created before autonomy existed have no field and are treated as supervised. */
export function autonomyOf(state) {
  return state.project.autonomy ?? 'supervised';
}

export function maxRecoveryMeetings(state) {
  return state.project.limits.max_recovery_meetings ?? 1;
}

export function requireMutable(state) {
  const s = state.project.status;
  if (s === 'CANCELLED' || s === 'COMPLETED') {
    throw new OrkestrError(`Project is ${s}; its state can no longer be changed.`, 'E_PROJECT_CLOSED');
  }
}

export function requireFounder(by) {
  if (by !== FOUNDER) {
    throw new OrkestrError(
      'This is a founder-only action. Pass "--by founder" only after the founder has explicitly approved it in chat.',
      'E_FOUNDER_ONLY',
    );
  }
}

export function getTask(state, id) {
  const task = state.tasks.find((t) => t.id === id);
  if (!task) throw new OrkestrError(`Unknown task: ${id}`, 'E_NOT_FOUND');
  return task;
}

export function getApproval(state, id) {
  const a = state.approvals.find((x) => x.id === id);
  if (!a) throw new OrkestrError(`Unknown approval: ${id}`, 'E_NOT_FOUND');
  return a;
}

// ---------------------------------------------------------------------------
// Task creation

function nonEmptyString(v) {
  return typeof v === 'string' && v.trim().length > 0;
}

function stringList(v) {
  return Array.isArray(v) && v.every((x) => typeof x === 'string');
}

/**
 * Validates and creates tasks. `items` may reference each other with "#n" (1-based)
 * and existing tasks by ID. Throws with every problem listed; creates nothing on error.
 */
export function createTasks(state, items, source, { actor = 'orchestrator', decisionId = null } = {}) {
  if (!Array.isArray(items) || items.length === 0) {
    throw new OrkestrError('At least one task is required.');
  }
  const errors = [];
  const existing = new Set(state.tasks.map((t) => t.id));
  const counter = state.project.counters[PREFIX.task] ?? 0;
  const newIds = items.map((_, i) => formatId(PREFIX.task, counter + i + 1));

  const normalized = items.map((item, i) => {
    const where = `task #${i + 1}`;
    if (!item || typeof item !== 'object') {
      errors.push(`${where}: must be an object`);
      return null;
    }
    if (!nonEmptyString(item.title)) errors.push(`${where}: "title" is required`);
    if (!OWNERS.includes(item.owner)) {
      errors.push(`${where}: "owner" must be one of ${OWNERS.join(', ')} (got ${JSON.stringify(item.owner)})`);
    }
    const criteria = item.acceptance_criteria;
    if (!stringList(criteria) || criteria.filter(nonEmptyString).length === 0) {
      errors.push(`${where}: "acceptance_criteria" needs at least one non-empty string`);
    }
    const deps = item.depends_on ?? [];
    const resolved = [];
    if (!stringList(deps)) {
      errors.push(`${where}: "depends_on" must be a list of strings`);
    } else {
      for (const d of deps) {
        const local = /^#(\d+)$/.exec(d);
        if (local) {
          const n = Number(local[1]);
          if (n < 1 || n > items.length) errors.push(`${where}: "${d}" does not refer to an item in this draft`);
          else if (n === i + 1) errors.push(`${where}: a task cannot depend on itself`);
          else resolved.push(newIds[n - 1]);
        } else if (isId(PREFIX.task, d)) {
          if (!existing.has(d)) errors.push(`${where}: unknown dependency ${d}`);
          else resolved.push(d);
        } else {
          errors.push(`${where}: invalid dependency "${d}" (use a task ID such as T-001 or "#n")`);
        }
      }
    }
    if (item.requires_approval !== undefined && typeof item.requires_approval !== 'boolean') {
      errors.push(`${where}: "requires_approval" must be true or false`);
    }
    if (item.description !== undefined && typeof item.description !== 'string') {
      errors.push(`${where}: "description" must be a string`);
    }
    for (const p of scopeProblems(item.scope)) errors.push(`${where}: ${p}`);
    return {
      title: item.title?.trim?.() ?? item.title,
      description: item.description ?? '',
      owner: item.owner,
      depends_on: [...new Set(resolved)],
      acceptance_criteria: stringList(criteria) ? criteria.filter(nonEmptyString) : [],
      requires_approval: item.requires_approval === true,
      scope: stringList(item.scope) ? item.scope.map((s) => s.trim().replace(/\\/g, '/')) : [],
    };
  });

  if (errors.length) throw new OrkestrError(`Invalid tasks:\n- ${errors.join('\n- ')}`);

  const ts = now();
  const created = normalized.map((n, i) => ({
    id: newIds[i],
    title: n.title,
    description: n.description,
    owner: n.owner,
    status: 'BACKLOG',
    depends_on: n.depends_on,
    acceptance_criteria: n.acceptance_criteria,
    scope: n.scope,
    requires_approval: n.requires_approval,
    approval_id: null,
    authorized: false,
    source,
    decision_id: decisionId,
    blocked_reason: null,
    blocked_by_dependency: null,
    failure: null,
    fix_attempts: 0,
    recovery_meetings: 0,
    evidence: [],
    notes: [],
    created_at: ts,
    updated_at: ts,
  }));

  const cycle = findCycle([...state.tasks, ...created]);
  if (cycle) throw new OrkestrError(`Dependency cycle: ${cycle.join(' -> ')}`);

  state.project.counters[PREFIX.task] = counter + items.length;
  state.tasks.push(...created);
  for (const t of created) {
    emit(state, {
      type: 'task.create',
      actor,
      entity: t.id,
      to: t.status,
      summary: t.title,
      data: { owner: t.owner, depends_on: t.depends_on, source: t.source, decision_id: decisionId },
    });
  }
  return created;
}

export function findCycle(tasks) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const color = new Map();
  const stack = [];
  const visit = (id) => {
    color.set(id, 1);
    stack.push(id);
    for (const d of byId.get(id)?.depends_on ?? []) {
      if (!byId.has(d)) continue;
      if (color.get(d) === 1) return [...stack.slice(stack.indexOf(d)), d];
      if (!color.get(d)) {
        const c = visit(d);
        if (c) return c;
      }
    }
    stack.pop();
    color.set(id, 2);
    return null;
  };
  for (const t of tasks) {
    if (!color.get(t.id)) {
      const c = visit(t.id);
      if (c) return c;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Guards

function depsCompleted(state, task) {
  return task.depends_on.every((d) => state.tasks.find((t) => t.id === d)?.status === 'COMPLETED');
}

export function readinessProblems(state, task) {
  const problems = [];
  if (!task.authorized) problems.push('task is not authorized (plan or decision approval, or founder authorization, is missing)');
  if (state.project.status !== 'ACTIVE') problems.push(`project is ${state.project.status}`);
  const open = task.depends_on.filter((d) => state.tasks.find((t) => t.id === d)?.status !== 'COMPLETED');
  if (open.length) problems.push(`dependencies not completed: ${open.join(', ')}`);
  return problems;
}

/** Projects created before parallel work existed keep the old sequential behavior. */
export function maxParallel(state) {
  return state.project.limits.max_parallel_tasks ?? 1;
}

/** Problems that prevent `task` from running alongside the tasks already IN_PROGRESS (MASTER_SPEC §5.3). */
export function parallelProblems(state, task, running = state.tasks.filter((t) => t.status === 'IN_PROGRESS' && t.id !== task.id)) {
  const problems = [];
  if (running.length >= maxParallel(state)) {
    problems.push(`parallel limit reached (${running.length}/${maxParallel(state)} tasks in progress: ${running.map((t) => t.id).join(', ')})`);
  }
  for (const other of running) {
    if (scopesOverlap(task.scope ?? [], other.scope ?? [])) {
      const why = !(task.scope ?? []).length || !(other.scope ?? []).length ? 'one of them has no scope, so it runs exclusively' : 'their file scopes overlap';
      problems.push(`conflicts with ${other.id} (in progress): ${why}`);
    }
  }
  return problems;
}

/** READY tasks that can start now, together, without exceeding the limit or overlapping scopes. */
export function parallelCandidates(state) {
  const chosen = [];
  const running = state.tasks.filter((t) => t.status === 'IN_PROGRESS');
  for (const t of state.tasks) {
    if (t.status !== 'READY') continue;
    if (startProblems(state, t, [...running, ...chosen]).length) continue;
    chosen.push(t);
  }
  return chosen;
}

/**
 * Attributes changed files to in-progress tasks. With a task ID, every file must be inside that
 * task's scope; without one, every file must belong to some IN_PROGRESS or REVIEW task.
 */
export function scopeCheck(state, files, taskId = null) {
  if (!Array.isArray(files) || files.length === 0) throw new OrkestrError('Pass at least one --file (the files that actually changed).');
  const active = state.tasks.filter((t) => t.status === 'IN_PROGRESS' || t.status === 'REVIEW');
  const target = taskId ? getTask(state, taskId) : null;
  const rows = files.map((file) => {
    const owners = active.filter((t) => fileInScope(file, t.scope ?? [])).map((t) => t.id);
    let problem = null;
    if (target && !fileInScope(file, target.scope ?? [])) problem = `outside ${target.id}'s scope`;
    else if (!target && owners.length === 0) problem = 'not in the scope of any active task';
    return { file, owners, problem };
  });
  const violations = rows.filter((r) => r.problem);
  if (target) {
    addEvidence(state, target.id, {
      type: 'check',
      result: violations.length ? 'failed' : 'passed',
      command: 'orkestr scope check',
      summary: violations.length
        ? `Out-of-scope changes: ${violations.map((v) => v.file).join(', ')}`
        : `All ${rows.length} changed file(s) are inside the task scope`,
    });
  }
  return { rows, violations };
}

export function startProblems(state, task, running) {
  const problems = readinessProblems(state, task);
  problems.push(...parallelProblems(state, task, running));
  if (task.requires_approval) {
    const a = task.approval_id ? state.approvals.find((x) => x.id === task.approval_id) : null;
    if (!a) problems.push('task requires approval but has no approval request');
    else if (a.status !== 'GRANTED') problems.push(`approval ${a.id} is ${a.status}, not GRANTED`);
  }
  return problems;
}

export function completionProblems(task) {
  const problems = [];
  const ev = task.evidence;
  const verification = ev.filter((e) => ['test', 'check', 'manual'].includes(e.type));
  if (!verification.some((e) => e.result === 'passed')) {
    problems.push('no passed test, check, or manual-verification evidence');
  }
  const lastCheck = ev.filter((e) => ['test', 'check'].includes(e.type) && e.result !== 'not_run').at(-1);
  if (lastCheck?.result === 'failed') problems.push('the latest test/check failed');
  const lastReview = ev.filter((e) => e.type === 'review').at(-1);
  if (!lastReview) problems.push('no review evidence');
  else if (lastReview.result !== 'passed') problems.push('the latest review did not pass');
  return problems;
}

// ---------------------------------------------------------------------------
// Transitions

function setStatus(state, task, to, { actor, summary, data } = {}) {
  const from = task.status;
  task.status = to;
  task.updated_at = now();
  emit(state, { type: 'task.transition', actor, entity: task.id, from, to, summary: summary ?? '', data: data ?? null });
}

export function transition(state, id, to, opts = {}) {
  requireMutable(state);
  const { reason, error, nextAction, retry = false, actor = 'orchestrator' } = opts;
  const task = getTask(state, id);
  if (!TASK_STATES.includes(to)) {
    throw new OrkestrError(`Unknown state "${to}". Valid states: ${TASK_STATES.join(', ')}`);
  }
  const from = task.status;
  if (!TRANSITIONS[from].includes(to)) {
    const allowed = TRANSITIONS[from].length ? TRANSITIONS[from].join(', ') : 'none (terminal state)';
    throw new OrkestrError(`Transition ${from} -> ${to} is not allowed for ${id}. Allowed from ${from}: ${allowed}.`, 'E_TRANSITION');
  }

  if (to === 'READY') {
    if (from === 'FAILED' && !retry) {
      throw new OrkestrError(
        `${id} is FAILED. Retrying needs an explicit founder decision: use --retry (resets the fix-attempt counter).`,
        'E_RETRY_REQUIRED',
      );
    }
    const problems = readinessProblems(state, task);
    if (problems.length) throw new OrkestrError(`${id} cannot become READY: ${problems.join('; ')}.`, 'E_GUARD');
  }
  if (to === 'IN_PROGRESS') {
    const problems = startProblems(state, task);
    if (problems.length) throw new OrkestrError(`${id} cannot start: ${problems.join('; ')}.`, 'E_GUARD');
  }
  if ((to === 'BLOCKED' || to === 'CANCELLED') && !nonEmptyString(reason)) {
    throw new OrkestrError(`A reason is required to move ${id} to ${to} (use --reason).`);
  }
  if (to === 'FAILED' && (!nonEmptyString(error) || !nonEmptyString(nextAction))) {
    throw new OrkestrError(`Moving ${id} to FAILED requires --error and --next-action.`);
  }
  if (to === 'COMPLETED') {
    const problems = completionProblems(task);
    if (problems.length) throw new OrkestrError(`${id} cannot be completed: ${problems.join('; ')}.`, 'E_GUARD');
  }

  if (to === 'BLOCKED') {
    task.blocked_reason = reason.trim();
    task.blocked_by_dependency = null;
  }
  if (from === 'BLOCKED') {
    task.blocked_reason = null;
    task.blocked_by_dependency = null;
  }
  if (to === 'FAILED') task.failure = { error: error.trim(), next_action: nextAction.trim() };
  if (from === 'FAILED' && to === 'READY') {
    task.failure = null;
    task.fix_attempts = 0;
  }

  setStatus(state, task, to, {
    actor,
    summary: reason ?? error ?? (retry ? 'founder retry; fix attempts reset' : ''),
    data: to === 'FAILED' ? task.failure : retry ? { retry: true } : null,
  });
  if (to === 'FAILED' || to === 'CANCELLED') blockDependents(state, id);
  sync(state);
  return task;
}

export function blockDependents(state, rootId) {
  const root = getTask(state, rootId);
  const queue = [rootId];
  const seen = new Set();
  while (queue.length) {
    const current = queue.shift();
    for (const t of state.tasks) {
      if (!t.depends_on.includes(current) || seen.has(t.id)) continue;
      seen.add(t.id);
      queue.push(t.id);
      if (TERMINAL.includes(t.status) || t.status === 'BLOCKED') continue;
      t.blocked_reason = `Dependency ${rootId} is ${root.status}`;
      t.blocked_by_dependency = rootId;
      setStatus(state, t, 'BLOCKED', { actor: 'orkestr', summary: t.blocked_reason });
    }
  }
}

function brokenDependency(state, task) {
  for (const d of task.depends_on) {
    const dep = state.tasks.find((t) => t.id === d);
    if (!dep) return d;
    if (dep.status === 'FAILED' || dep.status === 'CANCELLED') return d;
    if (dep.status === 'BLOCKED' && dep.blocked_by_dependency) return d;
  }
  return null;
}

/** Applies automatic transitions until nothing changes (MASTER_SPEC §5.3). */
export function sync(state) {
  if (state.project.status === 'CANCELLED' || state.project.status === 'COMPLETED') return;
  let changed = true;
  while (changed) {
    changed = false;
    for (const t of state.tasks) {
      if (t.status === 'BLOCKED' && t.blocked_by_dependency && !brokenDependency(state, t)) {
        t.blocked_reason = null;
        t.blocked_by_dependency = null;
        setStatus(state, t, 'BACKLOG', { actor: 'orkestr', summary: 'dependency recovered' });
        changed = true;
      }
      if (
        t.status === 'BACKLOG' &&
        t.authorized &&
        state.project.status === 'ACTIVE' &&
        depsCompleted(state, t)
      ) {
        setStatus(state, t, 'READY', { actor: 'orkestr', summary: 'authorized and dependencies completed' });
        changed = true;
      }
    }
  }
}

export function authorize(state, tasks, { actor, via }) {
  for (const t of tasks) {
    if (t.authorized) continue;
    t.authorized = true;
    t.updated_at = now();
    emit(state, { type: 'task.authorize', actor, entity: t.id, summary: `authorized via ${via}` });
  }
  sync(state);
}

// ---------------------------------------------------------------------------
// Evidence and the fix-attempt limit (MASTER_SPEC §12.1)

export function addEvidence(state, id, { type, result, command = null, summary, ref = null, actor = 'orchestrator' }) {
  requireMutable(state);
  const task = getTask(state, id);
  if (TERMINAL.includes(task.status)) throw new OrkestrError(`${id} is ${task.status}; evidence can no longer be added.`);
  if (!EVIDENCE_TYPES.includes(type)) throw new OrkestrError(`--type must be one of ${EVIDENCE_TYPES.join(', ')}`);
  if (!EVIDENCE_RESULTS.includes(result)) throw new OrkestrError(`--result must be one of ${EVIDENCE_RESULTS.join(', ')}`);
  if (!nonEmptyString(summary)) throw new OrkestrError('--summary is required: describe what was checked and what happened.');
  if ((type === 'test' || type === 'check') && result !== 'not_run' && !nonEmptyString(command)) {
    throw new OrkestrError('--command is required for an executed test or check (record the command that actually ran).');
  }
  const entry = { type, result, command, summary: summary.trim(), ref, at: now() };
  task.evidence.push(entry);
  task.updated_at = now();
  emit(state, { type: 'task.evidence', actor, entity: id, summary: `${type} ${result}: ${entry.summary}`, data: { command, ref } });

  const outcome = { task, failedTask: false, fixAttempts: task.fix_attempts, max: state.project.limits.max_fix_attempts };
  if ((type === 'test' || type === 'check') && result === 'failed' && task.status === 'IN_PROGRESS') {
    const max = state.project.limits.max_fix_attempts;
    if (task.fix_attempts >= max) {
      const canRecover = autonomyOf(state) === 'bounded' && (task.recovery_meetings ?? 0) < maxRecoveryMeetings(state);
      transition(state, id, 'FAILED', {
        actor: 'orkestr',
        error: `Checks still failing after ${max} fix attempts: ${entry.summary}`,
        nextAction: canRecover
          ? `Recovery meeting required: hold a meeting with "recovery_for": "${id}" to choose a new approach (no founder approval needed).`
          : 'Founder decision required: retry with --retry, change the approach, or cancel the task.',
      });
      outcome.failedTask = true;
    } else {
      task.fix_attempts += 1;
    }
    outcome.fixAttempts = task.fix_attempts;
  }
  return outcome;
}

// ---------------------------------------------------------------------------
// Approvals (MASTER_SPEC §10.3)

export function requestApproval(state, input) {
  requireMutable(state);
  const { action, category, reason, consequences = '', risks = '', taskId = null, requestedBy = 'orchestrator' } = input;
  if (!nonEmptyString(action)) throw new OrkestrError('--action is required.');
  if (!APPROVAL_CATEGORIES.includes(category)) {
    throw new OrkestrError(`--category must be one of ${APPROVAL_CATEGORIES.join(', ')}`);
  }
  if (!nonEmptyString(reason)) throw new OrkestrError('--reason is required: why is this action needed?');
  let task = null;
  if (taskId) {
    task = getTask(state, taskId);
    if (TERMINAL.includes(task.status)) throw new OrkestrError(`${taskId} is ${task.status}.`);
    const current = task.approval_id ? state.approvals.find((a) => a.id === task.approval_id) : null;
    if (current && (current.status === 'PENDING' || current.status === 'GRANTED')) {
      throw new OrkestrError(`${taskId} already has approval ${current.id} (${current.status}).`);
    }
  }
  const approval = {
    id: nextId(state, 'approval'),
    action: action.trim(),
    category,
    reason: reason.trim(),
    consequences,
    risks,
    requested_by: requestedBy,
    task_id: taskId,
    status: 'PENDING',
    decided_by: null,
    decided_at: null,
    decision_note: null,
    execution_result: null,
    created_at: now(),
  };
  state.approvals.push(approval);
  if (task) {
    task.requires_approval = true;
    task.approval_id = approval.id;
    task.updated_at = now();
  }
  emit(state, {
    type: 'approval.request',
    actor: requestedBy,
    entity: approval.id,
    to: 'PENDING',
    summary: approval.action,
    data: { category, task_id: taskId },
  });
  return approval;
}

export function decideApproval(state, id, decision, { by, note = null }) {
  requireMutable(state);
  requireFounder(by);
  const a = getApproval(state, id);
  const target = { grant: 'GRANTED', reject: 'REJECTED', expire: 'EXPIRED', cancel: 'CANCELLED' }[decision];
  if (!target) throw new OrkestrError(`Unknown approval decision: ${decision}`);
  if (a.status !== 'PENDING') {
    throw new OrkestrError(`${id} is ${a.status}; only PENDING approvals can change, and only once.`, 'E_APPROVAL_CLOSED');
  }
  a.status = target;
  a.decided_by = by;
  a.decided_at = now();
  a.decision_note = note;
  emit(state, { type: `approval.${decision}`, actor: by, entity: id, from: 'PENDING', to: target, summary: note ?? a.action });
  if (target === 'REJECTED' && a.task_id) {
    const task = getTask(state, a.task_id);
    if (TRANSITIONS[task.status].includes('BLOCKED')) {
      transition(state, task.id, 'BLOCKED', { actor: 'orkestr', reason: `Approval ${id} was rejected` });
    }
  }
  return a;
}

export function recordApprovalResult(state, id, result, actor = 'orchestrator') {
  const a = getApproval(state, id);
  if (a.status !== 'GRANTED') throw new OrkestrError(`${id} is ${a.status}; only GRANTED approvals have an execution result.`);
  if (a.execution_result) throw new OrkestrError(`${id} already has an execution result.`);
  if (!nonEmptyString(result)) throw new OrkestrError('--result is required.');
  a.execution_result = result.trim();
  emit(state, { type: 'approval.result', actor, entity: id, summary: a.execution_result });
  return a;
}

// ---------------------------------------------------------------------------
// Project lifecycle (MASTER_SPEC §12.2)

export function setProjectStatus(state, action, { reason = null, by = null } = {}) {
  const p = state.project;
  const from = p.status;
  if (action === 'pause') {
    if (from !== 'ACTIVE') throw new OrkestrError(`Only an ACTIVE project can be paused (project is ${from}).`);
    if (!nonEmptyString(reason)) throw new OrkestrError('--reason is required to pause.');
    p.status = 'PAUSED';
    p.status_reason = reason.trim();
  } else if (action === 'resume') {
    if (from !== 'PAUSED') throw new OrkestrError(`Only a PAUSED project can be resumed (project is ${from}).`);
    p.status = 'ACTIVE';
    p.status_reason = null;
  } else if (action === 'cancel') {
    requireFounder(by);
    if (from !== 'ACTIVE' && from !== 'PAUSED') throw new OrkestrError(`Project is ${from}; it cannot be cancelled.`);
    if (!nonEmptyString(reason)) throw new OrkestrError('--reason is required to cancel.');
    for (const t of state.tasks) {
      if (!TERMINAL.includes(t.status)) setStatus(state, t, 'CANCELLED', { actor: 'orkestr', summary: 'project cancelled' });
    }
    for (const a of state.approvals) {
      if (a.status === 'PENDING') {
        a.status = 'CANCELLED';
        a.decided_by = by;
        a.decided_at = now();
        a.decision_note = 'project cancelled';
        emit(state, { type: 'approval.cancel', actor: 'orkestr', entity: a.id, from: 'PENDING', to: 'CANCELLED', summary: 'project cancelled' });
      }
    }
    p.status = 'CANCELLED';
    p.status_reason = reason.trim();
  } else if (action === 'complete') {
    if (from !== 'ACTIVE') throw new OrkestrError(`Only an ACTIVE project can be completed (project is ${from}).`);
    const open = state.tasks.filter((t) => !TERMINAL.includes(t.status)).map((t) => t.id);
    if (open.length) throw new OrkestrError(`Open tasks remain: ${open.join(', ')}.`);
    if (!state.tasks.some((t) => t.status === 'COMPLETED')) throw new OrkestrError('No task has been completed.');
    p.status = 'COMPLETED';
    p.status_reason = reason;
  } else {
    throw new OrkestrError(`Unknown project action: ${action}`);
  }
  emit(state, { type: 'project.status', actor: by ?? 'orchestrator', entity: 'project', from, to: p.status, summary: reason ?? '' });
  sync(state);
}

/**
 * Reopens a FAILED task after an approved recovery decision (MASTER_SPEC §12.1).
 * The recovery-meeting budget is consumed when the meeting is recorded, so this
 * cannot be repeated without a new meeting, and meetings are capped per task.
 */
export function recoverTask(state, id, { decisionId, approach, actor }) {
  const task = getTask(state, id);
  if (task.status !== 'FAILED') return null;
  transition(state, id, 'READY', { retry: true, actor, reason: `recovery decision ${decisionId}` });
  addNote(state, id, { author: actor, text: `Recovery decision ${decisionId}: ${approach}` });
  return task;
}

export function addNote(state, id, { author, text }) {
  requireMutable(state);
  const task = getTask(state, id);
  if (!OWNERS.includes(author)) throw new OrkestrError(`--author must be one of ${OWNERS.join(', ')}`);
  if (!nonEmptyString(text)) throw new OrkestrError('--text is required.');
  task.notes = task.notes ?? [];
  task.notes.push({ author, text: text.trim(), at: now() });
  task.updated_at = now();
  emit(state, { type: 'task.note', actor: author, entity: id, summary: text.trim() });
  return task;
}

export const IMPACTS = Object.freeze(['low', 'medium', 'high']);

/** Records an assumption made instead of asking the founder (autonomous mode). */
export function addAssumption(state, { text, taskId = null, impact = 'low', actor = 'orchestrator' }) {
  requireMutable(state);
  if (!nonEmptyString(text)) throw new OrkestrError('--text is required.');
  if (!IMPACTS.includes(impact)) throw new OrkestrError(`--impact must be one of ${IMPACTS.join(', ')}`);
  if (taskId) getTask(state, taskId);
  state.project.assumptions = state.project.assumptions ?? [];
  const entry = { text: text.trim(), task: taskId, impact, at: now() };
  state.project.assumptions.push(entry);
  emit(state, { type: 'assumption.record', actor, entity: taskId ?? 'project', summary: entry.text, data: { impact } });
  return entry;
}

export function setAutonomy(state, mode, { by }) {
  requireMutable(state);
  requireFounder(by);
  if (!AUTONOMY_MODES.includes(mode)) throw new OrkestrError(`Autonomy must be one of ${AUTONOMY_MODES.join(', ')}`);
  const from = autonomyOf(state);
  state.project.autonomy = mode;
  emit(state, { type: 'project.autonomy', actor: by, entity: 'project', from, to: mode });
}

/** The task to work on next: an interrupted IN_PROGRESS task first, then the lowest READY task. */
export function nextTask(state) {
  return (
    state.tasks.find((t) => t.status === 'IN_PROGRESS') ??
    state.tasks.find((t) => t.status === 'READY') ??
    null
  );
}
