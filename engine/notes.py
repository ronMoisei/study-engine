"""The ONE format for concept notes in Graph/<domain>/<slug>.md. Rendered by code, validated before every write.

A concept is a teachable unit (graph Laplacian, FGSM, star schema): one definition plus the propositions that belong
to it. The teaching DAG is drawn between concepts; propositions are ordered inside the note.

    ---
    id: graph_laplacian
    type: concept
    title: "Graph Laplacian"
    aliases:
      - "Laplacian matrix"
    kind: definition
    domain: spectral-graph-theory
    topic: graph-matrices
    epistemic_status: definition
    sources:
      - "Spectral/ch2.pdf#page=42"
    tags: [domain/spectral-graph-theory, kind/definition, topic/graph-matrices]
    ---
    # Graph Laplacian

    $L = D - A$, where $D$ is the diagonal degree matrix and $A$ the adjacency matrix.

    ## Propositions
    - For every $x$, $x^\\top L x = \\sum_{(u,v)\\in E}(x_u - x_v)^2$. `src: Spectral/ch2.pdf#page=43`

    ## Requires
    - requires:: [[degree_matrix]]

    ## Prerequisite of
    - prerequisite_of:: [[fiedler_vector_partitioning]]

    ## Related
    - contrasts_with:: [[normalized_laplacian]]

`Prerequisite of` records "that note requires this one" without editing that note (course notes are read-only):
the index reads it as a `requires` link stored in the other note.

Nothing else is allowed in the note: no extra sections, no breadcrumbs, no free text between sections.
"""
import json
import re

from .graph import KINDS, split_frontmatter

STATUSES = ["proof", "definition", "empirical_bounded", "practitioner_folklore", "unverified"]
RELATED = ["contrasts_with", "analogue_of"]
SECTIONS = ["Propositions", "Requires", "Prerequisite of", "Related"]
SLUG = re.compile(r"^[a-z0-9][a-z0-9_]*$")
TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PROP_RE = re.compile(r"^- (.+?) `src: ([^`]+)`$")
REQ_RE = re.compile(r"^- requires:: \[\[([a-z0-9_]+)\]\]$")
PRE_RE = re.compile(r"^- prerequisite_of:: \[\[([a-z0-9_]+)\]\]$")
REL_RE = re.compile(r"^- (" + "|".join(RELATED) + r"):: \[\[([a-z0-9_]+)\]\]$")
STATUS_FOR_KIND = {"definition": "definition", "mechanism": "definition", "theorem": "proof"}
NONE_LINE = "*none*"


def _q(s):
    """YAML double-quoted scalar (JSON escaping is valid YAML)."""
    return json.dumps(str(s), ensure_ascii=False)


def _oneline(s):
    return " ".join(str(s).split())


def render_concept(c):
    """c: id, title, aliases, kind, domain, topic, definition, propositions [{text, source}], requires [ids],
    related [(pred, id)], sources [anchors]; optional epistemic_status, falsification_conditions."""
    kind = c["kind"]
    status = c.get("epistemic_status") or STATUS_FOR_KIND.get(kind, "unverified")
    fm = ["---", f"id: {c['id']}", "type: concept", f"title: {_q(_oneline(c['title']))}"]
    aliases = [a for a in dict.fromkeys(_oneline(a) for a in c.get("aliases", [])) if a and a != c["title"]]
    fm += ["aliases:"] + [f"  - {_q(a)}" for a in aliases] if aliases else ["aliases: []"]
    fm += [f"kind: {kind}", f"domain: {c['domain']}", f"topic: {c['topic']}", f"epistemic_status: {status}"]
    if status not in ("proof", "definition"):
        fc = c.get("falsification_conditions") or ["Taken from the source without independent verification"]
        fm += ["falsification_conditions:"] + [f"  - {_q(_oneline(x))}" for x in fc]
    fm += ["sources:"] + [f"  - {_q(s)}" for s in dict.fromkeys(c["sources"])]
    fm += [f"tags: [domain/{c['domain']}, kind/{kind}, topic/{c['topic']}]", "---"]
    body = [f"# {_oneline(c['title'])}", "", _oneline(c["definition"]), "", "## Propositions"]
    props = [f"- {_oneline(p['text'])} `src: {p['source']}`" for p in c.get("propositions", [])]
    body += (props or [NONE_LINE]) + ["", "## Requires"]
    body += [f"- requires:: [[{t}]]" for t in dict.fromkeys(c.get("requires", []))] or [NONE_LINE]
    body += ["", "## Prerequisite of"]
    body += [f"- prerequisite_of:: [[{t}]]" for t in dict.fromkeys(c.get("prerequisite_of", []))] or [NONE_LINE]
    body += ["", "## Related"]
    body += [f"- {p}:: [[{t}]]" for p, t in dict.fromkeys(tuple(x) for x in c.get("related", []))] or [NONE_LINE]
    return "\n".join(fm) + "\n" + "\n".join(body) + "\n"


