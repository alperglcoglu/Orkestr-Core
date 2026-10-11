// PreToolUse guard tests (MASTER_SPEC §10.2 "Enforcement in Phase 1").

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { GUARD } from './helpers.mjs';
import { decide } from '../scripts/lib/guard-rules.mjs';

const bash = (command) => ({ tool_name: 'Bash', tool_input: { command } });
const ps = (command) => ({ tool_name: 'PowerShell', tool_input: { command } });

test('asks before restricted commands', () => {
  const risky = [
    bash('git push origin main'),
    bash('git -C repo push --force'),
    bash('npm publish'),
    bash('gh release create v1.0.0'),
    bash('docker push me/app'),
    bash('vercel --prod'),
    bash('netlify deploy --prod'),
    bash('kubectl apply -f k8s.yaml'),
    bash('terraform apply'),
    bash('rm -rf build'),
    bash('rm -r -f node_modules'),
    bash('git reset --hard HEAD~1'),
    bash('git clean -fd'),
    bash('git checkout -- .'),
    bash('chmod -R 777 .'),
    bash('psql -c "DROP TABLE users"'),
    ps('Remove-Item -Recurse -Force .\\dist'),
    ps('icacls C:\\data /grant Everyone:F'),
    bash('rmdir /s /q build'),
  ];
  for (const input of risky) assert.ok(decide(input), `expected ask: ${input.tool_input.command}`);
});

test('asks before founder-only orkestr commands', () => {
  const ork = 'node "C:/x/plugins/orkestr/scripts/orkestr.mjs"';
  for (const sub of [
    'plan approve --by founder',
    'decision approve D-001 --by founder',
    'decision reject D-001 --by founder --note x',
    'task authorize T-001 --by founder',
    'approval grant A-001 --by founder',
    'approval reject A-001 --by founder',
    'task move T-001 READY --retry --by founder',
    'project cancel --reason x --by founder',
    'project autonomy bounded --by founder',
  ]) {
    assert.match(decide(bash(`${ork} ${sub}`)), /founder-only orkestr action/, sub);
  }
  // Chaining a risky command after a state-tool call is still caught.
  assert.match(decide(bash(`${ork} status && git push`)), /git push/);
  assert.match(decide(bash(`${ork} status; rm -rf src`)), /destructive/);
});

test('stays silent for routine commands', () => {
  const ork = 'node "C:/x/plugins/orkestr/scripts/orkestr.mjs"';
  const routine = [
    bash('npm test'),
    bash('git status'),
    bash('git diff'),
    bash('git log --oneline'),
    bash('ls -la'),
    bash('rm notes.tmp'),
    bash(`${ork} status`),
    bash(`${ork} task move T-001 IN_PROGRESS`),
    bash(`${ork} evidence add T-001 --type test --result passed --command "npm test" --summary ok`),
    bash(`${ork} approval request --action "git push" --category git_push --reason release`),
    ps('Get-ChildItem'),
    { tool_name: 'Read', tool_input: { file_path: '.orkestr/tasks.json' } },
  ];
  for (const input of routine) assert.equal(decide(input), null, input.tool_input.command ?? input.tool_input.file_path);
});

test('asks before direct edits of orkestr state files but not drafts or prose', () => {
  const edit = (file_path) => ({ tool_name: 'Write', tool_input: { file_path } });
  for (const f of [
    'C:\\proj\\.orkestr\\tasks.json',
    '/proj/.orkestr/project.json',
    '/proj/.orkestr/approvals.json',
    '/proj/.orkestr/history.jsonl',
    '/proj/.orkestr/decisions/D-001.json',
    '/proj/.orkestr/meetings/M-002.md',
    '/proj/.orkestr/retros/RT-001.json',
  ]) {
    assert.ok(decide(edit(f)), f);
    assert.ok(decide({ tool_name: 'Edit', tool_input: { file_path: f } }), f);
  }
  for (const f of ['/proj/.orkestr/drafts/plan.json', '/proj/.orkestr/requirements/prd.md', '/proj/src/tasks.json']) {
    assert.equal(decide(edit(f)), null, f);
  }
});

test('hook process prints a PreToolUse ask decision, or nothing', () => {
  const run = (input) => spawnSync(process.execPath, [GUARD], { input, encoding: 'utf8' });
  const hit = run(JSON.stringify(bash('git push')));
  assert.equal(hit.status, 0);
  const out = JSON.parse(hit.stdout);
  assert.equal(out.hookSpecificOutput.hookEventName, 'PreToolUse');
  assert.equal(out.hookSpecificOutput.permissionDecision, 'ask');
  assert.match(out.hookSpecificOutput.permissionDecisionReason, /git push/);

  const quiet = run(JSON.stringify(bash('npm test')));
  assert.equal(quiet.status, 0);
  assert.equal(quiet.stdout, '');

  const garbage = run('not json');
  assert.equal(garbage.status, 0, 'a broken input never blocks work');
  assert.equal(garbage.stdout, '');
});
