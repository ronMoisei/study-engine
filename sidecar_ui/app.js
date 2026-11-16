/* Study panel for the Antigravity side pane (also works opened directly in a browser against the local engine).
 * Plain JS, no libraries. All logic lives in the Python engine; this file only displays and sends input.
 * The app opens the page with ?token=…&conversationId=…; every API call carries the token. */
"use strict";

const params = new URLSearchParams(location.search);
const TOKEN = params.get("token");
const CONVERSATION = params.get("conversationId");
const STATUS = ["misconception", "fragile", "in_progress", "untested", "held"];
const EASE = { 1: "Again", 2: "Hard", 3: "Good", 4: "Easy" };
const DECISION = { new: "NEW", extends: "ADDS TO NOTE", covered: "ALREADY COVERED", merged: "MERGED" };
const NODE_W = 172, NODE_H = 34, COL = 196, ROW = 74;

// ── host theme (same messages the app sends to its own sidecars) ──
window.addEventListener("message", (ev) => {
  if (ev.data && ev.data.type === "theme-change" && ev.data.payload) {
    for (const [k, v] of Object.entries(ev.data.payload.cssVariables || {})) document.documentElement.style.setProperty(k, v);
  }
});

// ── helpers ──
async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (TOKEN) headers["X-Sidecar-Token"] = TOKEN;
  const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  let data = {};
  try { data = await r.json(); } catch (e) { data = { error: `HTTP ${r.status}` }; }
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
}
const svg = (tag, attrs) => { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v); return e; };

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

/** Math through the vendored KaTeX (vendor/katex); if it failed to load, the LaTeX source is shown instead. */
function tex(src, display) {
  if (window.katex) {
    try { return katex.renderToString(src, { displayMode: display, throwOnError: false, output: "html", strict: "ignore" }); }
    catch (e) { /* fall through to source */ }
  }
  return `<code class="math">${esc(display ? `$$${src}$$` : `$${src}$`)}</code>`;
}

/** Minimal markdown: paragraphs, "- " lists, **bold**, *italic*, ==highlight==, `code`, [[wikilinks]], $math$, $$math$$. */
function md(text) {
  const maths = [];
  let s = String(text ?? "").replace(/\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g, (m, d, i) => { maths.push(tex(d ?? i, d !== undefined)); return `\u0000${maths.length - 1}\u0000`; });
  s = esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>").replace(/==([^=\n]+)==/g, "<mark>$1</mark>")
    .replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, t, alias) => `<b class="wl">${alias || t}</b>`);
  const blocks = s.split(/\n{2,}/).map((b) => {
    const lines = b.split("\n");
    if (lines.every((l) => /^\s*- /.test(l))) return "<ul>" + lines.map((l) => `<li>${l.replace(/^\s*- /, "")}</li>`).join("") + "</ul>";
    return `<p>${lines.join("<br>")}</p>`;
  });
  return blocks.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => maths[+i]);
}
const mdEl = (text, cls) => { const d = el("div", { class: cls || "" }); d.innerHTML = md(text); return d; };
const pill = (status) => el("span", { class: `pill st st-${status}` }, status.replace("_", " "));

// ── self-rated level 1-5: picks the teaching mode (doctrine §11) ──
const LEVELS = {
  1: ["I don't know anything", "teach from scratch: introduction, then you derive it through exercises and proofs"],
  2: ["I know parts of it", "probe for the gaps, then teach only those"],
  3: ["I know it", "evaluation: hard problems on this topic alone"],
  4: ["I think I know everything", "exam: hard multi-step, multi-topic questions"],
  5: ["I master it", "project: a real, complex project for your CV"],
};
function levelPicker(onchange) {
  const hint = el("div", { class: "muted" }, "Pick your level: it decides how the session runs.");
  const box = el("div", { class: "levels", role: "radiogroup" });
  const picker = { value: null, el: el("div", {}, box, hint) };
  for (const [n, [label, mode]] of Object.entries(LEVELS)) {
    const b = el("button", { type: "button", role: "radio", "aria-checked": "false", title: `${label} — ${mode}`, onclick: () => {
      picker.value = +n;
      box.querySelectorAll("button").forEach((x) => { const on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-checked", String(on)); });
      hint.replaceChildren(el("b", {}, `${n} · ${label}`), ` → ${mode}`);
      onchange && onchange(+n);
    } }, el("span", { class: "lv" }, n), el("span", { class: "lv-label" }, label));
    box.append(b);
  }
  return picker;
}
const goalBox = (placeholder) => el("textarea", { class: "goal", rows: 2, placeholder });
const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== "")).toString();

