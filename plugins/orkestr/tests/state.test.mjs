// State tool tests: PHASE1_IMPLEMENTATION.md §9 and acceptance criteria 1–13.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { approvedPlan, completeT1, meetingDraft, project, twoTaskPlan } from './helpers.mjs';
import { findSecrets, redactString } from '../scripts/lib/core.mjs';

// --- Initialization (criterion 1) --------------------------------------------------------------

test('init creates the data files and leaves unrelated files untouched', (t) => {
  const p = project(t, { init: false });
  fs.writeFileSync(path.join(p.dir, 'README.md'), 'user file');
  p.ok('init', '--name', 'Demo', '--goal', 'Goal', '--constraint', 'no paid services', '--success', 'tests pass');
  for (const f of ['project.json', 'tasks.json', 'approvals.json', 'history.jsonl']) {
    assert.ok(fs.existsSync(path.join(p.dir, '.orkestr', f)), f);
  }
  assert.equal(fs.readFileSync(path.join(p.dir, 'README.md'), 'utf8'), 'user file');
  const proj = p.read('project.json');
  assert.equal(proj.status, 'ACTIVE');
  assert.deepEqual(proj.limits, { max_meeting_participants: 4, max_discussion_rounds: 2, max_fix_attempts: 3, max_recovery_meetings: 1, max_parallel_tasks: 2 });
  assert.equal(proj.autonomy, 'bounded', 'bounded autonomy is the default for new projects');
  assert.deepEqual(proj.assumptions, []);
  assert.deepEqual(proj.constraints, ['no paid services']);
  assert.equal(p.history()[0].type, 'project.init');
  p.ok('validate');
});

test('init refuses to overwrite an existing project or an unknown .orkestr folder', (t) => {
  const p = project(t);
  const before = fs.readFileSync(path.join(p.dir, '.orkestr', 'project.json'), 'utf8');
  const again = p.run('init', '--name', 'Other', '--goal', 'Other');
  assert.equal(again.code, 1);
  assert.match(again.err, /refusing to overwrite/);
  assert.equal(fs.readFileSync(path.join(p.dir, '.orkestr', 'project.json'), 'utf8'), before);

  const q = project(t, { init: false });
  fs.mkdirSync(path.join(q.dir, '.orkestr'));
  fs.writeFileSync(path.join(q.dir, '.orkestr', 'notes.txt'), 'keep me');
  const r = q.run('init', '--name', 'X', '--goal', 'Y');
  assert.equal(r.code, 1);
  assert.equal(fs.readFileSync(path.join(q.dir, '.orkestr', 'notes.txt'), 'utf8'), 'keep me');
});

test('commands fail clearly without a project; status explains how to start', (t) => {
  const p = project(t, { init: false });
  assert.match(p.ok('status').out, /No orkestr project/);
  const r = p.run('task', 'list');
  assert.equal(r.code, 1);
  assert.match(r.err, /Run "init" first/);
});

// --- Planning, dependencies, authorization (criteria 2, 3) -------------------------------------

test('plan import validates owners, criteria, dependencies, and cycles atomically', (t) => {
  const p = project(t);
  const bad = p.run('plan', 'import', p.draft('bad.json', {
    tasks: [
      { title: 'A', owner: 'wizard', acceptance_criteria: [] },
      { title: 'B', owner: 'qa-engineer', acceptance_criteria: ['x'], depends_on: ['T-999'] },
      { title: 'C', owner: 'qa-engineer', acceptance_criteria: ['x'], depends_on: ['#3'] },
    ],
  }));
  assert.equal(bad.code, 1);
  assert.match(bad.err, /owner/);
  assert.match(bad.err, /acceptance_criteria/);
  assert.match(bad.err, /unknown dependency T-999/);
  assert.match(bad.err, /cannot depend on itself/);
  assert.equal(p.read('tasks.json').tasks.length, 0);
  assert.equal(p.read('project.json').counters.T, 0);

  const cyc = p.run('plan', 'import', p.draft('cycle.json', {
    tasks: [
      { title: 'A', owner: 'qa-engineer', acceptance_criteria: ['x'], depends_on: ['#2'] },
      { title: 'B', owner: 'qa-engineer', acceptance_criteria: ['x'], depends_on: ['#1'] },
    ],
  }));
  assert.equal(cyc.code, 1);
  assert.match(cyc.err, /Dependency cycle/);
  assert.equal(p.read('tasks.json').tasks.length, 0);
});

