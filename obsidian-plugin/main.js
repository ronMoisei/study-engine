/* Study Panel — Obsidian UI for the local study-engine (http://127.0.0.1:8770).
 * Plain CommonJS, no build step. All logic lives in the engine; this file only displays and sends input.
 * Screens: Home (review start, ingest, inbox list) · Card · Result · Summary · Proposal (inbox review). */
"use strict";
const { Plugin, ItemView, PluginSettingTab, Setting, Notice, MarkdownRenderer, requestUrl } = require("obsidian");

const VIEW = "study-panel-view";
const DEFAULTS = {
  engineUrl: "http://127.0.0.1:8770",
  autostart: true,
  pythonPath: "python",
  enginePath: "C:\\Users\\ronal\\code\\study-engine",
  engineArgs: "",
  defaultDeck: "",
  defaultDomain: "",
  limit: 20,
};
const EASE = { 1: "Again", 2: "Hard", 3: "Good", 4: "Easy" };
const DECISION = { new: "NEW", extends: "ADDS TO NOTE", covered: "ALREADY COVERED", merged: "MERGED" };
const STAGE = { parse: "reading file", extract: "extracting claims", group: "grouping into concepts", relate: "deciding concepts and links" };

class StudyView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.sid = null;
    this.poll = null;
  }
  getViewType() { return VIEW; }
  getDisplayText() { return "Study"; }
  getIcon() { return "graduation-cap"; }

  async onOpen() { await this.renderHome(); }
  async onClose() {
    this.keyHandler && document.removeEventListener("keydown", this.keyHandler);
    this.poll && window.clearInterval(this.poll);
  }

  // ── helpers ──
  async api(method, path, body) {
    const r = await requestUrl({
      url: this.plugin.settings.engineUrl + path, method,
      body: body ? JSON.stringify(body) : undefined, contentType: "application/json", throw: false,
    });
    let data = {};
    try { data = r.json; } catch (e) { data = { error: r.text }; }
    if (r.status >= 400) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }
  async md(el, text) {
    if (MarkdownRenderer.render) await MarkdownRenderer.render(this.app, text || "", el, "", this);
    else await MarkdownRenderer.renderMarkdown(text || "", el, "", this);
  }
  root() {
    this.poll && window.clearInterval(this.poll);
    this.poll = null;
    const c = this.containerEl.children[1];
    c.empty();
    c.addClass("study-panel");
    return c;
  }
  error(c, msg) { c.createDiv({ cls: "sp-error", text: msg }); }
  nodeLink(el, id) {
    const a = el.createEl("a", { text: id, cls: "sp-nodelink" });
    a.onclick = () => this.app.workspace.openLinkText(id, "", false);
    return a;
  }

  // ── home ──
  async renderHome(note) {
    const c = this.root();
    c.createEl("h3", { text: "Study" });
    if (note) c.createDiv({ cls: "sp-note", text: note });
    const status = c.createDiv({ cls: "sp-status", text: "checking engine…" });
    let health;
    try {
      health = await this.plugin.ensureEngine();
    } catch (e) {
      status.setText("");
      this.error(c, `Engine not reachable: ${e.message}`);
      c.createEl("button", { text: "Retry" }).onclick = () => this.renderHome();
      return;
    }
    status.setText(`engine ✓ · models: ${health.provider}${health.dry_run ? " · DRY RUN" : ""} · Anki ${health.anki ? "✓" : "✗ (open Anki to review)"}`);

    // review
    c.createEl("h4", { text: "Review cards" });
    if (health.anki) {
      const decks = (await this.api("GET", "/decks")).decks;
      const row = c.createDiv({ cls: "sp-row" });
      const sel = row.createEl("select");
      sel.createEl("option", { text: "(all decks)", value: "" });
      for (const d of decks) sel.createEl("option", { text: d, value: d });
      sel.value = this.plugin.settings.defaultDeck || "";
      const lim = row.createEl("input", { type: "number", value: String(this.plugin.settings.limit) });
      lim.addClass("sp-limit");
      const go = row.createEl("button", { text: "Start review", cls: "mod-cta" });
      go.onclick = async () => {
        go.disabled = true;
        try {
          this.plugin.settings.defaultDeck = sel.value;
          await this.plugin.saveSettings();
          const view = await this.api("POST", "/review/start", { deck: sel.value || null, limit: Number(lim.value) || 20 });
          this.sid = view.session || null;
          await this.renderView(view);
        } catch (e) { new Notice(e.message); go.disabled = false; }
      };
    } else {
      c.createDiv({ cls: "sp-meta", text: "Anki is closed." });
    }

    // ingest
    c.createEl("h4", { text: "Add material" });
    const path = c.createEl("input", { type: "text", cls: "sp-wide", attr: { placeholder: "File path (.pdf .md .txt) — absolute, or relative to Material/" } });
    const row2 = c.createDiv({ cls: "sp-row" });
    const dom = row2.createEl("input", { type: "text", attr: { placeholder: "domain, e.g. deep-nlp" } });
    dom.value = this.plugin.settings.defaultDomain || "";
    const ing = row2.createEl("button", { text: "Analyse" });
    const prog = c.createDiv({ cls: "sp-meta" });
    ing.onclick = async () => {
      if (!path.value.trim() || !dom.value.trim()) { new Notice("Give a file path and a domain."); return; }
      ing.disabled = true;
      try {
        this.plugin.settings.defaultDomain = dom.value.trim();
        await this.plugin.saveSettings();
        const job = await this.api("POST", "/ingest", { path: path.value, domain: dom.value });
        this.watchJob(job.id, prog, ing);
      } catch (e) { new Notice(e.message); ing.disabled = false; }
    };

    // inbox
    c.createEl("h4", { text: "Inbox" });
    const list = (await this.api("GET", "/proposals")).proposals;
    if (!list.length) c.createDiv({ cls: "sp-meta", text: "No proposals. Analyse a file to get one." });
    for (const p of list) {
      const d = c.createDiv({ cls: "sp-proposal" });
      const a = d.createEl("a", { text: p.source.split("/").pop() });
      if (p.outdated) {
        d.createDiv({ cls: "sp-meta", text: "made by the old pipeline — discard and analyse again" });
        const x = d.createEl("button", { text: "Discard" });
        x.onclick = async () => { await this.api("POST", `/proposals/${p.id}/discard`); this.renderHome(); };
        continue;
      }
      a.onclick = () => this.renderProposal(p.id);
      d.createDiv({ cls: "sp-meta", text: `${p.domain} · ${p.stats.new} new · ${p.stats.covered} covered · ${p.pending ? p.pending + " to review" : "done"} · ${p.created.replace("T", " ")}` });
    }
  }

  watchJob(jid, prog, button) {
    this.poll = window.setInterval(async () => {
      try {
        const j = await this.api("GET", `/jobs/${jid}`);
        if (j.state === "running") {
          prog.setText(`${STAGE[j.stage] || j.stage}… ${j.total ? `${j.done + 1}/${j.total}` : ""}`);
        } else {
          window.clearInterval(this.poll); this.poll = null;
          if (j.state === "done") await this.renderProposal(j.proposal);
          else { prog.setText(""); this.error(prog, j.error || "failed"); button.disabled = false; }
        }
      } catch (e) { window.clearInterval(this.poll); this.poll = null; prog.setText(e.message); button.disabled = false; }
    }, 1000);
  }

  // ── proposal (inbox review) ──
  async renderProposal(pid, note) {
    const c = this.root();
    let p;
    try { p = await this.api("GET", `/proposals/${pid}`); } catch (e) { return this.renderHome(e.message); }
    this.backLink(c, () => this.renderHome());
    c.createEl("h3", { text: p.source.name.split("/").pop() });
    const st = p.stats;
    c.createDiv({ cls: "sp-meta", text: `${p.domain} · ${st.claims} claims → ${st.concepts} concepts: ${st.new} new, ${st.extends} extend a note, ${st.covered} already covered, ${st.merged} merged · ${st.dropped_unquoted} claims dropped (no supporting quote)` });
    const lk = st.links || {};
    c.createDiv({ cls: "sp-meta", text: `links: ${lk.confident || 0} confident · ${lk.model || 0} model only · ${lk.conflict || 0} conflicts · ${st.pairs_judged} pairs judged` });
    const t = (st.usage && st.usage.tokens) || {};
    c.createDiv({ cls: "sp-meta", text: `cloud tokens ${t.total_tokens || 0} · local tokens ${(t.local_prompt_tokens || 0) + (t.local_output_tokens || 0)} · ${st.seconds}s` });
    if (p.new_topics && p.new_topics.length) c.createDiv({ cls: "sp-meta", text: `new topics: ${p.new_topics.map((x) => x.title).join(", ")}` });
    if (note) c.createDiv({ cls: "sp-note", text: note });

    const items = new Map();   // index -> checkbox
    const links = [];          // {cb, key: [from, predicate, to], conflict}
    const linkRow = (parent, e, from, to, label) => {
      const row = parent.createDiv({ cls: `sp-edge sp-conf-${e.confidence}` });
      const cb = row.createEl("input", { type: "checkbox" });
      cb.checked = e.accepted;
      links.push({ cb, key: [from, e.predicate, to], conflict: e.confidence === "conflict" });
      row.createSpan({ cls: `sp-badge sp-badge-${e.confidence}`, text: e.confidence });
      row.appendText(" " + label + " ");
      this.nodeLink(row, label === "prerequisite of" ? from : to);
      for (const x of e.evidence || []) row.createDiv({ cls: "sp-meta sp-indent", text: "✓ " + x });
      for (const x of e.conflict || []) row.createDiv({ cls: "sp-itemnote sp-indent", text: "⚠ " + x });
    };

    for (const it of p.items) {
      const cpt = it.concept;
      const d = c.createDiv({ cls: `sp-item sp-item-${it.decision} sp-st-${it.status}` });
      const head = d.createDiv({ cls: "sp-item-head" });
      const writable = it.status === "pending" && (it.decision === "new" || it.decision === "extends");
      if (writable) { const cb = head.createEl("input", { type: "checkbox" }); cb.checked = true; items.set(it.index, cb); }
      head.createSpan({ cls: `sp-badge sp-badge-${it.decision}`, text: DECISION[it.decision] || it.decision });
      head.createSpan({ cls: "sp-item-name", text: " " + cpt.title });
      head.createSpan({ cls: "sp-meta", text: ` · ${cpt.kind} · ${cpt.topic}${it.status !== "pending" ? " · " + it.status : ""}` });
      if (it.match) {
        const m = d.createDiv({ cls: "sp-meta" });
        m.appendText(it.decision === "merged" ? "merged into " : it.decision === "extends" ? "adds to " : "already covered by ");
        this.nodeLink(m, it.match);
      }
      await this.md(d.createDiv({ cls: "sp-statement" }), cpt.definition);
      d.createDiv({ cls: "sp-quote", text: `“${cpt.quote}” — ${cpt.anchor.split("/").pop()}` });
      const props = it.decision === "extends" ? it.new_propositions : (it.decision === "new" ? cpt.propositions : []);
      if (props && props.length) {
        const ul = d.createEl("ul", { cls: "sp-props" });
        for (const x of props) await this.md(ul.createEl("li"), `${x.text} *(${x.source.split("#").pop()})*`);
      }
      if (it.decision === "new") {
        d.createDiv({ cls: "sp-meta", text: `→ Graph/${p.domain}/${it.slug}.md` });
        for (const e of it.edges) linkRow(d, e, it.slug, e.to, e.predicate === "requires" ? "requires" : e.predicate.replace("_", " "));
        for (const e of it.required_by) linkRow(d, e, e.from, it.slug, "prerequisite of");
        if (it.dropped_edges.length) {
          const det = d.createEl("details", { cls: "sp-dropped" });
          det.createEl("summary", { text: `${it.dropped_edges.length} links dropped by the checks` });
          for (const x of it.dropped_edges) det.createDiv({ cls: "sp-meta", text: `${x.from} ${x.predicate} ${x.to} — ${x.reason}` });
        }
      }
    }

    if (p.uncovered && p.uncovered.length) {
      const det = c.createEl("details");
      det.createEl("summary", { text: `${p.uncovered.length} passages produced no claims` });
      for (const u of p.uncovered) det.createDiv({ cls: "sp-meta", text: `${u.anchor.split("#").pop()}: ${u.preview}…` });
    }

    const bar = c.createDiv({ cls: "sp-row sp-bar" });
    const act = async (fn) => { try { await fn(); } catch (e) { new Notice(e.message); } };
    if (items.size) {
      bar.createEl("button", { text: "Approve checked", cls: "mod-cta" }).onclick = () => act(async () => {
        const accept = [...items].filter(([, cb]) => cb.checked).map(([i]) => i);
        if (!accept.length) { new Notice("No concept checked."); return; }
        const reject_edges = links.filter((l) => !l.cb.checked).map((l) => l.key);
        const accept_edges = links.filter((l) => l.cb.checked && l.conflict).map((l) => l.key);
        const s = await this.api("POST", `/proposals/${pid}/apply`, { accept, reject_edges, accept_edges });
        const msg = `Written ${s.written.length} · extended ${s.extended.length} · ${s.dropped_edges.length} links dropped` + (s.errors.length ? ` · ${s.errors.length} errors: ${s.errors.join("; ")}` : "");
        await this.renderProposal(pid, msg);
      });
      bar.createEl("button", { text: "Reject unchecked" }).onclick = () => act(async () => {
        const reject = [...items].filter(([, cb]) => !cb.checked).map(([i]) => i);
        if (!reject.length) { new Notice("Nothing unchecked."); return; }
        await this.api("POST", `/proposals/${pid}/apply`, { accept: [], reject });
        await this.renderProposal(pid, `Rejected ${reject.length}.`);
      });
    }
    bar.createEl("button", { text: "Build study plan" }).onclick = () => act(async () => {
      const plan = await this.api("POST", `/proposals/${pid}/plan`);
      new Notice(`${plan.steps.length} steps, ${plan.to_study} not yet held${plan.warnings.length ? `, ${plan.warnings.length} possible gaps` : ""}`);
      const rel = plan.file.replace(/\\/g, "/").split("/Obsidian Notes/").pop();
      await this.app.workspace.openLinkText(rel, "", true);
    });
    bar.createEl("button", { text: "Discard", cls: "mod-warning" }).onclick = () => act(async () => {
      await this.api("POST", `/proposals/${pid}/discard`);
      await this.renderHome("Proposal discarded. Notes already written stay in the vault.");
    });
  }

  backLink(c, fn) {
    const a = c.createEl("a", { text: "← back", cls: "sp-back-link" });
    a.onclick = fn;
    return a;
  }


  // ── review ──
  async renderView(view) {
    if (view.state === "done") return this.renderSummary(view.summary);
    if (view.state === "answered") return this.renderResult(view, view.result);
    return this.renderCard(view);
  }

  async renderCard(view) {
    const c = this.root();
    this.backLink(c, () => this.renderHome("Review paused. Cards already answered are rated and logged."));
    c.createDiv({ cls: "sp-meta", text: `${view.position}/${view.total} · ${view.deck} · ${view.node}` });
    await this.md(c.createDiv({ cls: "sp-front" }), view.front);

    const ta = c.createEl("textarea", { cls: "sp-answer", attr: { placeholder: "Your answer — type, or dictate with Win+H. Think out loud." } });
    const confRow = c.createDiv({ cls: "sp-row sp-conf" });
    confRow.createSpan({ text: "Confidence " });
    const slider = confRow.createEl("input", { type: "range", attr: { min: "0", max: "100", step: "5" } });
    slider.value = "50";
    const confLbl = confRow.createSpan({ cls: "sp-unset", text: "not set" });
    let confSet = false;
    slider.oninput = () => { confSet = true; confLbl.setText(slider.value + "%"); confLbl.removeClass("sp-unset"); };
    const idkLbl = confRow.createEl("label", { cls: "sp-idk" });
    const idk = idkLbl.createEl("input", { type: "checkbox" });
    idkLbl.appendText(" I don't know");

    const submit = c.createEl("button", { text: "Submit  (Ctrl+Enter)", cls: "mod-cta" });
    const send = async () => {
      if (!ta.value.trim() && !idk.checked) { new Notice("Write an answer or tick 'I don't know'."); return; }
      submit.disabled = true; submit.setText("Grading…");
      try {
        const r = await this.api("POST", `/review/${this.sid}/answer`, {
          answer: ta.value, confidence: idk.checked || !confSet ? null : Number(slider.value), idk: idk.checked,
        });
        await this.renderResult(view, r);
      } catch (e) { new Notice(e.message); submit.disabled = false; submit.setText("Submit  (Ctrl+Enter)"); }
    };
    submit.onclick = send;
    ta.onkeydown = (ev) => { if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); send(); } };
    ta.focus();
  }

  async renderResult(view, r) {
    const c = this.root();
    this.backLink(c, () => this.renderHome("Review paused. Cards already answered are rated and logged."));
    c.createDiv({ cls: "sp-meta", text: `${view.position}/${view.total} · ${view.deck} · ${view.node}` });
    await this.md(c.createDiv({ cls: "sp-front sp-dim" }), view.front);

    const v = c.createDiv({ cls: `sp-verdict sp-${r.verdict}` });
    v.setText(`${r.verdict.toUpperCase()} · ${EASE[r.ease]}${r.misconception ? " · MISCONCEPTION" : ""}${r.miss ? " · miss: " + r.miss : ""}`);

    const ul = c.createEl("ul", { cls: "sp-items" });
    for (const it of r.items) {
      const li = ul.createEl("li", { cls: it.met ? "sp-met" : "sp-unmet" });
      li.createSpan({ text: (it.met ? "✓ " : "✗ ") + (it.essential ? "★ " : "") });
      await this.md(li.createSpan({ cls: "sp-point" }), it.point);
      if (it.quote) li.createDiv({ cls: "sp-quote", text: `“${it.quote}”` });
      if (it.note) li.createDiv({ cls: "sp-itemnote", text: it.note });
    }
    if (r.feedback) c.createDiv({ cls: "sp-feedback", text: r.feedback });
    if (r.contradiction_note) c.createDiv({ cls: "sp-error", text: "False claim: " + r.contradiction_note });

    c.createEl("h4", { text: "Card answer" });
    await this.md(c.createDiv({ cls: "sp-back" }), r.back);

    if (r.remedial && r.remedial.length) {
      c.createEl("h4", { text: "Remedial cards (due now)" });
      for (const rc of r.remedial) {
        const d = c.createDiv({ cls: "sp-remedial" });
        await this.md(d, `**Q:** ${rc.front}\n\n**A:** ${rc.back}${rc.added === false ? "\n\n*(not added)*" : ""}`);
      }
    }
    for (const e of r.errors || []) this.error(c, e);

    const next = c.createEl("button", { text: "Next  (Ctrl+Enter)", cls: "mod-cta" });
    const go = async () => {
      next.disabled = true;
      try { await this.renderView(await this.api("POST", `/review/${this.sid}/next`)); }
      catch (e) { new Notice(e.message); next.disabled = false; }
    };
    next.onclick = go;
    this.keyHandler && document.removeEventListener("keydown", this.keyHandler);
    this.keyHandler = (ev) => {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey) && this.containerEl.contains(next)) {
        ev.preventDefault(); document.removeEventListener("keydown", this.keyHandler); go();
      }
    };
    document.addEventListener("keydown", this.keyHandler);
    next.focus();
  }

  renderSummary(s) {
    const c = this.root();
    c.createEl("h3", { text: "Session done" });
    const v = s.verdicts || {};
    c.createDiv({ text: `Reviewed ${s.reviewed} · correct ${v.correct || 0} · partial ${v.partial || 0} · wrong ${v.wrong || 0} · idk ${v.idk || 0}` });
    c.createDiv({ text: `Remedial cards added: ${s.remedial_added}` });
    const t = (s.usage && s.usage.tokens) || {};
    const tok = Object.keys(t).length ? Object.entries(t).map(([k, n]) => `${k} ${n}`).join(" · ") : "not reported";
    c.createDiv({ cls: "sp-meta", text: `Model calls: ${(s.usage && s.usage.calls) || 0} · tokens: ${tok}` });
    c.createEl("button", { text: "Back" }).onclick = () => this.renderHome();
  }
}

