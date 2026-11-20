"""Sync Knowledge v5 (2026-11-20): raw notes -> atomic notes + cards -> the graph, per course.

  1-2  offline     new recordings -> raw notes (the lecture pipeline: Whisper, slides, the professor's handwriting)
  3    notes       Hydrogen HIGH reads a lecture's raw notes and writes its typed atomic notes (engine/notes.py:
                   definition, theorem, algorithm, mechanism, pitfall, application, heuristic), each with its topics
                   from Graph/_topics.md and its DIRECT prerequisites — other notes of this lecture, or existing graph
                   nodes from a catalog — and marks a note that IS an existing node (that node gains the source and
                   the cards instead of a duplicate). Same call: EASY cards for the definitions, theorems, algorithms.
  4    exercises   Hydrogen HIGH: 5-10 MEDIUM exercises from the raw notes, on what the professor stresses, each for
                   one note (theory: Basilare; code: Code with # TODO starter code)
  5    graph       code only, no model: prerequisites resolved to slugs; loops, shortcuts and prerequisites from a
                   later lecture of the same course dropped; one Inbox proposal per course (notes + their cards).
                   Approving it writes the notes to Graph/ and sends the approved notes' cards to the course's Anki
                   deck (Agents/skills/anki-bridge/decks.json), tagged lh::<slug> + the note's topics as macro::sub.

Each lecture's results are saved (.study/sync/v5/<course>.json) as soon as it is done; the graph step waits for every
lecture of the run. Model calls are cached: a re-run pays only for what changed.
"""
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import graph as G
from . import ingest, topics
from .coursesync import _cached, _strip_frontmatter, lecture_key, lecture_name, sections, source_text
from .graph import KINDS

NOTES_VERSION, EXERCISES_VERSION = "lnotes-v5", "lexercises-v2"     # v5: proposed topics shown to later parts
# Notes are drafted per PART of a lecture (~40-50 minutes, cut at its act headings). The whole lecture fits Gemini's
# window, but one answer covers only its first ~40 minutes in detail (tested 2026-11-20: the outline stopped at
# 00:43 of a 3-hour lecture; a lab's last act was skipped as "remainder"). Each part sees the notes drafted from the
# earlier parts and lectures. The exercises call reads the whole lecture.
LECTURE_CHARS = 45000
EXERCISE_CHARS = 1200000
DECKS_FILE = ("Agents", "skills", "anki-bridge", "decks.json")
CARD_KINDS = {"definition", "theorem", "algorithm"}
_lock = threading.Lock()


class SyncError(Exception):
    pass


# ── state ──

def state_file(cfg, course):
    return cfg.shared_dir / "sync" / "v5" / f"{course}.json"


def load_state(cfg, course):
    try:
        return json.loads(state_file(cfg, course).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"course": course, "lectures": {}}


def save_state(cfg, st):
    with _lock:
        f = state_file(cfg, st["course"])
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(f)


def graphed(cfg):
    """{lecture rel path} already in a v5 course proposal that still exists."""
    d = cfg.shared_dir / "sync" / "v5"
    ids = {f.stem for f in cfg.proposals_dir.glob("*.json")} if cfg.proposals_dir.is_dir() else set()
    out = set()
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out |= {rel for rel, x in st.get("lectures", {}).items() if x.get("graphed") in ids}
    return out


# ── decks ──

