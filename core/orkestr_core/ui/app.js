// Orkestr founder dashboard (PHASE4_IMPLEMENTATION.md §7). Plain ES modules, no build step.
// Every value from the API is untrusted and reaches the page only as text (see dom.js).

import { api, ApiError, forgetToken, initToken, setToken } from './api.js';
import { add, h, mount, svg } from './dom.js';
import * as f from './format.js';
import { layout, NODE_H, NODE_W, related } from './graph.js';

const VIEWS = [
  ['overview', 'Overview'],
  ['tasks', 'Tasks'],
  ['graph', 'Graph'],
  ['runs', 'Runs'],
  ['meetings', 'Meetings'],
  ['team', 'Team'],
  ['policy', 'Policy'],
  ['report', 'Report'],
];
const ACTIVE_POLL_MS = 2000;
const IDLE_POLL_MS = 15000;
const FEED_MAX = 200;

const ui = {
  main: document.getElementById('main'),
  tabs: document.getElementById('tabs'),
  name: document.getElementById('project-name'),
  status: document.getElementById('topbar-status'),
  toasts: document.getElementById('toasts'),
  dialog: document.getElementById('dialog'),
};

const state = {
  project: null,
  view: 'overview',
  selectedTask: null,
  selectedRun: null,
  selectedMeeting: null,
  graphFocus: null,
  feed: [],
  feedAfter: null,
  timer: null,
  rendering: false,
};

// ---------------------------------------------------------------------------------------------
// Small UI helpers

function badge(status, text) {
  return h('span', { class: ['badge', `tone-${f.tone(status)}`] }, text ?? f.label(status));
}

function toast(message, error = false) {
  const el = h('div', { class: ['toast', error && 'error'] }, message);
  ui.toasts.append(el);
  setTimeout(() => el.remove(), error ? 9000 : 4500);
}

function section(title, ...children) {
  return h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', null, title)), ...children);
}

function empty(text) {
  return h('p', { class: 'empty' }, text);
}

function kv(pairs) {
  return h('dl', { class: 'kv' }, pairs.filter(Boolean).flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v ?? '—')]));
}

function bullets(items, fallback = '(none)') {
  return items?.length ? h('ul', { class: 'bullets' }, items.map((x) => h('li', null, x))) : h('p', { class: 'muted' }, fallback);
}

function meterView(label, used, limit) {
  const m = f.meter(used, limit);
  return h(
    'div',
    { class: 'meter' },
    h('div', { class: 'row spread small' }, h('span', { class: 'muted' }, label), h('span', { class: 'mono' }, m.text)),
    h('div', { class: 'meter-bar' }, h('div', { class: ['meter-fill', m.over && 'over'], 'data-ratio': m.ratio.toFixed(3) })),
  );
}

// Meter widths are set from script, not from inline style attributes (CSP has no 'unsafe-inline').
function sizeMeters(root) {
  for (const el of root.querySelectorAll('.meter-fill[data-ratio]')) el.style.width = `${Number(el.dataset.ratio) * 100}%`;
}

async function act(label, fn, { refreshAfter = true } = {}) {
  try {
    const res = await fn();
    toast(res?.message ?? `${label}: done`);
    if (refreshAfter) await refresh(true);
    return res;
  } catch (err) {
    toast(`${label}: ${err.message}`, true);
    return null;
  }
}

/** A modal form. Resolves to the field values, or null when cancelled. */
function ask({ title, text, fields = [], confirm = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    const inputs = {};
    const error = h('p', { class: 'error-text' });
    const body = h(
      'form',
      { class: 'dialog-body', method: 'dialog' },
      h('h2', { id: 'dialog-title' }, title),
      text ? h('p', { class: 'muted' }, text) : null,
      fields.map((fl) => {
        let input;
        if (fl.type === 'select') {
          input = h('select', { name: fl.name }, fl.options.map((o) => h('option', { value: o.value, selected: o.value === fl.value }, o.label)));
        } else if (fl.type === 'textarea') {
          input = h('textarea', { name: fl.name, rows: 3, placeholder: fl.placeholder ?? '' });
          input.value = fl.value ?? '';
        } else {
          input = h('input', { name: fl.name, type: fl.type ?? 'text', placeholder: fl.placeholder ?? '', value: fl.value ?? '' });
        }
        inputs[fl.name] = input;
        return h('label', null, h('span', null, fl.label, fl.required ? ' *' : ''), input, fl.hint ? h('span', { class: 'hint' }, fl.hint) : null);
      }),
      error,
      h(
        'div',
        { class: 'dialog-actions' },
        h('button', { type: 'button', onclick: () => close(null) }, 'Cancel'),
        h('button', { type: 'submit', class: danger ? 'danger' : 'primary' }, confirm),
      ),
    );
    const close = (value) => {
      ui.dialog.close();
      resolve(value);
    };
    body.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const values = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value.trim()]));
      const missing = fields.filter((fl) => fl.required && !values[fl.name]).map((fl) => fl.label);
      if (missing.length) {
        error.textContent = `Required: ${missing.join(', ')}`;
        return;
      }
      close(values);
    });
    ui.dialog.onclose = () => resolve(null);
    mount(ui.dialog, body);
    ui.dialog.showModal();
    (Object.values(inputs)[0] ?? body.querySelector('button[type="submit"]')).focus();
  });
}

// ---------------------------------------------------------------------------------------------
// Founder actions (each one is a token-protected API call)

