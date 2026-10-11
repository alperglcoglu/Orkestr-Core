// File-scope rules for parallel implementation (MASTER_SPEC §5.3).
// A task's scope is a list of path patterns relative to the project root:
//   "src/api/**"   everything under src/api
//   "src/api"      the file or directory src/api (and everything under it)
//   "src/*.js"     .js files directly in src
// An empty scope means "exclusive": the task may touch anything, so it runs alone.
// Overlap detection is deliberately conservative: when in doubt, patterns overlap.
// Matching is case-insensitive because Windows and macOS file systems usually are.

const WILDCARD = /[*?[\]{}]/;

export function normalizePattern(p) {
  return p.trim().replace(/\\/g, '/').replace(/^\.\/+/, '').replace(/\/+/g, '/').replace(/\/$/, '').toLowerCase();
}

/** Returns a list of problems with a scope list (empty when valid). */
export function scopeProblems(scope) {
  if (scope === undefined) return [];
  if (!Array.isArray(scope) || !scope.every((s) => typeof s === 'string')) return ['"scope" must be a list of path patterns'];
  const problems = [];
  for (const raw of scope) {
    const p = raw.trim().replace(/\\/g, '/');
    if (!p) problems.push('scope patterns must not be empty');
    else if (/^([a-zA-Z]:|\/)/.test(p)) problems.push(`scope "${raw}" must be relative to the project root`);
    else if (p.split('/').includes('..')) problems.push(`scope "${raw}" must not contain ".."`);
    else if (/^\.orkestr(\/|$)/i.test(p)) problems.push(`scope "${raw}" must not include .orkestr/ (state changes go through the state tool)`);
  }
  return problems;
}

function literalSegments(pattern) {
  const out = [];
  for (const seg of normalizePattern(pattern).split('/')) {
    if (WILDCARD.test(seg)) break;
    out.push(seg);
  }
  return out;
}

function isPrefix(a, b) {
  return a.length <= b.length && a.every((seg, i) => seg === b[i]);
}

/** Two patterns overlap if the literal part of one is a path prefix of the other's literal part. */
export function patternsOverlap(a, b) {
  const la = literalSegments(a);
  const lb = literalSegments(b);
  if (la.length === 0 || lb.length === 0) return true;
  return isPrefix(la, lb) || isPrefix(lb, la);
}

export function scopesOverlap(a = [], b = []) {
  if (a.length === 0 || b.length === 0) return true;
  return a.some((x) => b.some((y) => patternsOverlap(x, y)));
}

function patternRegex(pattern) {
  const p = normalizePattern(pattern);
  if (!WILDCARD.test(p)) {
    const lit = p.replace(/[.+^$()|\\]/g, '\\$&');
    return new RegExp(`^${lit}(/.*)?$`);
  }
  let re = '';
  for (let i = 0; i < p.length; i++) {
    const c = p[i];
    if (c === '*' && p[i + 1] === '*') {
      re += '.*';
      i++;
      if (p[i + 1] === '/') i++;
    } else if (c === '*') re += '[^/]*';
    else if (c === '?') re += '[^/]';
    else re += c.replace(/[.+^$()|\\[\]{}]/g, '\\$&');
  }
  return new RegExp(`^${re}$`);
}

export function fileInScope(file, scope = []) {
  if (scope.length === 0) return true;
  const f = normalizePattern(file);
  return scope.some((p) => patternRegex(p).test(f));
}
