"""One-time move to the flat, topic-organised graph (2026-11-20).

  Graph/<domain>/<slug>.md (engine concepts, format v2)  ->  Graph/<slug>.md, format v3 (engine/notes.py)
  Material/<course>/…/Atomic Notes/<stem>.md (older hand-written notes, read-only)
                                                          ->  split by Hydrogen into typed atomic notes in Graph/;
                                                              the main note keeps <stem> (links and Anki lh:: tags
                                                              keep working), pitfalls etc. get their own slugs;
                                                              the originals move to .study/retired/atomic-notes/

`report(cfg)` is the dry run: what would move, slug collisions, links that would not resolve, kinds that need a new
kind, likely duplicates (the same title in two places). Nothing is written.
"""
import json
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import graph as G
from . import notes, topics
from .graph import KINDS

# older note `type:` -> the closest kind; "?" = decided when the note is split (step 3)
OLD_TYPE_KIND = {"primitive": "definition", "metric": "definition", "mechanism": "mechanism",
                 "application": "application", "problem": "?"}


def _norm(title):
    return re.sub(r"[^a-z0-9]+", " ", str(title).lower()).strip()


def sources(cfg):
    """-> (engine notes [(path, parsed)], old notes [(path, frontmatter, body)])."""
    eng, old = [], []
    if cfg.graph.is_dir():
        for p in sorted(cfg.graph.rglob("*.md")):
            if p.name.startswith("_"):
                continue
            text = p.read_text(encoding="utf-8")
            if re.search(r"^type: concept\s*$", text[:400], re.M):
                eng.append((p, notes.parse_concept(text)))
    seen = set()
    for pattern in cfg.atomic_globs:
        for p in sorted(cfg.magistrale.glob(pattern)):
            if p in seen:
                continue
            seen.add(p)
            fm, body = G.split_frontmatter(p.read_text(encoding="utf-8"))
            if fm:
                old.append((p, fm, body))
    return eng, old


def course_of_path(cfg, p):
    try:
        return p.resolve().relative_to(cfg.material.resolve()).parts[0]
    except ValueError:
        return ""


def report(cfg):
    eng, old = sources(cfg)
    out = {"engine": len(eng), "old": len(old), "engine_by_folder": dict(Counter(p.parent.name for p, _ in eng)),
           "old_by_course": dict(Counter(course_of_path(cfg, p) for p, _, _ in old)),
           "already_flat": sum(1 for p, _ in eng if p.parent == cfg.graph)}
    # slugs after flattening: engine slugs, old stems (each old note's main node keeps its stem)
    owners = defaultdict(list)
    for p, _ in eng:
        owners[p.stem].append(str(p.relative_to(cfg.magistrale)))
    for p, _, _ in old:
        owners[p.stem].append(str(p.relative_to(cfg.magistrale)))
    out["collisions"] = {s: ps for s, ps in sorted(owners.items()) if len(ps) > 1}
    ids = set(owners)
    # links that would not resolve
    dangling = []
    for p, c in eng:
        for t in c["requires"] + c["prerequisite_of"] + [t for _, t in c["related"]]:
            if t not in ids:
                dangling.append((p.stem, t))
    for p, fm, _ in old:
        for key in ("prerequisites", "implications", "contrasts_with"):
            for t in G._links(fm.get(key) or []):
                if t not in ids:
                    dangling.append((p.stem, t))
    out["dangling"] = dangling
    # kinds
    out["engine_kinds"] = dict(Counter(c["kind"] for _, c in eng))
    out["engine_kind_changes"] = sum(1 for _, c in eng if c["kind"] not in G.KINDS)
    out["old_types"] = dict(Counter(str(fm.get("type", "")) for _, fm, _ in old))
    # the same title in two places: candidates for one note (decided in step 3)
    by_title = defaultdict(list)
    for p, c in eng:
        by_title[_norm(c["title"])].append(p.stem)
    for p, fm, _ in old:
        by_title[_norm(fm.get("title") or p.stem)].append(p.stem)
    out["same_title"] = {t: s for t, s in sorted(by_title.items()) if len(s) > 1}
    # what the old notes hold (sections Hydrogen splits into typed notes)
    secs = Counter()
    for _, _, body in old:
        for h in re.findall(r"^## (.+)$", body, re.M):
            secs[re.split(r"[/(]", h)[0].strip()] += 1
    out["old_sections"] = dict(secs.most_common(12))
    out["old_chars"] = sum(len(b) for _, _, b in old)
    return out


