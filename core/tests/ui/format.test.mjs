// Dashboard formatting helpers. Run: node --test "core/tests/ui/*.test.mjs"
import test from 'node:test';
import assert from 'node:assert/strict';
import * as f from '../../orkestr_core/ui/format.js';

test('labels and tones', () => {
  assert.equal(f.label('IN_PROGRESS'), 'In progress');
  assert.equal(f.label('WAITING_FOUNDER'), 'Waiting founder');
  assert.equal(f.tone('COMPLETED'), 'good');
  assert.equal(f.tone('WAITING_FOUNDER'), 'warn');
  assert.equal(f.tone('FAILED'), 'bad');
  assert.equal(f.tone('something new'), 'muted');
});

test('durations', () => {
  assert.equal(f.duration(null), '—');
  assert.equal(f.duration(119), '119 ms');
  assert.equal(f.duration(5879), '5.9 s');
  assert.equal(f.duration(42_000), '42 s');
  assert.equal(f.duration(185_000), '3 min 5 s');
  assert.equal(f.duration(3_720_000), '1 h 2 min');
  assert.equal(f.between('2026-10-12T16:21:00.000Z', '2026-10-12T16:21:02.500Z'), 2500);
  assert.equal(f.between(null, 'x'), null);
});

test('relative time', () => {
  const now = Date.parse('2026-10-12T12:00:00Z');
  assert.equal(f.ago('2026-10-12T11:59:30Z', now), '30 s ago');
  assert.equal(f.ago('2026-10-12T11:00:00Z', now), '1 h ago');
  assert.equal(f.ago('garbage', now), '—');
});

test('tokens, cost, and meters', () => {
  assert.equal(f.tokens(950), '950');
  assert.equal(f.tokens(24_344), '24k');
  assert.equal(f.tokens(386_803), '387k');
  assert.equal(f.tokens(2_500_000), '2.5M');
  assert.equal(f.cost(0.6718), '$0.672');
  assert.equal(f.cost(12.5), '$12.50');
  assert.equal(f.cost(null), '—');
  assert.deepEqual(f.meter(11, 20), { text: '11 / 20', ratio: 0.55, over: false });
  assert.equal(f.meter(30, 30).over, true);
  assert.equal(f.meter(3, 0).ratio, 0);
});

test('board keeps the state order and unknown states', () => {
  const cols = f.board([{ id: 'T-1', status: 'READY' }, { id: 'T-2', status: 'COMPLETED' }, { id: 'T-3', status: 'ODD' }]);
  assert.deepEqual(cols.slice(0, 8).map((c) => c.status), f.TASK_STATES);
  assert.deepEqual(cols.find((c) => c.status === 'READY').items.map((t) => t.id), ['T-1']);
  assert.equal(cols.at(-1).status, 'ODD');
});

test('clip', () => {
  assert.equal(f.clip('short'), 'short');
  assert.equal(f.clip('x'.repeat(10), 5), 'xxxx…');
  assert.equal(f.clip(null), '');
});

test('policy editor parsing', () => {
  assert.deepEqual(f.parsePolicy('{"protected_paths": ["LICENSE"]}'), { policy: { protected_paths: ['LICENSE'] } });
  assert.match(f.parsePolicy('[]').error, /JSON object/);
  assert.match(f.parsePolicy('{oops').error, /Not valid JSON/);
});

test('the token is read from the fragment only when it looks like one', () => {
  assert.equal(f.tokenFromHash('#token=abcdefghijklmnopqrstuvwxyz_-0123'), 'abcdefghijklmnopqrstuvwxyz_-0123');
  assert.equal(f.tokenFromHash('#token=short'), null);
  assert.equal(f.tokenFromHash('#token=<script>alert(1)</script>xxxxxxxx'), null);
  assert.equal(f.tokenFromHash('#/tasks'), null);
  assert.equal(f.tokenFromHash(''), null);
});