class StudySettings extends PluginSettingTab {
  constructor(app, plugin) { super(app, plugin); this.plugin = plugin; }
  display() {
    const c = this.containerEl;
    c.empty();
    const s = this.plugin.settings;
    const text = (name, key, desc) => new Setting(c).setName(name).setDesc(desc || "")
      .addText((t) => t.setValue(String(s[key])).onChange(async (v) => { s[key] = key === "limit" ? Number(v) || 20 : v; await this.plugin.saveSettings(); }));
    text("Engine URL", "engineUrl", "Local study-engine API");
    new Setting(c).setName("Start engine automatically").setDesc("Launch `python study.py serve` when the panel opens and the engine is not running")
      .addToggle((t) => t.setValue(s.autostart).onChange(async (v) => { s.autostart = v; await this.plugin.saveSettings(); }));
    text("Python", "pythonPath", "Python executable");
    text("Engine folder", "enginePath", "Folder containing study.py");
    text("Extra engine flags", "engineArgs", "e.g. --stub or --dry-run while testing");
    text("Cards per session", "limit");
  }
}

module.exports = class StudyPanelPlugin extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULTS, await this.loadData());
    this.child = null;
    this.registerView(VIEW, (leaf) => new StudyView(leaf, this));
    this.addRibbonIcon("graduation-cap", "Open Study panel", () => this.open());
    this.addCommand({ id: "open-study-panel", name: "Open Study panel", callback: () => this.open() });
    this.addSettingTab(new StudySettings(this.app, this));
  }
  async onunload() { if (this.child) { try { this.child.kill(); } catch (e) { /* ignore */ } } }
  async saveSettings() { await this.saveData(this.settings); }

  async open() {
    let leaf = this.app.workspace.getLeavesOfType(VIEW)[0];
    if (!leaf) { leaf = this.app.workspace.getRightLeaf(false); await leaf.setViewState({ type: VIEW, active: true }); }
    this.app.workspace.revealLeaf(leaf);
  }

  async health() {
    const r = await requestUrl({ url: this.settings.engineUrl + "/health", throw: false });
    if (r.status !== 200) throw new Error(`HTTP ${r.status}`);
    return r.json;
  }

  async ensureEngine() {
    try { return await this.health(); } catch (e) { if (!this.settings.autostart) throw e; }
    if (!this.child) {
      const { spawn } = require("child_process");
      const args = ["study.py", "serve"].concat(this.settings.engineArgs.split(/\s+/).filter(Boolean));
      this.child = spawn(this.settings.pythonPath, args, { cwd: this.settings.enginePath, windowsHide: true });
      this.child.on("exit", () => { this.child = null; });
      this.child.stderr.on("data", (d) => console.log("[study-engine]", String(d)));
    }
    for (let i = 0; i < 20; i++) {
      await new Promise((res) => setTimeout(res, 300));
      try { return await this.health(); } catch (e) { /* still starting */ }
    }
    throw new Error("engine did not start — check the Engine folder / Python settings");
  }
};