# ═══════════════════════ step 3: prepare (model calls; results staged, nothing written to Graph/) ═══════════════

SPLIT_VERSION, TAXONOMY_VERSION, ASSIGN_VERSION, DEDUPE_VERSION = "split-v1", "taxonomy-v1", "assign-v1", "dedupe-v1"
SPLIT_CHARS, ASSIGN_BATCH, DEDUPE_CHARS = 28000, 140, 60000
ARCHIVE = ".study/retired/atomic-notes"

_ITEM = {"title": {"type": "string"}, "kind": {"type": "string", "enum": KINDS}, "definition": {"type": "string"},
         "pseudocode": {"type": "string"}, "proof": {"type": "string"},
         "propositions": {"type": "array", "maxItems": 8, "items": {"type": "string"}}}
SPLIT_SCHEMA = {"type": "object", "required": ["notes"], "properties": {"notes": {"type": "array", "items": {
    "type": "object", "required": ["stem", "kind", "definition", "propositions", "extras"],
    "properties": {"stem": {"type": "string"}, **{k: v for k, v in _ITEM.items() if k != "title"},
                   "extras": {"type": "array", "maxItems": 4, "items": {
                       "type": "object", "required": ["title", "kind", "definition", "propositions"],
                       "properties": _ITEM}}}}}}}

SPLIT_PROMPT = """You restructure a student's hand-written study notes into TYPED atomic notes. Output JSON only.
Each input note explains one concept at length (core statement, the professor's intuition, a derivation, an example,
a pitfall). Keep its knowledge, restructured into the fixed shape of its kind:

  definition   what something is              definition = the statement; propositions = key properties
  theorem      a result under conditions      definition = the statement; propositions = its conditions;
                                              proof = a short proof sketch from the derivation
  algorithm    how to compute something       definition = what it computes; pseudocode = plain code lines (no
                                              ``` fences); propositions = why each step is there
  mechanism    how/why a system works         definition = the statement; propositions = the steps or causes
  pitfall      a typical mistake / failure    definition = what goes wrong; propositions = why it happens, then how
                                              to avoid it
  application  when/where to use something    definition = the use case; propositions = why it fits
  heuristic    a rule of thumb                definition = the rule; propositions = when it holds

For each input note (by `stem`) give:
- the MAIN note: its kind and fields (the concept the note is named after; its title stays the note's title);
- extras: a SEPARATE note only for a distinct retrievable unit — every pitfall (the `easy_pitfall` and pitfalls in the
  text) is its own `pitfall` note; a separate theorem, application or heuristic only when it stands on its own.
  Do not split a procedure into steps; advantages and limits stay propositions of the main note. 0-4 extras.
  Extra titles: a short standard name (for a pitfall: "<Concept>: <what goes wrong>").
Rules: use ONLY what the note says (no new facts, formulas or examples); worked numeric examples and implementation
listings are not notes (drop them; an algorithm's pseudocode is the general procedure, not one run). English. Math in
LaTeX $...$. Each proposition is one complete sentence that stands on its own (no "this", "the above"). At most 8.

INPUT_JSON:
"""

