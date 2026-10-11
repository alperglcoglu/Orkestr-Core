// Test helpers: each project lives in its own temporary directory under the OS temp folder.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const PLUGIN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const CLI = path.join(PLUGIN_ROOT, 'scripts', 'orkestr.mjs');
export const GUARD = path.join(PLUGIN_ROOT, 'scripts', 'guard.mjs');

export function tempDir(t, prefix = 'orkestr-test-') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

// Default "supervised" keeps the founder-approval tests meaningful; autonomy tests pass "bounded".
export function project(t, { init = true, autonomy = 'supervised' } = {}) {
  const dir = tempDir(t);
  const run = (...args) => {
    const r = spawnSync(process.execPath, [CLI, ...args], { cwd: dir, encoding: 'utf8' });
    return { code: r.status, out: r.stdout, err: r.stderr };
  };
  const ok = (...args) => {
    const r = run(...args);
    if (r.code !== 0) throw new Error(`Expected success for "${args.join(' ')}" but got ${r.code}: ${r.err || r.out}`);
    return r;
  };
  const draft = (name, data) => {
    const file = path.join(dir, name);
    fs.writeFileSync(file, JSON.stringify(data, null, 2));
    return name;
  };
  const read = (rel) => JSON.parse(fs.readFileSync(path.join(dir, '.orkestr', rel), 'utf8'));
  const task = (id) => read('tasks.json').tasks.find((x) => x.id === id);
  const history = () =>
    fs
      .readFileSync(path.join(dir, '.orkestr', 'history.jsonl'), 'utf8')
      .split('\n')
      .filter(Boolean)
      .map((l) => JSON.parse(l));
  if (init) ok('init', '--name', 'Test project', '--goal', 'Ship a small app', '--autonomy', autonomy);
  return { dir, run, ok, draft, read, task, history };
}

export const twoTaskPlan = {
  summary: 'API then UI',
  tasks: [
    { title: 'Build API', owner: 'backend-engineer', acceptance_criteria: ['GET /items returns 200'] },
    { title: 'Build UI', owner: 'frontend-engineer', acceptance_criteria: ['List renders'], depends_on: ['#1'] },
  ],
};

/** Initializes a project with an approved two-task plan (T-001 READY, T-002 BACKLOG). */
export function approvedPlan(t) {
  const p = project(t);
  p.ok('plan', 'import', p.draft('plan.json', twoTaskPlan));
  p.ok('plan', 'approve', '--by', 'founder');
  return p;
}

/** Drives T-001 through implementation, a passing test, and a passing review. */
export function completeT1(p) {
  p.ok('task', 'move', 'T-001', 'IN_PROGRESS');
  p.ok('evidence', 'add', 'T-001', '--type', 'test', '--result', 'passed', '--command', 'npm test', '--summary', 'all tests pass');
  p.ok('task', 'move', 'T-001', 'REVIEW');
  p.ok('evidence', 'add', 'T-001', '--type', 'review', '--result', 'passed', '--summary', 'reviewed by QA');
  p.ok('task', 'move', 'T-001', 'COMPLETED');
}

export function meetingDraft(overrides = {}) {
  return {
    objective: 'Choose the API style',
    context: 'Need an API for the list view.',
    trigger: 'Affects backend, frontend, and QA.',
    participants: [
      { role: 'tech-lead', responsibility: 'architecture' },
      { role: 'backend-engineer', responsibility: 'API implementation' },
      { role: 'frontend-engineer', responsibility: 'API consumer' },
    ],
    rounds: [
      {
        round: 1,
        contributions: [
          { role: 'tech-lead', position: 'REST', key_points: ['simple'], concerns: [] },
          { role: 'backend-engineer', position: 'REST', key_points: [], concerns: [] },
          { role: 'frontend-engineer', position: 'GraphQL', key_points: ['flexible'], concerns: ['overfetching'] },
        ],
      },
    ],
    options: [
      { id: 'O1', name: 'REST', summary: 'Plain JSON endpoints' },
      { id: 'O2', name: 'GraphQL', summary: 'Single endpoint' },
    ],
    criteria: ['simplicity', 'cost'],
    evaluation: 'REST is simpler for an MVP.',
    outcome: 'DECIDED',
    decision: { question: 'Which API style?', selected_option: 'O1', rationale: 'Simplest option', tradeoffs: ['less flexible'], risks: [] },
    dissent: [{ role: 'frontend-engineer', objection: 'May need more endpoints later' }],
    open_questions: [],
    action_items: [
      { title: 'Define REST endpoints', owner: 'backend-engineer', acceptance_criteria: ['OpenAPI file exists'] },
      { title: 'Consume endpoints', owner: 'frontend-engineer', acceptance_criteria: ['List loads'], depends_on: ['#1'] },
    ],
    ...overrides,
  };
}
