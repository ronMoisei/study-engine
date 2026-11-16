/* All-knowledge view: concept clusters as macro-nodes, opened on click into their concepts; reasoning drawn as
 * chains (the premises of a concept meet at a junction, one arrow goes on to the concept: A ∧ B ⟹ C).
 * Uses the helpers of app.js (el, svg, md, mdEl, pill, KCOL, KROW). Settings live in localStorage (per viewer). */
"use strict";

const KDEFAULTS = {
  detail: "clusters", shape: "layered", colour: "cluster", palette: "vivid", glow: 0.6,
  nodeSize: 1, labelSize: 1, edgeOpacity: 0.45, junctions: true,
  repulsion: 1, linkLength: 1, gravity: 1, direction: 1,
};
const PALETTES = {
  vivid: ["#a26be0", "#3fb3ad", "#c9b23e", "#55b86a", "#d9705f", "#5f8fe0", "#d07fbf", "#86b63f", "#e09a4a", "#4fb0d9"],
  pastel: ["#c3a6e8", "#93d3cf", "#e3d58f", "#9fd6ab", "#eaa89c", "#a6c0ee", "#e6b3da", "#bcd78f", "#f0c393", "#9ccfe8"],
  mono: ["#d8d8d8", "#bcbcbc", "#a0a0a0", "#e8e8e8", "#c8c8c8", "#b0b0b0"],
};
const URGENCY = ["misconception", "fragile", "in_progress", "held", "untested"];
const BOX = { macroW: 210, macroH: 130, pad: 34, gapX: 46, gapY: 80 };

