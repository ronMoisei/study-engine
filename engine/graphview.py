"""What the side panel shows: the graph (filtered, laid out, coloured by learner status) and one node in detail."""
import re
import urllib.parse

from . import learn
from .graph import LATERAL, Graph
from .layout import layout
from .learner import statuses


def _rel(path, base):
    try:
        return path.resolve().relative_to(base.resolve())
    except (ValueError, AttributeError):
        return None


def group_of(cfg, node):
    """Graph concepts group by domain; course atomic notes by their course folder in Material/."""
    if node.is_concept or not node.readonly:
        return node.domain or "graph"
    rel = _rel(node.path, cfg.material)
    return rel.parts[0] if rel is not None and len(rel.parts) > 1 else "course"


def workspace_rel(cfg, node):
    """Path relative to the Magistrale folder (what an agent working in that project can open)."""
    rel = _rel(node.path, cfg.magistrale)
    return rel.as_posix() if rel is not None else None


def obsidian_uri(cfg, node):
    """obsidian:// link into the Magistrale vault (graph concepts and course atomic notes alike)."""
    rel = _rel(node.path, cfg.vault)
    if rel is None:
        return None
    return "obsidian://open?vault=" + urllib.parse.quote(cfg.vault.name) + "&file=" + \
        urllib.parse.quote(rel.as_posix()[:-3])


def _dependents(graph):
    out = {}
    for n in graph.nodes:
        for d in graph.requires(n):
            out.setdefault(d, []).append(n)
    return out


def graph_payload(cfg, group=None, focus=None, around=1):
    """group: show one domain/course; focus: show the prerequisite path of a node plus `around` levels of
    dependents. With neither, everything is returned (the panel asks for a group by default)."""
    g = Graph.load(cfg)
    st = statuses(cfg.ledger)
    groups = {}
    for n in g.nodes.values():
        k = group_of(cfg, n)
        groups[k] = groups.get(k, 0) + 1
    if focus and focus in g.nodes:
        shown = set(g.closure([focus]))
        dep = _dependents(g)
        frontier = {focus}
        for _ in range(max(0, around)):
            frontier = {d for f in frontier for d in dep.get(f, [])} - shown
            shown |= frontier
    elif group:
        shown = {i for i, n in g.nodes.items() if group_of(cfg, n) == group}
    else:
        shown = set(g.nodes)
    deps = {n: [d for d in g.requires(n) if d in shown] for n in shown}
    topic_key = lambda i: (g.nodes[i].topic or "", g.nodes[i].name.lower())  # noqa: E731
    pos = layout(shown, deps, key=topic_key)
    nodes = []
    for i in sorted(shown):
        n = g.nodes[i]
        nodes.append({"id": i, "name": n.name, "kind": n.kind, "topic": n.topic, "group": group_of(cfg, n),
                      "status": st.get(i, "untested"), "concept": n.is_concept, "readonly": n.readonly,
                      "focus": i == focus, **pos[i]})
    edges = [{"from": n, "to": d, "type": "requires"} for n in sorted(shown) for d in deps[n]]
    for i in sorted(shown):
        for p, t in g.nodes[i].edges:
            if p in LATERAL and t in shown and i < t:
                edges.append({"from": i, "to": t, "type": p})
    counts = {}
    for x in nodes:
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    return {"nodes": nodes, "edges": edges, "groups": dict(sorted(groups.items())), "group": group,
            "focus": focus, "levels": max((p["level"] for p in pos.values()), default=-1) + 1,
            "status_counts": counts, "total_nodes": len(g.nodes)}