/** Send / show / copy buttons for a message the engine builds (GET msgPath, POST sendPath). */
function messageActions(getParams, msgPath, sendPath, ready) {
  const msgBox = el("div", { class: "msgbox", style: "display:none" });
  const out = el("div", { class: "muted" });
  const fetchMsg = async () => (await api("GET", `${msgPath}?${qs(getParams())}`)).message;
  const send = el("button", { class: "primary", disabled: true, onclick: async () => {
    send.disabled = true;
    try {
      const r = await api("POST", sendPath, { ...getParams(), conversationId: CONVERSATION });
      out.textContent = r.sent_to ? "Sent to this conversation." : "Started a new conversation.";
    } catch (e) { out.textContent = e.message + " — use 'Show message' and paste it into a conversation."; }
    send.disabled = !ready();
  } }, CONVERSATION ? "Start in this conversation" : "Start (new conversation)");
  const show = el("button", { onclick: async () => {
    if (msgBox.style.display !== "none") { msgBox.style.display = "none"; return; }
    try { msgBox.textContent = await fetchMsg(); msgBox.style.display = "block"; } catch (e) { out.textContent = e.message; }
  } }, "Show message");
  const copy = el("button", { onclick: async () => {
    let m;
    try { m = await fetchMsg(); } catch (e) { out.textContent = e.message; return; }
    try { await navigator.clipboard.writeText(m); out.textContent = "Copied."; } catch (e) { msgBox.textContent = m; msgBox.style.display = "block"; out.textContent = "Clipboard blocked: select the text below."; }
  } }, "Copy");
  return { send, show, copy, out, msgBox, refresh: () => { send.disabled = !ready(); } };
}
function setStatus(t) { document.getElementById("status").textContent = t || ""; }
function view() { const v = document.getElementById("view"); v.replaceChildren(); return v; }
function err(parent, e) { parent.append(el("div", { class: "error" }, e.message || String(e))); }

// ── tabs ──
const TABS = { graph: () => Graph.render(), inbox: () => Inbox.home(), review: () => Review.home() };
document.getElementById("tabs").addEventListener("click", (ev) => {
  const t = ev.target.closest("button[data-tab]");
  if (!t) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === t));
  TABS[t.dataset.tab]();
});

