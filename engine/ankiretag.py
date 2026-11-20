"""Bring existing Anki cards in line with the graph (2026-11-20): a card carries ONLY
    lh::<slug>           its node (a node merged away during the migration -> the node it was merged into)
    macro::sub           the node's topics, from the note
    remedial, gap::<slug>, leech, marked   kept (gap:: follows merges too)
Everything else (node::, req::, domain::topic, old macro tags, ...) is removed: the DAG lives in the notes only.
Cards without an lh:: tag are left alone and listed; a card whose node does not exist is left alone and listed.
"""
import json
import re

from . import graph as G

KEEP = re.compile(r"^(remedial|leech|marked)$", re.I)


def redirects(cfg):
    try:
        r = json.loads((cfg.shared_dir / "migrate" / "redirects.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        r = {}
    for k, v in list(r.items()):                                   # chains a -> b -> c
        seen = {k}
        while v in r and v not in seen:
            seen.add(v)
            v = r[v]
        r[k] = v
    return r


def wanted(tags, graph, red):
    """-> (new tags, problem): the tags this card should carry, or a reason to leave it."""
    lh = [t for t in tags if t.lower().startswith("lh::")]
    if not lh:
        return None, "no lh:: tag"
    slugs = list(dict.fromkeys(red.get(t[4:], t[4:]) for t in lh))
    if len(slugs) > 1:
        return None, f"several nodes: {', '.join(slugs)}"
    node = graph.nodes.get(slugs[0])
    if node is None:
        return None, f"node {slugs[0]} is not in the graph"
    keep = [t for t in tags if KEEP.match(t)]
    gaps = [f"gap::{red.get(t[5:], t[5:])}" for t in tags if t.lower().startswith("gap::")]
    return list(dict.fromkeys([f"lh::{slugs[0]}"] + [t.replace("/", "::") for t in node.topics] + keep + gaps)), None


def retag(cfg, anki, dry_run=False):
    """-> {"changed", "unchanged", "skipped": {reason: n}, "examples": [...]}"""
    graph, red = G.Graph.load(cfg), redirects(cfg)
    nids = anki.call("findNotes", query="deck:*") or []
    out = {"notes": len(nids), "changed": 0, "unchanged": 0, "skipped": {}, "examples": [], "orphans": []}
    updates = []
    for k in range(0, len(nids), 500):
        for n in anki.call("notesInfo", notes=nids[k:k + 500]) or []:
            tags = list(n.get("tags") or [])
            new, problem = wanted(tags, graph, red)
            if problem:
                key = problem.split(":")[0] if problem.startswith("several") else re.sub(r" \S+ ", " <slug> ", problem)
                out["skipped"][key] = out["skipped"].get(key, 0) + 1
                if "not in the graph" in problem:
                    out["orphans"].append(problem.split()[1])
                continue
            if sorted(new) == sorted(tags):
                out["unchanged"] += 1
                continue
            out["changed"] += 1
            if len(out["examples"]) < 4:
                out["examples"].append({"before": tags, "after": new})
            updates.append({"action": "updateNoteTags", "params": {"note": n["noteId"], "tags": new}})
    if not dry_run:
        for k in range(0, len(updates), 200):
            anki.call("multi", actions=updates[k:k + 200])
    out["orphans"] = sorted(set(out["orphans"]))
    return out
