// Bounded-autonomy tests: work proceeds without founder approval for routine steps,
// failures escalate to a recovery meeting before the founder, and restricted actions stay gated.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { meetingDraft, project, twoTaskPlan } from './helpers.mjs';

const bounded = (t) => project(t, { autonomy: 'bounded' });

function failFourTimes(p, id) {
  p.ok('task', 'move', id, 'IN_PROGRESS');
  let last;
  for (let i = 0; i < 4; i++) {
    last = p.ok('evidence', 'add', id, '--type', 'test', '--result', 'failed', '--command', 'npm test', '--summary', `still failing ${i + 1}`);
  }
  return last.out;
}

function recoveryDraft(taskId, overrides = {}) {
  return meetingDraft({
    objective: `Unblock ${taskId}`,
    trigger: 'Fix-attempt limit reached',
    recovery_for: taskId,
    options: [
      { id: 'O1', name: 'Mock the clock in tests', summary: 'Tests depend on wall time' },
      { id: 'O2', name: 'Rewrite the scheduler', summary: 'Bigger change' },
    ],
    decision: { question: `How do we fix ${taskId}?`, selected_option: 'O1', rationale: 'Root cause is time-dependent tests', tradeoffs: [], risks: [] },
    action_items: [],
    ...overrides,
  });
}

test('bounded: an imported plan is authorized at once; no founder approval needed', (t) => {
  const p = bounded(t);
  const r = p.ok('plan', 'import', p.draft('plan.json', twoTaskPlan));
  assert.match(r.out, /authorized/);
  assert.equal(p.task('T-001').status, 'READY');
  assert.equal(p.task('T-002').status, 'BACKLOG', 'still waits for its dependency');
  assert.equal(p.task('T-001').authorized, true);
  assert.ok(p.history().some((e) => e.type === 'task.authorize' && e.actor === 'orchestrator'));
  assert.match(p.run('plan', 'approve', '--by', 'founder').err, /no unapproved plan tasks/);
  p.ok('validate');
});

test('bounded: restricted tasks still need a founder-granted approval', (t) => {
  const p = bounded(t);
  p.ok('plan', 'import', p.draft('plan.json', {
    tasks: [{ title: 'Deploy to production', owner: 'orchestrator', acceptance_criteria: ['live'], requires_approval: true }],
  }));
  assert.equal(p.task('T-001').status, 'READY');
  assert.match(p.run('task', 'move', 'T-001', 'IN_PROGRESS').err, /no approval request/);
  p.ok('approval', 'request', '--action', 'deploy', '--category', 'deploy', '--reason', 'release', '--task', 'T-001');
  assert.match(p.run('task', 'move', 'T-001', 'IN_PROGRESS').err, /PENDING/);
  assert.match(p.run('approval', 'grant', 'A-001').err, /founder-only/);
});

test('bounded: a decided technical meeting takes effect automatically and its tasks proceed', (t) => {
  const p = bounded(t);
  const r = p.ok('meeting', 'record', p.draft('m.json', meetingDraft()));
  assert.match(r.out, /D-001 is APPROVED/);
  const d = p.read('decisions/D-001.json');
  assert.equal(d.approval_status, 'APPROVED');
  assert.equal(d.decided_by, 'orchestrator');
  assert.equal(p.task('T-001').status, 'READY');
  assert.equal(p.task('T-002').status, 'BACKLOG');
});

test('bounded: founder-level decisions still wait for the founder', (t) => {
  const p = bounded(t);
  const draft = meetingDraft();
  draft.decision.founder_required = true;
  p.ok('meeting', 'record', p.draft('m.json', draft));
  assert.equal(p.read('decisions/D-001.json').approval_status, 'PROPOSED');
  assert.equal(p.task('T-001').status, 'BACKLOG');

  const q = bounded(t);
  q.ok('meeting', 'record', q.draft('m.json', meetingDraft({
    outcome: 'ESCALATED',
    decision: { question: 'Change the product scope?', selected_option: null, rationale: '', tradeoffs: [], risks: [] },
    action_items: [],
  })));
  assert.equal(q.read('decisions/D-001.json').approval_status, 'NEEDS_FOUNDER');
});

test('bounded: after 3 fix attempts a recovery meeting reopens the task without the founder', (t) => {
  const p = bounded(t);
  p.ok('plan', 'import', p.draft('plan.json', twoTaskPlan));
  const message = failFourTimes(p, 'T-001');
  assert.match(message, /Next: Recovery meeting required/, 'the tool points to the recovery meeting, not the founder');
  assert.doesNotMatch(message, /ask the founder/);
  let t1 = p.task('T-001');
  assert.equal(t1.status, 'FAILED');
  assert.match(t1.failure.next_action, /Recovery meeting required/);
  assert.equal(p.task('T-002').status, 'BLOCKED');

  const r = p.ok('meeting', 'record', p.draft('rec.json', recoveryDraft('T-001')));
  assert.match(r.out, /Recovery for T-001/);
  t1 = p.task('T-001');
  assert.equal(t1.status, 'READY', 'reopened automatically');
  assert.equal(t1.fix_attempts, 0);
  assert.equal(t1.recovery_meetings, 1);
  assert.match(t1.notes.at(-1).text, /Recovery decision D-001: Mock the clock in tests/);
  assert.equal(p.task('T-002').status, 'BACKLOG', 'dependent unblocked');
  assert.equal(p.read('meetings/M-001.json').recovery_for, 'T-001');
  p.ok('validate');
});

