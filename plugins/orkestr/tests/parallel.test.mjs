// Parallel implementation tests (MASTER_SPEC §5.3): file scopes, the parallel limit,
// exclusive tasks, candidate selection, and scope checks of changed files.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { project } from './helpers.mjs';
import { fileInScope, patternsOverlap, scopeProblems, scopesOverlap } from '../scripts/lib/scope.mjs';

const task = (title, owner, scope, extra = {}) => ({ title, owner, acceptance_criteria: [`${title} works`], scope, ...extra });

function threeIndependent(t) {
  const p = project(t, { autonomy: 'bounded' });
  p.ok('plan', 'import', p.draft('plan.json', {
    tasks: [
      task('API', 'backend-engineer', ['src/api/**', 'tests/api/**']),
      task('UI', 'frontend-engineer', ['src/ui/**', 'tests/ui/**']),
      task('CLI', 'backend-engineer', ['src/cli/**']),
    ],
  }));
  return p;
}

test('scope patterns: overlap is conservative, matching follows glob rules', () => {
  assert.ok(patternsOverlap('src/api/**', 'src/api/users.js'));
  assert.ok(patternsOverlap('src/api', 'src/api/users.js'), 'a directory covers its files');
  assert.ok(patternsOverlap('src/**', 'src/ui/x.js'));
  assert.ok(patternsOverlap('**/*.js', 'docs/a.md'), 'leading wildcard overlaps everything');
  assert.ok(patternsOverlap('SRC/Api/**', 'src/api/x.js'), 'case-insensitive');
  assert.ok(!patternsOverlap('src/api/**', 'src/ui/**'));
  assert.ok(!patternsOverlap('src/a.js', 'src/a.json'));
  assert.ok(scopesOverlap([], ['src/**']), 'an empty scope is exclusive');

  assert.ok(fileInScope('src/api/v1/users.js', ['src/api/**']));
  assert.ok(fileInScope('src\\api\\users.js', ['src/api/**']), 'Windows separators');
  assert.ok(fileInScope('src/api/users.js', ['src/api']));
  assert.ok(fileInScope('src/main.js', ['src/*.js']));
  assert.ok(!fileInScope('src/lib/main.js', ['src/*.js']), '* does not cross directories');
  assert.ok(!fileInScope('src/ui/app.js', ['src/api/**']));

  assert.deepEqual(scopeProblems(['src/**']), []);
  assert.equal(scopeProblems(['../outside']).length, 1);
  assert.equal(scopeProblems(['/etc/passwd']).length, 1);
  assert.equal(scopeProblems(['C:\\x']).length, 1);
  assert.equal(scopeProblems(['.orkestr/tasks.json']).length, 1);
});

