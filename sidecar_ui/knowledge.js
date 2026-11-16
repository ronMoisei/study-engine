/* All-knowledge view: concept clusters as macro-nodes, opened on click into their concepts; reasoning drawn as
 * chains (the premises of a concept meet at a junction, one arrow goes on to the concept: A ∧ B ⟹ C).
 * Layout is computed once per setting and cached; dragging a cluster only moves it (no re-simulation).
 * Uses the helpers of app.js (el, svg, md, mdEl, pill, api, err, levelPicker, goalBox, messageActions, KCOL, KROW).
 * Settings live in localStorage (per viewer). */
"use strict";

const KDEFAULTS = {
  detail: "clusters", shape: "layered", colour: "cluster", palette: "vivid", glow: 0.6,
  nodeSize: 1, labelSize: 1, edgeOpacity: 0.45, junctions: true,
  repulsion: 1.6, linkLength: 1.2, gravity: 0.8, direction: 1, spacing: 1,
};
const PALETTES = {
  vivid: ["#a26be0", "#3fb3ad", "#c9b23e", "#55b86a", "#d9705f", "#5f8fe0", "#d07fbf", "#86b63f", "#e09a4a", "#4fb0d9"],
  pastel: ["#c3a6e8", "#93d3cf", "#e3d58f", "#9fd6ab", "#eaa89c", "#a6c0ee", "#e6b3da", "#bcd78f", "#f0c393", "#9ccfe8"],
  mono: ["#d8d8d8", "#bcbcbc", "#a0a0a0", "#e8e8e8", "#c8c8c8", "#b0b0b0"],
};
const URGENCY = ["misconception", "fragile", "in_progress", "held", "untested"];
const BOX = { macroW: 210, macroH: 130, pad: 34, gapX: 46, gapY: 80 };
const GEOMETRY = ["detail", "shape", "repulsion", "linkLength", "gravity", "direction", "spacing", "nodeSize", "labelSize"];
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && Knowledge.sel) Knowledge.clearEdge(); });

