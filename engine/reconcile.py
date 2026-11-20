"""Is a new draft something the student already has? Deterministic candidates, then one small model decision.

1. candidates (code, no model): for each draft of a lecture, the existing notes it may repeat — the graph's nodes
   plus the drafts of this course's earlier lectures and of earlier parts of this lecture. Score = the best of
   title/alias word overlap, definition word overlap, and (when Ollama is up) embedding cosine; a shared subtopic
   adds a little. Titles equal after normalisation always match. Top 3 above the threshold.
2. decision (model "reconcile", only for drafts that HAVE candidates — the rest are new at no cost):
     integrate  same concept; the draft adds facts -> only those facts are appended to the existing note
     skip       same concept, nothing new -> no note, no tokens spent later (its easy cards are dropped)
     sub_note   a narrower concept (special case, variant, pitfall of it) -> a new note that requires the existing one
     new        a different concept after all
   Code checks: the target must be one of the candidates; "integrate" with nothing to add becomes "skip".
"""
import re

import numpy as np

TOP_K = 3
LEX_TITLE, LEX_DEF, EMB = 0.5, 0.35, 0.62          # candidate thresholds (embedding: same idea >= 0.65, bench)
STOP = set("a an and are as at be by for from in into is it its of on or that the to with which using".split())
ACTIONS = ["new", "integrate", "skip", "sub_note"]


def _words(s):
    return {w for w in re.findall(r"[a-z0-9]+", str(s).lower()) if w not in STOP and len(w) > 1}