// ═════════════════════════════ GRAPH ═════════════════════════════
const Graph = {
  state: { group: localStorage.getItem("study.group") || "", focus: null, lateral: true, data: null, tx: 0, ty: 0, k: 1 },

  async render() {
    const v = view();
    const s = this.state;
    const bar = el("div", { class: "row" });
    const sel = el("select", { onchange: () => { s.group = sel.value; s.focus = null; try { localStorage.setItem("study.group", s.group); } catch (e) { /* storage blocked */ } this.load(); } });
    const search = el("input", { type: "search", placeholder: "find a concept…", list: "node-names", style: "flex:1;min-width:120px" });
    const names = el("datalist", { id: "node-names" });
    search.addEventListener("change", () => {
      const hit = (s.data?.nodes || []).find((n) => n.name.toLowerCase() === search.value.toLowerCase()) ||
                  (s.data?.nodes || []).find((n) => n.name.toLowerCase().includes(search.value.toLowerCase()));
      if (hit) { this.center(hit); this.select(hit.id); }
    });
    const lat = el("label", { class: "muted" }, el("input", { type: "checkbox", checked: s.lateral, onchange: (e) => { s.lateral = e.target.checked; this.draw(); } }), " lateral links");
    bar.append(sel, search, names, lat);
    const info = el("div", { class: "row muted" });
    const legend = el("div", { class: "legend muted" }, ...STATUS.map((st) => el("span", { style: `--c: var(--st-${st})` }, st.replace("_", " "))),
      el("span", { style: "--c: transparent; border: 1px dashed var(--muted)" }, "course note (read-only)"));
    const wrap = el("div", { class: "graph-wrap" });
    v.append(bar, info, legend, wrap);
    this.els = { sel, search, names, info, wrap };
    await this.load();
  },

  async load() {
    const s = this.state;
    const q = new URLSearchParams();
    if (s.focus) q.set("focus", s.focus); else if (s.group) q.set("group", s.group);
    setStatus("loading graph…");
    try {
      s.data = await api("GET", "/graph?" + q.toString());
    } catch (e) { setStatus(""); return err(this.els.wrap, e); }
    setStatus("");
    const d = s.data;
    if (!s.focus && !s.group) {                         // first open: pick the largest group instead of everything
      const g = Object.entries(d.groups).sort((a, b) => b[1] - a[1])[0];
      if (g) { s.group = g[0]; return this.load(); }
    }
    const { sel, names, info } = this.els;
    sel.replaceChildren(...Object.entries(d.groups).map(([g, n]) => el("option", { value: g, selected: g === s.group && !s.focus }, `${g} (${n})`)));
    if (s.focus) sel.prepend(el("option", { value: "", selected: true }, `path to: ${d.nodes.find((n) => n.focus)?.name || s.focus}`));
    names.replaceChildren(...d.nodes.map((n) => el("option", { value: n.name })));
    const c = d.status_counts;
    info.replaceChildren(`${d.nodes.length} concepts · ${d.levels} levels · `,
      ...STATUS.filter((st) => c[st]).map((st) => el("span", {}, pill(st), ` ${c[st]} `)),
      s.focus ? el("a", { onclick: () => { s.focus = null; this.load(); } }, " ← whole group") : "");
    s.tx = 20; s.ty = 20; s.k = 1;
    this.draw();
    requestAnimationFrame(() => this.home());          // size is only known after layout
    if (!this.resizeObs) {
      let last = 0;
      this.resizeObs = new ResizeObserver(() => {
        const w = this.els.wrap?.getBoundingClientRect().width || 0;
        if (Math.abs(w - last) > 40) { last = w; this.home(); }
      });
    }
    this.resizeObs.disconnect();
    this.resizeObs.observe(this.els.wrap);
  },

  /** Default view: fit the WIDTH, show the foundations (bottom rows); pan up for what builds on them. */
  home() {
    const s = this.state, pos = Object.values(this.pos || {});
    if (!pos.length || !this.els.svg) return;
    const r = this.els.svg.getBoundingClientRect();
    if (!r.width) return;
    const focus = (s.data.nodes || []).find((n) => n.focus);
    if (focus) return this.center(focus);
    const w = Math.max(...pos.map((p) => p.x)) + NODE_W, h = Math.max(...pos.map((p) => p.y)) + NODE_H;
    s.k = Math.min(1, Math.max(0.8, (r.width - 40) / w));   // keep labels readable; pan sideways if wider
    s.tx = (r.width - w * s.k) / 2;                    // centred; a wider graph overflows evenly on both sides
    s.ty = h * s.k + 40 <= r.height ? (r.height - h * s.k) / 2 : r.height - 20 - h * s.k;
    this.apply();
  },

  draw() {
    const s = this.state, d = s.data, wrap = this.els.wrap;
    if (!d) return;
    wrap.replaceChildren();
    const root = svg("svg");
    const defs = svg("defs");
    const mk = svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" });
    mk.append(svg("path", { d: "M0,0 L10,5 L0,10 z", fill: "var(--muted)" }));
    defs.append(mk); root.append(defs);
    const g = svg("g"); root.append(g);
    this.els.svg = root; this.els.g = g;
    const pos = {};
    for (const n of d.nodes) pos[n.id] = { x: n.x * COL, y: n.y * ROW };
    this.edgeEls = [];
    for (const e of d.edges) {
      if (e.type !== "requires" && !s.lateral) continue;
      const a = pos[e.to], b = pos[e.from];             // requires: arrow goes from the prerequisite up to the dependent
      if (!a || !b) continue;
      const x1 = a.x + NODE_W / 2, y1 = a.y, x2 = b.x + NODE_W / 2, y2 = b.y + NODE_H;
      const dy = Math.max(30, Math.abs(y1 - y2) / 2);
      const path = e.type === "requires"
        ? svg("path", { class: "edge", d: `M${x1},${y1} C${x1},${y1 - dy} ${x2},${y2 + dy} ${x2},${y2}`, "marker-end": "url(#arrow)" })
        : svg("path", { class: "edge lateral", d: `M${x1},${a.y + NODE_H / 2} L${x2},${b.y + NODE_H / 2}` });
      path.dataset.a = e.from; path.dataset.b = e.to;
      g.append(path); this.edgeEls.push(path);
    }
    this.nodeEls = {};
    for (const n of d.nodes) {
      const p = pos[n.id];
      const ng = svg("g", { class: `node${n.readonly ? " readonly" : ""}${n.focus ? " focus" : ""}`, transform: `translate(${p.x},${p.y})`, style: "cursor:pointer" });
      ng.append(svg("rect", { width: NODE_W, height: NODE_H, fill: "var(--card-bg)", stroke: `var(--st-${n.status})` }));
      ng.append(svg("rect", { width: 6, height: NODE_H, fill: `var(--st-${n.status})`, stroke: "none" }));
      const label = n.name.length > 26 ? n.name.slice(0, 25) + "…" : n.name;
      const t = svg("text", { x: 12, y: 21 }); t.textContent = label; ng.append(t);
      const title = svg("title"); title.textContent = `${n.name}\n${n.status}${n.topic ? " · " + n.topic : ""}`; ng.append(title);
      ng.addEventListener("click", (ev) => { ev.stopPropagation(); this.select(n.id); });
      ng.addEventListener("mouseenter", () => this.highlight(n.id));
      ng.addEventListener("mouseleave", () => this.highlight(null));
      g.append(ng); this.nodeEls[n.id] = ng;
    }
    this.pos = pos;
    this.panzoom(root);
    wrap.append(root, el("div", { class: "zoom" },
      el("button", { onclick: () => this.zoomBy(1.25), title: "zoom in" }, "+"),
      el("button", { onclick: () => this.zoomBy(0.8), title: "zoom out" }, "−"),
      el("button", { onclick: () => this.fit(), title: "show everything" }, "⤢"),
      el("button", { onclick: () => this.home(), title: "foundations" }, "⌂")));
    this.apply();
  },

  highlight(id) {
    const d = this.state.data;
    if (!id) { Object.values(this.nodeEls).forEach((n) => n.classList.remove("dim")); this.edgeEls.forEach((e) => e.classList.remove("dim", "hot")); return; }
    const near = new Set([id]);
    for (const e of d.edges) { if (e.from === id) near.add(e.to); if (e.to === id) near.add(e.from); }
    for (const [nid, n] of Object.entries(this.nodeEls)) n.classList.toggle("dim", !near.has(nid));
    for (const e of this.edgeEls) { const hot = e.dataset.a === id || e.dataset.b === id; e.classList.toggle("hot", hot); e.classList.toggle("dim", !hot); }
  },

  panzoom(root) {
    const s = this.state;
    let drag = null;
    root.addEventListener("pointerdown", (ev) => {
      if (ev.target.closest && ev.target.closest(".node")) return;   // let nodes receive their click
      drag = { x: ev.clientX, y: ev.clientY, tx: s.tx, ty: s.ty }; root.classList.add("dragging"); root.setPointerCapture(ev.pointerId);
    });
    root.addEventListener("pointermove", (ev) => { if (!drag) return; s.tx = drag.tx + ev.clientX - drag.x; s.ty = drag.ty + ev.clientY - drag.y; this.apply(); });
    root.addEventListener("pointerup", () => { drag = null; root.classList.remove("dragging"); });
    root.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const r = root.getBoundingClientRect();
      this.zoomBy(ev.deltaY < 0 ? 1.12 : 0.89, ev.clientX - r.left, ev.clientY - r.top);
    }, { passive: false });
  },
  zoomBy(f, cx, cy) {
    const s = this.state, r = this.els.svg.getBoundingClientRect();
    cx = cx ?? r.width / 2; cy = cy ?? r.height / 2;
    const k = Math.min(3, Math.max(0.15, s.k * f));
    s.tx = cx - (cx - s.tx) * (k / s.k); s.ty = cy - (cy - s.ty) * (k / s.k); s.k = k;
    this.apply();
  },
  apply() { const s = this.state; this.els.g?.setAttribute("transform", `translate(${s.tx},${s.ty}) scale(${s.k})`); },
  fit() {
    const s = this.state, pos = Object.values(this.pos || {});
    if (!pos.length) return;
    const r = this.els.svg.getBoundingClientRect();
    if (!r.width) return;
    const w = Math.max(...pos.map((p) => p.x)) + NODE_W, h = Math.max(...pos.map((p) => p.y)) + NODE_H;
    s.k = Math.min(1.2, Math.max(0.15, Math.min((r.width - 40) / w, (r.height - 40) / h)));
    s.tx = (r.width - w * s.k) / 2; s.ty = 20;
    this.apply();
  },
  center(n) {
    const s = this.state, r = this.els.svg.getBoundingClientRect(), p = this.pos[n.id];
    s.k = Math.max(s.k, 0.8);
    s.tx = r.width / 2 - (p.x + NODE_W / 2) * s.k; s.ty = r.height / 2 - (p.y + NODE_H / 2) * s.k;
    this.apply();
  },

  async select(id) {
    const wrap = this.els.wrap;
    wrap.querySelector(".drawer")?.remove();
    const dr = el("div", { class: "drawer" }, el("span", { class: "muted" }, "loading…"));
    wrap.append(dr);
    let n;
    try { n = await api("GET", `/node/${encodeURIComponent(id)}`); } catch (e) { dr.replaceChildren(); return err(dr, e); }
    const link = (x) => el("a", { onclick: () => { if (this.pos[x.id]) { this.center({ id: x.id }); } this.select(x.id); } }, x.name);
    const goal = goalBox("Goal / scope (optional): e.g. theory only, no code");
    let acts;
    const picker = levelPicker(() => acts.refresh());
    acts = messageActions(() => ({ id, level: picker.value, goal: goal.value.trim() }), "/teach-message", "/teach", () => !!picker.value);
    acts.send.textContent = CONVERSATION ? "Teach me this" : "Teach me this (new conversation)";
    dr.replaceChildren(
      el("button", { class: "close", onclick: () => dr.remove() }, "✕"),
      el("h3", {}, n.name),
      el("div", { class: "row" }, pill(n.status), el("span", { class: "muted" }, [n.kind, n.topic || n.group].filter(Boolean).join(" · ") + (n.readonly ? " · course note" : ""))),
      mdEl(n.definition),
      n.propositions.length ? el("ul", {}, ...n.propositions.map((p) => { const li = el("li"); li.innerHTML = md(p.text); return li; })) : "",
      el("h4", {}, "How well do you know it?"), picker.el, goal,
      el("div", { class: "row" }, acts.send, acts.show, acts.copy,
        el("button", { onclick: () => { this.state.focus = id; this.load(); } }, "Show path"),
        n.obsidian ? el("a", { href: n.obsidian, target: "_blank" }, "Open in Obsidian") : ""),
      acts.out, acts.msgBox,
      el("h4", {}, `Requires (${n.requires.length})`),
      n.requires.length ? el("ul", { class: "links" }, ...n.requires.map((x) => el("li", {}, pill(x.status), link(x)))) : el("div", { class: "muted" }, "nothing: a foundation"),
      el("h4", {}, `Needed by (${n.required_by.length})`),
      el("ul", { class: "links" }, ...n.required_by.map((x) => el("li", {}, pill(x.status), link(x)))),
      n.related.length ? el("h4", {}, "Related") : "",
      n.related.length ? el("ul", { class: "links" }, ...n.related.map((x) => el("li", {}, `${x.type.replace("_", " ")}: `, link(x)))) : "",
      el("h4", {}, `Study path (${n.path.length} steps)`),
      el("ol", { class: "path" }, ...n.path.map((x) => el("li", {}, pill(x.status), x.id === id ? el("b", {}, x.name) : link(x)))),
      n.file ? el("div", { class: "muted" }, n.file) : "",
    );
  },
};

