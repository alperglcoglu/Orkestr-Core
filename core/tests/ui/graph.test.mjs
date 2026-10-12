// Dashboard graph layout (PHASE4_IMPLEMENTATION.md §7.2, §9). Run: node --test "core/tests/ui/*.test.mjs"
import test from 'node:test';
import assert from 'node:assert/strict';
import { layers, edges, crossings, order, layout, related, NODE_W, GAP_X } from '../../orkestr_core/ui/graph.js';

const n = (id, ...depends_on) => ({ id, depends_on });

test('a chain becomes one task per layer', () => {
  assert.deepEqual(layers([n('T-001'), n('T-002', 'T-001'), n('T-003', 'T-002')]), [['T-001'], ['T-002'], ['T-003']]);
});

test('a diamond puts the join after its longest path', () => {
  const g = [n('A'), n('B', 'A'), n('C', 'A'), n('D', 'B', 'C'), n('E', 'A', 'D')];
  assert.deepEqual(layers(g), [['A'], ['B', 'C'], ['D'], ['E']]);
});

test('independent tasks share the first layer', () => {
  assert.deepEqual(layers([n('A'), n('B'), n('C')]), [['A', 'B', 'C']]);
});

test('dependencies on unknown tasks are ignored', () => {
  assert.deepEqual(layers([n('A', 'T-404')]), [['A']]);
  assert.deepEqual(edges([n('A', 'T-404')]), []);
});

test('a cycle is rejected', () => {
  assert.throws(() => layers([n('A', 'B'), n('B', 'A')]), /cycle/);
  assert.throws(() => layers([n('A', 'A')]), /cycle/);
});

test('ordering removes avoidable crossings', () => {
  // A→Y and B→X cross in the given order; swapping the second layer fixes it.
  const g = [n('A'), n('B'), n('X', 'B'), n('Y', 'A')];
  assert.equal(crossings(['A', 'B'], ['X', 'Y'], edges(g)), 1);
  const { layers: ls, crossings: c } = order(g);
  assert.equal(c, 0);
  assert.deepEqual(ls[1], ['Y', 'X']);
});

test('a wide graph keeps every task and never adds crossings', () => {
  const g = [];
  for (let i = 0; i < 12; i++) g.push(n(`R${i}`));
  for (let i = 0; i < 12; i++) g.push(n(`C${i}`, `R${11 - i}`));
  const { layers: ls, crossings: c } = order(g);
  assert.equal(ls.flat().length, 24);
  assert.equal(c, 0);
});

test('layout places layers left to right and draws one link per dependency', () => {
  const g = [n('A'), n('B', 'A'), n('C', 'A')];
  const { positions, links, width, height } = layout(g);
  assert.equal(positions.get('B').x - positions.get('A').x, NODE_W + GAP_X);
  assert.equal(positions.get('B').x, positions.get('C').x);
  assert.notEqual(positions.get('B').y, positions.get('C').y);
  assert.equal(links.length, 2);
  assert.match(links[0].d, /^M\d+(\.\d+)? \d+(\.\d+)? C/);
  assert.ok(width > 2 * NODE_W && height > 0);
});

test('an empty graph has a size and no links', () => {
  const { links, width, height } = layout([]);
  assert.deepEqual(links, []);
  assert.ok(width > 0 && height > 0);
});

test('related finds everything upstream and downstream', () => {
  const g = [n('A'), n('B', 'A'), n('C', 'B'), n('D')];
  const r = related(g, 'B');
  assert.deepEqual([...r.upstream], ['A']);
  assert.deepEqual([...r.downstream], ['C']);
  assert.equal(related(g, 'D').upstream.size, 0);
});
