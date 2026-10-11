#!/usr/bin/env node
// orkestr state tool. The only writer of .orkestr/ project state.
// Usage: node orkestr.mjs <command> [subcommand] [options]   (run "help" for details)
// Node.js standard library only; no network access.

import fs from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';
import {
  AUTONOMY_MODES,
  DATA_DIR_NAME,
  DEFAULT_AUTONOMY,
  DEFAULT_LIMITS,
  OrkestrError,
  PREFIX,
  appendHistory,
  now,
  paths,
  withLock,
  writeJsonAtomic,
} from './lib/core.mjs';
import {
  addAssumption,
  addEvidence,
  addNote,
  authorize,
  autonomyOf,
  createTasks,
  decideApproval,
  emit,
  getTask,
  loadState,
  nextTask,
  parallelCandidates,
  recordApprovalResult,
  requestApproval,
  requireFounder,
  requireMutable,
  saveState,
  scopeCheck,
  setAutonomy,
  setProjectStatus,
  transition,
} from './lib/state.mjs';
import { decideDecision, loadDraft, recordMeeting, recordRetro, recordReview } from './lib/records.mjs';
import { renderReport, renderStatus } from './lib/render.mjs';
import { validateStore } from './lib/validate.mjs';

const HELP = `orkestr state tool — manages ${DATA_DIR_NAME}/ in the current project.

Project
  init --name <n> --goal <g> [--constraint <c>]... [--success <s>]... [--autonomy bounded|supervised]
  project pause --reason <r> | project resume
  project cancel --reason <r> --by founder        (founder only)
  project autonomy bounded|supervised --by founder (founder only)
  project complete [--reason <r>]
  assumption add --text <t> [--impact low|medium|high] [--task <T-id>]
Plan and tasks
  plan import <draft.json>                        bounded: tasks authorized at once; supervised: BACKLOG until approved
  plan approve --by founder [--note <n>]          (founder only) authorizes plan tasks (supervised mode)
  task add --title <t> --owner <role> --criterion <c>... [--depends-on <T-001>]...
           [--scope <path-pattern>]... [--description <d>] [--requires-approval]
  task authorize <T-id> --by founder              (founder only) authorizes a manual task
  task move <T-id> <STATE> [--reason <r>] [--error <e> --next-action <a>] [--retry --by founder]
  task note <T-id> --author <role> --text <t>     hand-off note shared between agents
  task show <T-id> | task list [--status <STATE>] | next
  next --parallel                                 READY tasks that can start together now (JSON list)
  scope check [<T-id>] --file <path>...           verify changed files against task scopes
  evidence add <T-id> --type test|check|review|manual --result passed|failed|not_run
           --summary <s> [--command <cmd>] [--ref <id>]
Meetings, decisions, reviews, retrospectives
  meeting record <draft.json>
  decision approve <D-id> --by founder [--option <id>] [--note <n>]   (founder only)
  decision reject <D-id> --by founder --note <n>                      (founder only)
  review record <draft.json>
  retro record <draft.json>
Approvals
  approval request --action <a> --category <c> --reason <r> [--task <T-id>]
           [--consequences <c>] [--risks <r>] [--requested-by <role>]
  approval grant|reject|expire|cancel <A-id> --by founder [--note <n>]  (founder only)
  approval result <A-id> --result <text>
Reports
  status            Markdown status report (blockers, failures, pending approvals)
  report            Completion report (done, checks actually run, assumptions, open issues, founder actions)
  validate          Validate every file in ${DATA_DIR_NAME}/ (exit code 1 on errors)

Global: --root <dir> (default: current directory)`;

const OPTIONS = {
  root: { type: 'string' },
  name: { type: 'string' },
  goal: { type: 'string' },
  constraint: { type: 'string', multiple: true },
  success: { type: 'string', multiple: true },
  reason: { type: 'string' },
  error: { type: 'string' },
  'next-action': { type: 'string' },
  retry: { type: 'boolean' },
  by: { type: 'string' },
  note: { type: 'string' },
  option: { type: 'string' },
  type: { type: 'string' },
  result: { type: 'string' },
  summary: { type: 'string' },
  command: { type: 'string' },
  ref: { type: 'string' },
  title: { type: 'string' },
  description: { type: 'string' },
  owner: { type: 'string' },
  criterion: { type: 'string', multiple: true },
  'depends-on': { type: 'string', multiple: true },
  'requires-approval': { type: 'boolean' },
  action: { type: 'string' },
  category: { type: 'string' },
  consequences: { type: 'string' },
  risks: { type: 'string' },
  task: { type: 'string' },
  'requested-by': { type: 'string' },
  status: { type: 'string' },
  autonomy: { type: 'string' },
  author: { type: 'string' },
  text: { type: 'string' },
  impact: { type: 'string' },
  scope: { type: 'string', multiple: true },
  file: { type: 'string', multiple: true },
  parallel: { type: 'boolean' },
  help: { type: 'boolean', short: 'h' },
};

