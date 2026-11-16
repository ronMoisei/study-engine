"""Deterministic layered layout for the prerequisite DAG (no library, same input -> same picture).

  level   longest path from a foundation: a node with no prerequisites (in the shown subgraph) is level 0,
          every other node sits one level above its highest prerequisite. Drawn bottom-up: foundations at
          the bottom, each concept above everything it requires.
  order   inside a level: start by (topic, title, id), then a few barycenter sweeps (each node moves toward
          the average position of its neighbours in the adjacent level) to reduce crossings; ties by id.
  x, y    grid coordinates; the panel scales them.
"""
from collections import defaultdict

SWEEPS = 6


def levels(nodes, deps):
    """deps: {id: [prerequisite ids]} restricted to `nodes`. Loops (should not exist) are broken by id order."""
    level, state = {}, {}

    def visit(n, stack):
        if n in level:
            return level[n]
        if state.get(n) == "active":                 # loop in hand-written notes: cut it here
            return -1
        state[n] = "active"
        lv = 0
        for d in sorted(deps.get(n, [])):
            lv = max(lv, visit(d, stack) + 1)
        state[n] = "done"
        level[n] = lv
        return lv

    for n in sorted(nodes):
        visit(n, [])
    return level


def layout(nodes, deps, key=None):
    """nodes: iterable of ids; deps: {id: [prereq ids]}; key: id -> sort tuple for the initial order.
    -> {id: {"level": L, "x": col, "y": row}} with row 0 at the TOP (the highest level)."""
    nodes = sorted(set(nodes))
    deps = {n: [d for d in deps.get(n, []) if d in set(nodes) and d != n] for n in nodes}
    up = defaultdict(list)                           # dependents
    for n, ds in deps.items():
        for d in ds:
            up[d].append(n)
    lv = levels(nodes, deps)
    top = max(lv.values(), default=0)
    rows = defaultdict(list)
    for n in nodes:
        rows[lv[n]].append(n)
    key = key or (lambda n: (n,))
    for r in rows.values():
        r.sort(key=lambda n: (key(n), n))

    def pos_map():
        return {n: i for r in rows.values() for i, n in enumerate(r)}

    for sweep in range(SWEEPS):
        pos = pos_map()
        order = range(1, top + 1) if sweep % 2 == 0 else range(top - 1, -1, -1)
        for L in order:
            neigh = deps if sweep % 2 == 0 else up    # upward sweep: look at prerequisites; downward: dependents

            def bary(n):
                ns = [pos[m] for m in neigh.get(n, []) if m in pos]
                return sum(ns) / len(ns) if ns else pos[n]
            rows[L].sort(key=lambda n: (bary(n), key(n), n))
            for i, n in enumerate(rows[L]):
                pos[n] = i

    width = max((len(r) for r in rows.values()), default=1)
    out = {}
    for L, r in rows.items():
        offset = (width - len(r)) / 2                # centre each row
        for i, n in enumerate(r):
            out[n] = {"level": L, "x": round(offset + i, 2), "y": top - L}
    return out


def layout_blocks(nodes, deps, block_of, key=None, gap=1):
    """Several courses/domains at once: lay out each block on its own and put the blocks side by side (sorted by
    name), foundations of every block on the same bottom row. Links between blocks are drawn but do not move nodes."""
    blocks = defaultdict(list)
    for n in nodes:
        blocks[block_of(n)].append(n)
    parts = {b: layout(ns, deps, key) for b, ns in blocks.items()}
    top = max((p["level"] for part in parts.values() for p in part.values()), default=0)
    out, x0 = {}, 0.0
    for b in sorted(parts):
        part = parts[b]
        for n, p in part.items():
            out[n] = {"level": p["level"], "x": round(x0 + p["x"], 2), "y": top - p["level"]}
        x0 += max((p["x"] for p in part.values()), default=0) + 1 + gap
    return out


def crossings(pos, deps):
    """Number of crossing edge pairs between adjacent rows (for tests / tuning)."""
    edges = [(pos[d], pos[n]) for n, ds in deps.items() for d in ds if n in pos and d in pos
             and abs(pos[n]["y"] - pos[d]["y"]) == 1]
    c = 0
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            (a1, b1), (a2, b2) = edges[i], edges[j]
            if a1["y"] != a2["y"]:
                continue
            if (a1["x"] - a2["x"]) * (b1["x"] - b2["x"]) < 0:
                c += 1
    return c