def node_detail(cfg, node_id):
    g = Graph.load(cfg)
    if node_id not in g.nodes:
        raise KeyError(f"unknown node {node_id}")
    n = g.nodes[node_id]
    st = statuses(cfg.ledger)
    dep = _dependents(g)
    order, _ = g.study_order([node_id])
    name = lambda i: g.nodes[i].name if i in g.nodes else i  # noqa: E731
    return {
        "id": node_id, "name": n.name, "kind": n.kind, "topic": n.topic, "group": group_of(cfg, n),
        "definition": n.statement, "propositions": n.propositions, "aliases": n.aliases, "sources": n.sources,
        "status": st.get(node_id, "untested"), "readonly": n.readonly, "concept": n.is_concept,
        "requires": [{"id": d, "name": name(d), "status": st.get(d, "untested")} for d in g.requires(node_id)],
        "required_by": [{"id": d, "name": name(d), "status": st.get(d, "untested")} for d in sorted(dep.get(node_id, []))],
        "related": [{"id": t, "name": name(t), "type": p} for p, t in n.edges if p in LATERAL],
        "path": [{"id": i, "name": name(i), "status": st.get(i, "untested")} for i in order],
        "file": workspace_rel(cfg, n), "obsidian": obsidian_uri(cfg, n),
    }


def teach_message(cfg, node_id, level=None, goal=None):
    """The message handed to the agy conversation: deterministic, built from the graph, not by a model.
    `level` (1-5, self-rated) picks the session mode; `goal` is the learner's scope in their own words."""
    level = learn.level_of(level)
    d = node_detail(cfg, node_id)
    todo = [p for p in d["path"] if p["status"] != "held"]
    lines = [
        "Use the `learning-harness` skill.",
        f"Topic: **{d['name']}** (graph node `{d['id']}`" + (f", note `{d['file']}`" if d["file"] else "") + ").",
    ]
    lines += learn.header(level, goal)
    lines += ["", "Prerequisite path from the study engine, foundations first, with my status from the evidence log:"]
    lines += [f"{i}. {p['name']} (`{p['id']}`) — {p['status']}" for i, p in enumerate(d["path"], 1)]
    if level and level >= 3 and (d["related"] or d["required_by"]):
        lines += ["", "Connected topics to combine with it:"]
        lines += [f"- {x['name']} (`{x['id']}`) — {x['type'].replace('_', ' ')}" for x in d["related"]]
        lines += [f"- {x['name']} (`{x['id']}`) — builds on it" for x in d["required_by"]]
    lines += [""] + [x for x in learn.instructions(level, len(todo), len(d["path"])) if x]
    return "\n".join(lines)


def _nodes_from(cfg, g, st, path):
    """Graph nodes whose note or source lies in `path` (a Material file or folder), in study order."""
    try:
        rel = path.resolve().relative_to(cfg.material.resolve()).as_posix()
    except ValueError:
        return []
    hits = []
    for i, n in g.nodes.items():
        np_ = _rel(n.path, cfg.material)
        srcs = [str(s).split("#")[0].strip("[]") for s in (n.sources or [])]
        if (np_ is not None and (np_.as_posix() + "/").startswith(rel.rstrip("/") + "/")) or \
                any((s + "/").startswith(rel.rstrip("/") + "/") for s in srcs):
            hits.append(i)
    order, _ = g.study_order(hits)
    keep = set(hits)
    return [{"id": i, "name": g.nodes[i].name, "status": st.get(i, "untested")} for i in order if i in keep]


def learn_message(cfg, target, level=None, goal=None):
    """Inbox 'Start learning': target = a graph node (id or name), a Material file/folder/course, or a topic in words."""
    level = learn.level_of(level)
    t = str(target or "").strip()
    if not t and not (goal or "").strip():
        raise KeyError("say what you want to learn: a file, folder, course or topic, or a goal")
    g = Graph.load(cfg)
    st = statuses(cfg.ledger)
    if t in g.nodes:
        return teach_message(cfg, t, level, goal)
    by_name = {n.name.lower(): i for i, n in g.nodes.items()}
    for i, n in g.nodes.items():
        for a in n.aliases or []:
            by_name.setdefault(str(a).lower(), i)
    if t.lower() in by_name:
        return teach_message(cfg, by_name[t.lower()], level, goal)
    path = learn.find_material(cfg, t)
    if path is not None:
        return learn.material_message(cfg, path, level, goal, _nodes_from(cfg, g, st, path))
    words = [w for w in re.findall(r"[a-z0-9]{4,}", t.lower())]
    related = [{"id": i, "name": n.name, "status": st.get(i, "untested")} for i, n in sorted(g.nodes.items())
               if words and any(w in n.name.lower() for w in words)][:8]
    return learn.topic_message(level, goal, t, related)