test('imported plan tasks stay in BACKLOG until the founder approves the plan', (t) => {
  const p = project(t);
  p.ok('plan', 'import', p.draft('plan.json', twoTaskPlan));
  assert.equal(p.task('T-001').status, 'BACKLOG');
  assert.equal(p.task('T-001').authorized, false);
  assert.deepEqual(p.task('T-002').depends_on, ['T-001']);
  assert.match(p.run('task', 'move', 'T-001', 'READY').err, /not authorized/);
  assert.match(p.run('plan', 'approve').err, /founder-only/);
  assert.match(p.run('plan', 'approve', '--by', 'agent').err, /founder-only/);
  p.ok('plan', 'approve', '--by', 'founder');
  assert.equal(p.task('T-001').status, 'READY');
  assert.equal(p.task('T-002').status, 'BACKLOG', 'dependency not completed yet');
  assert.ok(p.history().some((e) => e.type === 'plan.approve' && e.actor === 'founder'));
});

test('manual tasks need individual founder authorization', (t) => {
  const p = project(t);
  p.ok('task', 'add', '--title', 'Write docs', '--owner', 'orchestrator', '--criterion', 'README exists');
  assert.equal(p.task('T-001').source.type, 'manual');
  assert.equal(p.task('T-001').status, 'BACKLOG');
  assert.equal(p.run('task', 'authorize', 'T-001').code, 1);
  p.ok('task', 'authorize', 'T-001', '--by', 'founder');
  assert.equal(p.task('T-001').status, 'READY');
});

// --- State machine and completion (criteria 6, 7) ----------------------------------------------

test('illegal transitions and missing reasons are rejected', (t) => {
  const p = approvedPlan(t);
  assert.match(p.run('task', 'move', 'T-001', 'COMPLETED').err, /not allowed/);
  assert.match(p.run('task', 'move', 'T-001', 'DONE').err, /Unknown state/);
  assert.match(p.run('task', 'move', 'T-001', 'BLOCKED').err, /reason is required/);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  assert.match(p.run('task', 'move', 'T-001', 'FAILED', '--error', 'x').err, /--next-action/);
  assert.match(p.run('task', 'move', 'T-002', 'IN_PROGRESS').err, /not allowed|cannot start/);
});

test('completion requires passing verification and a passing review', (t) => {
  const p = approvedPlan(t);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('task', 'move', 'T-001', 'REVIEW');
  let r = p.run('task', 'move', 'T-001', 'COMPLETED');
  assert.match(r.err, /no passed test/);
  assert.match(r.err, /no review evidence/);
  assert.match(p.run('evidence', 'add', 'T-001', '--type', 'test', '--result', 'passed', '--summary', 'ok').err, /--command is required/);
  p.ok('evidence', 'add', 'T-001', '--type', 'test', '--result', 'passed', '--command', 'npm test', '--summary', 'ok');
  r = p.run('task', 'move', 'T-001', 'COMPLETED');
  assert.match(r.err, /no review evidence/);
  p.ok('evidence', 'add', 'T-001', '--type', 'review', '--result', 'passed', '--summary', 'looks good');
  p.ok('task', 'move', 'T-001', 'COMPLETED');
  assert.equal(p.task('T-002').status, 'READY', 'dependent promoted automatically');
  assert.match(p.run('task', 'move', 'T-001', 'REVIEW').err, /terminal state/);
  assert.match(p.run('evidence', 'add', 'T-001', '--type', 'manual', '--result', 'passed', '--summary', 'x').err, /COMPLETED/);
});

test('a later failing check prevents completion', (t) => {
  const p = approvedPlan(t);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('evidence', 'add', 'T-001', '--type', 'test', '--result', 'passed', '--command', 'npm test', '--summary', 'ok');
  p.ok('task', 'move', 'T-001', 'REVIEW');
  p.ok('evidence', 'add', 'T-001', '--type', 'check', '--result', 'failed', '--command', 'npm run lint', '--summary', 'lint errors');
  p.ok('evidence', 'add', 'T-001', '--type', 'review', '--result', 'passed', '--summary', 'ok');
  assert.match(p.run('task', 'move', 'T-001', 'COMPLETED').err, /latest test\/check failed/);
});

