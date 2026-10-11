#!/usr/bin/env node
// Phase 2 scorecard: reads a project's .orkestr/ records and measures the seven
// MASTER_SPEC Phase 2 criteria. Uses no model calls and no network.
// Usage: node phase2/scorecard.mjs [project-dir] [--rerun]
//   --rerun   re-execute every recorded passing test/check command and compare results
// Writes <project-dir>/phase2-scorecard.md and prints it.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { fileInScope } from '../plugins/orkestr/scripts/lib/scope.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const ORK = path.join(here, '..', 'plugins', 'orkestr', 'scripts', 'orkestr.mjs');
const args = process.argv.slice(2);
const rerun = args.includes('--rerun');
const root = path.resolve(args.find((a) => !a.startsWith('--')) ?? '.');
const dir = path.join(root, '.orkestr');

if (!fs.existsSync(path.join(dir, 'project.json'))) {
  console.error(`No Orkestr project in ${root} (.orkestr/project.json missing).`);
  process.exit(1);
}

const read = (rel) => JSON.parse(fs.readFileSync(path.join(dir, rel), 'utf8'));
const listJson = (sub) =>
  fs.existsSync(path.join(dir, sub))
    ? fs.readdirSync(path.join(dir, sub)).filter((f) => f.endsWith('.json')).sort().map((f) => read(`${sub}/${f}`))
    : [];
