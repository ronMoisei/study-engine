"""Term index: which concept 'owns' which name, and where those names are used. Pure code.

Measured on the 196 course notes (2026-11-15): a mention anywhere in a note body is NOT a direction signal
(as many mentions point against the existing prerequisite links as along them). A mention inside the short
definition is better (10 along, 3 against) but rare. So mentions are used as
  - a hint shown to the link model ("B's definition uses the term 'degree matrix' of A"), and
  - the defined-before-use check on a study plan (a warning, never an edge).
"""
import re
from collections import Counter, defaultdict

GENERIC_TAIL = {"formulation", "definition", "mechanism", "concept", "step", "model", "problem", "framework",
                "formalism", "approach", "method", "methods", "properties", "property", "layer", "architecture",
                "foundations", "overview", "principles", "theory", "taxonomy"}
MAX_DF = 0.15          # a term used in more than 15% of notes says nothing
MAX_DF_SINGLE = 0.03   # a one-word term must be used in at most 3% of notes
MAX_OWNERS = 2


def norm(s):
    return " " + re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip() + " "


def node_terms(node):
    out = set()
    for t in [node.name] + list(node.aliases):
        w = norm(t).split()
        if w:
            out.add(" ".join(w))
        while w and w[-1] in GENERIC_TAIL:
            w = w[:-1]
            if w:
                out.add(" ".join(w))
    return {t for t in out if len(t) >= 4}


def defining_text(node):
    """The part of a note that states what it IS (definition + propositions), not the commentary."""
    return " ".join([node.statement] + [p["text"] for p in getattr(node, "propositions", [])])


class TermIndex:
    def __init__(self, graph, extra_nodes=()):
        """extra_nodes: objects with id, name, aliases, statement (new concepts not yet written)."""
        nodes = {**graph.nodes, **{n.id: n for n in extra_nodes}}
        self.nodes = nodes
        texts = {i: norm(defining_text(n)) for i, n in nodes.items()}
        owners = defaultdict(set)
        for i, n in nodes.items():
            for t in node_terms(n):
                owners[t].add(i)
        df = Counter()
        for t in owners:
            df[t] = sum(1 for x in texts.values() if f" {t} " in x)
        limit = max(2, MAX_DF * len(nodes))
        single_limit = max(2, MAX_DF_SINGLE * len(nodes))     # one-word terms ("attention") must be rarer
        self.owner = {t: o for t, o in owners.items() if len(o) <= MAX_OWNERS
                      and df[t] <= (single_limit if " " not in t else limit)}
        self.texts = texts

    def mentions(self, node_id):
        """{owner_id: [terms]} of other concepts whose terms appear in this concept's definition text."""
        text = self.texts.get(node_id, "")
        out = defaultdict(list)
        for t, owners in self.owner.items():
            if f" {t} " in text:
                for o in owners:
                    if o != node_id:
                        out[o].append(t)
        return dict(out)

    def uses(self, a, b):
        """Terms of `b` that appear in `a`'s definition (a hint that a builds on b)."""
        return self.mentions(a).get(b, [])


def defined_before_use(order, index, graph_ids):
    """Walk a study order; report terms used before the concept that owns them is taught.

    -> [{"step", "id", "uses", "term", "problem"}]  problem: "taught later" | "not in the plan"
    """
    rank = {n: i for i, n in enumerate(order)}
    out = []
    for i, nid in enumerate(order):
        for other, terms in sorted(index.mentions(nid).items()):
            if other in rank and rank[other] > i:
                out.append({"step": i + 1, "id": nid, "uses": other, "term": terms[0], "problem": "taught later"})
            elif other not in rank and other in graph_ids:
                out.append({"step": i + 1, "id": nid, "uses": other, "term": terms[0], "problem": "not in the plan"})
    return out