test('fix attempts are limited to 3; the next failure fails the task and blocks dependents', (t) => {
  const p = approvedPlan(t);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  const fail = () => p.run('evidence', 'add', 'T-001', '--type', 'test', '--result', 'failed', '--command', 'npm test', '--summary', 'still failing');
  for (let i = 1; i <= 3; i++) {
    const r = fail();
    assert.equal(r.code, 0);
    assert.equal(p.task('T-001').fix_attempts, i);
    assert.equal(p.task('T-001').status, 'IN_PROGRESS');
  }
  const last = fail();
  assert.match(last.out, /limit \(3\) reached/);
  const t1 = p.task('T-001');
  assert.equal(t1.status, 'FAILED');
  assert.match(t1.failure.next_action, /Founder decision required/);
  const t2 = p.task('T-002');
  assert.equal(t2.status, 'BLOCKED');
  assert.equal(t2.blocked_by_dependency, 'T-001');

  // No automatic retry: --retry is required, and it is a founder action.
  assert.match(p.run('task', 'move', 'T-001', 'READY').err, /--retry/);
  assert.match(p.run('task', 'move', 'T-001', 'READY', '--retry').err, /founder-only/);
  p.ok('task', 'move', 'T-001', 'READY', '--retry', '--by', 'founder');
  assert.equal(p.task('T-001').fix_attempts, 0);
  assert.equal(p.task('T-001').failure, null);
  assert.equal(p.task('T-002').status, 'BACKLOG', 'dependent unblocked when the dependency recovered');
  p.ok('validate');
});

test('review rework keeps the fix-attempt counter', (t) => {
  const p = approvedPlan(t);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('evidence', 'add', 'T-001', '--type', 'test', '--result', 'failed', '--command', 'npm test', '--summary', 'x');
  p.ok('task', 'move', 'T-001', 'REVIEW');
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  assert.equal(p.task('T-001').fix_attempts, 1);
});

test('cancelling a task blocks transitive dependents with a reason', (t) => {
  const p = project(t);
  p.ok('plan', 'import', p.draft('plan.json', {
    tasks: [
      { title: 'A', owner: 'tech-lead', acceptance_criteria: ['x'] },
      { title: 'B', owner: 'backend-engineer', acceptance_criteria: ['x'], depends_on: ['#1'] },
      { title: 'C', owner: 'qa-engineer', acceptance_criteria: ['x'], depends_on: ['#2'] },
    ],
  }));
  p.ok('plan', 'approve', '--by', 'founder');
  p.ok('task', 'move', 'T-001', 'CANCELLED', '--reason', 'scope cut');
  assert.equal(p.task('T-002').status, 'BLOCKED');
  assert.equal(p.task('T-003').status, 'BLOCKED');
  assert.match(p.task('T-003').blocked_reason, /T-001 is CANCELLED/);
});

// --- Meetings and decisions (criteria 4, 5) ------------------------------------------------------

test('meeting limits: at most 4 participants and 2 rounds', (t) => {
  const p = project(t);
  const five = meetingDraft({
    participants: ['tech-lead', 'backend-engineer', 'frontend-engineer', 'qa-engineer', 'security-reviewer'].map((role) => ({ role, responsibility: 'x' })),
  });
  assert.match(p.run('meeting', 'record', p.draft('m5.json', five)).err, /at most 4 participants/);
  const base = meetingDraft();
  const three = meetingDraft({ rounds: [1, 2, 3].map((n) => ({ ...base.rounds[0], round: n })) });
  assert.match(p.run('meeting', 'record', p.draft('m3.json', three)).err, /at most 2 discussion rounds/);
  const noItems = meetingDraft({ action_items: [] });
  assert.match(p.run('meeting', 'record', p.draft('mi.json', noItems)).err, /at least one action item/);
  assert.equal(p.read('project.json').counters.M, 0, 'rejected meetings consume no IDs');
});