TAXONOMY_SCHEMA = {"type": "object", "required": ["macros"], "properties": {"macros": {"type": "array",
    "minItems": 3, "maxItems": 16, "items": {"type": "object", "required": ["slug", "title", "subs"], "properties": {
        "slug": {"type": "string"}, "title": {"type": "string"},
        "subs": {"type": "array", "minItems": 1, "maxItems": 20, "items": {
            "type": "object", "required": ["slug", "title"],
            "properties": {"slug": {"type": "string"}, "title": {"type": "string"}}}}}}}}}

TAXONOMY_PROMPT = """You organise ALL of a student's knowledge (several university courses: data science, big data,
NLP, databases, ...) into one topic tree. Output JSON only.
- macros: 5-14 MACRO topics = fields of knowledge (e.g. machine learning, optimization, distributed data processing,
  databases, natural language processing, statistics, innovation management) — never a course name; the same field
  taught in two courses is ONE macro topic. Each macro becomes a colour in the knowledge graph.
- subs: for each macro, its subtopics (3-15) in TEACHING ORDER, foundations first. A subtopic is a chapter-sized area
  (e.g. "decision trees", "spark sql", "attention"), not a single concept.
- slug: lowercase kebab-case, short (e.g. "machine-learning", "decision-trees"); title: a short human name.
Every note listed must fit some subtopic. `hint` is the chapter the note had before (may be course-specific).

INPUT_JSON:
"""

