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
    lv = levels(nodes, deps)
    rows = _arrange(nodes, deps, lv, key)
    top = max(lv.values(), default=0)
    width = max((len(r) for r in rows.values()), default=1)
    out = {}
    for L, r in rows.items():
        offset = (width - len(r)) / 2                # centre each row
        for i, n in enumerate(r):
            out[n] = {"level": L, "x": round(offset + i, 2), "y": top - L}
    return out


def _arrange(nodes, deps, lv, key=None):
    """Order the nodes inside each level (initial key order, then barycenter sweeps). -> {level: [ids]}."""
    up = defaultdict(list)                           # dependents
    for n, ds in deps.items():
        for d in ds:
            up[d].append(n)
    top = max((lv[n] for n in nodes), default=0)
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
            if L not in rows:
                continue
            neigh = deps if sweep % 2 == 0 else up    # upward sweep: look at prerequisites; downward: dependents

            def bary(n):
                ns = [pos[m] for m in neigh.get(n, []) if m in pos]
                return sum(ns) / len(ns) if ns else pos[n]
            rows[L].sort(key=lambda n: (bary(n), key(n), n))
            for i, n in enumerate(rows[L]):
                pos[n] = i
    return rows


def layout_clusters(nodes, deps, cluster_of, key=None, gap=1.5, band_gap=2.5, aspect=1.6):
    """The all-knowledge view: one vertical block per concept cluster (foundations at its bottom, levels counted
    inside the cluster), blocks packed left to right in bands like shelves so the whole map is about `aspect`
    times wider than tall. Clusters sharing many links are placed next to each other. Links between clusters are
    drawn but do not move nodes. -> ({id: {"level", "x", "y"}}, [{"id", "x0", "x1", "y0", "y1"}])."""
    nodes = sorted(set(nodes))
    deps = {n: [d for d in deps.get(n, []) if d in set(nodes) and d != n] for n in nodes}
    members = defaultdict(list)
    for n in nodes:
        members[cluster_of(n)].append(n)
    link = defaultdict(int)                          # links between two clusters
    for n, ds in deps.items():
        for d in ds:
            a, b = cluster_of(n), cluster_of(d)
            if a != b:
                link[(a, b)] += 1
                link[(b, a)] += 1
    # order: start with the largest cluster, then always the one most linked to the last placed one
    left = sorted(members, key=lambda c: (-len(members[c]), c))
    order = [left.pop(0)] if left else []
    while left:
        last = order[-1]
        nxt = min(left, key=lambda c: (-link[(last, c)], -sum(link[(o, c)] for o in order), -len(members[c]), c))
        left.remove(nxt)
        order.append(nxt)
    # each cluster laid out on its own
    parts = {}
    for c in order:
        ns = members[c]
        inner = {n: [d for d in deps[n] if d in set(ns)] for n in ns}
        lv = levels(ns, inner)
        rows = _arrange(ns, inner, lv, key)
        parts[c] = (rows, max(len(r) for r in rows.values()), max(lv.values()) + 1)
    # shelf packing: target band width from the total area
    area = sum((w + gap) * (h + band_gap) for _, w, h in parts.values())
    target = max(max(w for _, w, _ in parts.values()), (area * aspect) ** 0.5) if parts else 0
    out, blocks, x0, y0, band_h = {}, [], 0.0, 0.0, 0
    for c in order:
        rows, width, height = parts[c]
        if x0 > 0 and x0 + width > target:
            x0, y0, band_h = 0.0, y0 + band_h + band_gap, 0
        for L, r in rows.items():
            offset = (width - len(r)) / 2
            for i, n in enumerate(r):
                out[n] = {"level": L, "x": round(x0 + offset + i, 2), "y": y0 + height - 1 - L}
        blocks.append({"id": c, "x0": x0, "x1": x0 + width - 1, "y0": y0, "y1": y0 + height - 1})
        band_h = max(band_h, height)
        x0 += width + gap
    # foundations of every cluster in a band on the band's bottom row
    for b in blocks:
        bottom = max(x["y1"] for x in blocks if x["y0"] == b["y0"])
        shift = bottom - b["y1"]
        if shift:
            for n in members[b["id"]]:
                out[n]["y"] += shift
            b["y0"] += shift
            b["y1"] += shift
    return out, blocks


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
