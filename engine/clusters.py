"""Concept clusters for the all-knowledge view: grouped by what the ideas are about, not by course.

Deterministic, no model call:
  1. every node starts in its topic (graph concepts: `topic`; course atomic notes: `macro_topic`);
     the same topic slug in two courses is one cluster;
  2. a cluster smaller than MIN_SIZE is folded into the cluster it shares the most links with
     (ties: the larger cluster, then the name), one merge at a time, until nothing small is linked;
  3. small clusters with no links at all are gathered in one "Unlinked" cluster.
A cluster is named after the topic that contributes most of its nodes.
"""
from collections import Counter, defaultdict

MIN_SIZE = 6
UNLINKED = "unlinked"


def norm_topic(s):
    s = str(s or "").strip().strip('"').lower().replace("-", "_").replace(" ", "_")
    return s or "other"


WORDS = {"nlp": "NLP", "sql": "SQL", "nosql": "NoSQL", "mongodb": "MongoDB", "olap": "OLAP", "llm": "LLM",
         "rl": "RL", "ml": "ML", "ai": "AI"}


def title_of(slug):
    if slug == UNLINKED:
        return "Unlinked"
    words = [WORDS.get(w, w) for w in slug.split("_") if w]
    t = " ".join(words)
    return t[:1].upper() + t[1:]


def cluster(ids, links, topic_of, min_size=MIN_SIZE):
    """ids: node ids; links: iterable of (a, b) pairs (any direction); topic_of: id -> topic string.
    -> {id: cluster_id}, {cluster_id: title}."""
    ids = sorted(ids)
    topic = {i: norm_topic(topic_of(i)) for i in ids}
    label = dict(topic)
    nbr = defaultdict(set)
    for a, b in links:
        if a in label and b in label and a != b:
            nbr[a].add(b)
            nbr[b].add(a)
    while True:
        sizes = Counter(label.values())
        small = sorted((c for c in sizes if sizes[c] < min_size), key=lambda c: (sizes[c], c))
        merged = False
        for c in small:
            mem = [i for i in ids if label[i] == c]
            out = Counter(label[j] for i in mem for j in nbr[i] if label[j] != c)
            if not out:
                continue
            target = min(out, key=lambda t: (-out[t], -sizes[t], t))
            for i in mem:
                label[i] = target
            merged = True
            break
        if not merged:
            break
    sizes = Counter(label.values())
    for i in ids:
        if sizes[label[i]] < 3 and not any(label[j] == label[i] for j in nbr[i]):
            label[i] = UNLINKED
    # name: the topic contributing most nodes (ties by name); the label itself stays the stable id
    names = {}
    for c in set(label.values()):
        if c == UNLINKED:
            names[c] = title_of(c)
            continue
        counts = Counter(topic[i] for i in ids if label[i] == c)
        names[c] = title_of(min(counts, key=lambda t: (-counts[t], t)))
    return label, names