// ═════════════════════════════ INBOX ═════════════════════════════
const Inbox = {
  poll: null,
  async home(note) {
    clearInterval(this.poll);
    const v = view();
    if (note) v.append(el("div", { class: "muted" }, note));
    // 1. what to learn: target + goal + level -> a learning session in agy
    v.append(el("h3", {}, "What do you want to learn?"));
    const target = el("input", { type: "text", list: "courses", style: "width:100%",
      placeholder: "file, folder, course or topic — e.g. 01TXLWS, Slides/lecture3.pdf, reinforcement learning" });
    const courseList = el("datalist", { id: "courses" });
    api("GET", "/courses").then((r) => courseList.replaceChildren(...r.courses.map((c) => el("option", { value: c })))).catch(() => {});
    const goal = goalBox("What you want to learn, and what you think you're missing. E.g. \"exam question: build a classifier. I know how classifiers work, not numpy\" · \"theory only, no code\"");
    goal.rows = 3;
    let acts;
    const picker = levelPicker(() => acts.refresh());
    acts = messageActions(() => ({ target: target.value.trim(), level: picker.value, goal: goal.value.trim() }),
      "/learn-message", "/learn", () => !!picker.value && !!(target.value.trim() || goal.value.trim()));
    acts.send.textContent = CONVERSATION ? "Start learning" : "Start learning (new conversation)";
    target.addEventListener("input", acts.refresh);
    goal.addEventListener("input", acts.refresh);
    v.append(target, courseList, goal, picker.el, el("div", { class: "row" }, acts.send, acts.show, acts.copy), acts.out, acts.msgBox);

    // 2. the same file / folder / course into concept proposals for the graph
    v.append(el("h3", {}, "Add material to the graph"),
      el("div", { class: "muted" }, "Turns the file, folder or course above into concept proposals for your approval (cloud calls per file)."));
    const dom = el("input", { type: "text", placeholder: "domain (default: from the course folder)" });
    const prog = el("div", { class: "muted" });
    const go = el("button", { onclick: async () => {
      if (!target.value.trim()) return (prog.textContent = "Give a file, folder or course above.");
      go.disabled = true;
      try { const job = await api("POST", "/ingest", { path: target.value, domain: dom.value }); this.watch(job.id, prog, go); }
      catch (e) { prog.textContent = e.message; go.disabled = false; }
    } }, "Analyse");
    v.append(el("div", { class: "row" }, dom, go), prog, el("h3", {}, "Proposals"));
    let list = [];
    try { list = (await api("GET", "/proposals")).proposals; } catch (e) { return err(v, e); }
    if (!list.length) v.append(el("div", { class: "muted" }, "None. Analyse a file to get one."));
    for (const p of list) {
      const row = el("div", { class: "proposal" });
      if (p.outdated) {
        row.append(el("b", {}, p.source.split("/").pop()), el("div", { class: "muted" }, "made by the old pipeline"),
          el("button", { onclick: async () => { await api("POST", `/proposals/${p.id}/discard`); this.home(); } }, "Discard"));
      } else {
        row.append(el("a", { onclick: () => this.show(p.id) }, p.source.split("/").pop()),
          el("div", { class: "muted" }, `${p.domain} · ${p.stats.new} new · ${p.stats.covered} covered · ${p.pending ? p.pending + " to review" : "done"} · ${p.created.replace("T", " ")}`));
      }
      v.append(row);
    }
  },

  watch(jid, prog, button) {
    const STAGE = { parse: "reading file", extract: "extracting claims", group: "grouping into concepts", relate: "deciding concepts and links" };
    this.poll = setInterval(async () => {
      try {
        const j = await api("GET", `/jobs/${jid}`);
        const file = j.files > 1 ? `file ${j.file_index + 1}/${j.files} (${j.current || ""}): ` : "";
        if (j.state === "running") prog.textContent = `${file}${STAGE[j.stage] || j.stage}… ${j.total ? `${j.done + 1}/${j.total}` : ""}`;
        else {
          clearInterval(this.poll);
          const errs = (j.errors || []).length ? ` · ${j.errors.length} failed: ${j.errors.join("; ")}` : "";
          if (j.state === "done" && j.proposals.length === 1) this.show(j.proposal, errs ? errs.slice(3) : undefined);
          else if (j.state === "done") this.home(`Analysed ${j.files} files into ${j.proposals.length} proposals (domain ${j.domain})${errs}.`);
          else { prog.textContent = j.error; button.disabled = false; }
        }
      } catch (e) { clearInterval(this.poll); prog.textContent = e.message; button.disabled = false; }
    }, 1000);
  },

  async show(pid, note) {
    clearInterval(this.poll);
    const v = view();
    let p;
    try { p = await api("GET", `/proposals/${pid}`); } catch (e) { return this.home(e.message); }
    const st = p.stats, lk = st.links || {}, t = (st.usage && st.usage.tokens) || {};
    v.append(el("a", { onclick: () => this.home() }, "← back"), el("h3", {}, p.source.name.split("/").pop()),
      el("div", { class: "muted" }, `${p.domain} · ${st.claims} claims → ${st.concepts} concepts: ${st.new} new, ${st.extends} extend a note, ${st.covered} covered, ${st.merged} merged`),
      el("div", { class: "muted" }, `links: ${lk.confident || 0} confident · ${lk.model || 0} model only · ${lk.conflict || 0} conflicts · cloud tokens ${t.total_tokens || 0}`));
    if (note) v.append(el("div", { class: "warn" }, note));
    const items = new Map(), links = [];
    const linkRow = (parent, e, from, to, label) => {
      const cb = el("input", { type: "checkbox", checked: e.accepted });
      links.push({ cb, key: [from, e.predicate, to], conflict: e.confidence === "conflict" });
      parent.append(el("div", { class: "edge-row" }, cb, el("span", { class: `badge badge-${e.confidence}` }, e.confidence), ` ${label} `,
        el("b", {}, label === "prerequisite of" ? from : to),
        ...(e.evidence || []).map((x) => el("div", { class: "muted indent" }, "✓ " + x)),
        ...(e.conflict || []).map((x) => el("div", { class: "warn indent" }, "⚠ " + x))));
    };
    for (const it of p.items) {
      const c = it.concept;
      const d = el("div", { class: `item item-${it.decision} st-${it.status}` });
      const head = el("div", { class: "row" });
      if (it.status === "pending" && (it.decision === "new" || it.decision === "extends")) { const cb = el("input", { type: "checkbox", checked: true }); items.set(it.index, cb); head.append(cb); }
      head.append(el("span", { class: "badge" }, DECISION[it.decision] || it.decision), el("b", {}, c.title), el("span", { class: "muted" }, `${c.kind} · ${c.topic}${it.status !== "pending" ? " · " + it.status : ""}`));
      d.append(head);
      if (it.match) d.append(el("div", { class: "muted" }, `${it.decision === "merged" ? "merged into" : it.decision === "extends" ? "adds to" : "already covered by"} ${it.match}`));
      d.append(mdEl(c.definition), el("div", { class: "quote" }, `“${c.quote}” — ${c.anchor.split("/").pop()}`));
      const props = it.decision === "extends" ? it.new_propositions : it.decision === "new" ? c.propositions : [];
      if (props.length) d.append(el("ul", {}, ...props.map((x) => { const li = el("li"); li.innerHTML = md(x.text); return li; })));
      if (it.decision === "new") {
        for (const e of it.edges) linkRow(d, e, it.slug, e.to, e.predicate === "requires" ? "requires" : e.predicate.replace("_", " "));
        for (const e of it.required_by) linkRow(d, e, e.from, it.slug, "prerequisite of");
        if (it.dropped_edges.length) {
          const det = el("details", {}, el("summary", { class: "muted" }, `${it.dropped_edges.length} links dropped by the checks`));
          for (const x of it.dropped_edges) det.append(el("div", { class: "muted" }, `${x.from} ${x.predicate} ${x.to} — ${x.reason}`));
          d.append(det);
        }
      }
      v.append(d);
    }
    const bar = el("div", { class: "row bar" });
    const act = async (fn) => { try { await fn(); } catch (e) { bar.append(el("span", { class: "error" }, e.message)); } };
    if (items.size) {
      bar.append(el("button", { class: "primary", onclick: () => act(async () => {
        const accept = [...items].filter(([, cb]) => cb.checked).map(([i]) => i);
        const reject_edges = links.filter((l) => !l.cb.checked).map((l) => l.key);
        const accept_edges = links.filter((l) => l.cb.checked && l.conflict).map((l) => l.key);
        const s = await api("POST", `/proposals/${pid}/apply`, { accept, reject_edges, accept_edges });
        this.show(pid, `Written ${s.written.length} · extended ${s.extended.length} · ${s.dropped_edges.length} links dropped` + (s.errors.length ? ` · errors: ${s.errors.join("; ")}` : ""));
      }) }, "Approve checked"),
      el("button", { onclick: () => act(async () => {
        const reject = [...items].filter(([, cb]) => !cb.checked).map(([i]) => i);
        await api("POST", `/proposals/${pid}/apply`, { accept: [], reject });
        this.show(pid, `Rejected ${reject.length}.`);
      }) }, "Reject unchecked"));
    }
    bar.append(el("button", { onclick: () => act(async () => {
      const plan = await api("POST", `/proposals/${pid}/plan`);
      this.show(pid, `Study plan written: ${plan.file.split(/[\\/]/).pop()} · ${plan.steps.length} steps, ${plan.to_study} not yet held${plan.warnings.length ? `, ${plan.warnings.length} possible gaps` : ""}`);
    }) }, "Build study plan"),
    el("button", { onclick: () => act(async () => { await api("POST", `/proposals/${pid}/discard`); this.home("Proposal discarded."); }) }, "Discard"));
    v.append(bar);
  },
};