test('a decided meeting produces a meeting record, a decision, and linked tasks', (t) => {
  const p = project(t);
  const r = p.ok('meeting', 'record', p.draft('m.json', meetingDraft()));
  assert.match(r.out, /M-001/);
  assert.match(r.out, /D-001 is PROPOSED/);
  const m = p.read('meetings/M-001.json');
  const d = p.read('decisions/D-001.json');
  assert.deepEqual(m.task_ids, ['T-001', 'T-002']);
  assert.equal(d.selected_option, 'O1');
  assert.equal(d.approval_status, 'PROPOSED');
  assert.equal(d.objections[0].role, 'frontend-engineer', 'dissent preserved');
  assert.ok(fs.existsSync(path.join(p.dir, '.orkestr', 'meetings', 'M-001.md')));
  assert.match(fs.readFileSync(path.join(p.dir, '.orkestr', 'decisions', 'D-001.md'), 'utf8'), /Generated by orkestr/);
  assert.equal(p.task('T-001').decision_id, 'D-001');
  assert.equal(p.task('T-001').status, 'BACKLOG');
  assert.deepEqual(p.task('T-002').depends_on, ['T-001']);

  assert.match(p.run('decision', 'approve', 'D-001').err, /founder-only/);
  p.ok('decision', 'approve', 'D-001', '--by', 'founder', '--note', 'go');
  assert.equal(p.read('decisions/D-001.json').approval_status, 'APPROVED');
  assert.equal(p.task('T-001').status, 'READY');
  assert.match(p.run('decision', 'approve', 'D-001', '--by', 'founder').err, /APPROVED/);
  p.ok('validate');
});

test('rejecting a decision cancels its unstarted tasks', (t) => {
  const p = project(t);
  p.ok('meeting', 'record', p.draft('m.json', meetingDraft()));
  assert.match(p.run('decision', 'reject', 'D-001', '--by', 'founder').err, /--note is required/);
  p.ok('decision', 'reject', 'D-001', '--by', 'founder', '--note', 'not now');
  assert.equal(p.task('T-001').status, 'CANCELLED');
  assert.equal(p.task('T-002').status, 'CANCELLED');
});

test('an escalated meeting waits for the founder to pick an option', (t) => {
  const p = project(t);
  const draft = meetingDraft({
    outcome: 'ESCALATED',
    decision: { question: 'REST or GraphQL?', selected_option: null, rationale: '', tradeoffs: [], risks: [] },
    action_items: [],
  });
  p.ok('meeting', 'record', p.draft('m.json', draft));
  assert.equal(p.read('decisions/D-001.json').approval_status, 'NEEDS_FOUNDER');
  assert.match(p.run('decision', 'approve', 'D-001', '--by', 'founder').err, /--option/);
  assert.match(p.run('decision', 'approve', 'D-001', '--by', 'founder', '--option', 'O9').err, /Unknown option/);
  p.ok('decision', 'approve', 'D-001', '--by', 'founder', '--option', 'O2');
  assert.equal(p.read('decisions/D-001.json').selected_option, 'O2');
});

test('NO_DECISION meetings cannot create tasks', (t) => {
  const p = project(t);
  const r = p.run('meeting', 'record', p.draft('m.json', meetingDraft({ outcome: 'NO_DECISION', decision: null })));
  assert.match(r.err, /NO_DECISION cannot create action items/);
  p.ok('meeting', 'record', p.draft('m2.json', meetingDraft({ outcome: 'NO_DECISION', decision: null, action_items: [] })));
  assert.equal(p.read('meetings/M-001.json').decision_id, null);
});

// --- Approvals (criterion 12) -------------------------------------------------------------------