function print(text) {
  process.stdout.write(`${text}\n`);
}

function need(value, label) {
  if (value === undefined || value === null || value === '') throw new OrkestrError(`Missing ${label}.`, 'E_USAGE');
  return value;
}

function mutate(root, fn) {
  return withLock(root, () => {
    const state = loadState(root);
    // A project handed to the standalone core is read-only here (PHASE3_IMPLEMENTATION.md §12).
    if (state.project.managed_by === 'core') {
      throw new OrkestrError(
        'This project is managed by orkestr-core; these files are read-only mirrors. ' +
          'Use orkestr-core, or run `orkestr-core export --release` to hand the project back to the plugin.',
        'E_NOT_OWNER',
      );
    }
    const result = fn(state);
    saveState(state);
    return result;
  });
}

function readHistory(file) {
  try {
    return fs
      .readFileSync(file, 'utf8')
      .split('\n')
      .filter((l) => l.trim())
      .flatMap((l) => {
        try {
          return [JSON.parse(l)];
        } catch {
          return [];
        }
      });
  } catch {
    return [];
  }
}

function cmdStatus(root, render = renderStatus) {
  if (!fs.existsSync(paths(root).project)) {
    print(`No orkestr project in ${root}. Start one with /orkestr:run or /orkestr:plan.`);
    return 0;
  }
  const v = validateStore(root);
  let state;
  try {
    state = loadState(root);
  } catch (err) {
    if (!(err instanceof OrkestrError)) throw err;
    print(`# Orkestr status — unavailable\n\nThe project state could not be read: ${err.message}\n\n## Validation\n`);
    for (const e of v.errors) print(`- ERROR: ${e}`);
    print('\nFix or restore the files above before continuing. Nothing was changed.');
    return 1;
  }
  print(
    render({
      project: state.project,
      tasks: state.tasks,
      approvals: state.approvals,
      decisions: v.decisions,
      history: readHistory(paths(root).history),
      validation: { errors: v.errors, warnings: v.warnings },
    }),
  );
  return 0;
}

function cmdInit(root, o) {
  const p = paths(root);
  if (fs.existsSync(p.project)) {
    throw new OrkestrError(`${DATA_DIR_NAME}/project.json already exists; refusing to overwrite an existing project.`, 'E_EXISTS');
  }
  if (fs.existsSync(p.dir) && fs.readdirSync(p.dir).length > 0) {
    throw new OrkestrError(`${DATA_DIR_NAME}/ exists but has no project.json; inspect it before initializing (nothing was changed).`, 'E_EXISTS');
  }
  need(o.name, '--name');
  need(o.goal, '--goal');
  const autonomy = o.autonomy ?? DEFAULT_AUTONOMY;
  if (!AUTONOMY_MODES.includes(autonomy)) {
    throw new OrkestrError(`--autonomy must be one of ${AUTONOMY_MODES.join(', ')}`, 'E_USAGE');
  }
  fs.mkdirSync(p.dir, { recursive: true });
  withLock(root, () => {
    const ts = now();
    const counters = Object.fromEntries(Object.values(PREFIX).map((k) => [k, 0]));
    writeJsonAtomic(p.tasks, { schema_version: 1, tasks: [] });
    writeJsonAtomic(p.approvals, { schema_version: 1, approvals: [] });
    fs.writeFileSync(p.history, '', 'utf8');
    writeJsonAtomic(p.project, {
      schema_version: 1,
      name: o.name.trim(),
      goal: o.goal.trim(),
      status: 'ACTIVE',
      status_reason: null,
      autonomy,
      constraints: o.constraint ?? [],
      success_criteria: o.success ?? [],
      assumptions: [],
      limits: { ...DEFAULT_LIMITS },
      counters,
      created_at: ts,
      updated_at: ts,
    });
    appendHistory(root, [
      { ts, type: 'project.init', actor: 'orchestrator', entity: 'project', from: null, to: 'ACTIVE', summary: o.name.trim(), data: null },
    ]);
  });
  print(`Initialized ${DATA_DIR_NAME}/ for "${o.name.trim()}" (autonomy: ${autonomy}).`);
}