const actions = {
  async grant(id) {
    const v = await ask({ title: `Grant ${id}?`, text: 'You carry out the action yourself afterwards and record the result; the core never does it.', fields: [{ name: 'note', label: 'Note' }], confirm: 'Grant' });
    if (v) await act(`Grant ${id}`, () => api.post(`/approvals/${id}/grant`, { note: v.note || null }));
  },
  async rejectApproval(id) {
    const v = await ask({ title: `Reject ${id}?`, fields: [{ name: 'note', label: 'Reason', required: true }], confirm: 'Reject', danger: true });
    if (v) await act(`Reject ${id}`, () => api.post(`/approvals/${id}/reject`, { note: v.note }));
  },
  async approveDecision(id) {
    const d = await api.get(`/decisions/${id}`);
    const options = (d.options ?? []).map((o) => ({ value: o.id, label: `${o.id} — ${o.name}` }));
    const fields = options.length ? [{ name: 'option', label: 'Option', type: 'select', options, value: d.selected_option ?? options[0].value }] : [];
    const v = await ask({ title: `Approve ${id}?`, text: d.question, fields: [...fields, { name: 'note', label: 'Note' }], confirm: 'Approve' });
    if (v) await act(`Approve ${id}`, () => api.post(`/decisions/${id}/approve`, { option: v.option || null, note: v.note || null }));
  },
  async rejectDecision(id) {
    const v = await ask({ title: `Reject ${id}?`, text: 'Say what the team should do instead.', fields: [{ name: 'note', label: 'Reason', type: 'textarea', required: true }], confirm: 'Reject', danger: true });
    if (v) await act(`Reject ${id}`, () => api.post(`/decisions/${id}/reject`, { note: v.note }));
  },
  authorize: (id) => act(`Authorize ${id}`, () => api.post(`/tasks/${id}/authorize`)),
  async retry(id) {
    const v = await ask({ title: `Retry ${id}?`, text: 'The task becomes READY again and its fix attempts are reset.', confirm: 'Retry' });
    if (v) await act(`Retry ${id}`, () => api.post(`/tasks/${id}/retry`));
  },
  async startRun(goal, model, maxCalls) {
    const body = { goal: goal || null, model: model || null, max_agent_calls: maxCalls ? Number(maxCalls) : null };
    await act('Start run', () => api.post('/runs', body));
  },
  pause: (id) => act(`Pause run ${id}`, () => api.post(`/runs/${id}/pause`)),
  resume: (id) => act(`Resume run ${id}`, () => api.post(`/runs/${id}/resume`)),
  async cancel(id) {
    const v = await ask({ title: `Cancel run ${id}?`, text: 'Running agent calls are stopped. Tasks keep their state.', confirm: 'Cancel run', danger: true });
    if (v) await act(`Cancel run ${id}`, () => api.post(`/runs/${id}/cancel`));
  },
  async retro() {
    const v = await ask({
      title: 'Run a retrospective?',
      text: 'One read-only tech-lead agent call reviews the project history. It uses Claude Code like a run does.',
      fields: [{ name: 'scope', label: 'Scope', placeholder: 'default: the project goal' }],
      confirm: 'Start retro',
    });
    if (v) await act('Retro', () => api.post('/retros', { scope: v.scope || null }));
  },
  async autonomy(current) {
    const v = await ask({
      title: 'Change autonomy',
      fields: [
        { name: 'mode', label: 'Mode', type: 'select', value: current, options: [{ value: 'bounded', label: 'bounded — the team decides routine matters' }, { value: 'supervised', label: 'supervised — plans and decisions wait for you' }] },
        { name: 'reason', label: 'Reason', required: true },
      ],
      confirm: 'Change',
    });
    if (v) await act('Autonomy', () => api.put('/project/autonomy', v));
  },
};

// ---------------------------------------------------------------------------------------------
// Header, tabs, routing, polling

function renderTabs() {
  const needs = state.project?.needs_founder?.length ?? 0;
  mount(
    ui.tabs,
    VIEWS.map(([id, name]) =>
      h(
        'button',
        { class: 'tab', type: 'button', 'aria-current': state.view === id ? 'page' : null, onclick: () => go(id) },
        name,
        id === 'overview' && needs ? h('span', { class: 'count', 'aria-label': `${needs} need you` }, String(needs)) : null,
      ),
    ),
  );
}

function renderHeader() {
  const p = state.project;
  ui.name.textContent = p ? p.name : '';
  document.title = p ? `${p.name} — Orkestr` : 'Orkestr dashboard';
  if (!p) return mount(ui.status);
  const run = p.latest_run;
  mount(
    ui.status,
    p.active ? h('span', { class: 'live' }, run ? `Run ${run.id} running` : 'Running') : null,
    badge(p.status),
    h('span', { class: 'badge tone-muted' }, `autonomy: ${p.autonomy}`),
    h('span', { class: 'badge tone-muted' }, `policy v${p.policy_version}`),
  );
}

function go(view) {
  if (location.hash !== `#/${view}`) location.hash = `#/${view}`;
  else route();
}