test('bounded: when the recovery budget is used up, the founder is informed', (t) => {
  const p = bounded(t);
  p.ok('plan', 'import', p.draft('plan.json', twoTaskPlan));
  failFourTimes(p, 'T-001');
  p.ok('meeting', 'record', p.draft('rec.json', recoveryDraft('T-001')));
  failFourTimes(p, 'T-001');
  const t1 = p.task('T-001');
  assert.equal(t1.status, 'FAILED');
  assert.match(t1.failure.next_action, /Founder decision required/);
  const again = p.run('meeting', 'record', p.draft('rec2.json', recoveryDraft('T-001')));
  assert.equal(again.code, 1);
  assert.match(again.err, /already used its 1 recovery meeting/);
  assert.match(p.ok('report').out, /T-001 could not be fixed within limits/);
});

test('bounded: a recovery meeting without a decision hands the task to the founder', (t) => {
  const p = bounded(t);
  p.ok('plan', 'import', p.draft('plan.json', twoTaskPlan));
  failFourTimes(p, 'T-001');
  p.ok('meeting', 'record', p.draft('rec.json', recoveryDraft('T-001', {
    outcome: 'ESCALATED',
    decision: { question: 'How do we fix T-001?', selected_option: null, rationale: '', tradeoffs: [], risks: [] },
  })));
  const t1 = p.task('T-001');
  assert.equal(t1.status, 'FAILED');
  assert.equal(t1.recovery_meetings, 1);
  assert.match(t1.failure.next_action, /Founder decision required/);
  // The founder can still choose an option; approving the decision reopens the task.
  p.ok('decision', 'approve', 'D-001', '--by', 'founder', '--option', 'O2');
  assert.equal(p.task('T-001').status, 'READY');
});

test('recovery meetings are only for FAILED tasks', (t) => {
  const p = bounded(t);
  p.ok('plan', 'import', p.draft('plan.json', twoTaskPlan));
  const r = p.run('meeting', 'record', p.draft('rec.json', recoveryDraft('T-001')));
  assert.match(r.err, /T-001 is READY, not FAILED/);
});

test('supervised: a recovery decision waits for the founder', (t) => {
  const p = project(t);
  p.ok('plan', 'import', p.draft('plan.json', twoTaskPlan));
  p.ok('plan', 'approve', '--by', 'founder');
  failFourTimes(p, 'T-001');
  assert.match(p.task('T-001').failure.next_action, /Founder decision required/);
});

test('changing autonomy is founder-only; old projects without the field count as supervised', (t) => {
  const p = bounded(t);
  assert.match(p.run('project', 'autonomy', 'supervised').err, /founder-only/);
  assert.match(p.run('project', 'autonomy', 'wild', '--by', 'founder').err, /Autonomy must be one of/);
  p.ok('project', 'autonomy', 'supervised', '--by', 'founder');
  assert.equal(p.read('project.json').autonomy, 'supervised');

  const old = bounded(t);
  const file = path.join(old.dir, '.orkestr', 'project.json');
  const proj = JSON.parse(fs.readFileSync(file, 'utf8'));
  delete proj.autonomy;
  delete proj.assumptions;
  delete proj.limits.max_recovery_meetings;
  fs.writeFileSync(file, JSON.stringify(proj));
  old.ok('validate');
  old.ok('plan', 'import', old.draft('plan.json', twoTaskPlan));
  assert.equal(old.task('T-001').status, 'BACKLOG', 'legacy project keeps founder approval');
  assert.match(old.ok('status').out, /Autonomy:\*\* supervised/);
});

test('assumptions and hand-off notes are shared records shown in status and report', (t) => {
  const p = bounded(t);
  p.ok('plan', 'import', p.draft('plan.json', twoTaskPlan));
  p.ok('assumption', 'add', '--text', 'Single-user app; no authentication in the MVP', '--impact', 'medium');
  p.ok('assumption', 'add', '--text', 'Node 20 is the runtime', '--task', 'T-001');
  assert.match(p.run('assumption', 'add', '--text', 'x', '--impact', 'huge').err, /--impact/);
  p.ok('task', 'note', 'T-001', '--author', 'qa-engineer', '--text', 'Test test_list fails: expected 3 items, got 2');
  assert.match(p.run('task', 'note', 'T-001', '--author', 'intern', '--text', 'x').err, /--author/);
  assert.equal(p.task('T-001').notes[0].author, 'qa-engineer');
  assert.match(p.ok('status').out, /Assumptions made without asking[\s\S]*\[medium\] Single-user app/);
  assert.ok(p.history().some((e) => e.type === 'task.note'));
  assert.ok(p.history().some((e) => e.type === 'assumption.record'));
});

test('completion report lists work done, checks that ran, assumptions, and open items', (t) => {
  const p = bounded(t);
  p.ok('plan', 'import', p.draft('plan.json', twoTaskPlan));
  p.ok('assumption', 'add', '--text', 'SQLite is acceptable', '--impact', 'low');
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('evidence', 'add', 'T-001', '--type', 'test', '--result', 'passed', '--command', 'npm test', '--summary', '12 passed');
  p.ok('task', 'move', 'T-001', 'REVIEW');
  p.ok('evidence', 'add', 'T-001', '--type', 'review', '--result', 'passed', '--summary', 'QA ok');
  p.ok('task', 'move', 'T-001', 'COMPLETED');
  p.ok('evidence', 'add', 'T-002', '--type', 'test', '--result', 'not_run', '--summary', 'no UI test runner yet');
  const out = p.ok('report').out;
  assert.match(out, /## What was done[\s\S]*T-001 — Build API/);
  assert.match(out, /## Checks that actually ran[\s\S]*`npm test` → passed — 12 passed/);
  assert.match(out, /### Not run[\s\S]*T-002: not run — no UI test runner yet/);
  assert.match(out, /## Assumptions[\s\S]*SQLite is acceptable/);
  assert.match(out, /## Unresolved[\s\S]*T-002 READY/);
  assert.match(out, /## Needs the founder[\s\S]*Nothing\./);
});