const project = read('project.json');
const tasks = read('tasks.json').tasks;
const approvals = fs.existsSync(path.join(dir, 'approvals.json')) ? read('approvals.json').approvals ?? [] : [];
const history = fs.readFileSync(path.join(dir, 'history.jsonl'), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
const meetings = listJson('meetings');
const decisions = listJson('decisions');
const reviews = listJson('reviews');
const byId = new Map(tasks.map((t) => [t.id, t]));
const run = (cmd, a, opts = {}) => spawnSync(cmd, a, { cwd: root, encoding: 'utf8', timeout: 120_000, ...opts });
const hasGit = run('git', ['rev-parse', '--is-inside-work-tree']).status === 0;

const out = [];
const sections = [];
function section(title) {
  const s = { title, ok: 0, warn: 0, fail: 0, lines: [] };
  sections.push(s);
  return {
    ok: (m) => (s.ok++, s.lines.push(`- ✅ ${m}`)),
    warn: (m) => (s.warn++, s.lines.push(`- ⚠️ ${m}`)),
    fail: (m) => (s.fail++, s.lines.push(`- ❌ ${m}`)),
    info: (m) => s.lines.push(`- ${m}`),
    failures: () => s.fail,
  };
}

// 1. Workflow completion -------------------------------------------------------
{
  const s = section('1. Workflow completed');
  const counts = {};
  for (const t of tasks) counts[t.status] = (counts[t.status] ?? 0) + 1;
  s.info(`Project status: ${project.status}, autonomy: ${project.autonomy ?? 'supervised (legacy)'}`);
  s.info(`Tasks: ${tasks.length} — ${Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(', ') || 'none'}`);
  if (!tasks.length) s.fail('No tasks were created');
  const open = tasks.filter((t) => !['COMPLETED', 'CANCELLED'].includes(t.status));
  if (tasks.length && !open.length) s.ok('Every task is closed');
  else if (open.length) s.warn(`Open tasks: ${open.map((t) => `${t.id} ${t.status}`).join(', ')}`);
  if (tasks.length && !tasks.some((t) => t.source?.type === 'plan')) s.warn('No task came from a plan import (the plan step created no tasks)');
  fs.existsSync(path.join(dir, 'requirements', 'prd.md')) ? s.ok('PRD written') : s.warn('No requirements/prd.md');
  for (const t of tasks.filter((x) => x.status === 'FAILED')) s.fail(`${t.id} FAILED: ${t.failure?.error ?? '?'} → ${t.failure?.next_action ?? '?'}`);
}

// 2. Artifact consistency ------------------------------------------------------
{
  const s = section('2. Artifacts consistent');
  const v = run(process.execPath, [ORK, 'validate']);
  v.status === 0 ? s.ok('`ork validate` passes') : s.fail(`\`ork validate\` failed:\n\n\`\`\`\n${(v.stdout + v.stderr).trim()}\n\`\`\``);
  for (const sub of ['meetings', 'decisions', 'reviews', 'retros']) {
    const p = path.join(dir, sub);
    if (!fs.existsSync(p)) continue;
    for (const f of fs.readdirSync(p).filter((x) => x.endsWith('.json'))) {
      if (!fs.existsSync(path.join(p, f.replace(/\.json$/, '.md')))) s.fail(`${sub}/${f} has no rendered .md`);
    }
  }
  for (const d of decisions) {
    if (d.meeting_id && !meetings.some((m) => m.id === d.meeting_id)) s.fail(`${d.id} points to missing ${d.meeting_id}`);
    for (const id of d.task_ids ?? []) if (!byId.has(id)) s.fail(`${d.id} points to missing task ${id}`);
  }
  for (const m of meetings) {
    if (m.decision_id && !decisions.some((d) => d.id === m.decision_id)) s.fail(`${m.id} points to missing ${m.decision_id}`);
    for (const id of m.task_ids ?? []) {
      const t = byId.get(id);
      if (!t) s.fail(`${m.id} action item ${id} is not a task`);
      else if (t.source?.ref !== m.id) s.warn(`${id} came from ${m.id} but its source is ${JSON.stringify(t.source)}`);
    }
  }
  for (const r of reviews) if (r.task_id && !byId.has(r.task_id)) s.fail(`${r.id} reviews missing task ${r.task_id}`);
  const historyTasks = new Set(history.filter((e) => e.type === 'task.create').map((e) => e.entity));
  for (const t of tasks) if (!historyTasks.has(t.id)) s.fail(`${t.id} has no task.create history event`);
  if (!s.failures()) s.ok('Cross-references between meetings, decisions, reviews, tasks and history resolve');
}

// 3. Meeting usefulness --------------------------------------------------------
{
  const s = section('3. Meetings useful, not repetitive');
  const words = (c) =>
    new Set([c.position, ...(c.key_points ?? []), ...(c.concerns ?? [])].join(' ').toLowerCase().match(/[\p{L}\p{N}]{4,}/gu) ?? []);
  const jaccard = (a, b) => {
    const inter = [...a].filter((x) => b.has(x)).length;
    const uni = new Set([...a, ...b]).size;
    return uni ? inter / uni : 0;
  };
  if (!meetings.length) s.warn('No meetings held — run the forced meeting step in phase2/README.md');
  for (const m of meetings) {
    const d = decisions.find((x) => x.id === m.decision_id);
    s.info(
      `**${m.id}** ${m.recovery_for ? `(recovery for ${m.recovery_for}) ` : ''}"${m.objective}" — ` +
        `${m.participants.length} participants, ${m.rounds.length} round(s), outcome ${m.outcome}` +
        `${d ? `, ${d.id} ${d.approval_status}` : ''}, ${m.dissent?.length ?? 0} dissent, ${m.task_ids?.length ?? 0} action item task(s)`,
    );
    const positions = new Set(m.rounds[0].contributions.map((c) => (c.position ?? '').trim().toLowerCase()));
    if (positions.size === 1 && m.participants.length > 1) s.warn(`${m.id}: every participant took the same position in round 1 (was a meeting needed?)`);
    const thin = m.rounds.flatMap((r) => r.contributions).filter((c) => !(c.key_points ?? []).length);
    if (thin.length) s.warn(`${m.id}: ${thin.length} contribution(s) without key points`);
    if (m.rounds.length === 2) {
      for (const c2 of m.rounds[1].contributions) {
        const c1 = m.rounds[0].contributions.find((c) => c.role === c2.role);
        if (!c1) continue;
        const sim = jaccard(words(c1), words(c2));
        sim > 0.6
          ? s.warn(`${m.id}: ${c2.role} repeated itself in round 2 (word overlap ${sim.toFixed(2)})`)
          : s.ok(`${m.id}: ${c2.role} added new content in round 2 (overlap ${sim.toFixed(2)})`);
      }
    }
    if (m.outcome === 'DECIDED' && !m.recovery_for && !(m.task_ids ?? []).length) s.warn(`${m.id}: decided but produced no tasks`);
    if (m.outcome === 'DECIDED' && !(m.dissent ?? []).length && positions.size > 1) s.info(`${m.id}: positions were worded differently and no dissent was recorded — check by hand whether a real objection was dropped`);
    for (const earlier of decisions.filter((x) => x.id < (m.decision_id ?? '') && x.approval_status === 'APPROVED')) {
      const sim = jaccard(new Set(earlier.question.toLowerCase().split(/\W+/)), new Set((d?.question ?? '').toLowerCase().split(/\W+/)));
      if (sim > 0.6) s.warn(`${m.id} may reopen ${earlier.id} ("${earlier.question}")`);
    }
  }
}

// 4. Tasks and dependencies ----------------------------------------------------
{
  const s = section('4. Tasks and dependencies tracked');
  const firstStart = (id) => history.find((e) => e.entity === id && e.to === 'IN_PROGRESS')?.ts;
  const completedAt = (id) => history.find((e) => e.entity === id && e.to === 'COMPLETED')?.ts;
  for (const t of tasks) {
    for (const dep of t.depends_on) {
      if (!byId.has(dep)) s.fail(`${t.id} depends on missing ${dep}`);
      const start = firstStart(t.id);
      const done = completedAt(dep);
      if (start && (!done || done > start)) s.fail(`${t.id} started (${start}) before its dependency ${dep} was completed`);
    }
    if (t.status === 'COMPLETED') {
      const ev = t.evidence ?? [];
      if (!ev.some((e) => ['test', 'check'].includes(e.type) && e.result === 'passed' && e.command)) s.fail(`${t.id} completed without a passed test/check that names its command`);
      if (!ev.some((e) => e.type === 'review' && e.result === 'passed')) s.fail(`${t.id} completed without a passed review`);
    }
    if (t.status === 'BLOCKED' && !t.blocked_reason && !t.blocked_by_dependency) s.warn(`${t.id} is BLOCKED without a reason`);
    if (!t.scope?.length && t.owner?.endsWith('engineer')) s.warn(`${t.id} (${t.owner}) has no file scope, so it can only run alone`);
  }
  const fixes = tasks.reduce((n, t) => n + (t.fix_attempts ?? 0), 0);
  s.info(`Fix attempts used: ${fixes}; notes written: ${tasks.reduce((n, t) => n + (t.notes?.length ?? 0), 0)}; assumptions: ${project.assumptions?.length ?? 0}`);
  const parallel = history.filter((e) => e.to === 'IN_PROGRESS');
  let maxConcurrent = 0;
  const active = new Set();
  for (const e of history) {
    if (e.type !== 'task.transition') continue;
    if (e.to === 'IN_PROGRESS') active.add(e.entity);
    else active.delete(e.entity);
    maxConcurrent = Math.max(maxConcurrent, active.size);
  }
  s.info(`Task starts: ${parallel.length}; max tasks in progress at once: ${maxConcurrent} (limit ${project.limits.max_parallel_tasks})`);
  if (hasGit) {
    const base = run('git', ['rev-list', '--max-parents=0', 'HEAD']).stdout.trim().split('\n')[0];
    const changed = new Set([
      ...run('git', ['diff', '--name-only', base]).stdout.split('\n'),
      ...run('git', ['ls-files', '--others', '--exclude-standard']).stdout.split('\n'),
    ].filter((f) => f && !f.startsWith('.orkestr/') && f !== 'phase2-scorecard.md'));
    const scoped = tasks.filter((t) => t.scope?.length && t.status !== 'CANCELLED');
    const unscopedTask = tasks.some((t) => !t.scope?.length && ['COMPLETED', 'IN_PROGRESS', 'REVIEW'].includes(t.status));
    const outside = [...changed].filter((f) => !scoped.some((t) => fileInScope(f, t.scope)));
    s.info(`Changed files since sandbox start: ${changed.size}`);
    if (!outside.length) s.ok('Every changed file belongs to some task scope');
    else (unscopedTask ? s.warn : s.fail)(`Files outside every task scope: ${outside.join(', ')}${unscopedTask ? ' (an unscoped task ran, so this may be fine)' : ''}`);
  }
  for (const r of reviews) {
    const owner = byId.get(r.task_id)?.owner;
    if ((r.reviewers ?? []).includes('orchestrator')) s.warn(`${r.id}: reviewed by the orchestrator, not by an independent reviewer agent`);
    else if (owner && owner !== 'qa-engineer' && (r.reviewers ?? []).includes(owner)) s.warn(`${r.id}: the task owner (${owner}) reviewed its own task`);
  }
  if (!s.failures()) s.ok('Dependencies resolve and were respected; completions carry evidence');
}

// 5. Generated code verifiable -------------------------------------------------
{
  const s = section('5. Generated code verifiable');
  const cmds = new Map();
  for (const t of tasks) {
    for (const e of t.evidence ?? []) {
      if (e.command && ['test', 'check'].includes(e.type) && !e.command.startsWith('orkestr ')) cmds.set(e.command, { task: t.id, result: e.result });
    }
  }
  if (!cmds.size) s.fail('No executed test/check command was recorded');
  for (const [cmd, rec] of cmds) {
    if (!rerun) {
      s.info(`\`${cmd}\` — recorded ${rec.result} (${rec.task}); not re-run (use --rerun)`);
      continue;
    }
    const r = spawnSync(cmd, { cwd: root, shell: true, encoding: 'utf8', timeout: 120_000 });
    const now = r.status === 0 ? 'passed' : 'failed';
    const tail = (r.stdout + r.stderr).trim().split('\n').slice(-3).join(' | ');
    if (rec.result === 'passed' && now === 'passed') s.ok(`\`${cmd}\` re-ran and still passes`);
    else if (rec.result === 'passed') s.fail(`\`${cmd}\` was recorded as passed but now fails: ${tail}`);
    else s.info(`\`${cmd}\` recorded ${rec.result}, now ${now}`);
  }
}

// 6. Access and budget ---------------------------------------------------------
{
  const s = section('6. Within access and budget limits');
  if (hasGit) {
    const remotes = run('git', ['remote']).stdout.trim();
    remotes ? s.warn(`Git remotes exist: ${remotes} — check nothing was pushed`) : s.ok('No Git remote (nothing could be pushed)');
  }
  const pkgPath = path.join(root, 'package.json');
  if (fs.existsSync(pkgPath)) {
    const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
    const deps = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
    deps.length ? s.warn(`package.json dependencies: ${deps.join(', ')} (were they needed and free?)`) : s.ok('No npm dependencies added');
  }
  const lim = project.limits;
  for (const m of meetings) {
    if (m.participants.length > lim.max_meeting_participants || m.rounds.length > lim.max_discussion_rounds) s.fail(`${m.id} exceeds meeting limits`);
  }
  for (const t of tasks) if ((t.fix_attempts ?? 0) > lim.max_fix_attempts) s.fail(`${t.id} exceeds the fix-attempt limit`);
  const agentCallsFromMeetings = meetings.reduce((n, m) => n + m.rounds.reduce((k, r) => k + r.contributions.length, 0), 0);
  s.info(`Meeting agent calls (from records): ${agentCallsFromMeetings}`);
  s.info(`Approvals: ${approvals.length ? approvals.map((a) => `${a.id} ${a.status} (${a.action})`).join(', ') : 'none requested'}`);
  s.info('Total agent calls and token usage are not recorded by Orkestr — copy them from Claude Code (`/cost` or `/usage`) into the manual section below.');
}

// 7. Missing or unreliable -----------------------------------------------------
{
  const s = section('7. Missing or unreliable features (fill in by hand)');
  s.info('Agent calls used in the run: …');
  s.info('Usage/tokens reported by Claude Code: …');
  s.info('Did the orchestrator follow the skill steps? Where did it skip or improvise? …');
  s.info('Anything it asked you that it should have decided itself (or the reverse)? …');
  s.info('Bugs or confusing output: …');
}

out.push(`# Orkestr Phase 2 scorecard`, '', `Project: ${project.name} — ${project.goal}`, `Generated: ${new Date().toISOString()}${rerun ? ' (with --rerun)' : ''}`, '');
out.push('| Criterion | ✅ | ⚠️ | ❌ |', '| :- | -: | -: | -: |');
for (const s of sections.slice(0, 6)) out.push(`| ${s.title} | ${s.ok} | ${s.warn} | ${s.fail} |`);
for (const s of sections) out.push('', `## ${s.title}`, '', ...s.lines);
const text = out.join('\n') + '\n';
fs.writeFileSync(path.join(root, 'phase2-scorecard.md'), text);
console.log(text);
process.exitCode = sections.some((s) => s.fail) ? 1 : 0;