function cmdTask(root, sub, args, o) {
  if (sub === 'show') {
    const state = loadState(root);
    print(JSON.stringify(getTask(state, need(args[0], 'task ID')), null, 2));
    return;
  }
  if (sub === 'list') {
    const state = loadState(root);
    const tasks = o.status ? state.tasks.filter((t) => t.status === o.status) : state.tasks;
    print(tasks.length ? tasks.map((t) => `${t.id}\t${t.status}\t${t.owner}\t${t.title}`).join('\n') : '(no tasks)');
    return;
  }
  if (sub === 'add') {
    const [task] = mutate(root, (s) => {
      requireMutable(s);
      return createTasks(
        s,
        [
          {
            title: o.title,
            owner: o.owner,
            description: o.description ?? '',
            acceptance_criteria: o.criterion ?? [],
            depends_on: o['depends-on'] ?? [],
            scope: o.scope ?? [],
            requires_approval: o['requires-approval'] === true,
          },
        ],
        { type: 'manual', ref: null },
      );
    });
    print(`Created ${task.id} (BACKLOG, needs founder authorization: task authorize ${task.id} --by founder).`);
    return;
  }
  if (sub === 'note') {
    const id = need(args[0], 'task ID');
    mutate(root, (s) => addNote(s, id, { author: o.author, text: o.text }));
    print(`Added a note to ${id}.`);
    return;
  }
  if (sub === 'authorize') {
    const id = need(args[0], 'task ID');
    mutate(root, (s) => {
      requireMutable(s);
      requireFounder(o.by);
      const t = getTask(s, id);
      if (t.authorized) throw new OrkestrError(`${id} is already authorized.`);
      authorize(s, [t], { actor: o.by, via: 'founder authorization' });
    });
    print(`Authorized ${id}.`);
    return;
  }
  if (sub === 'move') {
    const id = need(args[0], 'task ID');
    const to = need(args[1], 'target state');
    if (o.retry) requireFounder(o.by);
    const t = mutate(root, (s) =>
      transition(s, id, to, {
        reason: o.reason,
        error: o.error,
        nextAction: o['next-action'],
        retry: o.retry === true,
        actor: o.by ?? 'orchestrator',
      }),
    );
    print(`${id} is now ${t.status}.`);
    return;
  }
  throw new OrkestrError(`Unknown task subcommand: ${sub ?? '(none)'}`, 'E_USAGE');
}

function cmdEvidence(root, sub, args, o) {
  if (sub !== 'add') throw new OrkestrError('Usage: evidence add <T-id> ...', 'E_USAGE');
  const id = need(args[0], 'task ID');
  const r = mutate(root, (s) =>
    addEvidence(s, id, { type: o.type, result: o.result, command: o.command ?? null, summary: o.summary, ref: o.ref ?? null }),
  );
  if (r.failedTask) {
    print(`Recorded. Fix-attempt limit (${r.max}) reached: ${id} is now FAILED. Stop fixing. Next: ${r.task.failure.next_action}`);
  } else if (o.result === 'failed' && ['test', 'check'].includes(o.type) && r.task.status === 'IN_PROGRESS') {
    print(`Recorded. Fix attempt ${r.fixAttempts} of ${r.max} may now be made; a failure after attempt ${r.max} fails the task.`);
  } else {
    print(`Recorded ${o.type} evidence (${o.result}) on ${id}.`);
  }
}

function cmdApproval(root, sub, args, o) {
  if (sub === 'request') {
    const a = mutate(root, (s) =>
      requestApproval(s, {
        action: o.action,
        category: o.category,
        reason: o.reason,
        consequences: o.consequences ?? '',
        risks: o.risks ?? '',
        taskId: o.task ?? null,
        requestedBy: o['requested-by'] ?? 'orchestrator',
      }),
    );
    print(`Created ${a.id} (PENDING). Ask the founder; do not proceed until it is GRANTED.`);
    return;
  }
  if (['grant', 'reject', 'expire', 'cancel'].includes(sub)) {
    const id = need(args[0], 'approval ID');
    const a = mutate(root, (s) => decideApproval(s, id, sub, { by: o.by, note: o.note ?? null }));
    print(`${a.id} is now ${a.status}.`);
    return;
  }
  if (sub === 'result') {
    const id = need(args[0], 'approval ID');
    mutate(root, (s) => recordApprovalResult(s, id, o.result));
    print(`Recorded the execution result for ${id}.`);
    return;
  }
  throw new OrkestrError(`Unknown approval subcommand: ${sub ?? '(none)'}`, 'E_USAGE');
}