def _jacc(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def norm_title(t):
    return " ".join(sorted(_words(t)))


def pool_from_graph(g):
    """Existing notes as candidate entries."""
    return [{"id": i, "title": n.name, "aliases": list(n.aliases or []), "kind": n.kind, "topics": list(n.topics),
             "definition": n.statement or "", "propositions": [p.get("text", "") for p in n.propositions or []][:8]}
            for i, n in sorted(g.nodes.items())]


def pool_from_drafts(entries):
    """[(ref, draft)] of earlier lectures/parts as candidate entries (ref = "<rel>::<key>")."""
    return [{"id": ref, "title": d["title"], "aliases": d.get("aliases", []), "kind": d["kind"],
             "topics": d.get("topics", []), "definition": d["definition"], "propositions": d.get("propositions", [])[:8]}
            for ref, d in entries]


def candidates(drafts, pool, embedder=None, k=TOP_K):
    """{draft key: [(pool entry, score)]} — deterministic. `pool` may already include earlier drafts."""
    if not drafts or not pool:
        return {}
    tw = [set().union(*(_words(t) for t in [p["title"]] + p["aliases"])) for p in pool]
    dw = [_words(p["definition"]) for p in pool]
    exact = {}
    for j, p in enumerate(pool):
        for t in [p["title"]] + p["aliases"]:
            exact.setdefault(norm_title(t), j)
    cos = None
    if embedder is not None:
        try:
            pv = embedder.embed([f"{p['title']}: {p['definition']}" for p in pool])
            dv = embedder.embed([f"{d['title']}: {d['definition']}" for d in drafts])
            cos = np.asarray(dv) @ np.asarray(pv).T
        except Exception:  # noqa: BLE001  Ollama down: words only
            cos = None
    out = {}
    for i, d in enumerate(drafts):
        title_w = set().union(*(_words(t) for t in [d["title"]] + d.get("aliases", [])))
        def_w = _words(d["definition"])
        subs = set(d.get("topics") or [])
        scored = []
        for j, p in enumerate(pool):
            lt, ld = _jacc(title_w, tw[j]), _jacc(def_w, dw[j])
            e = float(cos[i, j]) if cos is not None else 0.0
            hit = lt >= LEX_TITLE or ld >= LEX_DEF or e >= EMB
            if exact.get(norm_title(d["title"])) == j:
                hit, lt = True, 1.0
            if hit:
                scored.append((p, round(max(lt, ld, e) + (0.05 if subs & set(p["topics"]) else 0.0), 3)))
        scored.sort(key=lambda x: (-x[1], x[0]["id"]))
        if scored:
            out[d["key"]] = scored[:k]
    return out


SCHEMA = {"type": "object", "required": ["decisions"], "properties": {"decisions": {"type": "array", "items": {
    "type": "object", "required": ["key", "action", "target", "add", "why"], "properties": {
        "key": {"type": "string"}, "action": {"type": "string", "enum": ACTIONS}, "target": {"type": "string"},
        "add": {"type": "array", "maxItems": 6, "items": {"type": "string"}}, "why": {"type": "string"},
        "retitle": {"type": "string"}}}}}}

PROMPT = """A student's knowledge graph already has notes; a new lecture produced draft notes. For each draft you get
the existing notes it MAY repeat (found by word and meaning similarity). Decide, per draft. Output JSON only.
  integrate  the draft IS one of them (same concept) and says something it does not: target = its id; add = ONLY the
             genuinely new facts, as complete standalone sentences (a rephrasing of what the note already says is
             NOT new)
  skip       the draft IS one of them and adds nothing new: target = its id; add = []
  sub_note   the draft is a NARROWER concept of one of them (a special case, a variant, a pitfall of it, a step that
             deserves its own note): target = that id; it becomes its own note that builds on the target; add = []
  new        a different concept despite the similarity: target = ""; add = []
Same name does not always mean same concept, and different names can be the same concept. why: one short sentence.
retitle: when you answer new or sub_note for a draft whose title is the SAME as a candidate's, give the draft a more
specific title that tells them apart (e.g. "Grid Search in scikit-learn" vs "Grid Search in Spark MLlib"); else "".

INPUT_JSON:
"""


def decide(cfg, store, llm, drafts, cands, session, cached):
    """One model call for every draft with candidates -> {key: {"action", "target", "add", "why"}}."""
    pairs = [{"draft": {"key": d["key"], "title": d["title"], "kind": d["kind"], "definition": d["definition"],
                        "propositions": d.get("propositions", [])[:8]},
              "candidates": [{"id": p["id"], "title": p["title"], "kind": p["kind"], "definition": p["definition"],
                              "propositions": p["propositions"]} for p, _ in cands[d["key"]]]}
             for d in drafts if d["key"] in cands]
    if not pairs:
        return {}
    out = cached(cfg, store, llm, "reconcile", "reconcile-v1", PROMPT, {"pairs": pairs}, SCHEMA, session)
    res = {}
    for x in out.get("decisions", []):
        key = str(x.get("key", ""))
        if key not in cands:
            continue
        ids = {p["id"] for p, _ in cands[key]}
        action = x.get("action") if x.get("action") in ACTIONS else "new"
        target = str(x.get("target") or "")
        add = [" ".join(str(t).split()) for t in x.get("add") or [] if str(t).strip()]
        if action != "new" and target not in ids:              # the target must be one of the candidates
            action, target = "new", ""
        retitle = " ".join(str(x.get("retitle") or "").split())
        draft = next(d for d in drafts if d["key"] == key)
        twin = next((p for p, _ in cands[key] if norm_title(p["title"]) == norm_title(draft["title"])), None)
        if action in ("new", "sub_note") and twin is not None:  # two notes may not share a title
            if retitle and norm_title(retitle) != norm_title(twin["title"]):
                pass
            else:                                              # no distinct title given: one concept after all
                action, target, retitle = "integrate", twin["id"], ""
                add = draft.get("propositions", [])[:6]
        if action == "integrate" and not add:
            action = "skip"
        res[key] = {"action": action, "target": target, "add": add if action == "integrate" else [],
                    "why": " ".join(str(x.get("why", "")).split())[:200],
                    "retitle": retitle if action in ("new", "sub_note") else ""}
    return res