function route() {
  const v = location.hash.replace(/^#\/?/, '').split('/')[0];
  state.view = VIEWS.some(([id]) => id === v) ? v : 'overview';
  renderTabs();
  renderView(true);
}

function editing() {
  const a = document.activeElement;
  return ui.dialog.open || (a && ui.main.contains(a) && ['INPUT', 'TEXTAREA', 'SELECT'].includes(a.tagName));
}

async function pollFeed() {
  const q = state.feedAfter ? `?after=${encodeURIComponent(state.feedAfter)}` : '';
  const events = await api.get(`/history${q}`);
  if (events.length) {
    state.feed = [...state.feed, ...events].slice(-FEED_MAX);
    state.feedAfter = events[events.length - 1].ts;
  }
}

async function refresh(force = false) {
  clearTimeout(state.timer);
  try {
    state.project = await api.get('/project');
    await pollFeed();
    renderHeader();
    renderTabs();
    // Views with forms are only redrawn when you navigate or act, never while you type.
    if (force || (!editing() && !['team', 'policy'].includes(state.view))) await renderView(false);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return showGate('The saved token was not accepted.');
    toast(err.message, true);
  }
  state.timer = setTimeout(() => refresh(), state.project?.active ? ACTIVE_POLL_MS : IDLE_POLL_MS);
}

async function renderView(navigated) {
  if (state.rendering) return;
  state.rendering = true;
  const scroll = window.scrollY;
  try {
    const view = await VIEW_RENDERERS[state.view]();
    mount(ui.main, view);
    sizeMeters(ui.main);
    if (navigated) {
      window.scrollTo(0, 0);
      ui.main.focus({ preventScroll: true });
    } else {
      window.scrollTo(0, scroll);
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) showGate('The saved token was not accepted.');
    else mount(ui.main, section('Something went wrong', h('p', { class: 'error-text' }, err.message)));
  } finally {
    state.rendering = false;
  }
}

function showGate(reason) {
  clearTimeout(state.timer);
  forgetToken();
  ui.name.textContent = '';
  mount(ui.status);
  mount(ui.tabs);
  const input = h('input', { type: 'password', name: 'token', autocomplete: 'off', placeholder: 'contents of .orkestr/core-token' });
  mount(
    ui.main,
    h(
      'div',
      { class: 'gate card' },
      h('h2', null, 'Connect to the core'),
      reason ? h('p', { class: 'error-text' }, reason) : null,
      h('p', { class: 'muted' }, 'Start the dashboard with `orkestr-core dashboard` from the project folder; it opens this page with the token. Or paste the token here.'),
      h(
        'form',
        {
          class: 'form',
          onsubmit: (ev) => {
            ev.preventDefault();
            if (!input.value.trim()) return;
            setToken(input.value);
            start();
          },
        },
        h('label', null, 'API token', input),
        h('div', { class: 'row' }, h('button', { class: 'primary', type: 'submit' }, 'Connect')),
      ),
    ),
  );
}

// ---------------------------------------------------------------------------------------------
// Overview

function needsList(needs) {
  if (!needs.length) return empty('Nothing needs you right now.');
  const buttons = {
    approval: (n) => [h('button', { class: 'small primary', onclick: () => actions.grant(n.id) }, 'Grant'), h('button', { class: 'small danger', onclick: () => actions.rejectApproval(n.id) }, 'Reject')],
    decision: (n) => [h('button', { class: 'small primary', onclick: () => actions.approveDecision(n.id) }, 'Approve'), h('button', { class: 'small danger', onclick: () => actions.rejectDecision(n.id) }, 'Reject')],
    authorize: (n) => [h('button', { class: 'small primary', onclick: () => actions.authorize(n.id) }, 'Authorize')],
    retry: (n) => [h('button', { class: 'small', onclick: () => actions.retry(n.id) }, 'Retry')],
    founder_task: (n) => [h('button', { class: 'small', onclick: () => openTask(n.id) }, 'Open task')],
    run: () => [],
  };
  const kindLabel = { approval: 'Approval', decision: 'Decision', authorize: 'Authorize task', retry: 'Failed task', founder_task: 'Your task', run: 'Run' };
  return h(
    'ul',
    { class: 'list' },
    needs.map((n) =>
      h(
        'li',
        null,
        h('div', { class: 'row spread' }, h('div', { class: 'row' }, badge('PENDING', kindLabel[n.kind] ?? n.kind), h('span', { class: 'id' }, n.id)), h('div', { class: 'row' }, (buttons[n.kind] ?? (() => []))(n))),
        h('p', { class: 'title-line' }, n.text),
      ),
    ),
  );
}

function runCard(p) {
  const run = p.latest_run;
  const usage = p.usage ?? {};
  const callLimit = run ? JSON.parse(run.options_json ?? '{}')?.max_agent_calls ?? p.limits.max_agent_calls_per_run : p.limits.max_agent_calls_per_run;
  const card = section(run ? `Run ${run.id}` : 'Runs');
  if (run) {
    add(card, 
      h('div', { class: 'row' }, badge(run.status), h('span', { class: 'muted small' }, `${run.kind} · started ${f.ago(run.started_at)}`)),
      run.status_reason ? h('p', null, run.status_reason) : null,
      meterView('Agent calls', run.agent_calls, callLimit),
      h(
        'div',
        { class: 'stats' },
        h('div', { class: 'stat' }, h('div', { class: 'stat-value' }, f.tokens(usage.input_tokens)), h('div', { class: 'stat-label' }, 'input tokens')),
        h('div', { class: 'stat' }, h('div', { class: 'stat-value' }, f.tokens(usage.output_tokens)), h('div', { class: 'stat-label' }, 'output tokens')),
        h('div', { class: 'stat' }, h('div', { class: 'stat-value' }, f.cost(usage.reported_cost_usd)), h('div', { class: 'stat-label' }, 'reported cost (estimate)')),
      ),
    );
  } else {
    add(card, empty('No run yet.'));
  }
  if (p.active && run) {
    add(card, 
      h(
        'div',
        { class: 'row' },
        h('button', { onclick: () => actions.pause(run.id) }, 'Pause after this step'),
        h('button', { class: 'danger', onclick: () => actions.cancel(run.id) }, 'Cancel run'),
        h('button', { class: 'link', onclick: () => go('runs') }, 'Watch steps →'),
      ),
    );
    return card;
  }
  const canResume = run && ['PAUSED', 'WAITING_FOUNDER'].includes(run.status);
  const goal = h('textarea', {
    rows: 2,
    required: !canResume,
    placeholder: canResume ? `Leave empty to continue run ${run.id}; a new request starts a new run` : 'What should the team do next?',
  });
  const model = h('input', { placeholder: 'default', value: '' });
  const calls = h('input', { type: 'number', min: 1, max: 200, placeholder: String(p.limits.max_agent_calls_per_run ?? 30) });
  add(card, 
    h(
      'form',
      {
        class: 'form',
        onsubmit: (ev) => {
          ev.preventDefault();
          actions.startRun(goal.value.trim(), model.value.trim(), calls.value.trim());
        },
      },
      h('label', null, 'New request', goal),
      h('div', { class: 'form-row' }, h('label', null, 'Model', model), h('label', null, 'Agent-call limit', calls)),
      h('p', { class: 'warn-note' }, 'A run calls Claude Code. With a subscription it counts against your plan; with an API key every call costs money.'),
      h(
        'div',
        { class: 'row' },
        h('button', { class: 'primary', type: 'submit' }, canResume ? 'Continue or start' : 'Start run'),
        canResume && run.status === 'PAUSED' ? h('button', { type: 'button', onclick: () => actions.resume(run.id) }, `Resume run ${run.id}`) : null,
        h('button', { type: 'button', onclick: () => actions.retro() }, 'Retrospective'),
      ),
    ),
  );
  return card;
}

async function overview() {
  const p = state.project;
  const tasks = await api.get('/tasks');
  const counts = f.board(tasks).filter((c) => c.items.length);
  return h(
    'div',
    { class: 'grid' },
    h('div', { class: ['card', 'span-7', p.needs_founder.length && 'attention'] }, h('div', { class: 'card-head' }, h('h2', null, 'Needs you'), h('span', { class: 'muted small' }, `${p.needs_founder.length} item(s)`)), needsList(p.needs_founder)),
    h('div', { class: 'span-5' }, runCard(p)),
    h(
      'div',
      { class: 'span-7' },
      section(
        'Project',
        h('p', null, p.goal),
        kv([
          ['Status', h('span', { class: 'row' }, badge(p.status), p.status_reason ? h('span', { class: 'muted' }, p.status_reason) : null)],
          ['Autonomy', h('span', { class: 'row' }, p.autonomy, h('button', { class: 'link', onclick: () => actions.autonomy(p.autonomy) }, 'change'))],
          ['Policy', h('span', { class: 'row' }, `version ${p.policy_version}`, h('button', { class: 'link', onclick: () => go('policy') }, 'open'))],
          p.constraints.length ? ['Constraints', bullets(p.constraints)] : null,
          p.success_criteria.length ? ['Success criteria', bullets(p.success_criteria)] : null,
        ]),
      ),
    ),
    h(
      'div',
      { class: 'span-5' },
      section(
        'Tasks',
        counts.length
          ? h('div', { class: 'stats' }, counts.map((c) => h('div', { class: 'stat' }, h('div', { class: 'stat-value' }, String(c.items.length)), h('div', { class: 'stat-label' }, badge(c.status)))))
          : empty('No tasks yet. Start a run to plan the goal.'),
        tasks.length ? h('button', { class: 'link', onclick: () => go('tasks') }, 'Open the board →') : null,
      ),
    ),
    h('div', { class: 'span-12' }, section('Recent activity', feedList(state.feed.slice(-10).reverse()))),
  );
}

function feedList(events) {
  if (!events.length) return empty('No activity yet.');
  return h(
    'ul',
    { class: 'feed' },
    events.map((e) =>
      h(
        'li',
        null,
        h('time', { datetime: e.ts, title: e.ts }, f.ago(e.ts)),
        h('div', null, h('div', null, h('span', { class: 'ev-type' }, e.type), ' ', e.entity ? h('span', { class: 'id' }, e.entity) : null, e.to ? [' → ', badge(e.to)] : null), e.summary ? h('div', null, f.clip(e.summary, 300)) : null),
      ),
    ),
  );
}

// ---------------------------------------------------------------------------------------------
// Tasks

function openTask(id) {
  state.selectedTask = id;
  go('tasks');
}

async function taskDrawer(id) {
  let t;
  try {
    t = await api.get(`/tasks/${id}`);
  } catch {
    state.selectedTask = null;
    return null;
  }
  const tests = await api.get(`/tasks/${id}/tests`);
  const buttons = [];
  if (!t.authorized && !['COMPLETED', 'CANCELLED'].includes(t.status)) buttons.push(h('button', { class: 'primary', onclick: () => actions.authorize(t.id) }, 'Authorize'));
  if (t.status === 'FAILED') buttons.push(h('button', { onclick: () => actions.retry(t.id) }, 'Retry'));
  return h(
    'aside',
    { class: 'card drawer', 'aria-label': `Task ${t.id}` },
    h('div', { class: 'card-head' }, h('div', { class: 'row' }, h('span', { class: 'id' }, t.id), badge(t.status)), h('button', { class: 'small', onclick: () => { state.selectedTask = null; renderView(false); } }, 'Close')),
    h('h2', null, t.title),
    t.description ? h('p', null, t.description) : null,
    kv([
      ['Owner', t.owner],
      ['Depends on', t.depends_on.join(', ') || '—'],
      ['Scope', t.scope.length ? h('span', { class: 'mono' }, t.scope.join('  ')) : 'not limited'],
      ['Verify', t.verify.length ? h('span', { class: 'mono' }, t.verify.join('  ')) : '—'],
      ['Fix attempts', String(t.fix_attempts)],
      ['Source', `${t.source.type}${t.source.ref ? ` ${t.source.ref}` : ''}`],
      t.decision_id ? ['Decision', t.decision_id] : null,
      t.blocked_reason ? ['Blocked', t.blocked_reason] : null,
    ]),
    buttons.length ? h('div', { class: 'row' }, buttons) : null,
    t.failure ? h('div', { class: 'stack' }, h('h3', null, 'Failure'), h('p', { class: 'error-text' }, t.failure.error), h('p', { class: 'muted' }, t.failure.next_action)) : null,
    h('div', { class: 'stack' }, h('h3', null, 'Acceptance criteria'), bullets(t.acceptance_criteria)),
    h(
      'div',
      { class: 'stack' },
      h('h3', null, 'Evidence'),
      t.evidence.length
        ? h(
            'ul',
            { class: 'list' },
            t.evidence.map((e) => h('li', null, h('div', { class: 'row' }, badge(e.result), h('span', { class: 'muted small' }, e.type), e.command ? h('code', null, e.command) : null), h('p', { class: 'small' }, f.clip(e.summary, 400)))),
          )
        : empty('No evidence yet.'),
    ),
    tests.length
      ? h(
          'div',
          { class: 'stack' },
          h('h3', null, 'Test runs by the core'),
          tests.slice(-4).reverse().map((r) =>
            h('details', null, h('summary', null, h('code', null, r.command), ' ', badge(r.exit_code === 0 ? 'passed' : 'failed', r.timed_out ? 'timed out' : `exit ${r.exit_code}`), ' ', h('span', { class: 'muted small' }, f.duration(r.duration_ms))), h('pre', null, r.output_tail || '(no output)')),
          ),
        )
      : null,
    h('div', { class: 'stack' }, h('h3', null, 'Notes'), t.notes.length ? h('ul', { class: 'list' }, t.notes.map((n) => h('li', null, h('div', { class: 'row small' }, h('strong', null, n.author), h('span', { class: 'muted' }, f.ago(n.at))), h('p', { class: 'small' }, n.text)))) : empty('No notes.')),
  );
}

async function tasksView() {
  const tasks = await api.get('/tasks');
  if (!tasks.length) return section('Tasks', empty('No tasks yet. Start a run from the overview to plan the goal.'));
  const columns = f.board(tasks).filter((c) => c.items.length || ['READY', 'IN_PROGRESS', 'REVIEW', 'COMPLETED'].includes(c.status));
  const board = h(
    'div',
    { class: 'board' },
    columns.map((c) =>
      h(
        'div',
        { class: 'column' },
        h('div', { class: 'column-head' }, badge(c.status), h('span', { class: 'muted small' }, String(c.items.length))),
        c.items.map((t) =>
          h(
            'button',
            { class: ['task-card', state.selectedTask === t.id && 'selected'], type: 'button', onclick: () => { state.selectedTask = t.id; renderView(false); } },
            h('div', { class: 'row spread' }, h('span', { class: 'id' }, t.id), h('span', { class: 'muted small' }, t.owner)),
            h('div', { class: 'title-line' }, t.title),
            t.fix_attempts ? h('div', { class: 'small muted' }, `fix attempts ${t.fix_attempts}`) : null,
          ),
        ),
      ),
    ),
  );
  const drawer = state.selectedTask ? await taskDrawer(state.selectedTask) : null;
  return h('div', { class: drawer ? 'with-drawer' : null }, h('div', { class: 'card' }, h('h2', null, 'Task board'), board), drawer);
}

// ---------------------------------------------------------------------------------------------
// Graph

async function graphView() {
  const g = await api.get('/graph');
  if (!g.nodes.length) return section('Dependency graph', empty('No tasks yet.'));
  let lay;
  try {
    lay = layout(g.nodes);
  } catch (err) {
    return section('Dependency graph', h('p', { class: 'error-text' }, err.message));
  }
  const now = new Set(g.can_start_now);
  const focus = state.graphFocus && g.nodes.some((n) => n.id === state.graphFocus) ? state.graphFocus : null;
  const rel = focus ? related(g.nodes, focus) : null;
  const inFocus = (id) => !rel || id === focus || rel.upstream.has(id) || rel.downstream.has(id);
  const byId = new Map(g.nodes.map((n) => [n.id, n]));

  const pic = svg(
    'svg',
    { width: lay.width, height: lay.height, viewBox: `0 0 ${lay.width} ${lay.height}`, role: 'img', 'aria-label': `Dependency graph of ${g.nodes.length} tasks` },
    svg('defs', null, svg('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, svg('path', { class: 'g-arrow', d: 'M0 0 L10 5 L0 10 z' }))),
    lay.links.map((l) => svg('path', { class: ['g-link', rel && !(inFocus(l.from) && inFocus(l.to)) && 'dim', focus && (l.from === focus || l.to === focus) && 'hot'], d: l.d, 'marker-end': 'url(#arrow)' })),
    g.nodes.map((n) => {
      const p = lay.positions.get(n.id);
      const select = () => {
        state.graphFocus = state.graphFocus === n.id ? null : n.id;
        renderView(false);
      };
      return svg(
        'g',
        {
          class: ['g-node', `tone-${f.tone(n.status)}`, now.has(n.id) && 'now', n.id === focus && 'focus', !inFocus(n.id) && 'dim'],
          transform: `translate(${p.x} ${p.y})`,
          tabindex: 0,
          role: 'button',
          'aria-label': `${n.id} ${n.title}, ${f.label(n.status)}`,
          onclick: select,
          onkeydown: (ev) => {
            if (ev.key === 'Enter' || ev.key === ' ') {
              ev.preventDefault();
              select();
            }
          },
        },
        svg('title', null, `${n.id} — ${n.title}`),
        svg('rect', { width: NODE_W, height: NODE_H, rx: 8 }),
        svg('text', { x: 10, y: 19, class: 'g-id' }, n.id),
        svg('text', { x: NODE_W - 10, y: 19, class: 'g-status', 'text-anchor': 'end' }, f.label(n.status)),
        svg('text', { x: 10, y: 40 }, f.clip(n.title, 26)),
      );
    }),
  );

  const detail = focus ? byId.get(focus) : null;
  return h(
    'div',
    { class: 'stack' },
    h(
      'section',
      { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', null, 'Dependency graph'), h('div', { class: 'legend' }, h('span', null, h('span', { class: 'swatch' }), 'can start now'), h('span', null, 'Arrows point from a task to the tasks that wait for it.'))),
      h('div', { class: 'graph-wrap' }, pic),
      h('p', { class: 'note' }, `Select a task to highlight what it waits for and what waits for it. ${lay.crossings ? `${lay.crossings} crossing(s) could not be avoided.` : ''}`),
    ),
    detail
      ? section(
          `${detail.id} — ${detail.title}`,
          kv([
            ['Status', badge(detail.status)],
            ['Owner', detail.owner],
            ['Waits for', [...rel.upstream].join(', ') || '—'],
            ['Waited on by', [...rel.downstream].join(', ') || '—'],
            ['Scope', detail.scope.join('  ') || 'not limited'],
            detail.blocked_reason ? ['Blocked', detail.blocked_reason] : null,
          ]),
          h('button', { class: 'link', onclick: () => openTask(detail.id) }, 'Open in the task board →'),
        )
      : null,
  );
}

// ---------------------------------------------------------------------------------------------
// Runs

async function runsView() {
  const runs = await api.get('/runs');
  if (!runs.length) return section('Runs', empty('No run yet. Start one from the overview.'));
  const selected = runs.find((r) => r.id === state.selectedRun) ?? runs[0];
  const [steps, execs] = await Promise.all([api.get(`/runs/${selected.id}/steps`), api.get(`/runs/${selected.id}/executions`)]);
  const totals = execs.reduce((a, e) => ({ turns: a.turns + (e.num_turns ?? 0), inT: a.inT + (e.input_tokens ?? 0), outT: a.outT + (e.output_tokens ?? 0), cost: a.cost + (e.reported_cost_usd ?? 0) }), { turns: 0, inT: 0, outT: 0, cost: 0 });
  const live = state.project.active && selected.status === 'RUNNING';
  return h(
    'div',
    { class: 'grid' },
    h(
      'div',
      { class: 'span-4' },
      section(
        'Runs',
        h(
          'div',
          { class: 'table-wrap' },
          h(
            'table',
            null,
            h('thead', null, h('tr', null, h('th', null, 'Run'), h('th', null, 'Status'), h('th', { class: 'num' }, 'Calls'), h('th', null, 'Started'))),
            h(
              'tbody',
              null,
              runs.map((r) =>
                h(
                  'tr',
                  { class: ['selectable', r.id === selected.id && 'selected'], tabindex: 0, onclick: () => { state.selectedRun = r.id; renderView(false); }, onkeydown: (ev) => { if (ev.key === 'Enter') { state.selectedRun = r.id; renderView(false); } } },
                  h('td', null, `${r.id} `, h('span', { class: 'muted small' }, r.kind)),
                  h('td', null, badge(r.status)),
                  h('td', { class: 'num' }, String(r.agent_calls)),
                  h('td', { class: 'muted small' }, f.ago(r.started_at)),
                ),
              ),
            ),
          ),
        ),
      ),
    ),
    h(
      'div',
      { class: 'span-8 stack' },
      section(
        `Run ${selected.id}`,
        h('div', { class: 'row' }, badge(selected.status), live ? h('span', { class: 'live' }, 'live') : null, selected.goal ? h('span', null, selected.goal) : null),
        selected.status_reason ? h('p', null, selected.status_reason) : null,
        live ? h('div', { class: 'row' }, h('button', { onclick: () => actions.pause(selected.id) }, 'Pause after this step'), h('button', { class: 'danger', onclick: () => actions.cancel(selected.id) }, 'Cancel run')) : null,
        h(
          'div',
          { class: 'stats' },
          [
            [String(execs.length), 'agent calls'],
            [String(totals.turns), 'turns'],
            [f.tokens(totals.inT), 'input tokens'],
            [f.tokens(totals.outT), 'output tokens'],
            [execs.some((e) => e.reported_cost_usd !== null) ? f.cost(totals.cost) : '—', 'reported cost'],
          ].map(([v, l]) => h('div', { class: 'stat' }, h('div', { class: 'stat-value' }, v), h('div', { class: 'stat-label' }, l))),
        ),
      ),
      section(
        'Steps',
        steps.length
          ? h(
              'div',
              { class: 'table-wrap' },
              h(
                'table',
                null,
                h('thead', null, h('tr', null, h('th', null, '#'), h('th', null, 'Step'), h('th', null, 'Task'), h('th', null, 'Status'), h('th', { class: 'num' }, 'Time'), h('th', null, 'Error'))),
                h('tbody', null, steps.map((s) => h('tr', null, h('td', { class: 'mono' }, String(s.seq)), h('td', null, s.kind), h('td', { class: 'id' }, s.task_id ?? ''), h('td', null, badge(s.status)), h('td', { class: 'num' }, s.ended_at ? f.duration(f.between(s.started_at, s.ended_at)) : s.status === 'RUNNING' ? f.ago(s.started_at).replace(' ago', '') : '—'), h('td', { class: 'small' }, s.error ? f.clip(s.error, 160) : '')))),
              ),
            )
          : empty('No steps yet.'),
      ),
      section(
        'Agent calls',
        execs.length
          ? h(
              'div',
              { class: 'table-wrap' },
              h(
                'table',
                null,
                h('thead', null, h('tr', null, h('th', null, 'Role'), h('th', null, 'Task'), h('th', null, 'Model'), h('th', null, 'Status'), h('th', { class: 'num' }, 'Turns'), h('th', { class: 'num' }, 'Tokens in/out'), h('th', { class: 'num' }, 'Cost'), h('th', { class: 'num' }, 'Time'))),
                h('tbody', null, execs.map((e) => h('tr', { title: e.error ?? '' }, h('td', null, e.role), h('td', { class: 'id' }, e.task_id ?? ''), h('td', { class: 'small' }, e.model ?? 'default'), h('td', null, badge(e.status)), h('td', { class: 'num' }, String(e.num_turns ?? '—')), h('td', { class: 'num' }, `${f.tokens(e.input_tokens)} / ${f.tokens(e.output_tokens)}`), h('td', { class: 'num' }, f.cost(e.reported_cost_usd)), h('td', { class: 'num' }, f.duration(e.duration_ms))))),
              ),
            )
          : empty('No agent calls in this run.'),
      ),
      section('Activity', feedList(state.feed.slice().reverse())),
    ),
  );
}

// ---------------------------------------------------------------------------------------------
// Meetings and decisions

function meetingDetail(m, decision) {
  return h(
    'div',
    { class: 'stack' },
    section(
      `${m.id} — ${m.objective}`,
      h('div', { class: 'row' }, badge(m.outcome), decision ? badge(decision.approval_status, `${decision.id} ${f.label(decision.approval_status)}`) : null, h('span', { class: 'muted small' }, f.ago(m.date))),
      kv([
        ['Why', m.trigger],
        ['Context', m.context],
        ['Participants', m.participants.map((p) => p.role).join(', ')],
        m.task_ids?.length ? ['Tasks created', m.task_ids.join(', ')] : null,
        m.applies_to?.length ? ['Steers', m.applies_to.join(', ')] : null,
      ]),
    ),
    m.rounds.map((r) =>
      section(
        `Round ${r.round}`,
        h(
          'div',
          { class: 'round' },
          r.contributions.map((c) => h('div', { class: 'contribution' }, h('strong', null, c.role), h('p', null, c.position), c.key_points?.length ? bullets(c.key_points) : null, c.concerns?.length ? h('p', { class: 'small muted' }, `Concerns: ${c.concerns.join('; ')}`) : null)),
        ),
      ),
    ),
    section('Options and outcome', m.options?.length ? h('ul', { class: 'bullets' }, m.options.map((o) => h('li', null, h('strong', null, `${o.id} ${o.name}`), o.summary ? ` — ${o.summary}` : ''))) : empty('No options recorded.'), m.evaluation ? h('p', null, m.evaluation) : null, h('h3', null, 'Dissent'), bullets(m.dissent?.map((d) => (typeof d === 'string' ? d : `${d.role ?? ''}: ${d.position ?? d.text ?? JSON.stringify(d)}`)))),
    decision
      ? section(
          `${decision.id} — ${decision.question}`,
          kv([
            ['Status', badge(decision.approval_status)],
            ['Selected', decision.selected_option ?? '—'],
            ['Rationale', decision.rationale || '—'],
            ['Founder required', decision.founder_required ? 'yes' : 'no'],
            decision.decided_by ? ['Decided by', `${decision.decided_by}${decision.note ? ` — ${decision.note}` : ''}`] : null,
          ]),
          ['PROPOSED', 'NEEDS_FOUNDER'].includes(decision.approval_status)
            ? h('div', { class: 'row' }, h('button', { class: 'primary', onclick: () => actions.approveDecision(decision.id) }, 'Approve'), h('button', { class: 'danger', onclick: () => actions.rejectDecision(decision.id) }, 'Reject'))
            : null,
        )
      : null,
  );
}

async function meetingsView() {
  const [meetings, decisions] = await Promise.all([api.get('/meetings'), api.get('/decisions')]);
  if (!meetings.length) return section('Meetings', empty('No meetings yet. The team meets on open design choices, on design questions raised during work, and to recover failed tasks.'));
  const dById = new Map(decisions.map((d) => [d.id, d]));
  const selected = meetings.find((m) => m.id === state.selectedMeeting) ?? meetings[meetings.length - 1];
  return h(
    'div',
    { class: 'grid' },
    h(
      'div',
      { class: 'span-4' },
      section(
        'Meetings',
        h(
          'ul',
          { class: 'list' },
          meetings
            .slice()
            .reverse()
            .map((m) =>
              h(
                'li',
                null,
                h(
                  'button',
                  { class: ['task-card', m.id === selected.id && 'selected'], type: 'button', onclick: () => { state.selectedMeeting = m.id; renderView(false); } },
                  h('div', { class: 'row spread' }, h('span', { class: 'id' }, m.id), badge(m.outcome)),
                  h('div', { class: 'title-line' }, f.clip(m.objective, 120)),
                  m.decision_id ? h('div', { class: 'small' }, badge(dById.get(m.decision_id)?.approval_status, `${m.decision_id} ${f.label(dById.get(m.decision_id)?.approval_status)}`)) : null,
                ),
              ),
            ),
        ),
      ),
    ),
    h('div', { class: 'span-8' }, meetingDetail(selected, selected.decision_id ? dById.get(selected.decision_id) : null)),
  );
}

// ---------------------------------------------------------------------------------------------
// Team

async function teamView() {
  const team = await api.get('/team');
  const rows = team.map((r) => {
    const enabled = h('input', { type: 'checkbox', checked: r.enabled, disabled: r.required, 'aria-label': `${r.role} enabled` });
    const model = h('input', { value: r.model ?? '', placeholder: 'run default', 'aria-label': `${r.role} model`, size: 14 });
    const turns = h('input', { type: 'number', min: 1, max: r.definition_max_turns ?? undefined, value: r.max_turns ?? '', placeholder: String(r.definition_max_turns ?? ''), 'aria-label': `${r.role} max turns` });
    const instructions = h('textarea', { rows: 2, maxlength: 2000, placeholder: 'Extra instructions for this role', 'aria-label': `${r.role} instructions` });
    instructions.value = r.instructions ?? '';
    const save = async () => {
      const body = { enabled: enabled.checked, model: model.value.trim(), max_turns: turns.value ? Number(turns.value) : null, instructions: instructions.value.trim() };
      if (r.required) delete body.enabled;
      const res = await act(`Team ${r.role}`, () => api.put(`/team/${r.role}`, body).then(() => ({ message: `${r.role} saved; it applies when a run starts or continues.` })));
      if (res) renderView(false);
    };
    return h(
      'tr',
      null,
      h('td', null, h('strong', null, r.role), r.required ? h('div', { class: 'small muted' }, 'required') : null),
      h('td', null, enabled),
      h('td', null, model),
      h('td', null, turns),
      h('td', null, instructions),
      h('td', null, h('button', { class: 'small primary', onclick: save }, 'Save')),
    );
  });
  return section(
    'Agent team',
    h('p', { class: 'note' }, 'Changes apply when a run starts or continues. Tool permissions and limits are not part of the team; the core sets them for each step. Turn limits can only be lowered.'),
    h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, h('tr', null, h('th', null, 'Role'), h('th', null, 'On'), h('th', null, 'Model'), h('th', null, 'Max turns'), h('th', null, 'Founder instructions'), h('th', null, ''))), h('tbody', null, rows))),
  );
}

// ---------------------------------------------------------------------------------------------
// Policy

async function policyView() {
  const [current, versions] = await Promise.all([api.get('/policy'), api.get('/policy/versions')]);
  const editor = h('textarea', { class: 'code', spellcheck: 'false', 'aria-label': 'Policy JSON' });
  editor.value = JSON.stringify(current.policy, null, 2);
  const reason = h('input', { placeholder: 'Why this change', 'aria-label': 'Reason' });
  const error = h('p', { class: 'error-text' });
  const save = async (ev) => {
    ev.preventDefault();
    error.textContent = '';
    const parsed = f.parsePolicy(editor.value);
    if (parsed.error) return void (error.textContent = parsed.error);
    if (!reason.value.trim()) return void (error.textContent = 'A reason is required.');
    try {
      const v = await api.put('/policy', { policy: parsed.policy, reason: reason.value.trim() });
      toast(`Saved policy version ${v.version}; it applies when a run starts or continues.`);
      await refresh(true);
    } catch (err) {
      error.textContent = err.message.replace(/^Policy rejected: /, 'Rejected:\n').replace(/; /g, '\n');
    }
  };
  return h(
    'div',
    { class: 'grid' },
    h(
      'div',
      { class: 'span-8' },
      section(
        `Policy version ${current.version}`,
        h('p', { class: 'note' }, 'A policy can only tighten the rules, except that it can add test commands. It never removes an approval for push, deploy, publish, deletes, spending, or permission changes.'),
        h('form', { class: 'form', onsubmit: save }, editor, h('label', null, 'Reason', reason), error, h('div', { class: 'row' }, h('button', { class: 'primary', type: 'submit' }, 'Save new version'))),
      ),
    ),
    h(
      'div',
      { class: 'span-4 stack' },
      section(
        'Fields',
        kv([
          ['test_commands', 'extra test command prefixes, such as "go test"'],
          ['protected_paths', 'files no task may change'],
          ['approval_paths', 'tasks touching these wait for your approval'],
          ['security_review_paths', 'always get a security review'],
          ['limits', 'limits within the caps below'],
        ]),
        h('h3', null, 'Always allowed tests'),
        bullets(current.default_test_commands),
      ),
      section('Hard caps', kv(Object.entries(current.hard_caps).map(([k, v]) => [k, v === null ? 'any positive value' : String(v)]))),
      section('History', h('ul', { class: 'list' }, versions.slice().reverse().map((v) => h('li', null, h('div', { class: 'row spread' }, h('strong', null, `v${v.version}`), h('span', { class: 'muted small' }, f.ago(v.created_at))), h('p', { class: 'small' }, `${v.created_by}: ${v.reason}`))))),
    ),
  );
}

// ---------------------------------------------------------------------------------------------
// Report

async function reportView() {
  const [report, retros] = await Promise.all([api.get('/report'), api.get('/retros')]);
  const r = retros[retros.length - 1];
  return h(
    'div',
    { class: 'grid' },
    h('div', { class: 'span-7' }, section('Completion report', h('pre', null, report.markdown))),
    h(
      'div',
      { class: 'span-5' },
      section(
        r ? `Retrospective ${r.id}` : 'Retrospective',
        r
          ? [
              h('p', { class: 'muted small' }, `${f.ago(r.date)} · ${r.scope}`),
              h('h3', null, 'Went well'),
              bullets(r.went_well),
              h('h3', null, 'Went badly'),
              bullets(r.went_badly),
              h('h3', null, 'Lessons'),
              bullets(r.lessons.map((l) => `${l.lesson} — evidence: ${l.evidence}`)),
              h('h3', null, 'Proposed changes (not applied)'),
              bullets(r.proposed_changes),
            ]
          : empty('No retrospective yet.'),
        state.project.active ? null : h('button', { onclick: () => actions.retro() }, 'Run a retrospective'),
      ),
    ),
  );
}

const VIEW_RENDERERS = {
  overview,
  tasks: tasksView,
  graph: graphView,
  runs: runsView,
  meetings: meetingsView,
  team: teamView,
  policy: policyView,
  report: reportView,
};

// ---------------------------------------------------------------------------------------------
// Start

async function start() {
  if (!initToken()) return showGate();
  try {
    state.project = await api.get('/project');
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return showGate('The token was not accepted.');
    mount(ui.main, section('The core is not reachable', h('p', { class: 'error-text' }, err.message)));
    return;
  }
  renderHeader();
  route();
  refresh();
}

window.addEventListener('hashchange', route);
start();