function main(argv) {
  let parsed;
  try {
    parsed = parseArgs({ args: argv, options: OPTIONS, allowPositionals: true, strict: true });
  } catch (err) {
    throw new OrkestrError(`${err.message}\nRun "help" for usage.`, 'E_USAGE');
  }
  const o = parsed.values;
  const [cmd, sub, ...rest] = parsed.positionals;
  const root = path.resolve(o.root ?? process.cwd());

  if (!cmd || cmd === 'help' || o.help) {
    print(HELP);
    return 0;
  }

  switch (cmd) {
    case 'init':
      cmdInit(root, o);
      return 0;
    case 'status':
      return cmdStatus(root);
    case 'report':
      return cmdStatus(root, renderReport);
    case 'assumption': {
      if (sub !== 'add') throw new OrkestrError('Usage: assumption add --text <t> [--impact <i>] [--task <T-id>]', 'E_USAGE');
      mutate(root, (s) => addAssumption(s, { text: o.text, impact: o.impact ?? 'low', taskId: o.task ?? null }));
      print('Recorded the assumption.');
      return 0;
    }
    case 'validate': {
      const v = validateStore(root);
      for (const e of v.errors) print(`ERROR: ${e}`);
      for (const w of v.warnings) print(`WARNING: ${w}`);
      print(
        v.errors.length
          ? `Validation failed: ${v.errors.length} error(s), ${v.warnings.length} warning(s).`
          : `Validation passed (${v.warnings.length} warning(s)).`,
      );
      return v.errors.length ? 1 : 0;
    }
    case 'next': {
      const state = loadState(root);
      if (o.parallel) {
        print(JSON.stringify(parallelCandidates(state), null, 2));
        return 0;
      }
      const t = nextTask(state);
      print(t ? JSON.stringify(t, null, 2) : 'null');
      return 0;
    }
    case 'scope': {
      if (sub !== 'check') throw new OrkestrError('Usage: scope check [<T-id>] --file <path>...', 'E_USAGE');
      const taskId = rest[0] ?? null;
      const r = taskId
        ? mutate(root, (s) => scopeCheck(s, o.file, taskId))
        : scopeCheck(loadState(root), o.file, null);
      for (const row of r.rows) {
        print(`${row.problem ? 'VIOLATION' : 'ok'}\t${row.file}\t${row.owners.join(',') || '-'}${row.problem ? `\t${row.problem}` : ''}`);
      }
      print(
        r.violations.length
          ? `Scope check failed: ${r.violations.length} file(s) outside the allowed scope.${taskId ? ' Recorded as a failed check.' : ''}`
          : `Scope check passed.${taskId ? ' Recorded as a passed check.' : ''}`,
      );
      return r.violations.length ? 1 : 0;
    }
    case 'plan': {
      if (sub === 'import') {
        const file = need(rest[0], 'draft file');
        let bounded = false;
        const created = mutate(root, (s) => {
          requireMutable(s);
          const draft = loadDraft(root, file);
          const tasks = createTasks(s, draft.tasks, { type: 'plan', ref: null });
          emit(s, { type: 'plan.import', entity: 'plan', summary: draft.summary ?? '', data: { task_ids: tasks.map((t) => t.id) } });
          bounded = autonomyOf(s) === 'bounded';
          // Bounded autonomy: the plan is the internal working plan; no founder approval needed.
          // Restricted actions inside tasks still need approval records (requires_approval).
          if (bounded) authorize(s, tasks, { actor: 'orchestrator', via: 'plan (bounded autonomy)' });
          return tasks;
        });
        const ids = created.map((t) => t.id).join(', ');
        print(
          bounded
            ? `Imported and authorized ${created.length} task(s): ${ids} (bounded autonomy). Tasks without open dependencies are READY.`
            : `Imported ${created.length} task(s): ${ids}. They stay in BACKLOG until the founder approves the plan.`,
        );
        return 0;
      }
      if (sub === 'approve') {
        const ids = mutate(root, (s) => {
          requireMutable(s);
          requireFounder(o.by);
          const pending = s.tasks.filter((t) => t.source.type === 'plan' && !t.authorized && t.status === 'BACKLOG');
          if (!pending.length) throw new OrkestrError('There are no unapproved plan tasks.');
          emit(s, { type: 'plan.approve', actor: o.by, entity: 'plan', summary: o.note ?? '', data: { task_ids: pending.map((t) => t.id) } });
          authorize(s, pending, { actor: o.by, via: 'plan approval' });
          return pending.map((t) => t.id);
        });
        print(`Plan approved; authorized ${ids.join(', ')}.`);
        return 0;
      }
      throw new OrkestrError(`Unknown plan subcommand: ${sub ?? '(none)'}`, 'E_USAGE');
    }
    case 'task':
      cmdTask(root, sub, rest, o);
      return 0;
    case 'evidence':
      cmdEvidence(root, sub, rest, o);
      return 0;
    case 'approval':
      cmdApproval(root, sub, rest, o);
      return 0;
    case 'meeting': {
      if (sub !== 'record') throw new OrkestrError('Usage: meeting record <draft.json>', 'E_USAGE');
      const r = mutate(root, (s) => recordMeeting(s, loadDraft(root, need(rest[0], 'draft file'))));
      print(
        `Recorded ${r.meeting.id} (${r.meeting.outcome}).` +
          (r.decision ? ` Decision ${r.decision.id} is ${r.decision.approval_status}.` : '') +
          (r.tasks.length
            ? ` Created ${r.tasks.map((t) => t.id).join(', ')}` +
              (r.decision?.approval_status === 'APPROVED' ? ' (authorized).' : ' (BACKLOG until the decision is approved).')
            : '') +
          (r.meeting.recovery_for ? ` Recovery for ${r.meeting.recovery_for}: see "task show ${r.meeting.recovery_for}".` : ''),
      );
      return 0;
    }
    case 'decision': {
      if (sub !== 'approve' && sub !== 'reject') throw new OrkestrError('Usage: decision approve|reject <D-id> ...', 'E_USAGE');
      const id = need(rest[0], 'decision ID');
      const r = mutate(root, (s) => decideDecision(s, id, sub, { by: o.by, option: o.option ?? null, note: o.note ?? null }));
      print(`${id} is now ${r.decision.approval_status}.`);
      return 0;
    }
    case 'review': {
      if (sub !== 'record') throw new OrkestrError('Usage: review record <draft.json>', 'E_USAGE');
      const r = mutate(root, (s) => recordReview(s, loadDraft(root, need(rest[0], 'draft file'))));
      print(`Recorded ${r.id} (${r.verdict})${r.task_id ? ` for ${r.task_id}` : ''}.`);
      return 0;
    }
    case 'retro': {
      if (sub !== 'record') throw new OrkestrError('Usage: retro record <draft.json>', 'E_USAGE');
      const r = mutate(root, (s) => recordRetro(s, loadDraft(root, need(rest[0], 'draft file'))));
      print(`Recorded ${r.id}${r.lessons.length ? `; appended ${r.lessons.length} lesson(s) to memory/lessons-learned.md` : ''}.`);
      return 0;
    }
    case 'project': {
      if (sub === 'autonomy') {
        const mode = need(rest[0], 'autonomy mode');
        mutate(root, (s) => setAutonomy(s, mode, { by: o.by }));
        print(`Autonomy is now ${mode}.`);
        return 0;
      }
      if (!['pause', 'resume', 'cancel', 'complete'].includes(sub)) {
        throw new OrkestrError('Usage: project pause|resume|cancel|complete|autonomy ...', 'E_USAGE');
      }
      const status = mutate(root, (s) => {
        setProjectStatus(s, sub, { reason: o.reason ?? null, by: o.by ?? null });
        return s.project.status;
      });
      print(`Project is now ${status}.`);
      return 0;
    }
    default:
      throw new OrkestrError(`Unknown command: ${cmd}. Run "help" for usage.`, 'E_USAGE');
  }
}

try {
  process.exitCode = main(process.argv.slice(2));
} catch (err) {
  if (err instanceof OrkestrError) {
    process.stderr.write(`orkestr: ${err.message}\n`);
    process.exitCode = err.code === 'E_USAGE' ? 2 : 1;
  } else {
    process.stderr.write(`orkestr: unexpected error: ${err.stack ?? err}\n`);
    process.exitCode = 3;
  }
}