// ═════════════════════════════ REVIEW ═════════════════════════════
const Review = {
  sid: null,
  async home(note) {
    const v = view();
    if (note) v.append(el("div", { class: "muted" }, note));
    let h;
    try { h = await api("GET", "/health"); } catch (e) { return err(v, e); }
    v.append(el("div", { class: "muted" }, `models: ${h.provider}${h.dry_run ? " · DRY RUN" : ""} · Anki ${h.anki ? "✓" : "✗ — open desktop Anki"}`));
    if (!h.anki) return v.append(el("button", { onclick: () => this.home() }, "Retry"));
    const decks = (await api("GET", "/decks")).decks;
    const sel = el("select", {}, el("option", { value: "" }, "(all decks)"), ...decks.map((d) => el("option", { value: d, selected: d === localStorage.getItem("study.deck") }, d)));
    const lim = el("input", { type: "number", value: "20", style: "width:64px" });
    const go = el("button", { class: "primary", onclick: async () => {
      go.disabled = true;
      try { localStorage.setItem("study.deck", sel.value); } catch (e) { /* storage blocked */ }
      try { const vw = await api("POST", "/review/start", { deck: sel.value || null, limit: +lim.value || 20 }); this.sid = vw.session; this.render(vw); }
      catch (e) { err(v, e); go.disabled = false; }
    } }, "Start review");
    v.append(el("div", { class: "row" }, sel, lim, go));
  },
  render(vw) {
    if (vw.state === "done") return this.summary(vw.summary);
    if (vw.state === "answered") return this.result(vw, vw.result);
    return this.card(vw);
  },
  header(v, vw) {
    v.append(el("a", { onclick: () => this.home("Review paused. Answered cards are rated and logged.") }, "← back"),
      el("div", { class: "muted" }, `${vw.position}/${vw.total} · ${vw.deck} · ${vw.node}`), mdEl(vw.front, "front"));
  },
  card(vw) {
    const v = view();
    this.header(v, vw);
    const ta = el("textarea", { class: "answer", placeholder: "Your answer — type or dictate. Think out loud." });
    const slider = el("input", { type: "range", min: 0, max: 100, step: 5, value: 50, style: "flex:1" });
    const lbl = el("span", { class: "muted" }, "confidence not set");
    let set = false;
    slider.addEventListener("input", () => { set = true; lbl.textContent = slider.value + "%"; });
    const idk = el("input", { type: "checkbox" });
    const submit = el("button", { class: "primary" }, "Submit (Ctrl+Enter)");
    const send = async () => {
      if (!ta.value.trim() && !idk.checked) return;
      submit.disabled = true; submit.textContent = "Grading…";
      try { this.result(vw, await api("POST", `/review/${this.sid}/answer`, { answer: ta.value, confidence: idk.checked || !set ? null : +slider.value, idk: idk.checked })); }
      catch (e) { err(v, e); submit.disabled = false; submit.textContent = "Submit (Ctrl+Enter)"; }
    };
    submit.addEventListener("click", send);
    ta.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); send(); } });
    v.append(ta, el("div", { class: "row" }, "Confidence", slider, lbl, el("label", {}, idk, " I don't know")), submit);
    ta.focus();
  },
  result(vw, r) {
    const v = view();
    this.header(v, vw);
    v.append(el("div", { class: `verdict v-${r.verdict}` }, `${r.verdict.toUpperCase()} · ${EASE[r.ease]}${r.misconception ? " · MISCONCEPTION" : ""}${r.miss ? " · miss: " + r.miss : ""}`));
    v.append(el("ul", { class: "items" }, ...r.items.map((it) => {
      const li = el("li", { class: it.met ? "" : "unmet" }, (it.met ? "✓ " : "✗ ") + (it.essential ? "★ " : ""));
      li.append(mdEl(it.point));
      if (it.quote) li.append(el("div", { class: "quote" }, `“${it.quote}”`));
      if (it.note) li.append(el("div", { class: "warn" }, it.note));
      return li;
    })));
    if (r.feedback) v.append(el("p", {}, r.feedback));
    if (r.contradiction_note) v.append(el("div", { class: "error" }, "False claim: " + r.contradiction_note));
    v.append(el("h4", {}, "Card answer"), mdEl(r.back, "back"));
    if (r.remedial?.length) v.append(el("h4", {}, "Remedial cards (due now)"), ...r.remedial.map((x) => mdEl(`**Q:** ${x.front}\n\n**A:** ${x.back}`, "item")));
    for (const e of r.errors || []) v.append(el("div", { class: "error" }, e));
    const next = el("button", { class: "primary", onclick: async () => { next.disabled = true; try { this.render(await api("POST", `/review/${this.sid}/next`)); } catch (e) { err(v, e); next.disabled = false; } } }, "Next");
    v.append(next);
    next.focus();
  },
  summary(s) {
    const v = view(), c = s.verdicts || {};
    v.append(el("h3", {}, "Session done"),
      el("div", {}, `Reviewed ${s.reviewed} · correct ${c.correct || 0} · partial ${c.partial || 0} · wrong ${c.wrong || 0} · idk ${c.idk || 0} · remedial cards ${s.remedial_added}`),
      el("button", { onclick: () => this.home() }, "Back"));
  },
};

Graph.render();
