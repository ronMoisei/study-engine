"""Concepts taught in an agy session -> a PROPOSAL in the Inbox (nothing is written to Graph/ until approved).

The teaching agent writes a small JSON file and runs `python study.py propose <file>`. Everything here is
deterministic, no model call:
  - identity: a concept whose title or alias matches an existing node (case, spaces and punctuation ignored)
    extends that node (only the new propositions) or is already covered; otherwise it is new;
  - links: `requires` entries must name an existing node (id, title or alias) or another concept of the same
    file; anything else is dropped and reported;
  - the usual approval (`apply`) then writes validated notes and refuses prerequisite loops.

Input:
{"session": "2026-11-16_dsml_labs_4_5_6", "course": "01VSCWS - Data science and machine learning",
 "source": "Learner/sessions/2026-11-16_dsml_labs_4_5_6.md",
 "new_topics": [],                  # optional: [{"macro", "macro_title", "sub", "title"}] when no listed topic fits
 "concepts": [{"title": "Gini impurity", "kind": "definition",
               "topics": ["machine-learning-and-signal-processing/decision-trees-and-impurity-measures"],
               "definition": "For class proportions $p_c$ ... $G(P) = 1 - \\sum_c p_c^2$.",
               "propositions": ["...", "..."], "aliases": ["Gini index"],
               "pseudocode": "",            # an algorithm: its pseudocode; a theorem: "proof" = a proof sketch
               "requires": ["class_proportions", "Shannon entropy"], "related": [["contrasts_with", "Shannon entropy"]]}]}
`topics` are "macro/sub" slugs from Graph/_topics.md (or declared in `new_topics`): a concept without a valid topic is
refused — map the topic first, then the concept, then its cards.
"""
import hashlib
import json
import re
import time

from . import graph as G
from . import learn, topics
from .ingest import load, save
from .notes import RELATED


class SessionError(ValueError):
    pass


def _key(s):
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


def _names(graph):
    """normalised title/alias/id -> node id (first wins, sorted ids for determinism)."""
    out = {}
    for i in sorted(graph.nodes):
        n = graph.nodes[i]
        for s in [i, n.name] + list(n.aliases or []):
            out.setdefault(_key(s), i)
    return out