ASSIGN_SCHEMA = {"type": "object", "required": ["assign"], "properties": {"assign": {"type": "array", "items": {
    "type": "object", "required": ["id", "topics", "kind"], "properties": {
        "id": {"type": "string"}, "kind": {"type": "string", "enum": KINDS},
        "topics": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string"}}}}}}}

ASSIGN_PROMPT = """Give each note its topic(s) from the topic list and its kind. Output JSON only.
- topics: 1 (or, only when it truly belongs to two areas, 2) slugs EXACTLY as listed ("macro/sub").
- kind: definition | theorem | algorithm | mechanism | pitfall | application | heuristic. Keep the note's current
  kind when it fits; a note with kind "constraint" (no longer used) becomes theorem (a condition of a result),
  pitfall (a failure mode) or heuristic (a rule of thumb). An algorithm must be a computable procedure.
One entry per note, by id.

INPUT_JSON:
"""

DEDUPE_SCHEMA = {"type": "object", "required": ["same"], "properties": {"same": {"type": "array", "items": {
    "type": "object", "required": ["keep", "same", "why"], "properties": {
        "keep": {"type": "string"}, "why": {"type": "string"},
        "same": {"type": "array", "minItems": 1, "items": {"type": "string"}}}}}}}

DEDUPE_PROMPT = """These notes are grouped by subtopic. Find notes that are THE SAME concept written twice (often
by two courses): same thing, possibly different words. NOT the same: a concept and its pitfall, a general concept and
a special case, two related concepts. Output JSON only: groups of the same concept — keep = the id with the richer
note, same = the others, why = one short sentence. Most notes have no duplicate; an empty list is fine.

INPUT_JSON:
"""


def plan_dir(cfg):
    return cfg.shared_dir / "migrate"


def _save(cfg, name, data):
    d = plan_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _load(cfg, name):
    try:
        return json.loads((plan_dir(cfg) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _call(cfg, store, llm, job, version, template, data, schema):
    from .coursesync import _cached
    return _cached(cfg, store, llm, job, version, template, data, schema, "migrate_" + job)


def archive_rel(cfg, p):
    return f"{ARCHIVE}/{course_of_path(cfg, p)}/{p.name}"


def _old_input(p, fm, body):
    return {"stem": p.stem, "title": str(fm.get("title") or p.stem), "type": str(fm.get("type", "")),
            "easy_definition": str(fm.get("easy_definition") or ""), "easy_pitfall": str(fm.get("easy_pitfall") or ""),
            "text": body.strip()}


def _batches(items, size_of, limit):
    out, cur, n = [], [], 0
    for it in items:
        s = size_of(it)
        if cur and n + s > limit:
            out.append(cur)
            cur, n = [], 0
        cur.append(it)
        n += s
    return out + ([cur] if cur else [])


def split_old(cfg, store, llm, old, say=print, workers=3):
    """Hydrogen: every older rich note -> its main typed note + extras. -> {stem: {...}}"""
    items = [_old_input(p, fm, body) for p, fm, body in old]
    batches = _batches(items, lambda x: len(json.dumps(x, ensure_ascii=False)), SPLIT_CHARS)
    out, lock = {}, threading.Lock()

    def one(k, batch):
        r = _call(cfg, store, llm, "split", SPLIT_VERSION, SPLIT_PROMPT, {"notes": batch}, SPLIT_SCHEMA)
        got = {n["stem"]: n for n in r.get("notes", []) if n.get("stem") in {b["stem"] for b in batch}}
        with lock:
            out.update(got)
        say(f"split {k + 1}/{len(batches)}: {len(got)}/{len(batch)} notes")
        return [b["stem"] for b in batch if b["stem"] not in got]

    missing = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in [ex.submit(one, k, b) for k, b in enumerate(batches)]:
            missing += f.result()
    return out, missing


def _clean(x):
    """Code checks on a typed note from the model: an algorithm without pseudocode is a mechanism."""
    kind = x.get("kind") if x.get("kind") in KINDS else "mechanism"
    pseudo = (x.get("pseudocode") or "").strip()
    if kind == "algorithm" and not pseudo:
        kind = "mechanism"
    return {"kind": kind, "definition": " ".join(str(x.get("definition") or "").split()),
            "pseudocode": pseudo if kind == "algorithm" else "", "proof": (x.get("proof") or "").strip()
            if kind == "theorem" else "", "propositions": [" ".join(str(t).split()) for t in x.get("propositions") or []
                                                            if str(t).strip()][:8]}


def build_notes(cfg, eng, old, split):
    """Every note of the new graph as a concept dict (topics not yet assigned). -> {id: concept}"""
    out, taken = {}, set()
    for p, c in eng:
        out[p.stem] = {**c, "id": p.stem}
        taken.add(p.stem)
    taken |= {p.stem for p, _, _ in old}
    inverse = []
    for p, fm, body in old:
        src = archive_rel(cfg, p)
        s = split.get(p.stem)
        main = _clean(s) if s else {"kind": OLD_TYPE_KIND.get(str(fm.get("type")), "definition").replace("?", "definition"),
                                    "definition": " ".join(str(fm.get("easy_definition") or fm.get("title")).split()),
                                    "pseudocode": "", "proof": "", "propositions": []}
        aliases = fm.get("aliases") or []
        out[p.stem] = {"id": p.stem, "title": str(fm.get("title") or p.stem),
                       "aliases": aliases if isinstance(aliases, list) else [aliases], **main,
                       "propositions": [{"text": t, "source": src} for t in main["propositions"]],
                       "requires": G._links(fm.get("prerequisites") or []),
                       "related": [("contrasts_with", t) for t in G._links(fm.get("contrasts_with") or [])],
                       "sources": [src], "topics": [], "from_old": course_of_path(cfg, p),
                       "old_hint": str(fm.get("macro_topic") or "")}
        inverse += [(t, p.stem) for t in G._links(fm.get("implications") or [])]
        for x in (s or {}).get("extras", []):
            e = _clean(x)
            slug = G.unique_slug(x.get("title") or "note", taken)
            taken.add(slug)
            out[slug] = {"id": slug, "title": " ".join(str(x.get("title")).split()), "aliases": [], **e,
                         "propositions": [{"text": t, "source": src} for t in e["propositions"]],
                         "requires": [p.stem], "related": [], "sources": [src], "topics": [],
                         "from_old": course_of_path(cfg, p), "old_hint": str(fm.get("macro_topic") or ""),
                         "extra_of": p.stem}
    for c in out.values():                     # v2 "prerequisite_of" and old "implications": the dependent stores it
        inverse += [(t, c["id"]) for t in c.get("prerequisite_of") or []]
        c["prerequisite_of"] = []
    for src_id, dst in inverse:
        if src_id in out and dst in out and dst not in out[src_id]["requires"] and src_id != dst:
            out[src_id]["requires"] = out[src_id]["requires"] + [dst]
    for c in out.values():                     # links to notes that do not exist are dropped (reported)
        c["dropped_links"] = [t for t in c["requires"] if t not in out] + [t for _, t in c["related"] if t not in out]
        c["requires"] = [t for t in dict.fromkeys(c["requires"]) if t in out and t != c["id"]]
        c["related"] = [(p_, t) for p_, t in dict.fromkeys(tuple(r) for r in c["related"]) if t in out and t != c["id"]]
    return out


def _hint(c):
    return c.get("old_hint") or (f"{c['domain']}/{c['topic']}" if c.get("domain") and c.get("topic") else "")


def taxonomy(cfg, store, llm, allnotes):
    data = {"notes": [{"id": c["id"], "title": c["title"], "kind": c["kind"], "hint": _hint(c)}
                      for c in sorted(allnotes.values(), key=lambda c: (_hint(c), c["title"]))]}
    r = _call(cfg, store, llm, "taxonomy", TAXONOMY_VERSION, TAXONOMY_PROMPT, data, TAXONOMY_SCHEMA)
    items, seen = [], set()
    for m in r.get("macros", []):
        ms = topics.slugify(m.get("slug") or m.get("title"))
        for s in m.get("subs", []):
            ss = topics.slugify(s.get("slug") or s.get("title"))
            if ms and ss and f"{ms}/{ss}" not in seen:
                seen.add(f"{ms}/{ss}")
                items.append({"slug": f"{ms}/{ss}", "macro": ms, "macro_title": " ".join(str(m.get("title")).split()),
                              "sub": ss, "title": " ".join(str(s.get("title")).split())})
    return items


def assign(cfg, store, llm, allnotes, tax, say=print, workers=3):
    listed = [f"{t['slug']} — {t['macro_title']}: {t['title']}" for t in tax]
    valid = {t["slug"] for t in tax}
    notes_in = sorted(allnotes.values(), key=lambda c: (_hint(c), c["title"]))
    batches = [notes_in[i:i + ASSIGN_BATCH] for i in range(0, len(notes_in), ASSIGN_BATCH)]
    out, lock = {}, threading.Lock()

    def one(k, batch):
        data = {"topic_list": listed, "notes": [{"id": c["id"], "title": c["title"], "kind": c["kind"],
                                                 "definition": c["definition"][:220]} for c in batch]}
        r = _call(cfg, store, llm, "assign", ASSIGN_VERSION, ASSIGN_PROMPT, data, ASSIGN_SCHEMA)
        got = {}
        for a in r.get("assign", []):
            tops = [t for t in a.get("topics", []) if t in valid]
            if a.get("id") in allnotes and tops:
                got[a["id"]] = {"topics": tops[:2], "kind": a.get("kind") if a.get("kind") in KINDS else None}
        with lock:
            out.update(got)
        say(f"assign {k + 1}/{len(batches)}: {len(got)}/{len(batch)} notes")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in [ex.submit(one, k, b) for k, b in enumerate(batches)]:
            f.result()
    return out


def dedupe(cfg, store, llm, allnotes, assigned, say=print):
    groups = defaultdict(list)
    for i, a in assigned.items():
        groups[a["topics"][0]].append(i)
    blocks = [{"topic": t, "notes": [{"id": i, "title": allnotes[i]["title"], "kind": allnotes[i]["kind"],
                                      "definition": allnotes[i]["definition"][:160]} for i in ids]}
              for t, ids in sorted(groups.items()) if len(ids) > 1]
    batches = _batches(blocks, lambda b: len(json.dumps(b, ensure_ascii=False)), DEDUPE_CHARS)
    merges, used = [], set()
    for k, batch in enumerate(batches):
        r = _call(cfg, store, llm, "dedupe", DEDUPE_VERSION, DEDUPE_PROMPT, {"groups": batch}, DEDUPE_SCHEMA)
        ok = 0
        for g in r.get("same", []):
            keep, same = g.get("keep"), [s for s in g.get("same", []) if s in allnotes and s != g.get("keep")]
            if keep in allnotes and same and keep not in used and not used & set(same):
                merges.append({"keep": keep, "same": same, "why": g.get("why", "")})
                used |= {keep, *same}
                ok += 1
        say(f"dedupe {k + 1}/{len(batches)}: {ok} groups")
    return merges


def prepare(cfg, store, llm, say=print):
    """Step 3: split the older notes, build the topic list, assign topics and kinds, find duplicates. Each stage is
    saved in .study/migrate/ (model calls are cached: a re-run only pays for what changed). Nothing touches Graph/."""
    t0 = time.time()
    eng, old = sources(cfg)
    split, missing = split_old(cfg, store, llm, old, say)
    _save(cfg, "split.json", split)
    allnotes = build_notes(cfg, eng, old, split)
    tax = taxonomy(cfg, store, llm, allnotes)
    say(f"topic list: {len(topics.macros(tax))} macro topics, {len(tax)} subtopics")
    assigned = assign(cfg, store, llm, allnotes, tax, say)
    merges = dedupe(cfg, store, llm, allnotes, assigned, say)
    plan = {"made": time.strftime("%Y-%m-%dT%H:%M:%S"), "taxonomy": tax, "assigned": assigned, "merges": merges,
            "split_missing": missing, "notes": allnotes, "seconds": round(time.time() - t0)}
    _save(cfg, "plan.json", plan)
    return plan


# ═══════════════════════ step 3b: apply (after checkpoint 2) ═══════════════════════

def final_notes(plan):
    """The notes as they will be written: merges applied, topics and kinds set. -> (notes {id: concept},
    redirects {old id: kept id}, problems [str])"""
    allnotes = {i: dict(c) for i, c in plan["notes"].items()}
    redirects = {}
    for m in plan["merges"]:
        keep = allnotes.get(m["keep"])
        if not keep:
            continue
        for s in m["same"]:
            o = allnotes.pop(s, None)
            if not o:
                continue
            redirects[s] = m["keep"]
            keep["aliases"] = list(dict.fromkeys(list(keep.get("aliases") or []) + [o["title"]] + list(o.get("aliases") or [])))
            keep["sources"] = list(dict.fromkeys(list(keep["sources"]) + list(o["sources"])))
            seen = {p["text"] for p in keep.get("propositions", [])}
            keep["propositions"] = list(keep.get("propositions", [])) + [p for p in o.get("propositions", [])
                                                                         if p["text"] not in seen]
            keep["requires"] = list(keep["requires"]) + list(o["requires"])
            keep["related"] = [tuple(r) for r in keep["related"]] + [tuple(r) for r in o["related"]]
    for k, v in list(redirects.items()):                      # chains a->b->c
        while v in redirects:
            v = redirects[v]
        redirects[k] = v
    problems = []
    for i, c in allnotes.items():
        r = lambda t: redirects.get(t, t)  # noqa: E731
        c["requires"] = [t for t in dict.fromkeys(r(t) for t in c["requires"]) if t != i and t in allnotes]
        c["related"] = [(p, t) for p, t in dict.fromkeys((p, r(t)) for p, t in c["related"]) if t != i and t in allnotes]
        a = plan["assigned"].get(i) or next((plan["assigned"][s] for s, k in redirects.items()
                                             if k == i and s in plan["assigned"]), None)
        if not a:
            problems.append(f"{i}: no topic assigned")
            continue
        c["topics"] = list(a["topics"])
        if c["kind"] not in KINDS:                             # a retired kind ("constraint"): Argon's choice
            c["kind"] = a.get("kind") or "heuristic"
        if c["kind"] == "algorithm" and not (c.get("pseudocode") or "").strip():
            c["kind"] = "mechanism"
        if c["kind"] == "pitfall" and not c["requires"]:
            c["kind"] = "heuristic"
        if c.get("epistemic_status") == "proof" and c["kind"] != "theorem":
            c["epistemic_status"] = None                       # the kind's default (engine/notes.py)
        c["prerequisite_of"] = []
        for k in ("domain", "topic", "course"):
            c.pop(k, None)
    return allnotes, redirects, problems


def apply(cfg, plan, say=print):
    """Write the new graph: every note validated first (nothing is written if one fails), then the old folders are
    moved to .study/retired/ (kept, not deleted) and Graph/<slug>.md + Graph/_topics.md are written."""
    import shutil
    allnotes, redirects, problems = final_notes(plan)
    texts = {}
    for i, c in sorted(allnotes.items()):
        if any(p.startswith(f"{i}:") for p in problems):
            continue
        text = notes.render_concept(c)
        errs = notes.validate_concept(text, i, set(allnotes))
        if errs:
            problems.append(f"{i}: " + "; ".join(errs))
        texts[i] = text
    if problems:
        return {"written": 0, "problems": problems}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    eng, old = sources(cfg)
    moved_dirs = sorted({p.parent for p, _ in eng if p.parent != cfg.graph})
    for d in moved_dirs:                                       # Graph/<domain>/ (v2 notes, _topics.md) -> archive
        dst = cfg.shared_dir / "retired" / f"graph-v2-{stamp}" / d.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(d), str(dst))
    for p, _, _ in old:                                        # older atomic notes -> archive (their sources point here)
        dst = cfg.magistrale / archive_rel(cfg, p)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dst))
    for p, c in eng:                                           # a note already flat in Graph/ is rewritten in place
        if p.parent == cfg.graph and p.stem not in texts:
            p.unlink()
    for i, text in texts.items():
        (cfg.graph / f"{i}.md").write_text(text, encoding="utf-8")
    topics.write_all(cfg, plan["taxonomy"])
    _save(cfg, "redirects.json", redirects)
    _save(cfg, "applied.json", {"at": stamp, "written": len(texts), "archived_dirs": [d.name for d in moved_dirs],
                                "archived_old": len(old), "redirects": len(redirects)})
    say(f"wrote {len(texts)} notes to Graph/, archived {len(moved_dirs)} folders and {len(old)} older notes")
    return {"written": len(texts), "problems": [], "redirects": redirects}


def summary(plan):
    """What checkpoint 2 shows: the topic list with counts, merges, kinds, what is left unassigned."""
    notes_, asg = plan["notes"], plan["assigned"]
    per = Counter(a["topics"][0] for a in asg.values())
    kinds = Counter((asg.get(i, {}).get("kind") or c["kind"]) for i, c in notes_.items())
    changed = Counter((c["kind"], asg[i]["kind"]) for i, c in notes_.items()
                      if i in asg and asg[i]["kind"] and asg[i]["kind"] != c["kind"])
    return {"notes": len(notes_), "from_old": sum(1 for c in notes_.values() if c.get("from_old")),
            "extras": sum(1 for c in notes_.values() if c.get("extra_of")),
            "extras_by_kind": dict(Counter(c["kind"] for c in notes_.values() if c.get("extra_of"))),
            "kinds": dict(kinds), "kind_changes": {f"{a} -> {b}": n for (a, b), n in changed.most_common()},
            "unassigned": sorted(set(notes_) - set(asg)), "split_missing": plan["split_missing"],
            "per_topic": [(t["slug"], t["macro_title"], t["title"], per.get(t["slug"], 0)) for t in plan["taxonomy"]],
            "merges": plan["merges"],
            "dropped_links": sum(len(c.get("dropped_links") or []) for c in notes_.values())}