test('a task that requires approval cannot start until the founder grants it', (t) => {
  const p = project(t);
  p.ok('plan', 'import', p.draft('plan.json', {
    tasks: [{ title: 'Deploy to production', owner: 'orchestrator', acceptance_criteria: ['site is live'], requires_approval: true }],
  }));
  p.ok('plan', 'approve', '--by', 'founder');
  assert.match(p.run('task', 'move', 'T-001', 'IN_PROGRESS').err, /no approval request/);
  assert.match(p.run('approval', 'request', '--action', 'deploy', '--category', 'free-money', '--reason', 'x').err, /--category/);
  p.ok('approval', 'request', '--action', 'Deploy v1 to production', '--category', 'deploy', '--reason', 'release', '--task', 'T-001');
  assert.match(p.run('task', 'move', 'T-001', 'IN_PROGRESS').err, /A-001 is PENDING/);
  assert.match(p.run('approval', 'grant', 'A-001').err, /founder-only/);
  p.ok('approval', 'grant', 'A-001', '--by', 'founder', '--note', 'ok');
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('approval', 'result', 'A-001', '--result', 'deployed build 42');
  const a = p.read('approvals.json').approvals[0];
  assert.equal(a.status, 'GRANTED');
  assert.equal(a.decided_by, 'founder');
  assert.equal(a.execution_result, 'deployed build 42');
  const types = p.history().map((e) => e.type);
  for (const ty of ['approval.request', 'approval.grant', 'approval.result']) assert.ok(types.includes(ty), ty);
});

test('a rejected approval never becomes granted and blocks its task', (t) => {
  const p = project(t);
  p.ok('task', 'add', '--title', 'Push release', '--owner', 'orchestrator', '--criterion', 'tag pushed', '--requires-approval');
  p.ok('task', 'authorize', 'T-001', '--by', 'founder');
  p.ok('approval', 'request', '--action', 'git push origin main', '--category', 'git_push', '--reason', 'publish', '--task', 'T-001');
  p.ok('approval', 'reject', 'A-001', '--by', 'founder', '--note', 'not yet');
  assert.match(p.run('approval', 'grant', 'A-001', '--by', 'founder').err, /only PENDING/);
  assert.equal(p.read('approvals.json').approvals[0].status, 'REJECTED');
  assert.equal(p.task('T-001').status, 'BLOCKED');
  assert.match(p.task('T-001').blocked_reason, /A-001 was rejected/);
  assert.match(p.run('approval', 'result', 'A-001', '--result', 'x').err, /only GRANTED/);
});

// --- Pause, resume, cancel (criterion 13) -------------------------------------------------------

test('pause blocks starting work; resume restores it', (t) => {
  const p = approvedPlan(t);
  assert.match(p.run('project', 'pause').err, /--reason/);
  p.ok('project', 'pause', '--reason', 'waiting for feedback');
  assert.match(p.run('task', 'move', 'T-001', 'IN_PROGRESS').err, /project is PAUSED/);
  assert.match(p.ok('status').out, /PAUSED — waiting for feedback/);
  p.ok('project', 'resume');
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
});

test('project cancel is founder-only, cancels open work, and freezes the state', (t) => {
  const p = approvedPlan(t);
  p.ok('approval', 'request', '--action', 'send email', '--category', 'external_communication', '--reason', 'x');
  assert.match(p.run('project', 'cancel', '--reason', 'stop').err, /founder-only/);
  p.ok('project', 'cancel', '--reason', 'stop', '--by', 'founder');
  assert.equal(p.read('project.json').status, 'CANCELLED');
  assert.ok(p.read('tasks.json').tasks.every((x) => x.status === 'CANCELLED'));
  assert.equal(p.read('approvals.json').approvals[0].status, 'CANCELLED');
  assert.match(p.run('task', 'add', '--title', 'x', '--owner', 'qa-engineer', '--criterion', 'y').err, /CANCELLED/);
  p.ok('validate');
});

test('next returns an interrupted IN_PROGRESS task before READY ones', (t) => {
  const p = approvedPlan(t);
  assert.equal(JSON.parse(p.ok('next').out).id, 'T-001');
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  const next = JSON.parse(p.ok('next').out);
  assert.equal(next.id, 'T-001');
  assert.equal(next.status, 'IN_PROGRESS');
});

test('project complete requires all tasks closed', (t) => {
  const p = approvedPlan(t);
  assert.match(p.run('project', 'complete').err, /Open tasks remain/);
  completeT1(p);
  p.ok('task', 'move', 'T-002', 'CANCELLED', '--reason', 'descoped');
  p.ok('project', 'complete');
  assert.equal(p.read('project.json').status, 'COMPLETED');
});

// --- Reviews and retrospectives (criteria 8, 10) ------------------------------------------------