test('two tasks with disjoint scopes run in parallel; the limit (2) stops a third', (t) => {
  const p = threeIndependent(t);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('task', 'move', 'T-002', 'IN_PROGRESS');
  const third = p.run('task', 'move', 'T-003', 'IN_PROGRESS');
  assert.equal(third.code, 1);
  assert.match(third.err, /parallel limit reached \(2\/2/);
  assert.match(p.ok('status').out, /scope: src\/api\/\*\*, tests\/api\/\*\*/);
  p.ok('validate');
});

test('overlapping scopes cannot run at the same time', (t) => {
  const p = project(t, { autonomy: 'bounded' });
  p.ok('plan', 'import', p.draft('plan.json', {
    tasks: [task('Users API', 'backend-engineer', ['src/api/**']), task('Auth middleware', 'backend-engineer', ['src/api/auth.js'])],
  }));
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  const r = p.run('task', 'move', 'T-002', 'IN_PROGRESS');
  assert.equal(r.code, 1);
  assert.match(r.err, /conflicts with T-001 \(in progress\): their file scopes overlap/);
});

test('a task without a scope is exclusive in both directions', (t) => {
  const p = project(t, { autonomy: 'bounded' });
  p.ok('plan', 'import', p.draft('plan.json', {
    tasks: [task('Refactor everything', 'tech-lead', undefined), task('UI', 'frontend-engineer', ['src/ui/**'])],
  }));
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  assert.match(p.run('task', 'move', 'T-002', 'IN_PROGRESS').err, /runs exclusively/);

  const q = project(t, { autonomy: 'bounded' });
  q.ok('plan', 'import', q.draft('plan.json', {
    tasks: [task('UI', 'frontend-engineer', ['src/ui/**']), task('Refactor everything', 'tech-lead', [])],
  }));
  q.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  assert.match(q.run('task', 'move', 'T-002', 'IN_PROGRESS').err, /runs exclusively/);
});

test('next --parallel returns a conflict-free batch within the limit', (t) => {
  const p = project(t, { autonomy: 'bounded' });
  p.ok('plan', 'import', p.draft('plan.json', {
    tasks: [
      task('API', 'backend-engineer', ['src/api/**']),
      task('API auth', 'backend-engineer', ['src/api/auth/**']),
      task('UI', 'frontend-engineer', ['src/ui/**']),
      task('Docs', 'product-manager', ['docs/**']),
    ],
  }));
  let batch = JSON.parse(p.ok('next', '--parallel').out).map((x) => x.id);
  assert.deepEqual(batch, ['T-001', 'T-003'], 'T-002 overlaps T-001; the limit stops T-004');

  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  batch = JSON.parse(p.ok('next', '--parallel').out).map((x) => x.id);
  assert.deepEqual(batch, ['T-003'], 'one slot left, and T-002 still overlaps the running T-001');
});

test('scope check records a failed check (a fix attempt) for out-of-scope changes', (t) => {
  const p = threeIndependent(t);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('task', 'move', 'T-002', 'IN_PROGRESS');

  const good = p.ok('scope', 'check', 'T-001', '--file', 'src/api/users.js', '--file', 'tests\\api\\users.test.js');
  assert.match(good.out, /Scope check passed/);

  const bad = p.run('scope', 'check', 'T-001', '--file', 'src/api/users.js', '--file', 'src/ui/app.js', '--file', 'package.json');
  assert.equal(bad.code, 1);
  assert.match(bad.out, /VIOLATION\tsrc\/ui\/app\.js\tT-002\toutside T-001's scope/);
  assert.match(bad.out, /VIOLATION\tpackage\.json\t-\toutside T-001's scope/);
  const t1 = p.task('T-001');
  assert.equal(t1.fix_attempts, 1, 'an out-of-scope change is a defect and consumes a fix attempt');
  const last = t1.evidence.at(-1);
  assert.equal(last.command, 'orkestr scope check');
  assert.equal(last.result, 'failed');
});

test('scope check without a task attributes every changed file or reports it', (t) => {
  const p = threeIndependent(t);
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('task', 'move', 'T-002', 'IN_PROGRESS');
  const r = p.run('scope', 'check', '--file', 'src/api/a.js', '--file', 'src/ui/b.js', '--file', 'README.md');
  assert.equal(r.code, 1);
  assert.match(r.out, /ok\tsrc\/api\/a\.js\tT-001/);
  assert.match(r.out, /ok\tsrc\/ui\/b\.js\tT-002/);
  assert.match(r.out, /VIOLATION\tREADME\.md\t-\tnot in the scope of any active task/);
  assert.equal(p.task('T-001').evidence.length, 0, 'a union check records nothing');
});

test('rework from review waits if an overlapping task started meanwhile', (t) => {
  const p = project(t, { autonomy: 'bounded' });
  p.ok('plan', 'import', p.draft('plan.json', {
    tasks: [task('API', 'backend-engineer', ['src/api/**']), task('API v2', 'backend-engineer', ['src/api/v2/**'])],
  }));
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('task', 'move', 'T-001', 'REVIEW');
  p.ok('task', 'move', 'T-002', 'IN_PROGRESS');
  assert.match(p.run('task', 'move', 'T-001', 'IN_PROGRESS').err, /conflicts with T-002/);
});

test('invalid scopes are rejected at import; manual tasks accept --scope', (t) => {
  const p = project(t, { autonomy: 'bounded' });
  const r = p.run('plan', 'import', p.draft('plan.json', { tasks: [task('X', 'backend-engineer', ['../../etc'])] }));
  assert.equal(r.code, 1);
  assert.match(r.err, /must not contain "\.\."/);
  p.ok('task', 'add', '--title', 'Docs', '--owner', 'product-manager', '--criterion', 'written', '--scope', 'docs/**');
  assert.deepEqual(p.task('T-001').scope, ['docs/**']);
});

test('legacy projects without max_parallel_tasks stay sequential; validate catches overlaps', (t) => {
  const p = threeIndependent(t);
  const file = path.join(p.dir, '.orkestr', 'project.json');
  const proj = JSON.parse(fs.readFileSync(file, 'utf8'));
  delete proj.limits.max_parallel_tasks;
  fs.writeFileSync(file, JSON.stringify(proj));
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  assert.match(p.run('task', 'move', 'T-002', 'IN_PROGRESS').err, /parallel limit reached \(1\/1/);

  const tasksFile = path.join(p.dir, '.orkestr', 'tasks.json');
  const doc = JSON.parse(fs.readFileSync(tasksFile, 'utf8'));
  doc.tasks[1].status = 'IN_PROGRESS';
  doc.tasks[1].scope = ['src/api/x.js'];
  fs.writeFileSync(tasksFile, JSON.stringify(doc));
  const v = p.run('validate');
  assert.equal(v.code, 1);
  assert.match(v.out, /2 tasks IN_PROGRESS but limits\.max_parallel_tasks is 1/);
  assert.match(v.out, /T-001 and T-002 are both IN_PROGRESS with overlapping scopes/);
});

test('system-architect, ml-engineer, and data-engineer are valid owners; devops is not', (t) => {
  const p = project(t, { autonomy: 'bounded' });
  p.ok('task', 'add', '--title', 'System design', '--owner', 'system-architect', '--criterion', 'architecture.md written', '--scope', 'docs/architecture/**');
  p.ok('task', 'add', '--title', 'Ranking model', '--owner', 'ml-engineer', '--criterion', 'F1 >= baseline', '--scope', 'src/ml/**');
  p.ok('task', 'add', '--title', 'Schema and seed data', '--owner', 'data-engineer', '--criterion', 'migration reversible', '--scope', 'db/**');
  assert.deepEqual(['T-001', 'T-002', 'T-003'].map((id) => p.task(id).owner), ['system-architect', 'ml-engineer', 'data-engineer']);
  assert.match(p.run('task', 'add', '--title', 'Deploy', '--owner', 'devops-engineer', '--criterion', 'x').err, /owner/);
});
