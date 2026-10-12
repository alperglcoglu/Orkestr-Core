// Dependency graph layout for the dashboard (PHASE4_IMPLEMENTATION.md §7.2). Pure functions,
// no DOM, so `node --test` can check them. Layers come from the longest dependency path; the
// order inside a layer is improved with a few barycenter sweeps to reduce edge crossings.

export const NODE_W = 188;
export const NODE_H = 56;
export const GAP_X = 72;
export const GAP_Y = 20;
export const PAD = 16;

/** Throws when the dependencies contain a cycle (the state tool never stores one). */
export function layers(nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const depth = new Map();
  const visiting = new Set();
  const visit = (id) => {
    if (depth.has(id)) return depth.get(id);
    if (visiting.has(id)) throw new Error(`dependency cycle through ${id}`);
    visiting.add(id);
    const deps = (byId.get(id)?.depends_on ?? []).filter((d) => byId.has(d));
    const d = deps.length ? Math.max(...deps.map(visit)) + 1 : 0;
    visiting.delete(id);
    depth.set(id, d);
    return d;
  };
  nodes.forEach((n) => visit(n.id));
  const out = [];
  for (const n of nodes) {
    const d = depth.get(n.id);
    (out[d] ??= []).push(n.id);
  }
  return out.map((l) => l ?? []);
}

/** Edges as [from, to] pairs, from a dependency to the task that waits for it. */
export function edges(nodes) {
  const ids = new Set(nodes.map((n) => n.id));
  return nodes.flatMap((n) => (n.depends_on ?? []).filter((d) => ids.has(d)).map((d) => [d, n.id]));
}

/** Number of crossing edges between two adjacent layers, for the given order. */
export function crossings(upper, lower, links) {
  const pu = new Map(upper.map((id, i) => [id, i]));
  const pl = new Map(lower.map((id, i) => [id, i]));
  const segs = links.filter(([a, b]) => pu.has(a) && pl.has(b)).map(([a, b]) => [pu.get(a), pl.get(b)]);
  let count = 0;
  for (let i = 0; i < segs.length; i++) {
    for (let j = i + 1; j < segs.length; j++) {
      const [a1, b1] = segs[i];
      const [a2, b2] = segs[j];
      if ((a1 - a2) * (b1 - b2) < 0) count++;
    }
  }
  return count;
}

function totalCrossings(ls, links) {
  let c = 0;
  for (let i = 0; i + 1 < ls.length; i++) c += crossings(ls[i], ls[i + 1], links);
  return c;
}

function sweep(ls, links, down) {
  const next = ls.map((l) => [...l]);
  const range = down ? [...next.keys()].slice(1) : [...next.keys()].slice(0, -1).reverse();
  for (const i of range) {
    const ref = new Map(next[down ? i - 1 : i + 1].map((id, k) => [id, k]));
    const center = (id) => {
      const ps = links.filter(([a, b]) => (down ? b === id && ref.has(a) : a === id && ref.has(b))).map(([a, b]) => ref.get(down ? a : b));
      return ps.length ? ps.reduce((s, p) => s + p, 0) / ps.length : null;
    };
    const keyed = next[i].map((id, k) => ({ id, k, c: center(id) }));
    // Nodes without neighbours keep their place; the sort is stable.
    keyed.sort((x, y) => (x.c ?? x.k) - (y.c ?? y.k) || x.k - y.k);
    next[i] = keyed.map((x) => x.id);
  }
  return next;
}

/** Layer order with fewer crossings, starting from the task order. */
export function order(nodes) {
  const links = edges(nodes);
  let best = layers(nodes);
  let bestCount = totalCrossings(best, links);
  let cur = best;
  for (let pass = 0; pass < 4 && bestCount > 0; pass++) {
    cur = sweep(sweep(cur, links, true), links, false);
    const c = totalCrossings(cur, links);
    if (c < bestCount) {
      best = cur;
      bestCount = c;
    }
  }
  return { layers: best, crossings: bestCount };
}

/** Positions for drawing: layers left to right, tasks top to bottom inside a layer. */
export function layout(nodes) {
  const { layers: ls, crossings: crossingCount } = order(nodes);
  const pos = new Map();
  const tallest = Math.max(1, ...ls.map((l) => l.length));
  const height = PAD * 2 + tallest * NODE_H + (tallest - 1) * GAP_Y;
  ls.forEach((ids, li) => {
    const used = ids.length * NODE_H + (ids.length - 1) * GAP_Y;
    const top = PAD + (height - PAD * 2 - used) / 2;
    ids.forEach((id, k) => pos.set(id, { x: PAD + li * (NODE_W + GAP_X), y: top + k * (NODE_H + GAP_Y), layer: li }));
  });
  const width = PAD * 2 + Math.max(1, ls.length) * NODE_W + Math.max(0, ls.length - 1) * GAP_X;
  const links = edges(nodes).map(([from, to]) => {
    const a = pos.get(from);
    const b = pos.get(to);
    const x1 = a.x + NODE_W;
    const y1 = a.y + NODE_H / 2;
    const x2 = b.x;
    const y2 = b.y + NODE_H / 2;
    const mid = (x1 + x2) / 2;
    return { from, to, d: `M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}` };
  });
  return { positions: pos, links, width, height: nodes.length ? height : PAD * 2, crossings: crossingCount };
}

/** Everything a task waits for (directly or not) and everything that waits for it. */
export function related(nodes, id) {
  const links = edges(nodes);
  const walk = (start, step) => {
    const seen = new Set();
    const stack = [start];
    while (stack.length) {
      const cur = stack.pop();
      for (const next of step(cur)) {
        if (!seen.has(next)) {
          seen.add(next);
          stack.push(next);
        }
      }
    }
    return seen;
  };
  return {
    upstream: walk(id, (cur) => links.filter(([, b]) => b === cur).map(([a]) => a)),
    downstream: walk(id, (cur) => links.filter(([a]) => a === cur).map(([, b]) => b)),
  };
}
