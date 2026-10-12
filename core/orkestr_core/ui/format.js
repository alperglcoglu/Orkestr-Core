// Formatting helpers for the dashboard. Pure functions, no DOM (tested with node --test).

export const TASK_STATES = ['BACKLOG', 'READY', 'IN_PROGRESS', 'BLOCKED', 'REVIEW', 'FAILED', 'COMPLETED', 'CANCELLED'];

const TONE = {
  COMPLETED: 'good',
  PASS: 'good',
  APPROVED: 'good',
  GRANTED: 'good',
  DONE: 'good',
  ok: 'good',
  passed: 'good',
  RUNNING: 'active',
  IN_PROGRESS: 'active',
  REVIEW: 'active',
  READY: 'info',
  PROPOSED: 'warn',
  PENDING: 'warn',
  NEEDS_FOUNDER: 'warn',
  WAITING_FOUNDER: 'warn',
  BLOCKED: 'warn',
  PAUSED: 'warn',
  CHANGES_REQUESTED: 'warn',
  FAILED: 'bad',
  REJECTED: 'bad',
  BLOCKED_REVIEW: 'bad',
  error: 'bad',
  failed: 'bad',
  invalid_output: 'bad',
  timeout: 'bad',
  usage_limit: 'bad',
};

/** A tone name for a status badge: good, active, info, warn, bad, or muted. */
export function tone(status) {
  return TONE[status] ?? 'muted';
}

/** "IN_PROGRESS" → "In progress". */
export function label(status) {
  if (!status) return '';
  const s = String(status).replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function duration(ms) {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s - m * 60);
  if (m < 60) return rest ? `${m} min ${rest} s` : `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${m - h * 60} min`;
}

/** Milliseconds between two ISO timestamps, or null. */
export function between(start, end) {
  if (!start || !end) return null;
  const a = Date.parse(start);
  const b = Date.parse(end);
  return Number.isNaN(a) || Number.isNaN(b) ? null : b - a;
}

export function ago(iso, now = Date.now()) {
  const t = Date.parse(iso ?? '');
  if (Number.isNaN(t)) return '—';
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function tokens(n) {
  if (n === null || n === undefined) return '—';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

export function cost(usd) {
  if (usd === null || usd === undefined) return '—';
  return `$${Number(usd).toFixed(usd < 1 ? 3 : 2)}`;
}

/** "12 / 30" with the share used, for a limit meter. */
export function meter(used, limit) {
  const u = Number(used) || 0;
  const l = Number(limit) || 0;
  return { text: l ? `${u} / ${l}` : String(u), ratio: l ? Math.min(1, u / l) : 0, over: l > 0 && u >= l };
}

/** Tasks grouped by state, in board order. */
export function board(tasks) {
  const cols = new Map(TASK_STATES.map((s) => [s, []]));
  for (const t of tasks) (cols.get(t.status) ?? cols.set(t.status, []).get(t.status)).push(t);
  return [...cols].map(([status, items]) => ({ status, items }));
}

/** Short text, never longer than `max` characters. */
export function clip(text, max = 140) {
  const s = String(text ?? '');
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}

/** Parses the policy editor text; returns { policy } or { error }. */
export function parsePolicy(text) {
  try {
    const value = JSON.parse(text);
    if (value === null || typeof value !== 'object' || Array.isArray(value)) return { error: 'The policy must be a JSON object.' };
    return { policy: value };
  } catch (err) {
    return { error: `Not valid JSON: ${err.message}` };
  }
}

/** Reads the API token from a URL fragment like "#token=abc&view=tasks". */
export function tokenFromHash(hash) {
  const params = new URLSearchParams(String(hash ?? '').replace(/^#/, ''));
  const t = params.get('token');
  return t && /^[A-Za-z0-9_-]{16,}$/.test(t) ? t : null;
}