const Knowledge = {
  cfg: (() => { try { return { ...KDEFAULTS, ...JSON.parse(localStorage.getItem("study.kview") || "{}") }; } catch (e) { return { ...KDEFAULTS }; } })(),
  open: new Set(),

  save() { try { localStorage.setItem("study.kview", JSON.stringify(this.cfg)); } catch (e) { /* storage blocked */ } },
  set(k, v) {
    this.cfg[k] = v; this.save();
    if (["detail", "shape", "repulsion", "linkLength", "gravity", "direction"].includes(k) && this.G?.knowledge()) this.draw(this.G, false);   // new geometry: refit
    else this.redraw();
  },
  redraw() { const G = this.G; if (G && G.knowledge()) { const t = [G.state.tx, G.state.ty, G.state.k]; this.draw(G, true); [G.state.tx, G.state.ty, G.state.k] = t; G.apply(); } },

  expanded(cid) { return this.cfg.detail === "all" || this.open.has(cid); },

  /** Show a concept even if its cluster is closed (search, links in the drawer). */
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
    const members = {};
    for (const n of d.nodes) (members[n.cluster] ||= []).push(n);
    const items = {}, rep = {};
    if (this.cfg.detail === "all") {                     // every concept: the server's shelf-packed cluster blocks
      for (const n of d.nodes) { items[n.id] = { key: n.id, kind: "concept", node: n, x: n.x * KCOL, y: n.y * KROW }; rep[n.id] = n.id; }
      return { items, rep, members };
    }
    // clusters in rows by their level in the cluster graph (prerequisite clusters lower), packed left to right
    const rows = {};
    for (const c of d.clusters) (rows[c.my] ||= []).push(c);
    const box = (c) => this.expanded(c.id) ? { w: c.w * KCOL + 2 * BOX.pad, h: c.h * KROW + 2 * BOX.pad } : { w: BOX.macroW, h: BOX.macroH };
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
          for (const n of members[c.id]) { items[n.id] = { key: n.id, kind: "concept", node: n, x: x + BOX.pad + n.lx * KCOL, y: y0 + BOX.pad + n.ly * KROW }; rep[n.id] = n.id; }
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
    const req = {}, lat = new Set();
    for (const e of d.edges) {
      const a = rep[e.from], b = rep[e.to];               // e.from requires e.to
      if (!a || !b || a === b) continue;
      if (e.type === "requires") { (req[a] ||= {}); req[a][b] = (req[a][b] || 0) + 1; }
      else lat.add([a, b].sort().join("|"));
    }
    return { req, lat: [...lat].map((s) => s.split("|")) };
  },

  radius(it) {
    const s = this.cfg.nodeSize;
    return it.kind === "macro" ? (14 + 4 * Math.sqrt(it.cluster.size)) * s : 4.5 * s;
  },

  /** Force layout ("physics"): deterministic — starts from the layered positions, fixed iterations, no randomness. */
  simulate(items, req, lat, members) {
    const c = this.cfg, list = Object.values(items), N = list.length;
    if (N < 2) return;
    const idx = new Map(list.map((it, i) => [it.key, i]));
    const P = list.map((it, i) => ({ x: it.x + ((i * 37) % 11) - 5, y: it.y + ((i * 53) % 7) - 3, vx: 0, vy: 0, m: it.kind === "macro" ? 3 : 1 }));
    const L = 120 * c.linkLength, K = 2600 * c.repulsion;
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
        if (sameCluster[i] && sameCluster[i] === sameCluster[j]) { const g = 0.004 * d; P[i].vx += dx * g; P[i].vy += dy * g; P[j].vx -= dx * g; P[j].vy -= dy * g; }
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

  draw(G, keepView) {
    this.G = G;
    const d = G.state.data, wrap = G.els.wrap, cfg = this.cfg;
    G.dims = { w: 0, h: 0 };
    wrap.replaceChildren();
    const { items, rep, members } = this.items(d);
    const { req, lat } = this.links(d, rep);
    if (cfg.shape === "force") this.simulate(items, req, G.state.lateral ? lat : [], members);
    const col = this.colours(d);

    const root = svg("svg", { class: "knowledge" });
    const defs = svg("defs");
    const arrow = svg("marker", { id: "karrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "9", markerHeight: "9", markerUnits: "userSpaceOnUse", orient: "auto" });
    arrow.append(svg("path", { d: "M0,0 L10,5 L0,10 z", fill: "#bdbdbd" }));
    defs.append(arrow); root.append(defs);
    const g = svg("g"); root.append(g);
    G.els.svg = root; G.els.g = g;

    // glows: one per cluster, around its macro-node or its open concepts
    const glows = svg("g"), names = svg("g");
    d.clusters.forEach((c, i) => {
      const colour = col.cluster(c.id);
      const pts = this.expanded(c.id) ? members[c.id].map((n) => items[n.id]) : [items["C:" + c.id]];
      const x0 = Math.min(...pts.map((p) => p.x)), x1 = Math.max(...pts.map((p) => p.x));
      const y0 = Math.min(...pts.map((p) => p.y)), y1 = Math.max(...pts.map((p) => p.y));
      const grad = svg("radialGradient", { id: `kglow${i}` });
      for (const [o, a] of [[0, 0.5], [0.55, 0.22], [1, 0]]) grad.append(svg("stop", { offset: o, "stop-color": colour, "stop-opacity": a * cfg.glow }));
      defs.append(grad);
      const open = this.expanded(c.id);
      glows.append(svg("ellipse", { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, rx: (x1 - x0) / 2 + (open ? 110 : 95), ry: (y1 - y0) / 2 + (open ? 80 : 75), fill: `url(#kglow${i})` }));
      if (open) {
        const t = svg("text", { x: (x0 + x1) / 2, y: y0 - 30, class: "cluster-label", fill: colour, "text-anchor": "middle", style: `font-size:${15 * cfg.labelSize}px` });
        t.textContent = `${c.name} · ${c.size}`;
        if (cfg.detail !== "all") { t.classList.add("clickable"); t.addEventListener("click", () => { this.open.delete(c.id); this.redrawAround(G, "C:" + c.id); }); }
        names.append(t);
      }
    });
    g.append(glows);

    // edges: reasoning chains. Premises of one concept meet at a junction, then one arrow to the concept.
    const edgeLayer = svg("g", { style: `opacity:${cfg.edgeOpacity}` });
    G.edgeEls = [];
    const trim = (from, to, r) => { const dx = to.x - from.x, dy = to.y - from.y, l = Math.hypot(dx, dy) || 1; return { x: to.x - dx / l * (r + 3), y: to.y - dy / l * (r + 3) }; };
    const line = (a, b, cls, keys, width, arrowed) => {
      const e = svg("line", { class: `kedge ${cls}`, x1: a.x, y1: a.y, x2: b.x, y2: b.y, "stroke-width": width });
      if (arrowed) e.setAttribute("marker-end", "url(#karrow)");
      e.dataset.keys = keys.join(" ");
      edgeLayer.append(e); G.edgeEls.push(e);
    };
    for (const [t, ss] of Object.entries(req)) {
      const T = items[t], srcs = Object.keys(ss).filter((s) => items[s]);
      if (!T || !srcs.length) continue;
      const rT = this.radius(T);
      if (T.kind === "concept" && cfg.junctions && srcs.length >= 2) {
        const mx = srcs.reduce((s, k) => s + items[k].x, 0) / srcs.length, my = srcs.reduce((s, k) => s + items[k].y, 0) / srcs.length;
        const J = { x: T.x + (mx - T.x) * 0.4, y: T.y + (my - T.y) * 0.4 };
        for (const s of srcs) line(items[s], J, "premise", [s, t], 1.4 * Math.sqrt(ss[s]), false);
        line(J, trim(J, T, rT), "conclusion", [t, ...srcs], 1.8, true);
        edgeLayer.append(svg("circle", { cx: J.x, cy: J.y, r: 2.2, class: "junction" }));
      } else {
        for (const s of srcs) line(items[s], trim(items[s], T, rT), "single", [s, t], Math.min(6, 1.2 + Math.log2(ss[s])), true);
      }
    }
    if (G.state.lateral) for (const [a, b] of lat) if (items[a] && items[b]) line(items[a], items[b], "lateral", [a, b], 1, false);
    g.append(edgeLayer, names);

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
        ng.addEventListener("click", (ev) => { ev.stopPropagation(); card.style.display = "none"; this.open.add(c.id); this.redrawAround(G, c.id); });
        ng.addEventListener("mouseenter", () => {
          this.highlight(it.key);
          showCard(ng, [el("b", {}, c.name), el("div", { class: "muted" }, `${c.size} concepts · click to open`),
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
    wrap.append(card, this.panel());
    G.finish(root);
    if (!keepView) requestAnimationFrame(() => G.fit());
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

  highlight(key) {
    const G = this.G;
    if (!key) { Object.values(G.nodeEls).forEach((n) => n.classList.remove("dim")); G.edgeEls.forEach((e) => e.classList.remove("dim", "hot")); return; }
    const near = new Set([key]);
    for (const e of G.edgeEls) { const ks = e.dataset.keys.split(" "); if (ks.includes(key)) ks.forEach((k) => near.add(k)); }
    for (const [k, n] of Object.entries(G.nodeEls)) n.classList.toggle("dim", !near.has(k));
    for (const e of G.edgeEls) { const hot = e.dataset.keys.split(" ").includes(key); e.classList.toggle("hot", hot); e.classList.toggle("dim", !hot); }
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
        el("button", { disabled: c.detail === "all", onclick: () => { this.open.clear(); this.redraw(); } }, "Close all")),
      row("Reasoning chains", check("junctions")),
      el("h4", {}, "Shape"),
      row("Layout", select("shape", [["layered", "Layered (arrows point up)"], ["force", "Force (physics)"]])),
      row("Repulsion", slider("repulsion", 0.2, 3, 0.1, force)),
      row("Link length", slider("linkLength", 0.4, 2.5, 0.1, force)),
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
      el("div", { class: "row" }, el("button", { onclick: () => { this.cfg = { ...KDEFAULTS }; this.save(); this.redraw(); } }, "Reset")));
    return el("div", { class: "kmenu" }, gear, box);
  },
};