def _sections(body):
    """-> (title, definition_lines, {section: [lines]}, stray_lines)"""
    lines = [l.rstrip() for l in body.split("\n")]
    title, definition, secs, stray, cur = None, [], {}, [], None
    for l in lines:
        if not l.strip():
            continue
        if l.startswith("# ") and title is None and cur is None and not definition:
            title = l[2:].strip()
        elif l.startswith("## "):
            cur = l[3:].strip()
            secs.setdefault(cur, [])
        elif l.startswith("#"):
            stray.append(l)
        elif cur is None:
            definition.append(l.strip())
        else:
            secs[cur].append(l.strip())
    return title, definition, secs, stray


def parse_concept(text):
    fm, body = split_frontmatter(text)
    title, definition, secs, _ = _sections(body)
    props = []
    for l in secs.get("Propositions", []):
        m = PROP_RE.match(l)
        if m:
            props.append({"text": m.group(1), "source": m.group(2)})
    req = [m.group(1) for l in secs.get("Requires", []) if (m := REQ_RE.match(l))]
    pre = [m.group(1) for l in secs.get("Prerequisite of", []) if (m := PRE_RE.match(l))]
    rel = [(m.group(1), m.group(2)) for l in secs.get("Related", []) if (m := REL_RE.match(l))]
    as_list = lambda v: v if isinstance(v, list) else ([v] if v else [])  # noqa: E731
    return {"id": fm.get("id"), "title": fm.get("title") or title, "aliases": as_list(fm.get("aliases")),
            "kind": fm.get("kind"), "domain": fm.get("domain"), "topic": fm.get("topic"),
            "epistemic_status": fm.get("epistemic_status"),
            "falsification_conditions": as_list(fm.get("falsification_conditions")),
            "sources": as_list(fm.get("sources")), "definition": " ".join(definition),
            "propositions": props, "requires": req, "prerequisite_of": pre, "related": rel}


def validate_concept(text, stem=None, known_ids=None):
    """-> list of errors (empty = valid). `known_ids`: if given, every link must resolve to one of them."""
    errs = []
    if not text.startswith("---\n"):
        return ["missing frontmatter"]
    fm, body = split_frontmatter(text)
    for k in ("id", "type", "title", "kind", "domain", "topic", "epistemic_status", "sources"):
        if not fm.get(k):
            errs.append(f"frontmatter: missing '{k}'")
    if fm.get("type") and fm["type"] != "concept":
        errs.append("frontmatter: type must be 'concept'")
    if fm.get("id") and not SLUG.match(str(fm["id"])):
        errs.append(f"id '{fm['id']}' is not snake_case")
    if stem and fm.get("id") and fm["id"] != stem:
        errs.append(f"id '{fm['id']}' does not match the file name '{stem}'")
    if fm.get("kind") and fm["kind"] not in KINDS:
        errs.append(f"kind '{fm['kind']}' not in {KINDS}")
    if fm.get("topic") and not TOPIC.match(str(fm["topic"])):
        errs.append(f"topic '{fm['topic']}' is not a kebab-case slug")
    st = fm.get("epistemic_status")
    if st and st not in STATUSES:
        errs.append(f"epistemic_status '{st}' not in {STATUSES}")
    if st and st not in ("proof", "definition") and not fm.get("falsification_conditions"):
        errs.append(f"epistemic_status '{st}' needs falsification_conditions")
    if not isinstance(fm.get("sources"), list) or not fm.get("sources"):
        errs.append("sources must be a non-empty list")

    title, definition, secs, stray = _sections(body)
    if title is None:
        errs.append("body must start with '# <title>'")
    elif fm.get("title") and title != fm["title"]:
        errs.append(f"H1 '{title}' differs from frontmatter title '{fm['title']}'")
    if len(" ".join(definition)) < 15:
        errs.append("definition paragraph under the title is missing or too short")
    if list(secs) != SECTIONS:
        errs.append(f"sections must be exactly {SECTIONS} in that order, found {list(secs)}")
    for l in stray:
        errs.append(f"unexpected heading '{l}'")
    checks = {"Propositions": PROP_RE, "Requires": REQ_RE, "Prerequisite of": PRE_RE, "Related": REL_RE}
    links = []
    for sec, rx in checks.items():
        for l in secs.get(sec, []):
            if l == NONE_LINE and len(secs[sec]) == 1:
                continue
            m = rx.match(l)
            if not m:
                errs.append(f"{sec}: malformed line '{l[:80]}'")
            elif sec in ("Requires", "Prerequisite of"):
                links.append(m.group(1))
            elif sec == "Related":
                links.append(m.group(2))
    if fm.get("id") in links:
        errs.append("links to itself")
    if known_ids is not None:
        for t in links:
            if t not in known_ids:
                errs.append(f"link [[{t}]] does not resolve")
    return errs