test('review records separate confirmed from suspected findings and add evidence', (t) => {
  const p = approvedPlan(t);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('evidence', 'add', 'T-001', '--type', 'test', '--result', 'passed', '--command', 'npm test', '--summary', 'ok');
  p.ok('task', 'move', 'T-001', 'REVIEW');
  const finding = (status, severity) => ({ title: 'SQL injection', severity, status, component: 'api', evidence: 'string concat in query', recommendation: 'use parameters' });
  const bad = p.run('review', 'record', p.draft('r.json', { task_id: 'T-001', reviewers: ['security-reviewer'], scope: 'API', verdict: 'PASS', findings: [finding('confirmed', 'high')] }));
  assert.match(bad.err, /PASS is not allowed/);
  p.ok('review', 'record', p.draft('r2.json', {
    task_id: 'T-001',
    reviewers: ['qa-engineer', 'security-reviewer'],
    scope: 'API',
    verdict: 'PASS',
    checks_run: [{ command: 'npm test', result: 'passed', summary: '12 passed' }],
    findings: [finding('suspected', 'high')],
    assumptions: ['No auth in MVP'],
  }));
  const md = fs.readFileSync(path.join(p.dir, '.orkestr', 'reviews', 'R-001.md'), 'utf8');
  assert.match(md, /Suspected findings \(not verified\)/);
  assert.match(md, /SQL injection/);
  const ev = p.task('T-001').evidence.map((e) => `${e.type}:${e.result}:${e.ref ?? ''}`);
  assert.ok(ev.includes('check:passed:R-001'));
  assert.ok(ev.includes('review:passed:R-001'));
  p.ok('task', 'move', 'T-001', 'COMPLETED');
});

test('retrospective lessons need evidence and are appended to memory', (t) => {
  const p = project(t);
  assert.match(p.run('retro', 'record', p.draft('rt.json', { scope: 'M1', lessons: [{ lesson: 'x' }] })).err, /evidence is required/);
  p.ok('retro', 'record', p.draft('rt.json', {
    scope: 'Milestone 1',
    went_well: ['plan was small'],
    went_badly: ['flaky test'],
    lessons: [{ lesson: 'Pin test seeds', evidence: 'T-001 failed twice with random data' }],
    proposed_changes: ['Add seed to QA agent instructions'],
  }));
  assert.ok(fs.existsSync(path.join(p.dir, '.orkestr', 'retros', 'RT-001.md')));
  const lessons = fs.readFileSync(path.join(p.dir, '.orkestr', 'memory', 'lessons-learned.md'), 'utf8');
  assert.match(lessons, /Pin test seeds/);
  assert.match(lessons, /RT-001/);
});

// --- Status (criterion 9) ----------------------------------------------------------------------