def propose(cfg, data):
    concepts = data.get("concepts") or []
    if not concepts:
        raise SessionError("no concepts in the file")
    course = str(data.get("course") or "").strip()
    if course and not (cfg.material / course).is_dir():        # a unique part of the folder name is enough
        hits = [c for c in learn.courses(cfg) if course.lower() in c.lower()]
        course = hits[0] if len(hits) == 1 else course
    source = str(data.get("source") or f"Learner/sessions/{data.get('session', 'session')}.md")
    graph = G.Graph.load(cfg)
    names = _names(graph)
    known_topics = {t["slug"] for t in topics.load_all(cfg)}
    declared = []
    for t in data.get("new_topics") or []:
        m, s = topics.slugify(t.get("macro")), topics.slugify(t.get("sub"))
        if m and s:
            declared.append({"macro": m, "macro_title": " ".join(str(t.get("macro_title") or m).split()),
                             "sub": s, "title": " ".join(str(t.get("title") or s).split())})
    allowed = known_topics | {f"{t['macro']}/{t['sub']}" for t in declared}

    errors, items, batch = [], [], {}
    taken = set(graph.nodes)
    for n, c in enumerate(concepts):
        title = " ".join(str(c.get("title") or "").split())
        definition = " ".join(str(c.get("definition") or "").split())
        kind = str(c.get("kind") or "definition").strip()
        if not title or not definition:
            errors.append(f"concept {n + 1}: title and definition are required")
            continue
        if kind not in G.KINDS:
            errors.append(f"{title}: kind must be one of {', '.join(G.KINDS)}")
            continue
        tops = [str(t).strip() for t in (c.get("topics") or []) if str(t).strip()][:2]
        bad = [t for t in tops if t not in allowed]
        if not tops or bad:
            errors.append(f"{title}: topics must be 1-2 'macro/sub' slugs from Graph/_topics.md (or declared in "
                          f"new_topics){'; unknown: ' + ', '.join(bad) if bad else ''}")
            continue
        pseudo = str(c.get("pseudocode") or "").strip()
        if kind == "algorithm" and not pseudo:
            errors.append(f"{title}: an algorithm needs its pseudocode")
            continue
        props = [p if isinstance(p, str) else str(p.get("text", "")) for p in c.get("propositions") or []]
        props = [" ".join(p.split()) for p in props if str(p).strip()]
        aliases = [" ".join(str(a).split()) for a in c.get("aliases") or [] if str(a).strip()]
        match = next((names[_key(s)] for s in [title] + aliases if _key(s) in names), None)
        concept = {"title": title, "kind": kind, "topics": tops, "course": course,
                   "definition": definition, "aliases": aliases, "pseudocode": pseudo,
                   "proof": str(c.get("proof") or "").strip() if kind == "theorem" else "",
                   "propositions": [{"text": p, "source": source} for p in props], "sources": [source],
                   "quote": "", "anchor": source}                 # taught, not quoted from a file
        it = {"index": n, "slug": None, "concept": concept, "decision": "new", "match": None, "status": "pending",
              "neighbours": [], "edges": [], "required_by": [], "dropped_edges": [], "new_propositions": [],
              "_requires": c.get("requires") or [], "_related": c.get("related") or []}
        if match:
            node = graph.nodes[match]
            it["match"] = match
            if node.readonly or not node.is_concept:
                it["decision"] = "covered"                 # a course atomic note already holds it (read-only)
            else:
                have = {_key(node.statement)} | {_key(p["text"]) for p in node.propositions}
                new = [{"text": definition, "source": source}] + concept["propositions"]
                it["new_propositions"] = [p for p in new if _key(p["text"]) not in have]
                it["decision"] = "extends" if it["new_propositions"] else "covered"
            it["slug"] = match
        else:
            it["slug"] = G.unique_slug(title, taken)
            taken.add(it["slug"])
            for s in [title] + aliases:
                batch.setdefault(_key(s), it["slug"])
        items.append(it)

    if errors:
        raise SessionError("; ".join(errors))

    def resolve(ref):
        k = _key(ref)
        return batch.get(k) or names.get(k)

    for it in items:
        req, rel = it.pop("_requires"), it.pop("_related")
        if it["decision"] != "new":
            continue
        for ref in req:
            to = resolve(ref)
            if not to or to == it["slug"]:
                it["dropped_edges"].append({"from": it["slug"], "to": str(ref), "predicate": "requires",
                                            "reason": "unknown concept" if not to else "self link"})
                continue
            it["edges"].append({"predicate": "requires", "to": to, "confidence": "model", "accepted": True,
                                "evidence": ["stated by the teaching session"], "conflict": []})
        for pair in rel:
            pred, ref = (pair + [None, None])[:2] if isinstance(pair, list) else (None, None)
            to = resolve(ref) if ref else None
            if pred not in RELATED or not to or to == it["slug"]:
                it["dropped_edges"].append({"from": it["slug"], "to": str(ref), "predicate": str(pred),
                                            "reason": "unknown concept or predicate"})
                continue
            it["edges"].append({"predicate": pred, "to": to, "confidence": "model", "accepted": True,
                                "evidence": [], "conflict": []})

    # one proposal per session: re-running `propose` with the growing file replaces it, keeping decisions already
    # taken in the Inbox (approved / rejected items stay as they were)
    pid = "session-" + G.slugify(str(data.get("session") or source))[:60]
    try:
        prev = load(cfg, pid)
    except KeyError:
        prev = None
    if prev:
        done = {_key(it["concept"]["title"]): it for it in prev["items"] if it["status"] != "pending"}
        items = [dict(done[_key(it["concept"]["title"])], index=it["index"]) if _key(it["concept"]["title"]) in done
                 else it for it in items]

    used = {t for it in items if it["decision"] == "new" for t in it["concept"].get("topics", [])}
    new_topics = [t for t in declared if f"{t['macro']}/{t['sub']}" in used and f"{t['macro']}/{t['sub']}" not in known_topics]
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    count = lambda d: sum(1 for it in items if it["decision"] == d)  # noqa: E731
    links = [e for it in items for e in it["edges"]]
    proposal = {
        "id": pid, "version": 2,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "pending" if any(it["status"] == "pending" for it in items) else "done", "domain": "",
        "method": "v5",                    # approval: flat Graph/, topics from the shared list (ingest.apply)
        "source": {"file": source, "name": f"session: {data.get('session') or source}", "hash": digest, "pages": 0},
        "origin": "session", "new_topics": new_topics,
        "stats": {"claims": len(items), "concepts": len(items), "new": count("new"), "extends": count("extends"),
                  "covered": count("covered"), "merged": 0,
                  "links": {k: sum(1 for e in links if e["confidence"] == k) for k in ("confident", "model", "conflict")},
                  "usage": {}},
        "items": items, "uncovered": [],
    }
    save(cfg, proposal)
    return proposal