def deck_for(cfg, course):
    try:
        decks = json.loads(cfg.magistrale.joinpath(*DECKS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        decks = {}
    return decks.get(course) or "Data Science::" + re.sub(r"^[0-9]{2}[A-Z]{5}\s+-\s+", "", course)


def card_tags(slug, topic_list):
    """lh::<slug> first (the card's node), then the node's topics as macro::sub. Nothing else."""
    return [f"lh::{slug}"] + [t.replace("/", "::") for t in topic_list]


# ── stage 3: notes + easy cards ──

_NOTE = {"key": {"type": "string"}, "title": {"type": "string"}, "kind": {"type": "string", "enum": KINDS},
         "topics": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string"}},
         "aliases": {"type": "array", "items": {"type": "string"}}, "definition": {"type": "string"},
         "pseudocode": {"type": "string"}, "proof": {"type": "string"},
         "propositions": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
         "prerequisites": {"type": "array", "maxItems": 6, "items": {"type": "string"}}}

LAB = re.compile(r"\b(lab|labs|laboratory|laboratorio|exercises?|esercitazion\w*|exam simulation|past exams?|"
                 r"exam exercise|coding exercises?|tutorial)\b", re.I)
LECTURE_RULE = ""
LAB_RULE = """
THIS IS A LAB / EXERCISE SESSION (`session`: "lab"). Labs are mostly exercises: they rarely teach theory, and their
slides restate the lecture. Write notes ONLY for what the session really teaches beyond the earlier lectures: a
technique or procedure the professor shows how to apply (application, algorithm), a pitfall or a rule of thumb they
stress (pitfall, heuristic). Do NOT write notes restating theory (definitions, theorems) the lectures cover — those
concepts are already in `catalog` / `earlier_lectures`: link to them as prerequisites. 0-8 notes is normal for a lab.
Easy cards only for genuinely new facts (often none)."""


def is_lab(rel):
    return bool(LAB.search(lecture_name(rel)))
_CARD = {"note": {"type": "string"}, "model": {"type": "string", "enum": ["Basilare", "Code"]},
         "front": {"type": "string"}, "back": {"type": "string"}, "initial_code": {"type": "string"}}
NOTES_SCHEMA = {"type": "object", "required": ["outline", "notes", "new_topics", "easy_cards"], "properties": {
    "outline": {"type": "array", "minItems": 1, "maxItems": 40, "items": {"type": "object",
                "required": ["section", "notes"], "properties": {
                    "section": {"type": "string"},
                    "notes": {"type": "array", "items": {"type": "string"}}}}},
    "notes": {"type": "array", "maxItems": 40, "items": {"type": "object", "properties": _NOTE,
              "required": ["key", "title", "kind", "topics", "definition", "propositions", "prerequisites"]}},
    "new_topics": {"type": "array", "maxItems": 4, "items": {"type": "object",
                   "required": ["macro", "macro_title", "sub", "title"], "properties": {
                       "macro": {"type": "string"}, "macro_title": {"type": "string"},
                       "sub": {"type": "string"}, "title": {"type": "string"}}}},
    "easy_cards": {"type": "array", "maxItems": 60, "items": {"type": "object", "properties": _CARD,
                   "required": ["note", "model", "front", "back"]}}}}

NOTES_PROMPT = """You turn ONE part of a university lecture (`part`: about 40-50 minutes) into atomic notes for a
student's knowledge graph, plus easy flashcards. The input is that part's raw notes: the transcript of what the
professor said, the slide text and the professor's handwriting, in time order. The professor's insights (what
matters, why, what students get wrong) are the most valuable part. Output JSON only.

FIRST, THE OUTLINE — go through the WHOLE input, start to END: the last minutes matter as much as the first. List
its sections in order; for each, the keys of the notes you write for it.
A section gets NO notes only if it is logistics, a recap of an earlier section, or a worked example: then notes = [].
Every subject the professor teaches must have notes.

ATOMIC NOTES — one typed unit of knowledge each, in the fixed shape of its kind:
  definition   what something is             definition = the statement; propositions = key properties
  theorem      a result under conditions     definition = the statement; propositions = its conditions;
                                             proof = a short proof sketch (when the lecture gives one)
  algorithm    how to compute something      definition = what it computes; pseudocode = plain code lines (no ```
                                             fences); propositions = why each step is there
  mechanism    how/why a system works        definition = the statement; propositions = the steps or causes
  pitfall      a typical mistake / failure   definition = what goes wrong; propositions = why, then how to avoid it;
                                             its prerequisites MUST include the note it is a pitfall of
  application  when/where to use something   definition = the use case; propositions = why it fits
  heuristic    a rule of thumb               definition = the rule; propositions = when it holds
A note is something a student must be able to explain or use in the exam, named the way a textbook index lists it.
NOT a note: logistics, tool trivia, one exercise's numbers, a worked example (that is for flashcards). A procedure is
ONE note, not one per step; advantages and limits are propositions. Typically 5-20 notes per lecture, up to 40 for
a long, dense one.
For each note:
- key: n1, n2, ... (unique in this answer); title: the standard name (2-6 words, English, no course prefix).
- topics: 1 (rarely 2) slugs EXACTLY from `topic_list` ("macro/sub"). If none fits, add the topic to `new_topics`
  (macro = an existing macro slug when the field exists; a new field only if it truly is one) and use it.
- prerequisites: the DIRECT premises only — what the student must already know to understand THIS note (A + B => C:
  C lists A and B, nothing further down). Each is a key of a note in this answer, or a slug from `catalog` (the
  existing graph) or from `earlier_lectures` (notes drafted from this course's earlier lectures). Do not list what is
  only related.
- Write the note even if the student may already have it: code compares every draft with the existing notes and
  keeps only what is new.
{SESSION_RULE}
- definition, propositions: complete sentences that stand alone (no "this", "the above"); at most 8 propositions;
  ONLY what the lecture says — no new facts. Math in LaTeX $...$. English.

EASY CARDS — for every definition, theorem and algorithm note (1-2 each): instant recall of one fact.
- note: the note's key. model: Basilare (theory) or Code (a command, an API call, a one-liner; initial_code "").
- front: a precise question with one right answer (no yes/no, no giveaway wording); back: the direct answer.
  LaTeX $...$ / $$...$$. English.

INPUT_JSON:
"""

# ── stage 4: medium exercises ──

EXERCISES_SCHEMA = {"type": "object", "required": ["exercises"], "properties": {"exercises": {
    "type": "array", "minItems": 1, "maxItems": 15, "items": {"type": "object", "properties": _CARD,
                                                              "required": ["note", "model", "front", "back"]}}}}

EXERCISES_PROMPT = """You write MEDIUM-difficulty exercises (Anki cards) for one university lecture, from its raw
notes (transcript, slides, the professor's handwriting). Output JSON only.
Write {COUNT} exercises on what the PROFESSOR stresses: what they repeat, call important, put in exams, or warn about.
{SESSION_RULE}
Medium = practical command beyond rote recall: a single-step derivation, a short proof, an applied problem with
small numbers, choosing/justifying a method, or implementing an algorithm or method.
- note: the key or slug (from `notes`) of the note the exercise mainly trains.
- model: Basilare for theory (front = the problem, back = the worked solution with the reasoning); Code when the
  lecture is about code: front = the task with input/output, back = complete runnable code, initial_code = starter
  code with # TODO comments for the steps to write.
- One right answer; self-contained (all data in the front); no yes/no. LaTeX $...$ / $$...$$. English.
- Use only what the lecture teaches (no new facts).

INPUT_JSON:
"""


def catalog(cfg, g=None):
    """The existing graph for the prompt: 'slug — title (macro/sub)'."""
    g = g or G.Graph.load(cfg)
    return [f"{i} — {n.name}" + (f" ({n.topics[0]})" if n.topics else "") for i, n in sorted(g.nodes.items())]


def topic_lines(cfg):
    return [f"{t['slug']} — {t['macro_title']}: {t['title']}" for t in topics.load_all(cfg)]


def _clean_note(x):
    kind = x.get("kind") if x.get("kind") in KINDS else "mechanism"
    pseudo = (x.get("pseudocode") or "").strip()
    if kind == "algorithm" and not pseudo:
        kind = "mechanism"
    return {"key": str(x.get("key") or ""), "title": " ".join(str(x.get("title") or "").split()), "kind": kind,
            "topics": [str(t).strip() for t in x.get("topics") or [] if str(t).strip()][:2],
            "aliases": [a for a in (x.get("aliases") or []) if isinstance(a, str) and a.strip()][:4],
            "definition": " ".join(str(x.get("definition") or "").split()),
            "pseudocode": pseudo if kind == "algorithm" else "",
            "proof": (x.get("proof") or "").strip() if kind == "theorem" else "",
            "propositions": [" ".join(str(t).split()) for t in x.get("propositions") or [] if str(t).strip()][:8],
            "prerequisites": [str(p).strip() for p in x.get("prerequisites") or [] if str(p).strip()],
            "same_as": str(x.get("same_as") or "").strip()}


def _card(x, kind):
    return {"kind": kind, "note": str(x.get("note") or "").strip(), "model": x.get("model") if x.get("model") in
            ("Basilare", "Code") else "Basilare", "front": str(x.get("front") or "").strip(),
            "back": str(x.get("back") or "").strip(), "initial_code": str(x.get("initial_code") or "")}


REF = "::"                       # a draft of an earlier lecture: "<rel>::<key>"


def _topic_line(t):
    return f"{t['macro']}/{t['sub']} — {t.get('macro_title', t['macro'])}: {t.get('title', t['sub'])} (proposed)"


def prepare_notes(cfg, store, llm, course, rel, cat, tlist, earlier, session, pool=None, embedder=None,
                  proposed=None):
    """Stage 3 for one lecture. `earlier`: [(rel, note)] drafted from this course's earlier lectures — shown to the
    model as E1, E2…, stored back as "<rel>::<key>". `pool`: the graph's notes for the repeat check (reconcile.py).
    -> {"notes" (each with its "action"), "new_topics", "cards" (easy), "outline", "calls", "text", "lab"}"""
    from . import reconcile
    text = _strip_frontmatter(source_text(cfg, rel))
    parts = sections(text, LECTURE_CHARS) if len(text) > LECTURE_CHARS else [text]
    lab = is_lab(rel)
    template = NOTES_PROMPT.replace("{SESSION_RULE}", LAB_RULE if lab else LECTURE_RULE)
    notes_, new_topics, cards, outline, calls, part_of = [], [], [], [], 0, {}
    for k, part in enumerate(parts):
        # earlier lectures' drafts and this lecture's earlier parts: E1, E2, ... -> "<rel>::<key>"
        before = list(earlier) + [(rel, n) for n in notes_]
        eref = {f"E{j + 1}": f"{r}{REF}{n['key']}" for j, (r, n) in enumerate(before)}
        shown = [{"slug": f"E{j + 1}", "title": n["title"], "kind": n["kind"],
                  "lecture": lecture_name(r) + (" (earlier part of this lecture)" if r == rel else "")}
                 for j, (r, n) in enumerate(before)]
        # topics proposed earlier in this run are part of the list: reused, never proposed twice
        known = list(tlist) + [_topic_line(t) for t in (proposed or []) + new_topics]
        data = {"course": course, "lecture": lecture_name(rel), "session": "lab" if lab else "lecture",
                "part": f"{k + 1}/{len(parts)}", "topic_list": known, "catalog": cat, "earlier_lectures": shown,
                "raw_notes": part}
        out = _cached(cfg, store, llm, "notes", NOTES_VERSION, template, data, NOTES_SCHEMA, session)
        calls += 1
        rekey = {}
        for x in out.get("notes", []):
            n = _clean_note(x)
            if not n["title"] or len(n["definition"]) < 15:
                continue
            new_key = f"p{k}{n['key']}" if len(parts) > 1 else n["key"]
            rekey[n["key"]] = new_key
            notes_.append({**n, "key": new_key, "same_as": ""})
            part_of[new_key] = k
        for n in notes_[len(notes_) - len(rekey):]:
            n["prerequisites"] = [rekey.get(p) or eref.get(p) or p for p in n["prerequisites"]]
        new_topics += out.get("new_topics", [])
        outline += [{"section": str(s.get("section", "")), "notes": [rekey.get(x, x) for x in s.get("notes") or []]}
                    for s in out.get("outline", [])]
        cards += [{**_card(c, "easy"), "note": rekey.get(str(c.get("note")), str(c.get("note")))}
                  for c in out.get("easy_cards", [])]
    # does the student already have it? candidates by code (graph, earlier lectures, earlier parts), then one call
    cands = {}
    base = (pool or []) + reconcile.pool_from_drafts([(f"{r}{REF}{n['key']}", n) for r, n in earlier])
    for k in range(len(parts)):
        mine = [n for n in notes_ if part_of[n["key"]] == k]
        before = reconcile.pool_from_drafts([(f"{rel}{REF}{n['key']}", n) for n in notes_ if part_of[n["key"]] < k])
        cands.update(reconcile.candidates(mine, base + before, embedder))
    decisions = reconcile.decide(cfg, store, llm, notes_, cands, session, _cached) if cands else {}
    if cands:
        calls += 1
    for n in notes_:
        d = decisions.get(n["key"]) or {"action": "new", "target": "", "add": [], "why": ""}
        n.update(action=d["action"], target=d["target"], add=d["add"], why=d["why"],
                 candidates=[p["id"] for p, _ in cands.get(n["key"], [])])
        if d.get("retitle"):                                   # it shares a title with a different concept
            n["aliases"] = list(dict.fromkeys(list(n["aliases"]) + [n["title"]]))
            n["title"] = d["retitle"]
    return {"notes": notes_, "new_topics": new_topics, "cards": cards, "outline": outline, "calls": calls,
            "text": text, "lab": lab}


EXERCISE_LAB_RULE = """This is a LAB / exercise session: turn its exercises and the professor's solutions into cards.
When the lab is about code, most cards are Code cards (the lab's tasks: input/output, complete solution, starter code
with # TODO)."""


def prepare_exercises(cfg, store, llm, course, rel, notes_, text, session, lab=False):
    """Stage 4 for one lecture -> medium cards (a lab: 8-15, mostly its own exercises)."""
    data = {"course": course, "lecture": lecture_name(rel), "session": "lab" if lab else "lecture",
            "notes": [{"key": n["key"], "title": n["title"], "kind": n["kind"]} for n in notes_]
            + [{"key": n["target"], "title": n["title"], "kind": n["kind"]} for n in notes_
               if n.get("action") in ("skip", "integrate") and n.get("target") and REF not in n["target"]],
            "raw_notes": text[:EXERCISE_CHARS]}
    template = EXERCISES_PROMPT.replace("{COUNT}", "8-15" if lab else "5-10").replace(
        "{SESSION_RULE}", EXERCISE_LAB_RULE if lab else "")
    ex = _cached(cfg, store, llm, "exercises", EXERCISES_VERSION, template, data, EXERCISES_SCHEMA, session)
    return [_card(c, "medium") for c in ex.get("exercises", [])]


def _finish(r, medium):
    keys = {n["key"] for n in r["notes"]} | {n["target"] for n in r["notes"] if n.get("target")}
    skipped = {n["key"] for n in r["notes"] if n.get("action") == "skip"}
    cards = [c for c in r["cards"] + medium if c["note"] in keys and c["front"] and c["back"]
             and not (c["kind"] == "easy" and c["note"] in skipped)]           # nothing new: no new easy cards
    acts = [n.get("action", "new") for n in r["notes"]]
    return {"notes": r["notes"], "new_topics": r["new_topics"], "cards": cards, "outline": r["outline"],
            "lab": r.get("lab", False),
            "stats": {"notes": len(r["notes"]), "new": acts.count("new"), "integrate": acts.count("integrate"),
                      "skip": acts.count("skip"), "sub_note": acts.count("sub_note"),
                      "easy": sum(c["kind"] == "easy" for c in cards),
                      "medium": sum(c["kind"] == "medium" for c in cards), "calls": r["calls"] + 1}}


def prepare(cfg, store, llm, course, rel, cat, tlist, session, earlier=(), pool=None, embedder=None):
    """Stages 3-4 for one lecture -> {"notes", "new_topics", "cards", "outline", "stats"}."""
    r = prepare_notes(cfg, store, llm, course, rel, cat, tlist, list(earlier), session, pool, embedder)
    return _finish(r, prepare_exercises(cfg, store, llm, course, rel, r["notes"], r["text"], session, r["lab"]))


def _hash(cfg, rel):
    return hashlib.sha256((cfg.material / rel).read_bytes()).hexdigest()[:16]


def prepare_all(cfg, store, llm, course, files, say=lambda d: None, stop=None, workers=None, embedder=None):
    """Prepare every lecture not yet prepared (or whose raw notes changed), IN LECTURE ORDER: each lecture's notes
    call sees the notes drafted from the earlier ones (a lab links to its lecture's notes instead of rewriting
    them). The exercises calls run alongside. -> (state, errors)"""
    from . import reconcile
    st = load_state(cfg, course)
    g = G.Graph.load(cfg)
    cat, tlist, pool = catalog(cfg, g), topic_lines(cfg), reconcile.pool_from_graph(g)
    session = "sync5_" + time.strftime("%Y-%m-%d_%H%M%S")
    files = sorted(files, key=lecture_key)
    errors, done, earlier, run_topics = [], 0, [], []
    say({"course": course, "stage": "notes", "done": 0, "total": len(files)})
    workers_pool = ThreadPoolExecutor(max_workers=workers or cfg.sync_workers)
    pending = {}

    def finish(rel, h, r, fut):
        nonlocal done
        try:
            res = _finish(r, fut.result())
        except Exception as e:  # noqa: BLE001
            errors.append(f"{rel}: exercises: {type(e).__name__}: {e}")
            return
        with _lock:
            st["lectures"][rel] = {"hash": h, **res, "graphed": None}
            done += 1
        save_state(cfg, st)
        say({"course": course, "stage": "notes", "done": done, "total": len(files), "file": rel})

    for rel in files:
        if stop and stop():
            break
        h = _hash(cfg, rel)
        x = st["lectures"].get(rel)
        if x and x.get("hash") == h and x.get("notes") is not None:      # already prepared
            earlier += [(rel, n) for n in x["notes"] if n.get("action") != "skip"]
            done += 1
            continue
        try:
            proposed = [t for r_, x in st["lectures"].items() for t in x.get("new_topics", [])] + run_topics
            r = prepare_notes(cfg, store, llm, course, rel, cat, tlist, list(earlier), session, pool, embedder,
                              proposed)
            run_topics += r["new_topics"]
        except Exception as e:  # noqa: BLE001  one lecture failing does not stop the others
            errors.append(f"{rel}: {type(e).__name__}: {e}")
            continue
        earlier += [(rel, n) for n in r["notes"] if n.get("action") != "skip"]
        fut = workers_pool.submit(prepare_exercises, cfg, store, llm, course, rel, r["notes"], r["text"], session,
                                  r["lab"])
        pending[rel] = (h, r, fut)
        for prel, (ph, pr, pf) in list(pending.items()):                  # save the ones whose exercises are done
            if pf.done():
                finish(prel, ph, pr, pf)
                del pending[prel]
    for prel, (ph, pr, pf) in pending.items():
        finish(prel, ph, pr, pf)
    workers_pool.shutdown(wait=True)
    return st, errors


# ── stage 5: the graph, by code ──

def _norm(t):
    return re.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()


def build(cfg, course, files, st, g=None):
    """Every prepared lecture of `files` -> ONE course proposal (deterministic). -> proposal dict (saved)."""
    g = g or G.Graph.load(cfg)
    valid_topics = {t["slug"] for t in topics.load_all(cfg)}
    lectures = sorted(files, key=lecture_key)
    order = {rel: k for k, rel in enumerate(lectures)}
    deck = deck_for(cfg, course)
    # proposed topics: a near-twin of a listed or already proposed topic (same macro, mostly the same words — e.g.
    # "bayesian-and-naive-bayes-classification" / "bayesian-classification-and-naive-bayes") is merged into it
    new_topics, alias = {}, {}
    words = lambda s: {w for w in re.split(r"[-\s]+", s.lower()) if w and w not in ("and", "of", "the", "in")}  # noqa: E731
    known = [(t["macro"], t["sub"]) for t in topics.load_all(cfg)]
    for rel in lectures:
        for t in st["lectures"][rel].get("new_topics", []):
            m, s = topics.slugify(t.get("macro")), topics.slugify(t.get("sub"))
            if not (m and s) or f"{m}/{s}" in valid_topics or f"{m}/{s}" in new_topics:
                continue
            twin = next((f"{km}/{ks}" for km, ks in known if km == m and len(words(s) & words(ks))
                         / max(1, len(words(s) | words(ks))) >= 0.5), None)
            if twin:
                alias[f"{m}/{s}"] = twin
                continue
            new_topics[f"{m}/{s}"] = {"macro": m, "macro_title": " ".join(str(t.get("macro_title") or m).split()),
                                      "sub": s, "title": " ".join(str(t.get("title") or s).split())}
            known.append((m, s))
    for rel in lectures:                                        # notes follow the merge
        for n in st["lectures"][rel]["notes"]:
            n["topics"] = list(dict.fromkeys(alias.get(t, t) for t in n["topics"]))
    allowed = valid_topics | set(new_topics)
    # one item per distinct concept, following the repeat check (reconcile.py) of each draft:
    #   integrate -> the existing note (or an earlier draft) gets ONLY the new facts; skip -> no note, links and
    #   exercises go to the existing one; sub_note -> a new note that requires it; new -> a new note
    #   (the same title in two lectures is one note)
    items, by_key, taken = [], {}, set(g.nodes)
    by_title = {}

    def target_slug(t):
        if REF in t:
            return by_key.get(tuple(t.split(REF, 1)))
        return t if t in g.nodes else None

    for rel in lectures:
        for n in st["lectures"][rel]["notes"]:
            tops = [t for t in n["topics"] if t in allowed] or None
            src = f"{rel}"
            action, target = n.get("action", "new"), target_slug(n.get("target") or "")
            if action in ("integrate", "skip") and target:
                add = [{"text": t, "source": src} for t in n.get("add") or []]
                it = next((x for x in items if x["slug"] == target), None)
                if it is None:                                   # an existing node: extended with the new facts
                    it = {"decision": "extends", "match": target, "slug": target, "lectures": [],
                          "new_propositions": [], "title": g.nodes[target].name, "cards": [], "skipped": []}
                    items.append(it)
                if it["decision"] == "new":                      # an earlier draft of this course
                    c = it["concept"]
                    c["propositions"] += [p for p in add if p["text"] not in {x["text"] for x in c["propositions"]}]
                    if add:
                        c["sources"] = list(dict.fromkeys(c["sources"] + [src]))
                else:
                    have = {p["text"] for p in it["new_propositions"]}
                    it["new_propositions"] += [p for p in add if p["text"] not in have]
                    if action == "skip":
                        it["skipped"].append(n["title"])
                it["lectures"].append(rel)
                by_key[(rel, n["key"])] = it["slug"]
                continue
            if action == "sub_note" and target:
                n = {**n, "prerequisites": list(n["prerequisites"]) + [n["target"]]}
            props = [{"text": t, "source": src} for t in n["propositions"]]
            if _norm(n["title"]) in by_title:
                it = by_title[_norm(n["title"])]
                c = it["concept"]
                c["propositions"] += [p for p in props if p["text"] not in {x["text"] for x in c["propositions"]}]
                c["sources"] = list(dict.fromkeys(c["sources"] + [src]))
                it["lectures"].append(rel)
                it["prereq_raw"].append((rel, n["prerequisites"]))
            else:
                if not tops:
                    continue                                   # no valid topic: not a note of the graph
                slug = G.unique_slug(n["title"], taken)
                taken.add(slug)
                it = {"decision": "new", "slug": slug, "lectures": [rel], "cards": [], "title": n["title"],
                      "prereq_raw": [(rel, n["prerequisites"])],
                      "concept": {"id": slug, "title": n["title"], "aliases": n["aliases"], "kind": n["kind"],
                                  "topics": tops, "definition": n["definition"], "pseudocode": n["pseudocode"],
                                  "proof": n["proof"], "propositions": props, "sources": [src], "related": []}}
                items.append(it)
                by_title[_norm(n["title"])] = it
            by_key[(rel, n["key"])] = it["slug"]
    # prerequisites -> slugs; a prerequisite first taught in a LATER lecture of this course is dropped
    first = {}
    for it in items:
        first[it["slug"]] = min(order[r] for r in it["lectures"])
    dropped = []
    extra = {}
    for it in items:
        if it["decision"] != "new":
            continue
        want = []
        for rel, pre in it.pop("prereq_raw"):
            for p in pre:
                slug = by_key.get((rel, p)) or (by_key.get(tuple(p.split(REF, 1))) if REF in p else None) \
                    or (p if p in g.nodes else None)
                if slug and slug != it["slug"]:
                    want.append(slug)
        req = []
        for s in dict.fromkeys(want):
            if s in first and first[s] > first[it["slug"]]:
                dropped.append({"from": it["slug"], "to": s, "reason": "taught in a later lecture"})
            elif g.would_cycle(it["slug"], s, extra):
                dropped.append({"from": it["slug"], "to": s, "reason": "would create a loop"})
            else:
                req.append(s)
                extra.setdefault(it["slug"], []).append(s)
        it["requires"] = req
    for it in items:                                           # shortcuts: C needs B, B needs A -> C does not list A
        if it["decision"] != "new":
            continue
        keep = []
        for s in it["requires"]:
            others = {**extra, it["slug"]: [x for x in it["requires"] if x != s]}
            if g.reaches(it["slug"], s, others):
                dropped.append({"from": it["slug"], "to": s, "reason": "shortcut (reached through another prerequisite)"})
            else:
                keep.append(s)
        it["requires"] = keep
        extra[it["slug"]] = keep
    # cards -> the item of their note
    slug_of = {(rel, k): s for (rel, k), s in by_key.items()}
    for rel in lectures:
        for c in st["lectures"][rel]["cards"]:
            s = slug_of.get((rel, c["note"])) or (c["note"] if c["note"] in g.nodes else None)
            it = next((x for x in items if x["slug"] == s), None)
            if it:
                it["cards"].append({**c, "lecture": rel, "deck": deck, "status": "pending"})
    pitems = []
    for k, it in enumerate(items):
        base = {"index": k, "slug": it["slug"], "decision": it["decision"], "status": "pending",
                "lectures": it["lectures"], "cards": it["cards"]}
        if it["decision"] == "new":
            c = it["concept"]
            pitems.append({**base, "concept": c,
                           "edges": [{"to": s, "predicate": "requires", "confidence": "confident"} for s in it["requires"]],
                           "required_by": []})
        else:
            pitems.append({**base, "match": it["match"], "new_propositions": it["new_propositions"],
                           "skipped": it.get("skipped", []),
                           "concept": {"title": it["title"], "kind": g.nodes[it["match"]].kind,
                                       "topics": g.nodes[it["match"]].topics, "definition": g.nodes[it["match"]].statement,
                                       "propositions": [], "aliases": [], "sources": []}})
    pid = f"course5_{re.sub(r'[^A-Za-z0-9]+', '_', course.split(' ')[0])}_{time.strftime('%Y%m%d_%H%M%S')}"
    stats = {"lectures": len(lectures), "new": sum(i["decision"] == "new" for i in pitems),
             "extends": sum(i["decision"] == "extends" and bool(i["new_propositions"]) for i in pitems),
             "already_have": sum(len(i.get("skipped", [])) for i in pitems),
             "easy_cards": sum(c["kind"] == "easy" for i in pitems for c in i["cards"]),
             "medium_cards": sum(c["kind"] == "medium" for i in pitems for c in i["cards"]),
             "links": sum(len(i.get("edges", [])) for i in pitems), "dropped_links": len(dropped),
             "new_topics": len(new_topics)}
    p = {"id": pid, "version": 2, "method": "v5", "origin": "course", "course": course, "deck": deck,
         "source": {"name": course, "path": course}, "domain": "", "status": "pending",
         "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": pitems, "new_topics": list(new_topics.values()),
         "dropped_links": dropped, "stats": stats, "lectures": [lecture_name(r) for r in lectures],
         "held_conflicts": []}
    ingest.save(cfg, p)
    for rel in lectures:
        st["lectures"][rel]["graphed"] = pid
    save_state(cfg, st)
    return p
