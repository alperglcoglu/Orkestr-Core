// Token handling and requests to the local API (PHASE4_IMPLEMENTATION.md §7.1).
// `orkestr-core dashboard` opens the page with the token in the URL fragment, which browsers
// never send to a server. It moves to sessionStorage (this tab only) and leaves the address bar.

import { tokenFromHash } from './format.js';

const KEY = 'orkestr-token';
let token = null;

export function initToken() {
  const fromHash = tokenFromHash(location.hash);
  if (fromHash) {
    token = fromHash;
    try {
      sessionStorage.setItem(KEY, fromHash);
    } catch {
      // Storage can be unavailable; the token then lives in memory for this page only.
    }
    history.replaceState(null, '', location.pathname + location.search);
    return token;
  }
  try {
    token = sessionStorage.getItem(KEY);
  } catch {
    token = null;
  }
  return token;
}

export function setToken(value) {
  token = value.trim();
  try {
    sessionStorage.setItem(KEY, token);
  } catch {
    // memory only
  }
}

export function forgetToken() {
  token = null;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // nothing stored
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  const headers = { 'X-Orkestr-Token': token ?? '' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store' });
  } catch {
    throw new ApiError(0, 'The core is not reachable. Is `orkestr-core dashboard` still running?');
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const text = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : res.statusText;
    throw new ApiError(res.status, text || `HTTP ${res.status}`);
  }
  return data;
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body ?? {}),
  put: (path, body) => request('PUT', path, body),
};
