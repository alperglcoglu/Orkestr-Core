// Phase 3 plugin changes (PHASE3_IMPLEMENTATION.md §12): projects handed to the standalone
// core are read-only here, and tasks may carry exact verification commands.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { project } from './helpers.mjs';

test('a project managed by orkestr-core is read-only for the state tool', (t) => {
  const p = project(t, { autonomy: 'bounded' });
  const file = path.join(p.dir, '.orkestr', 'project.json');
  const proj = JSON.parse(fs.readFileSync(file, 'utf8'));
  proj.managed_by = 'core';
  fs.writeFileSync(file, JSON.stringify(proj, null, 2));
  const r = p.run('assumption', 'add', '--text', 'x');
  assert.equal(r.code, 1);
  assert.match(r.err, /managed by orkestr-core/);
  assert.match(r.err, /export --release/);
  p.ok('status');
  p.ok('validate');

  proj.managed_by = 'someone';
  fs.writeFileSync(file, JSON.stringify(proj, null, 2));
  assert.match(p.run('validate').out, /managed_by must be plugin or core/);
});

test('tasks accept exact verify commands; empty ones are rejected', (t) => {
  const p = project(t, { autonomy: 'bounded' });
  p.ok('plan', 'import', p.draft('plan.json', {
    tasks: [{ title: 'API', owner: 'backend-engineer', acceptance_criteria: ['works'], scope: ['src/**'], verify: ['node --test tests/'] }],
  }));
  assert.deepEqual(p.task('T-001').verify, ['node --test tests/']);
  p.ok('validate');
  const bad = p.run('plan', 'import', p.draft('bad.json', { tasks: [{ title: 'X', owner: 'qa-engineer', acceptance_criteria: ['y'], verify: [''] }] }));
  assert.equal(bad.code, 1);
  assert.match(bad.err, /"verify" must be a list of exact test commands/);
  p.ok('plan', 'import', p.draft('plain.json', { tasks: [{ title: 'Y', owner: 'qa-engineer', acceptance_criteria: ['y'] }] }));
  assert.equal('verify' in p.task('T-002'), false, 'tasks without verify keep the Phase 1 shape');
});