const Knowledge = {
  cfg: (() => { try { return { ...KDEFAULTS, ...JSON.parse(localStorage.getItem("study.kview") || "{}") }; } catch (e) { return { ...KDEFAULTS }; } })(),
  open: new Set(),
  offsets: {},            // cluster id -> {dx, dy}: where the learner dragged it (this session)
  sel: null,              // selected link

  save() { try { localStorage.setItem("study.kview", JSON.stringify(this.cfg)); } catch (e) { /* storage blocked */ } },
  set(k, v) {
    this.cfg[k] = v; this.save();
    if (["detail", "shape", "repulsion", "linkLength", "gravity", "direction", "spacing"].includes(k) && this.G?.knowledge()) this.draw(this.G, false);   // new geometry: refit
    else this.redraw();
  },
  redraw() { const G = this.G; if (G && G.knowledge()) this.draw(G, true); },

  expanded(cid) { return this.cfg.detail === "all" || this.open.has(cid); },

  /** Show a concept even if its cluster is closed (search, links in the drawer and in link cards). */
  reveal(G, id) {
    const n = G.state.data.nodes.find((x) => x.id === id);
    if (!n) return;
    if (!this.expanded(n.cluster)) { this.open.add(n.cluster); this.draw(G, true); }
    G.center({ id });
  },

  colours(d) {
    const pal = PALETTES[this.cfg.palette] || PALETTES.vivid, byCluster = {}, byCourse = {};
    d.clusters.forEach((c, i) => { byCluster[c.id] = c.id === "unlinked" ? "#8a8a8a" : pal[i % pal.length]; });
    [...new Set(d.nodes.map((n) => n.group))].sort().forEach((g, i) => { byCourse[g] = pal[(i * 3) % pal.length]; });
    const statusCol = (s) => s === "untested" ? "#ececec" : getComputedStyle(document.documentElement).getPropertyValue(`--st-${s}`).trim();
    return {
      cluster: (cid) => byCluster[cid],
      node: (n) => this.cfg.colour === "status" ? statusCol(n.status) : this.cfg.colour === "course" ? byCourse[n.group] : byCluster[n.cluster],
      macro: (c, members) => {
        if (this.cfg.colour === "status") return statusCol(URGENCY.find((s) => s !== "untested" && c.status_counts[s]) || "untested");
        if (this.cfg.colour === "course") { const cnt = {}; for (const n of members) cnt[n.group] = (cnt[n.group] || 0) + 1; return byCourse[Object.entries(cnt).sort((a, b) => b[1] - a[1])[0][0]]; }
        return byCluster[c.id];
      },
    };
  },

  /** Visible items (macro-nodes for closed clusters, concepts for open ones) with layered positions. */
  items(d) {
    const sp = this.cfg.spacing, col = KCOL * sp, row = KROW * sp;
    const members = {};
    for (const n of d.nodes) (members[n.cluster] ||= []).push(n);
    const items = {}, rep = {};
    if (this.cfg.detail === "all") {                     // every concept: the server's shelf-packed cluster blocks
      for (const n of d.nodes) { items[n.id] = { key: n.id, kind: "concept", node: n, x: n.x * col, y: n.y * row }; rep[n.id] = n.id; }
      return { items, rep, members };
    }
    // clusters in rows by their level in the cluster graph (prerequisite clusters lower), packed left to right
    const rows = {};
    for (const c of d.clusters) (rows[c.my] ||= []).push(c);
    const box = (c) => this.expanded(c.id) ? { w: c.w * col + 2 * BOX.pad, h: c.h * row + 2 * BOX.pad } : { w: BOX.macroW * sp, h: BOX.macroH };
    const widths = {};
    for (const [y, cs] of Object.entries(rows)) { cs.sort((a, b) => a.mx - b.mx); widths[y] = cs.reduce((s, c) => s + box(c).w, 0) + BOX.gapX * (cs.length - 1); }
    const maxW = Math.max(...Object.values(widths));
    let top = 0;
    for (const y of Object.keys(rows).map(Number).sort((a, b) => a - b)) {
      const cs = rows[y], rowH = Math.max(...cs.map((c) => box(c).h));
      let x = (maxW - widths[y]) / 2;
      for (const c of cs) {
        const b = box(c), y0 = top + rowH - b.h;           // bottom-aligned: foundations of a row share a line
        if (this.expanded(c.id)) {
          for (const n of members[c.id]) { items[n.id] = { key: n.id, kind: "concept", node: n, x: x + BOX.pad + n.lx * col, y: y0 + BOX.pad + n.ly * row }; rep[n.id] = n.id; }
        } else {
          const key = "C:" + c.id;
          items[key] = { key, kind: "macro", cluster: c, x: x + b.w / 2, y: y0 + b.h / 2 };
          for (const n of members[c.id]) rep[n.id] = key;
        }
        x += b.w + BOX.gapX;
      }
      top += rowH + BOX.gapY;
    }
    return { items, rep, members };
  },

  /** Requires edges mapped onto visible items: {target: {source: count}}; lateral pairs separately. */
  links(d, rep) {
    const req = {}, lat = new Map();
    for (const e of d.edges) {
      const a = rep[e.from], b = rep[e.to];               // e.from requires e.to
      if (!a || !b || a === b) continue;
      if (e.type === "requires") { (req[a] ||= {}); req[a][b] = (req[a][b] || 0) + 1; }
      else { const k = [a, b].sort().join("|"); if (!lat.has(k)) lat.set(k, e.type); }
    }
    return { req, lat: [...lat].map(([k, type]) => [...k.split("|"), type]) };
  },

  radius(it) {
    const s = this.cfg.nodeSize;
    return it.kind === "macro" ? (14 + 4 * Math.sqrt(it.cluster.size)) * s : 4.5 * s;
  },

  /** The space an item and its label take: half-width, half-height and the box centre's offset below the dot. */
  extent(it) {
    const r = this.radius(it), ls = this.cfg.labelSize;
    if (it.kind === "macro") {
      const w = it.cluster.name.length * 7.9 * ls, top = -r - 6, bottom = r + 22 * ls;
      return { hw: Math.max(r + 8, w / 2 + 8), hh: (bottom - top) / 2, cy: (top + bottom) / 2 };
    }
    const w = Math.min(21, it.node.name.length) * 5.8 * ls, top = -r - 3, bottom = r + 15 * ls;
    return { hw: Math.max(r + 5, w / 2 + 5), hh: (bottom - top) / 2, cy: (top + bottom) / 2 };
  },

  /** Collision: push overlapping items (dot + label boxes) apart. `rowsFixed`: only sideways (layered layout).
   *  `pinned`: item keys that never move (the cluster being dragged) — whatever they touch gets the whole push.
   *  `obstacles`: extra fixed boxes {x, y, ext} (open clusters' titles). */
  collide(items, rowsFixed, pinned, obstacles) {
    const list = Object.values(items), ext = list.map((it) => this.extent(it));
    const fixed = list.map((it) => !!(pinned && pinned.has(it.key)));
    // items pushed by the held cluster are expelled outward from its centre, so nothing gets trapped inside it
    const held = list.filter((_, i) => fixed[i]);
    const hc = held.length ? { x: held.reduce((s, p) => s + p.x, 0) / held.length, y: held.reduce((s, p) => s + p.y, 0) / held.length } : null;
    const anchor = list.map((it, i) => (fixed[i] ? hc : null));
    for (const o of obstacles || []) { list.push(o); ext.push(o.ext); fixed.push(true); anchor.push({ x: o.x, y: o.y }); }
    for (let pass = 0; pass < 300; pass++) {
      let moved = false;
      for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
        if (fixed[i] && fixed[j]) continue;
        const a = list[i], b = list[j], ea = ext[i], eb = ext[j];
        const dx = b.x - a.x, dy = (b.y + eb.cy) - (a.y + ea.cy);
        const ox = ea.hw + eb.hw - Math.abs(dx), oy = ea.hh + eb.hh - Math.abs(dy);
        if (ox <= 0.5 || oy <= 0.5) continue;
        moved = true;
        if (fixed[i] || fixed[j]) {                                 // free item vs pinned one: leave along the ray
          const [p, f, ef] = fixed[i] ? [i, b, eb] : [j, a, ea];     // from the pinned anchor through the free item
          const c = anchor[p];
          let ux = f.x - c.x, uy = (f.y + ef.cy) - c.y;
          if (Math.abs(ux) + Math.abs(uy) < 1e-6) { ux = 0; uy = -1; }
          if (rowsFixed) { uy = 0; ux = ux >= 0 ? 1 : -1; }
          const l = Math.hypot(ux, uy); ux /= l; uy /= l;
          const step = Math.min(Math.abs(ux) > 1e-6 ? ox / Math.abs(ux) : Infinity, Math.abs(uy) > 1e-6 ? oy / Math.abs(uy) : Infinity) + 0.5;
          f.x += ux * step; f.y += uy * step;
        } else if (rowsFixed || ox < oy) { const s = (dx >= 0 ? 1 : -1) * ox / 2; a.x -= s; b.x += s; }
        else { const s = (dy >= 0 ? 1 : -1) * oy / 2; a.y -= s; b.y += s; }
      }
      if (!moved) break;
    }
  },

  /** Title boxes of open clusters (name + close chip above the concepts). */
  titleBoxes(d, items, members) {
    const ls = this.cfg.labelSize, out = [];
    for (const c of d.clusters) {
      if (!this.expanded(c.id)) continue;
      const pts = members[c.id].map((n) => items[n.id]);
      const cx = (Math.min(...pts.map((p) => p.x)) + Math.max(...pts.map((p) => p.x))) / 2;
      const y0 = Math.min(...pts.map((p) => p.y));
      out.push({ cid: c.id, x: cx, y: y0 - 30 - 15 * ls,
        ext: { hw: (`${c.name} · ${c.size}`.length * 9.2 * ls) / 2 + 10, hh: 15 * ls + 14, cy: 0 } });
    }
    return out;
  },

  /** Open clusters carry a title (and close chip) above them: closed cluster nodes must not sit under it. */
  clearTitles(d, items, members) {
    for (const t of this.titleBoxes(d, items, members)) {
      for (const it of Object.values(items)) {
        if (it.kind !== "macro") continue;
        const e = this.extent(it);
        const dx = it.x - t.x, dy = (it.y + e.cy) - t.y;
        const ox = e.hw + t.ext.hw - Math.abs(dx), oy = e.hh + t.ext.hh - Math.abs(dy);
        if (ox <= 0 || oy <= 0) continue;
        if (ox < oy) it.x += (dx >= 0 ? 1 : -1) * ox; else it.y += (dy >= 0 ? 1 : -1) * oy;
      }
    }
  },

  /** Force layout ("physics"): deterministic — starts from the layered positions, fixed iterations, no randomness. */
  simulate(items, req, lat) {
    const c = this.cfg, list = Object.values(items), N = list.length;
    if (N < 2) return;
    const idx = new Map(list.map((it, i) => [it.key, i]));
    const P = list.map((it, i) => ({ x: it.x + ((i * 37) % 11) - 5, y: it.y + ((i * 53) % 7) - 3, vx: 0, vy: 0, m: it.kind === "macro" ? 3 : 1 }));
    const L = 120 * c.linkLength, K = 4200 * c.repulsion;
    const springs = [];
    for (const [t, ss] of Object.entries(req)) for (const s of Object.keys(ss)) springs.push([idx.get(s), idx.get(t), true]);
    for (const [a, b] of lat) springs.push([idx.get(a), idx.get(b), false]);
    const cx = list.reduce((s, it) => s + it.x, 0) / N, cy = list.reduce((s, it) => s + it.y, 0) / N;
    const sameCluster = list.map((it) => it.kind === "concept" ? it.node.cluster : null);
    for (let iter = 0, alpha = 1; iter < 320; iter++, alpha *= 0.985) {
      for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
        let dx = P[j].x - P[i].x, dy = P[j].y - P[i].y, d2 = dx * dx + dy * dy + 0.01;
        if (d2 > 640000) continue;
        const f = (K * P[i].m * P[j].m) / d2, d = Math.sqrt(d2);
        dx /= d; dy /= d;
        P[i].vx -= dx * f / P[i].m; P[i].vy -= dy * f / P[i].m; P[j].vx += dx * f / P[j].m; P[j].vy += dy * f / P[j].m;
        // a weak pull keeps an open cluster together; weaker than before so its concepts get room
        if (sameCluster[i] && sameCluster[i] === sameCluster[j] && d > 160) { const g = 0.0012 * (d - 160); P[i].vx += dx * g; P[i].vy += dy * g; P[j].vx -= dx * g; P[j].vy -= dy * g; }
      }
      for (const [s, t, directed] of springs) {
        if (s === undefined || t === undefined) continue;
        const a = P[s], b = P[t];
        const len = L * (list[s].kind === "macro" || list[t].kind === "macro" ? 1.8 : 1);
        let dx = b.x - a.x, dy = b.y - a.y; const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
        const f = (d - len) * 0.04; dx /= d; dy /= d;
        a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
        if (directed && c.direction > 0) {                  // the dependent (t) should sit above its premise (s)
          const want = len * 0.6, gap = a.y - b.y;
          if (gap < want) { const g = (want - gap) * 0.03 * c.direction; a.vy += g; b.vy -= g; }
        }
      }
      for (const p of P) {
        p.vx += (cx - p.x) * 0.0025 * c.gravity; p.vy += (cy - p.y) * 0.0025 * c.gravity;
        p.x += Math.max(-40, Math.min(40, p.vx * alpha)); p.y += Math.max(-40, Math.min(40, p.vy * alpha));
        p.vx *= 0.6; p.vy *= 0.6;
      }
    }
    list.forEach((it, i) => { it.x = P[i].x; it.y = P[i].y; });
  },

  /** Positions for the current settings: computed once, then reused (dragging and colour changes don't move things). */
  geometry(G) {
    const d = G.state.data, cfg = this.cfg;
    const key = JSON.stringify([GEOMETRY.map((k) => cfg[k]), [...this.open].sort(), !!G.state.lateral]);
    if (this.cache && this.cache.data === d && this.cache.key === key) return this.cache;
    const { items, rep, members } = this.items(d);
    const { req, lat } = this.links(d, rep);
    if (cfg.shape === "force") this.simulate(items, req, G.state.lateral ? lat : []);
    this.collide(items, cfg.shape !== "force" && cfg.detail !== "all");
    this.clearTitles(d, items, members);
    this.cache = { data: d, key, items, rep, members, req, lat };
    return this.cache;
  },

  draw(G, keepView) {
    this.G = G;
    this.geometry(G);
    const view = [G.state.tx, G.state.ty, G.state.k];
    this.render(G);
    if (keepView) { [G.state.tx, G.state.ty, G.state.k] = view; G.apply(); }
    else requestAnimationFrame(() => G.fit());
  },

  /** Draw from the cached geometry plus the learner's drags. */
  render(G) {
    const d = G.state.data, wrap = G.els.wrap, cfg = this.cfg, geo = this.cache;
    const { rep, members, req, lat } = geo;
    G.dims = { w: 0, h: 0 };
    const items = {};
    const clusterOf = (it) => it.kind === "macro" ? it.cluster.id : it.node.cluster;
    for (const [k, it] of Object.entries(geo.items)) {
      const o = this.offsets[clusterOf(it)] || { dx: 0, dy: 0 };
      items[k] = { ...it, x: it.x + o.dx, y: it.y + o.dy };
    }
    if (Object.keys(this.offsets).length) {
      // something was dragged: settle collisions again. The cluster in hand stays where it is put; everything it
      // touches (other concepts, cluster nodes) is pushed aside; open clusters' titles are obstacles too.
      const held = this.lastDragged, pinned = new Set(Object.keys(items).filter((k) => clusterOf(items[k]) === held));
      this.collide(items, false, pinned, this.titleBoxes(d, items, members));
    }
    const col = this.colours(d);
    const keepDrawer = wrap.querySelector(".drawer");
    wrap.replaceChildren();

    const root = svg("svg", { class: "knowledge" });
    const defs = svg("defs");
    const arrow = svg("marker", { id: "karrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "10", markerHeight: "10", markerUnits: "userSpaceOnUse", orient: "auto" });
    arrow.append(svg("path", { d: "M0,0 L10,5 L0,10 z", fill: "#bdbdbd" }));
    defs.append(arrow); root.append(defs);
    const g = svg("g"); root.append(g);
    G.els.svg = root; G.els.g = g;

    // glows, names, close chips — one per cluster, around its macro-node or its open concepts
    const glows = svg("g"), names = svg("g");
    this.openAreas = [];
    d.clusters.forEach((c, i) => {
      const colour = col.cluster(c.id);
      const pts = this.expanded(c.id) ? members[c.id].map((n) => items[n.id]) : [items["C:" + c.id]];
      const x0 = Math.min(...pts.map((p) => p.x)), x1 = Math.max(...pts.map((p) => p.x));
      const y0 = Math.min(...pts.map((p) => p.y)), y1 = Math.max(...pts.map((p) => p.y));
      const grad = svg("radialGradient", { id: `kglow${i}` });
      for (const [o, a] of [[0, 0.5], [0.55, 0.22], [1, 0]]) grad.append(svg("stop", { offset: o, "stop-color": colour, "stop-opacity": a * cfg.glow }));
      defs.append(grad);
      const open = this.expanded(c.id);
      const area = { id: c.id, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, rx: (x1 - x0) / 2 + (open ? 110 : 95), ry: (y1 - y0) / 2 + (open ? 80 : 75) };
      glows.append(svg("ellipse", { cx: area.cx, cy: area.cy, rx: area.rx, ry: area.ry, fill: `url(#kglow${i})` }));
      if (!open) return;
      const close = () => { this.open.delete(c.id); this.redrawAround(G, "C:" + c.id); };
      const t = svg("text", { x: area.cx, y: y0 - 30, class: "cluster-label", fill: colour, "text-anchor": "middle", style: `font-size:${15 * cfg.labelSize}px` });
      t.textContent = `${c.name} · ${c.size}`;
      names.append(t);
      if (cfg.detail === "all") return;
      this.openAreas.push(area);
      t.classList.add("clickable");
      t.addEventListener("pointerdown", (ev) => this.startDrag(ev, G, c.id));
      t.addEventListener("click", (ev) => { ev.stopPropagation(); if (!this.justDragged) this.clusterDrawer(G, c.id); });
      const tip = svg("title"); tip.textContent = "Click: prepare a lecture · drag: move the cluster"; t.append(tip);
      const chip = svg("g", { class: "kclose", transform: `translate(${area.cx - 34},${y0 - 30 - 15 * cfg.labelSize - 24})` });
      chip.append(svg("rect", { width: 68, height: 20, rx: 10 }));
      const ct = svg("text", { x: 34, y: 14, "text-anchor": "middle" }); ct.textContent = "✕ close"; chip.append(ct);
      chip.addEventListener("click", (ev) => { ev.stopPropagation(); close(); });
      names.append(chip);
    });
    g.append(glows);

    // links: reasoning chains (premises meet at a junction, one arrow to the conclusion); every link is clickable
    const edgeLayer = svg("g", { style: `opacity:${cfg.edgeOpacity}` }), hitLayer = svg("g");
    G.edgeEls = [];
    const trim = (from, to, r) => { const dx = to.x - from.x, dy = to.y - from.y, l = Math.hypot(dx, dy) || 1; return { x: to.x - dx / l * (r + 3), y: to.y - dy / l * (r + 3) }; };
    const line = (a, b, cls, info, width, arrowed) => {
      const e = svg("line", { class: `kedge ${cls}`, x1: a.x, y1: a.y, x2: b.x, y2: b.y, "stroke-width": width });
      if (arrowed) e.setAttribute("marker-end", "url(#karrow)");
      e.dataset.keys = info.keys.join(" ");
      e._info = info;
      edgeLayer.append(e); G.edgeEls.push(e);
      const hit = svg("line", { class: "khit", x1: a.x, y1: a.y, x2: b.x, y2: b.y });
      hit.addEventListener("click", (ev) => { ev.stopPropagation(); this.selectEdge(G, info, ev); });
      hitLayer.append(hit);
    };
    for (const [t, ss] of Object.entries(req)) {
      const T = items[t], srcs = Object.keys(ss).filter((s) => items[s]);
      if (!T || !srcs.length) continue;
      const rT = this.radius(T);
      if (T.kind === "concept" && cfg.junctions && srcs.length >= 2) {
        const info = { kind: "chain", target: t, sources: srcs, keys: [t, ...srcs] };
        const mx = srcs.reduce((s, k) => s + items[k].x, 0) / srcs.length, my = srcs.reduce((s, k) => s + items[k].y, 0) / srcs.length;
        const J = { x: T.x + (mx - T.x) * 0.4, y: T.y + (my - T.y) * 0.4 };
        for (const s of srcs) line(items[s], J, "premise", info, 1.4 * Math.sqrt(ss[s]), false);
        line(J, trim(J, T, rT), "conclusion", info, 1.8, true);
        edgeLayer.append(svg("circle", { cx: J.x, cy: J.y, r: 2.2, class: "junction" }));
      } else {
        for (const s of srcs) line(items[s], trim(items[s], T, rT), "single", { kind: "requires", target: t, sources: [s], count: ss[s], keys: [s, t] },
          Math.min(6, 1.2 + Math.log2(ss[s])), true);
      }
    }
    if (G.state.lateral) for (const [a, b, type] of lat) if (items[a] && items[b]) line(items[a], items[b], "lateral", { kind: "lateral", type, keys: [a, b] }, 1, false);
    g.append(edgeLayer, hitLayer, names);

    // nodes
    G.nodeEls = {}; G.pos = {};
    const card = el("div", { class: "kcard", style: "display:none" });
    const showCard = (ng, content) => {
      const r = ng.getBoundingClientRect(), w = wrap.getBoundingClientRect();
      card.replaceChildren(...content); card.style.display = "block";
      card.style.left = `${Math.max(6, Math.min(r.right - w.left + 8, w.width - 300))}px`;
      card.style.top = `${Math.max(6, Math.min(r.top - w.top, w.height - 160))}px`;
    };
    for (const it of Object.values(items)) {
      const rr = this.radius(it);
      const ng = svg("g", { class: `node ${it.kind === "macro" ? "macro" : "dot"}`, transform: `translate(${it.x},${it.y})`, style: "cursor:pointer" });
      if (it.kind === "macro") {
        const c = it.cluster, colour = col.macro(c, members[c.id]);
        ng.append(svg("circle", { r: rr + 10, class: "hit" }), svg("circle", { r: rr, fill: colour, "fill-opacity": 0.85, stroke: colour, "stroke-width": 2 }));
        const held = (c.status_counts.held || 0) / c.size;           // progress ring: share of held concepts
        if (held > 0) ng.append(svg("circle", { r: rr + 4, class: "ring", "stroke-dasharray": `${2 * Math.PI * (rr + 4) * held} 9999`, transform: "rotate(-90)" }));
        const num = svg("text", { class: "count", "text-anchor": "middle", y: 4 * cfg.labelSize, style: `font-size:${12 * cfg.labelSize}px` }); num.textContent = c.size; ng.append(num);
        const t = svg("text", { class: "mlabel", "text-anchor": "middle", y: rr + 18 * cfg.labelSize, style: `font-size:${13 * cfg.labelSize}px` }); t.textContent = c.name; ng.append(t);
        ng.addEventListener("pointerdown", (ev) => this.startDrag(ev, G, c.id));
        ng.addEventListener("click", (ev) => { ev.stopPropagation(); card.style.display = "none"; if (this.justDragged) return; this.open.add(c.id); this.redrawAround(G, c.id); });
        ng.addEventListener("mouseenter", () => {
          this.highlight(it.key);
          showCard(ng, [el("b", {}, c.name), el("div", { class: "muted" }, `${c.size} concepts · click to open · drag to move`),
            el("div", {}, "Foundations: " + c.foundations.join(", ")),
            el("div", { class: "row" }, ...URGENCY.filter((s) => c.status_counts[s]).map((s) => el("span", {}, pill(s), ` ${c.status_counts[s]}`)))]);
        });
      } else {
        const n = it.node;
        ng.append(svg("circle", { r: Math.max(9, rr + 4), class: "hit" }), svg("circle", { r: rr, fill: col.node(n) }));
        const t = svg("text", { y: rr + 12 * cfg.labelSize, "text-anchor": "middle", style: `font-size:${9.5 * cfg.labelSize}px` });
        t.textContent = n.name.length > 21 ? n.name.slice(0, 20) + "…" : n.name; ng.append(t);
        ng.addEventListener("click", (ev) => { ev.stopPropagation(); card.style.display = "none"; G.select(n.id); });
        ng.addEventListener("mouseenter", () => {
          this.highlight(it.key);
          const cl = d.clusters.find((c) => c.id === n.cluster);
          showCard(ng, [el("b", {}, n.name), n.summary ? mdEl(n.summary, "kcard-text") : "", el("div", { class: "muted" }, pill(n.status), ` ${cl ? cl.name : ""} · ${n.group}`)]);
        });
      }
      ng.addEventListener("mouseleave", () => { this.highlight(null); card.style.display = "none"; });
      g.append(ng); G.nodeEls[it.key] = ng; G.pos[it.key] = { x: it.x, y: it.y };
    }

    // empty space: double-click closes the open cluster there (or all); a plain click clears the selected link
    let down = null;
    root.addEventListener("pointerdown", (ev) => { down = { x: ev.clientX, y: ev.clientY }; });
    root.addEventListener("click", (ev) => {
      if (!this.sel || !down || Math.hypot(ev.clientX - down.x, ev.clientY - down.y) > 4) return;
      if (!ev.target.closest(".node, .khit, .kclose, .clickable")) this.clearEdge();
    });
    root.addEventListener("dblclick", (ev) => {
      if (cfg.detail === "all" || ev.target.closest(".node, .kclose, .clickable, .khit")) return;
      const r = root.getBoundingClientRect(), s = G.state;
      const x = (ev.clientX - r.left - s.tx) / s.k, y = (ev.clientY - r.top - s.ty) / s.k;
      const hit = this.openAreas.find((a) => ((x - a.cx) / a.rx) ** 2 + ((y - a.cy) / a.ry) ** 2 <= 1);
      if (hit) { this.open.delete(hit.id); this.redrawAround(G, "C:" + hit.id); }
      else if (this.open.size) { this.open.clear(); this.redraw(); }
    });
    this.edgeCard = el("div", { class: "kcard kedgecard", style: "display:none" });
    wrap.append(card, this.edgeCard, this.panel());
    if (keepDrawer) wrap.append(keepDrawer);
    G.finish(root);
    if (this.sel) this.selectEdge(G, this.sel, null);
  },

  /** Drag a closed cluster's node or an open cluster's name; everything in the cluster moves with it. */
  startDrag(ev, G, cid) {
    if (ev.button !== 0) return;
    ev.stopPropagation();
    this.lastDragged = cid;
    const s0 = { x: ev.clientX, y: ev.clientY }, o0 = { ...(this.offsets[cid] || { dx: 0, dy: 0 }) };
    let moved = false, raf = 0;
    const move = (e) => {
      if (!moved && Math.hypot(e.clientX - s0.x, e.clientY - s0.y) < 4) return;
      moved = true;
      this.offsets[cid] = { dx: o0.dx + (e.clientX - s0.x) / G.state.k, dy: o0.dy + (e.clientY - s0.y) / G.state.k };
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; this.render(G); });
    };
    const up = () => {
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
      if (!moved) return;
      if (raf) cancelAnimationFrame(raf);
      this.render(G);
      this.justDragged = true; setTimeout(() => { this.justDragged = false; }, 0);   // the click that ends a drag opens nothing
    };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  },

  name(key) {
    const d = this.G.state.data;
    if (key.startsWith("C:")) return (d.clusters.find((c) => "C:" + c.id === key) || {}).name || key;
    return (d.nodes.find((n) => n.id === key) || {}).name || key;
  },

  /** A clicked link: highlight both ends and list what it connects (for cluster links: the concept pairs behind it). */
  selectEdge(G, info, ev) {
    this.sel = info;
    this.highlight(null);
    const d = G.state.data, rep = this.cache.rep;
    const go = (key) => el("a", { onclick: () => {
      if (key.startsWith("C:")) { this.open.add(key.slice(2)); this.clearEdge(); this.redrawAround(G, key.slice(2)); }
      else { this.reveal(G, key); G.select(key); }
    } }, this.name(key));
    const body = [el("button", { class: "close", onclick: () => this.clearEdge() }, "✕")];
    if (info.kind === "chain") {
      body.push(el("b", {}, "Reasoning chain"), el("div", { class: "muted" }, "these premises together give the conclusion"),
        el("ul", { class: "links" }, ...info.sources.map((s) => el("li", {}, go(s)))), el("div", {}, "⟹ ", go(info.target)));
    } else if (info.kind === "requires") {
      const [s, t] = [info.sources[0], info.target];
      body.push(el("b", {}, "Prerequisite link"), el("div", {}, go(s), " → ", go(t)));
      if (s.startsWith("C:") || t.startsWith("C:")) {
        const pairs = d.edges.filter((e) => e.type === "requires" && rep[e.from] === t && rep[e.to] === s);
        body.push(el("div", { class: "muted" }, `${pairs.length} concept link${pairs.length === 1 ? "" : "s"} behind it:`),
          el("ul", { class: "links" }, ...pairs.slice(0, 14).map((e) => el("li", {}, go(e.to), " → ", go(e.from)))),
          pairs.length > 14 ? el("div", { class: "muted" }, `… and ${pairs.length - 14} more`) : "");
      }
    } else {
      body.push(el("b", {}, (info.type || "related").replace(/_/g, " ")), el("div", {}, go(info.keys[0]), " ↔ ", go(info.keys[1])));
    }
    const card = this.edgeCard;
    card.replaceChildren(...body);
    card.style.display = "block";
    if (ev) {
      const w = G.els.wrap.getBoundingClientRect();
      card.style.left = `${Math.max(6, Math.min(ev.clientX - w.left + 12, w.width - 300))}px`;
      card.style.top = `${Math.max(6, Math.min(ev.clientY - w.top + 12, w.height - 220))}px`;
    }
    this.applySelection();
  },

  clearEdge() {
    this.sel = null;
    if (this.edgeCard) this.edgeCard.style.display = "none";
    if (this.G?.nodeEls) this.highlight(null);
  },

  applySelection() {
    const G = this.G, info = this.sel;
    if (!info) return;
    const keys = new Set(info.keys);
    for (const [k, n] of Object.entries(G.nodeEls)) n.classList.toggle("dim", !keys.has(k));
    for (const e of G.edgeEls) { const on = e._info === info || (e._info && e._info.kind === info.kind && e.dataset.keys === info.keys.join(" ")); e.classList.toggle("hot", on); e.classList.toggle("dim", !on); }
  },

  highlight(key) {
    const G = this.G;
    if (!key) {
      Object.values(G.nodeEls).forEach((n) => n.classList.remove("dim")); G.edgeEls.forEach((e) => e.classList.remove("dim", "hot"));
      return this.applySelection();
    }
    const near = new Set([key]);
    for (const e of G.edgeEls) { const ks = e.dataset.keys.split(" "); if (ks.includes(key)) ks.forEach((k) => near.add(k)); }
    for (const [k, n] of Object.entries(G.nodeEls)) n.classList.toggle("dim", !near.has(k));
    for (const e of G.edgeEls) { const hot = e.dataset.keys.split(" ").includes(key); e.classList.toggle("hot", hot); e.classList.toggle("dim", !hot); }
  },

  /** A whole cluster as one lecture: order, outside prerequisites, level + goal, "Prepare lecture". */
  async clusterDrawer(G, cid) {
    const wrap = G.els.wrap;
    wrap.querySelector(".drawer")?.remove();
    const dr = el("div", { class: "drawer" }, el("span", { class: "muted" }, "loading…"));
    wrap.append(dr);
    let c;
    try { c = await api("GET", `/cluster/${encodeURIComponent(cid)}`); } catch (e) { dr.replaceChildren(); return err(dr, e); }
    const goal = goalBox("Goal / scope (optional): e.g. theory only, exam on 12 June");
    let acts;
    const picker = levelPicker(() => acts.refresh());
    acts = messageActions(() => ({ cluster: cid, level: picker.value, goal: goal.value.trim() }), "/teach-message", "/teach", () => !!picker.value);
    acts.send.textContent = CONVERSATION ? "Prepare lecture" : "Prepare lecture (new conversation)";
    const link = (x) => el("a", { onclick: () => { this.reveal(G, x.id); G.select(x.id); } }, x.name);
    dr.replaceChildren(
      el("button", { class: "close", onclick: () => dr.remove() }, "✕"),
      el("h3", {}, c.name),
      el("div", { class: "row" }, ...URGENCY.filter((s) => c.status_counts[s]).map((s) => el("span", {}, pill(s), ` ${c.status_counts[s]}`))),
      el("div", { class: "muted" }, `${c.size} concepts · ${c.courses.join(", ")}`),
      el("h4", {}, "How well do you know it?"), picker.el, goal,
      el("div", { class: "row" }, acts.send, acts.show, acts.copy), acts.out, acts.msgBox,
      c.outside.length ? el("h4", {}, `Prerequisites from other clusters (${c.outside.length})`) : "",
      c.outside.length ? el("ul", { class: "links" }, ...c.outside.map((x) => el("li", {}, pill(x.status), link(x), el("span", { class: "muted" }, ` · ${x.cluster}`)))) : "",
      el("h4", {}, `Lecture order (${c.order.length} steps)`),
      el("ol", { class: "path" }, ...c.order.map((x) => el("li", {}, pill(x.status), link(x)))));
  },

  /** Re-draw after opening/closing a cluster and keep that cluster in view. */
  redrawAround(G, key) {
    this.draw(G, true);
    const c = key.startsWith("C:") ? key.slice(2) : key;
    const pts = Object.entries(G.pos).filter(([k]) => k === "C:" + c || (G.state.data.nodes.find((n) => n.id === k) || {}).cluster === c).map(([, p]) => p);
    if (!pts.length) return G.fit();
    const r = G.els.svg.getBoundingClientRect(), s = G.state;
    const x0 = Math.min(...pts.map((p) => p.x)) - 140, x1 = Math.max(...pts.map((p) => p.x)) + 140;
    const y0 = Math.min(...pts.map((p) => p.y)) - 110, y1 = Math.max(...pts.map((p) => p.y)) + 110;
    s.k = Math.min(1.1, Math.max(0.15, Math.min(r.width / (x1 - x0), r.height / (y1 - y0))));
    s.tx = r.width / 2 - (x0 + x1) / 2 * s.k; s.ty = r.height / 2 - (y0 + y1) / 2 * s.k;
    G.apply();
  },

  /** The gear menu: level of detail, shape and physics, colours, sizes. */
  panel() {
    const c = this.cfg;
    const box = el("div", { class: "kpanel", style: this.panelOpen ? "" : "display:none" });
    const gear = el("button", { class: "kgear", title: "graph settings", onclick: () => { this.panelOpen = !this.panelOpen; box.style.display = this.panelOpen ? "" : "none"; } }, "⚙");
    const select = (k, opts) => el("select", { onchange: (e) => this.set(k, e.target.value) }, ...opts.map(([v, t]) => el("option", { value: v, selected: c[k] === v }, t)));
    const slider = (k, min, max, step, disabled) => {
      const out = el("span", { class: "muted" }, String(c[k]));
      const s = el("input", { type: "range", min, max, step, value: c[k], disabled, oninput: (e) => { out.textContent = e.target.value; }, onchange: (e) => this.set(k, +e.target.value) });
      return el("span", { class: "kslider" }, s, out);
    };
    const check = (k) => el("input", { type: "checkbox", checked: c[k], onchange: (e) => this.set(k, e.target.checked) });
    const row = (label, ...ctl) => el("label", { class: "krow" }, el("span", {}, label), ...ctl);
    const force = c.shape !== "force";
    box.append(
      el("h4", {}, "Level of detail"),
      row("Show", select("detail", [["clusters", "Clusters (click to open)"], ["all", "All concepts"]])),
      el("div", { class: "row" },
        el("button", { disabled: c.detail === "all", onclick: () => { this.G.state.data.clusters.forEach((x) => this.open.add(x.id)); this.redraw(); } }, "Open all"),
        el("button", { disabled: c.detail === "all", onclick: () => { this.open.clear(); this.redraw(); } }, "Close all"),
        el("button", { disabled: !Object.keys(this.offsets).length, onclick: () => { this.offsets = {}; this.redraw(); } }, "Reset positions")),
      row("Reasoning chains", check("junctions")),
      el("h4", {}, "Shape"),
      row("Layout", select("shape", [["layered", "Layered (arrows point up)"], ["force", "Force (physics)"]])),
      row("Spacing", slider("spacing", 0.7, 2.5, 0.1)),
      row("Repulsion", slider("repulsion", 0.2, 5, 0.1, force)),
      row("Link length", slider("linkLength", 0.4, 3, 0.1, force)),
      row("Gravity", slider("gravity", 0, 3, 0.1, force)),
      row("Direction pull", slider("direction", 0, 3, 0.1, force)),
      el("h4", {}, "Colours"),
      row("Colour by", select("colour", [["cluster", "Cluster"], ["status", "Learner status"], ["course", "Course"]])),
      row("Palette", select("palette", [["vivid", "Vivid"], ["pastel", "Pastel"], ["mono", "Mono"]])),
      row("Glow", slider("glow", 0, 1, 0.05)),
      el("h4", {}, "Size"),
      row("Nodes", slider("nodeSize", 0.5, 2, 0.1)),
      row("Labels", slider("labelSize", 0.6, 1.8, 0.1)),
      row("Links", slider("edgeOpacity", 0.1, 1, 0.05)),
      el("div", { class: "row" }, el("button", { onclick: () => { this.cfg = { ...KDEFAULTS }; this.save(); this.offsets = {}; this.draw(this.G, false); } }, "Reset")));
    return el("div", { class: "kmenu" }, gear, box);
  },
};