test('status lists blockers, failures, pending approvals, and waiting decisions', (t) => {
  const p = approvedPlan(t);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('task', 'move', 'T-001', 'FAILED', '--error', 'build broke', '--next-action', 'ask founder');
  p.ok('approval', 'request', '--action', 'buy a domain', '--category', 'spending', '--reason', 'launch');
  p.ok('meeting', 'record', p.draft('m.json', meetingDraft()));
  const out = p.ok('status').out;
  assert.match(out, /## Blocked[\s\S]*T-002[\s\S]*Dependency T-001 is FAILED/);
  assert.match(out, /## Failed[\s\S]*build broke[\s\S]*ask founder/);
  assert.match(out, /Pending approvals[\s\S]*A-001 \[spending\] buy a domain/);
  assert.match(out, /Decisions waiting for the founder[\s\S]*D-001 \[PROPOSED\]/);
  assert.match(out, /Tasks waiting for authorization[\s\S]*T-003/);
  assert.match(out, /Validation[\s\S]*OK/);
  assert.doesNotMatch(out, /%/, 'no invented percentages');
});

// --- Validation, recovery, and secrets (criteria 11, 13) ---------------------------------------

test('validate and status report corrupted state without crashing', (t) => {
  const p = approvedPlan(t);
  fs.writeFileSync(path.join(p.dir, '.orkestr', 'tasks.json'), '{ not json');
  const v = p.run('validate');
  assert.equal(v.code, 1);
  assert.match(v.out, /tasks.json: corrupted JSON/);
  const s = p.run('status');
  assert.equal(s.code, 1);
  assert.match(s.out, /could not be read/);
  assert.match(p.run('task', 'move', 'T-001', 'IN_PROGRESS').err, /Corrupted JSON/);
});

test('validate detects broken references and illegal states written by hand', (t) => {
  const p = approvedPlan(t);
  const file = path.join(p.dir, '.orkestr', 'tasks.json');
  const doc = JSON.parse(fs.readFileSync(file, 'utf8'));
  doc.tasks[0].status = 'COMPLETED';
  doc.tasks[1].depends_on = ['T-404'];
  fs.writeFileSync(file, JSON.stringify(doc));
  const v = p.run('validate');
  assert.equal(v.code, 1);
  assert.match(v.out, /T-001: COMPLETED but/);
  assert.match(v.out, /unknown dependency T-404/);
});

test('an active lock blocks writers; a stale lock is replaced', (t) => {
  const p = approvedPlan(t);
  const lock = path.join(p.dir, '.orkestr', '.lock');
  fs.writeFileSync(lock, '{}');
  const r = p.run('task', 'move', 'T-001', 'IN_PROGRESS');
  assert.equal(r.code, 1);
  assert.match(r.err, /holds the lock/);
  assert.equal(p.task('T-001').status, 'READY');
  const old = new Date(Date.now() - 11 * 60 * 1000);
  fs.utimesSync(lock, old, old);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  assert.ok(!fs.existsSync(lock));
});

test('leftover temp files from an interrupted write are reported', (t) => {
  const p = approvedPlan(t);
  fs.writeFileSync(path.join(p.dir, '.orkestr', 'tasks.json.tmp-1234'), '{}');
  const v = p.ok('validate');
  assert.match(v.out, /leftover temporary file/);
});

test('secrets are redacted before writing and detected by validate', (t) => {
  const p = approvedPlan(t);
  const token = `ghp_${'a1B2'.repeat(9)}`;
  p.ok('task', 'move', 'T-001', 'BLOCKED', '--reason', `token=${token} expired`);
  const raw = fs.readFileSync(path.join(p.dir, '.orkestr', 'tasks.json'), 'utf8')
    + fs.readFileSync(path.join(p.dir, '.orkestr', 'history.jsonl'), 'utf8');
  assert.ok(!raw.includes(token));
  assert.match(raw, /\[REDACTED\]/);
  p.ok('validate');

  const file = path.join(p.dir, '.orkestr', 'approvals.json');
  fs.writeFileSync(file, JSON.stringify({ schema_version: 1, approvals: [], note: `password: ${'hunter2hunter2'}` }));
  const v = p.run('validate');
  assert.equal(v.code, 1);
  assert.match(v.out, /possible secret/);
});

test('every state change is recorded in history', (t) => {
  const p = approvedPlan(t);
  completeT1(p);
  const transitions = p.history().filter((e) => e.type === 'task.transition' && e.entity === 'T-001').map((e) => `${e.from}>${e.to}`);
  assert.deepEqual(transitions, ['BACKLOG>READY', 'READY>IN_PROGRESS', 'IN_PROGRESS>REVIEW', 'REVIEW>COMPLETED']);
  for (const e of p.history()) {
    assert.ok(e.ts && e.type && 'actor' in e && 'summary' in e, JSON.stringify(e));
  }
});

test('redaction helpers cover common secret formats', () => {
  const samples = [
    `sk-ant-${'x'.repeat(30)}`,
    `AKIA${'A'.repeat(16)}`,
    `Bearer ${'abc.def-'.repeat(3)}`,
    'api_key = "abcdef123456"',
    '-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----',
  ];
  for (const s of samples) {
    const out = redactString(s);
    assert.match(out, /\[REDACTED\]/, s);
    assert.deepEqual(findSecrets(out), [], `still detected after redaction: ${out}`);
    assert.ok(findSecrets(s).length > 0, s);
  }
  assert.equal(redactString('token budget is 4 meetings'), 'token budget is 4 meetings');
});

test('Unicode (Turkish) project names are stored and reported correctly', (t) => {
  const p = project(t, { init: false });
  p.ok('init', '--name', 'Müşteri Portalı', '--goal', 'Çalışan bir MVP');
  assert.equal(p.read('project.json').name, 'Müşteri Portalı');
  assert.match(p.ok('status').out, /Müşteri Portalı/);
});
