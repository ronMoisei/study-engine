"""The node index, built from markdown (the notes are the source of truth; this is derived and rebuildable).

Two kinds of node
  Graph/<domain>/<slug>.md      writable proposition notes (schema: Agents/skills/knowledge-graph)
  <course>/atomic/<slug>.md     read-only course atomic notes (process-material pipeline)

Edge direction: an edge (a, pred, b) is written in note `a` and points at what `a` rests on.
Vertical predicates form the prerequisite DAG; the rest are lateral.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

VERTICAL = {"requires", "derived_from", "instantiates", "interprets_via"}
LATERAL = {"contrasts_with", "analogue_of", "constrained_by", "implemented_via", "bottleneck_solved_by"}
KINDS = ["definition", "theorem", "mechanism", "constraint", "heuristic", "application"]
PRED_RE = re.compile(r"^\s*[-*]?\s*([a-z_]+)::\s*(.+)$")
LINK_RE = re.compile(r"\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")


@dataclass
class Node:
    id: str
    name: str
    statement: str
    kind: str = ""
    domain: str = ""
    aliases: list = field(default_factory=list)
    path: Path | None = None
    readonly: bool = False
    edges: list = field(default_factory=list)          # [(predicate, target_id)]

    @property
    def text(self):
        """What gets embedded."""
        return f"{self.name}: {self.statement}".strip()


# ── frontmatter (small YAML subset: scalars, inline/block lists, | block scalars) ──

def split_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    return parse_yaml(text[3:end]), text[end + 4:]


def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def parse_yaml(raw):
    data, key, block = {}, None, None
    for line in raw.split("\n"):
        if block is not None:                              # inside "key: |"
            if line.startswith((" ", "\t")) or not line.strip():
                block.append(line.strip())
                continue
            data[key] = " ".join(x for x in block if x).strip()
            block = None
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val in ("|", ">", "|-", ">-"):
                block = []
            elif val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                data[key] = [_scalar(x) for x in re.split(r",\s*(?![^\[]*\]\])", inner)] if inner else []
            elif val == "":
                data[key] = []
            else:
                data[key] = _scalar(val)
        elif key is not None:
            m = re.match(r"^\s*-\s+(.*)$", line)
            if m:
                if not isinstance(data.get(key), list):
                    data[key] = []
                data[key].append(_scalar(m.group(1)))
            elif line.strip() == "[]":
                data[key] = []
    if block is not None:
        data[key] = " ".join(x for x in block if x).strip()
    return data


def _links(values):
    out = []
    for v in values if isinstance(values, list) else [values]:
        out += [t.strip().split("/")[-1] for t in LINK_RE.findall(str(v))]
    return out


# ── loading ──

def _graph_note(path):
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    lines = body.split("\n")
    name = next((l[2:].strip() for l in lines if l.startswith("# ")), fm.get("title") or path.stem)
    stmt, started = [], False
    for l in lines:                                        # statement = first paragraph after the title
        if l.startswith("# "):
            started = True
            continue
        if not started:
            continue
        if l.startswith("#") or PRED_RE.match(l):
            break
        if l.strip():
            stmt.append(l.strip())
        elif stmt:
            break
    edges = []
    for l in lines:
        m = PRED_RE.match(l)
        if m:
            edges += [(m.group(1), t.strip()) for t in LINK_RE.findall(m.group(2))]
    return Node(id=path.stem, name=name, statement=" ".join(stmt), kind=str(fm.get("kind", "")),
                domain=str(fm.get("domain", "")), aliases=list(fm.get("aliases") or []), path=path, edges=edges)


def _atomic_note(path):
    fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    if not fm:
        return None, []
    stmt = fm.get("easy_definition") or ""
    if isinstance(stmt, list):
        stmt = " ".join(stmt)
    edges = [("requires", t) for t in _links(fm.get("prerequisites") or [])]
    edges += [("contrasts_with", t) for t in _links(fm.get("contrasts_with") or [])]
    inverse = _links(fm.get("implications") or [])         # "implications: [[x]]" means x rests on this note
    node = Node(id=path.stem, name=str(fm.get("title") or path.stem), statement=str(stmt).strip(),
                kind=str(fm.get("type", "")), domain=str(fm.get("macro_topic", "")),
                aliases=list(fm.get("aliases") or []), path=path, readonly=True, edges=edges)
    return node, inverse


class Graph:
    def __init__(self, nodes=None):
        self.nodes = nodes or {}

    @classmethod
    def load(cls, cfg):
        nodes, inverse = {}, []
        if cfg.graph.is_dir():
            for p in sorted(cfg.graph.rglob("*.md")):
                nodes.setdefault(p.stem, _graph_note(p))
        for pattern in cfg.atomic_globs:
            for p in sorted(cfg.vault.glob(pattern)):
                if p.stem in nodes:
                    continue
                node, inv = _atomic_note(p)
                if node and node.statement:
                    nodes[p.stem] = node
                    inverse += [(t, p.stem) for t in inv]
        g = cls(nodes)
        for src, dst in inverse:                           # src requires dst
            if src in nodes and ("requires", dst) not in nodes[src].edges:
                nodes[src].edges.append(("requires", dst))
        return g

    def requires(self, node_id, extra=None):
        """Direct vertical dependencies that exist in the index (plus `extra` {id: [ids]} for uncommitted nodes)."""
        out = []
        if node_id in self.nodes:
            out += [t for p, t in self.nodes[node_id].edges if p in VERTICAL]
        if extra and node_id in extra:
            out += extra[node_id]
        out = list(dict.fromkeys(out))
        # With `extra` (loop checks during ingest) keep every target: a brand-new node with no dependencies of
        # its own is in neither map, yet it must still be reachable as the end of a path.
        return out if extra is not None else [t for t in out if t in self.nodes]

    def reaches(self, start, goal, extra=None):
        """True if `goal` is reachable from `start` along vertical edges."""
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            if cur == goal:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack += self.requires(cur, extra)
        return False

    def would_cycle(self, src, dst, extra=None):
        """Adding the vertical edge src -> dst closes a loop iff src is already reachable from dst."""
        return src == dst or self.reaches(dst, src, extra)

    def closure(self, targets):
        """All nodes the targets rest on (targets included)."""
        seen, stack = set(), [t for t in targets if t in self.nodes]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack += self.requires(cur)
        return seen

    def study_order(self, targets):
        """Deterministic topological order of the closure: prerequisites first, ties broken by id."""
        nodes = self.closure(targets)
        deps = {n: [d for d in self.requires(n) if d in nodes] for n in nodes}
        order, done = [], set()
        while len(order) < len(nodes):
            ready = sorted(n for n in nodes if n not in done and all(d in done for d in deps[n]))
            if not ready:                                  # a loop in hand-written notes: break it deterministically
                ready = [sorted(n for n in nodes if n not in done)[0]]
            for n in ready:
                order.append(n)
                done.add(n)
        return order, deps


# ── writing ──

def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s[:60].strip("_") or "node"


def unique_slug(name, taken):
    base = slugify(name)
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    return slug


STATUS_FOR_KIND = {"definition": "definition", "mechanism": "definition", "theorem": "proof"}


def render_note(slug, claim, domain, edges, source):
    """Markdown for a new proposition note. `edges`: [(predicate, target)] written in this note."""
    kind = claim["kind"] if claim["kind"] in KINDS else "definition"
    status = STATUS_FOR_KIND.get(kind, "unverified")
    fm = ["---", f"id: {slug}", "type: proposition", f"kind: {kind}", f"domain: {domain}",
          f"epistemic_status: {status}", "sources:", f'  - "{source}"']
    if status == "unverified":
        fm += ["falsification_conditions:", '  - "Taken from the source without independent verification"']
    fm += ["aliases: []", f"tags: [domain/{domain}, kind/{kind}]", "---"]
    body = [f"# {claim['name']}", "", claim["statement"].strip(), ""]
    by_pred = {}
    for pred, target in edges:
        by_pred.setdefault(pred, []).append(target)
    titles = {"requires": "Requires", "instantiates": "Instantiates", "contrasts_with": "Contrasts with",
              "analogue_of": "Analogue of"}
    for pred in ("requires", "instantiates", "contrasts_with", "analogue_of"):
        if pred in by_pred:
            body += [f"## {titles[pred]}"] + [f"- {pred}:: [[{t}]]" for t in by_pred[pred]] + [""]
    return "\n".join(fm) + "\n" + "\n".join(body)


def append_source(path, source):
    """Add a source line to an existing Graph note's frontmatter (no-op if already present)."""
    text = path.read_text(encoding="utf-8")
    if source in text or not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    head, rest = text[:end], text[end:]
    line = f'  - "{source}"'
    if re.search(r"^sources:\s*$", head, re.M):
        head = re.sub(r"^(sources:\s*)$", lambda m: m.group(1) + "\n" + line, head, count=1, flags=re.M)
    else:
        head += f"\nsources:\n{line}"
    path.write_text(head + rest, encoding="utf-8")
    return True


def append_edge(path, pred, target):
    text = path.read_text(encoding="utf-8")
    line = f"- {pred}:: [[{target}]]"
    if line in text:
        return False
    path.write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
    return True
