// Core constants, errors, secret redaction, and safe file storage for the orkestr state tool.
// Node.js standard library only. No network access.

import fs from 'node:fs';
import path from 'node:path';

// The data directory name is defined here and nowhere else (see MASTER_SPEC §2.4).
export const DATA_DIR_NAME = '.orkestr';
export const SCHEMA_VERSION = 1;

export const DEFAULT_LIMITS = Object.freeze({
  max_meeting_participants: 4,
  max_discussion_rounds: 2,
  max_fix_attempts: 3,
  max_recovery_meetings: 1,
  max_parallel_tasks: 2,
});

// "bounded": routine work proceeds without founder approval (MASTER_SPEC §10.1 Level 2).
// "supervised": plans and decisions wait for the founder (Level 1). Records without the
// field (created before autonomy existed) are treated as "supervised", the safer reading.
export const AUTONOMY_MODES = Object.freeze(['bounded', 'supervised']);
export const DEFAULT_AUTONOMY = 'bounded';

export const AGENT_ROLES = Object.freeze([
  'product-manager',
  'system-architect',
  'tech-lead',
  'backend-engineer',
  'frontend-engineer',
  'ml-engineer',
  'data-engineer',
  'qa-engineer',
  'security-reviewer',
  'researcher',
]);
export const OWNERS = Object.freeze([...AGENT_ROLES, 'orchestrator', 'founder']);

export const PREFIX = Object.freeze({
  task: 'T',
  meeting: 'M',
  decision: 'D',
  approval: 'A',
  review: 'R',
  retro: 'RT',
});

export const LOCK_STALE_MS = 10 * 60 * 1000;

export class OrkestrError extends Error {
  constructor(message, code = 'E_INVALID') {
    super(message);
    this.code = code;
  }
}

export function now() {
  return new Date().toISOString();
}

export function formatId(prefix, n) {
  return `${prefix}-${String(n).padStart(3, '0')}`;
}

export function idRegex(prefix) {
  return new RegExp(`^${prefix}-\\d{3,}$`);
}

export function isId(prefix, value) {
  return typeof value === 'string' && idRegex(prefix).test(value);
}

// ---------------------------------------------------------------------------
// Secret redaction. A safety net, not a guarantee (MASTER_SPEC §11.2).

const SECRET_PATTERNS = [
  ['private-key', /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)/g],
  ['api-key', /\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}/g],
  ['github-token', /\bgh[pousr]_[A-Za-z0-9]{20,}/g],
  ['github-token', /\bgithub_pat_[A-Za-z0-9_]{20,}/g],
  ['aws-access-key', /\bAKIA[0-9A-Z]{16}\b/g],
  ['slack-token', /\bxox[abprs]-[A-Za-z0-9-]{10,}/g],
  ['google-api-key', /\bAIza[0-9A-Za-z_-]{35}\b/g],
  ['bearer-token', /\bBearer\s+[A-Za-z0-9._~+/=-]{12,}/g],
];

const ASSIGNMENT =
  /\b(password|passwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)\b(\s*[:=]\s*)(["']?)([^\s"',;]{6,})\3/gi;

const REDACTED = '[REDACTED]';

export function redactString(text) {
  let out = text;
  for (const [, re] of SECRET_PATTERNS) out = out.replace(re, REDACTED);
  out = out.replace(ASSIGNMENT, (match, key, sep, quote, value) =>
    value === REDACTED ? match : `${key}${sep}${quote}${REDACTED}${quote}`,
  );
  return out;
}

export function redact(value) {
  if (typeof value === 'string') return redactString(value);
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = redact(v);
    return out;
  }
  return value;
}

export function findSecrets(text) {
  const found = new Set();
  for (const [name, re] of SECRET_PATTERNS) {
    re.lastIndex = 0;
    if (re.test(text)) found.add(name);
    re.lastIndex = 0;
  }
  ASSIGNMENT.lastIndex = 0;
  for (const m of text.matchAll(ASSIGNMENT)) {
    if (m[4] !== REDACTED) found.add('secret-assignment');
  }
  ASSIGNMENT.lastIndex = 0;
  return [...found];
}

// ---------------------------------------------------------------------------
// Storage

export function dataDir(root) {
  return path.join(root, DATA_DIR_NAME);
}

export function paths(root) {
  const dir = dataDir(root);
  return {
    dir,
    project: path.join(dir, 'project.json'),
    tasks: path.join(dir, 'tasks.json'),
    approvals: path.join(dir, 'approvals.json'),
    history: path.join(dir, 'history.jsonl'),
    lock: path.join(dir, '.lock'),
    meetings: path.join(dir, 'meetings'),
    decisions: path.join(dir, 'decisions'),
    reviews: path.join(dir, 'reviews'),
    retros: path.join(dir, 'retros'),
    memory: path.join(dir, 'memory'),
    drafts: path.join(dir, 'drafts'),
  };
}

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

// Sync folders (e.g. OneDrive) and antivirus tools can briefly lock files on Windows.
function retryOnBusy(fn) {
  const delays = [20, 50, 100, 200, 400];
  for (let i = 0; ; i++) {
    try {
      return fn();
    } catch (err) {
      if (i >= delays.length || !['EPERM', 'EBUSY', 'EACCES'].includes(err.code)) throw err;
      sleepMs(delays[i]);
    }
  }
}

export function readJson(file, root) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') {
      throw new OrkestrError(`Missing file: ${display(file, root)}`, 'E_MISSING');
    }
    throw err;
  }
  try {
    return JSON.parse(text.replace(/^﻿/, ''));
  } catch (err) {
    throw new OrkestrError(`Corrupted JSON in ${display(file, root)}: ${err.message}`, 'E_CORRUPT');
  }
}

export function display(file, root) {
  return root ? path.relative(root, file).split(path.sep).join('/') : file;
}

export function writeTextAtomic(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, text, 'utf8');
  retryOnBusy(() => fs.renameSync(tmp, file));
}

export function writeJsonAtomic(file, data) {
  writeTextAtomic(file, `${JSON.stringify(redact(data), null, 2)}\n`);
}

export function appendHistory(root, events) {
  if (!events.length) return;
  const lines = events.map((e) => JSON.stringify(redact(e))).join('\n');
  retryOnBusy(() => fs.appendFileSync(paths(root).history, `${lines}\n`, 'utf8'));
}

export function withLock(root, fn) {
  const lockFile = paths(root).lock;
  const acquire = () => {
    const fd = fs.openSync(lockFile, 'wx');
    fs.writeSync(fd, JSON.stringify({ pid: process.pid, at: now() }));
    fs.closeSync(fd);
  };
  try {
    acquire();
  } catch (err) {
    if (err.code !== 'EEXIST') throw err;
    const age = Date.now() - fs.statSync(lockFile).mtimeMs;
    if (age < LOCK_STALE_MS) {
      throw new OrkestrError(
        `Another orkestr command holds the lock (${display(lockFile, root)}). ` +
          'If no orkestr command is running, the lock expires after 10 minutes.',
        'E_LOCKED',
      );
    }
    fs.rmSync(lockFile, { force: true });
    acquire();
  }
  try {
    return fn();
  } finally {
    fs.rmSync(lockFile, { force: true });
  }
}
