"""The ONE format for atomic notes in Graph/<slug>.md (one flat folder). Rendered by code, validated before every write.

A note is one typed unit of knowledge. Its kind fixes its shape (Hydrogen fills the form, code checks it):

    definition   what something is                   statement + key properties
    theorem      a result that holds under conditions statement + conditions + "## Proof sketch"
    algorithm    how to compute something            what it does + "## Pseudocode" + why each step is there
    mechanism    how/why a system or process works   statement + the steps or causes
    pitfall      a typical mistake or failure mode   what goes wrong + why + how to avoid; REQUIRES the node it is
                                                     a pitfall of
    application  when/where to use something         the use case + why it fits
    heuristic    a rule of thumb                     the rule + when it holds

    ---
    id: gradient_descent
    type: concept
    title: "Gradient descent"
    aliases:
      - "GD"
    kind: algorithm
    topics: ["optimization/first-order-methods"]
    prerequisites: ["[[gradient]]", "[[learning_rate]]"]
    related: ["contrasts_with [[newton_method]]"]
    sources:
      - "01VSCWS - …/01_Study/Notes/Full Notes/09/09_Full_Notes.md"
    epistemic_status: definition
    tags: [optimization/first-order-methods, kind/algorithm]
    ---
    # Gradient descent

    Minimises a differentiable $f$ by stepping against its gradient.

    ## Pseudocode
    ```
    repeat: x <- x - eta * grad f(x)
    ```

    ## Propositions
    - Each step moves along the direction of steepest local decrease. `src: …#L40`

The DAG lives ONLY here: `prerequisites` are the direct premises (A + B => C: C lists A and B; nothing further down).
`topics` are "macro/sub" from the shared list Graph/_topics.md (the macro topic is the colour in the graph and the
Anki tag `macro::sub`). There is no course on a note: a concept is one piece of knowledge, whichever course taught
it; `sources` say where it was learned.

Older formats are still READ (v2: domain/topic + Requires/Prerequisite of/Related sections) so notes can be migrated
one by one; only this format is written.
"""
import json
import re

from .graph import KINDS, split_frontmatter

STATUSES = ["proof", "definition", "empirical_bounded", "practitioner_folklore", "unverified"]
RELATED = ["contrasts_with", "analogue_of"]
SLUG = re.compile(r"^[a-z0-9][a-z0-9_]*$")
TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$")            # macro/sub
PROP_RE = re.compile(r"^- (.+?) `src: ([^`]+)`$")
LINK = re.compile(r"\[\[([a-z0-9_]+)\]\]")
REL_ITEM = re.compile(r"^(" + "|".join(RELATED) + r") \[\[([a-z0-9_]+)\]\]$")
STATUS_FOR_KIND = {"definition": "definition", "mechanism": "definition", "theorem": "proof", "algorithm": "definition"}
NONE_LINE = "*none*"
# extra body section each kind carries before "## Propositions" (required for these kinds)
KIND_SECTION = {"algorithm": "Pseudocode", "theorem": "Proof sketch"}
# v2 (read only)
_REQ_RE = re.compile(r"^- requires:: \[\[([a-z0-9_]+)\]\]$")
_PRE_RE = re.compile(r"^- prerequisite_of:: \[\[([a-z0-9_]+)\]\]$")
_REL_RE = re.compile(r"^- (" + "|".join(RELATED) + r"):: \[\[([a-z0-9_]+)\]\]$")


def _q(s):
    """YAML double-quoted scalar (JSON escaping is valid YAML)."""
    return json.dumps(str(s), ensure_ascii=False)


def _oneline(s):
    return " ".join(str(s).split())


def _flow(items):
    return "[" + ", ".join(_q(x) for x in items) + "]"


def topics_of(c):
    """The note's topics; a v2 note (domain + topic) maps to "domain/topic" until it is migrated."""
    t = [x for x in (c.get("topics") or []) if x]
    if not t and c.get("domain") and c.get("topic"):
        t = [f"{c['domain']}/{c['topic']}"]
    return list(dict.fromkeys(t))


def render_concept(c):
    """c: id, title, aliases, kind, topics ["macro/sub"], definition, propositions [{text, source}], requires [ids],
    related [(pred, id)], sources [anchors]; pseudocode (algorithm), proof (theorem); optional epistemic_status,
    falsification_conditions."""
    kind = c["kind"]
    status = c.get("epistemic_status") or STATUS_FOR_KIND.get(kind, "unverified")
    tops = topics_of(c)
    fm = ["---", f"id: {c['id']}", "type: concept", f"title: {_q(_oneline(c['title']))}"]
    aliases = [a for a in dict.fromkeys(_oneline(a) for a in c.get("aliases", [])) if a and a != c["title"]]
    fm += ["aliases:"] + [f"  - {_q(a)}" for a in aliases] if aliases else ["aliases: []"]
    fm += [f"kind: {kind}", f"topics: {_flow(tops)}",
           f"prerequisites: {_flow(f'[[{t}]]' for t in dict.fromkeys(c.get('requires') or []))}",
           f"related: {_flow(f'{p} [[{t}]]' for p, t in dict.fromkeys(tuple(x) for x in c.get('related') or []))}"]
    if c.get("prerequisite_of"):        # transitional: "that read-only course note requires this one" (goes away
        fm.append(f"prerequisite_of: {_flow(f'[[{t}]]' for t in dict.fromkeys(c['prerequisite_of']))}")  # with them)
    fm += ["sources:"] + [f"  - {_q(s)}" for s in dict.fromkeys(c["sources"])]
    fm.append(f"epistemic_status: {status}")
    if status not in ("proof", "definition"):
        fc = c.get("falsification_conditions") or ["Taken from the source without independent verification"]
        fm += ["falsification_conditions:"] + [f"  - {_q(_oneline(x))}" for x in fc]
    fm += ["tags: [" + ", ".join(tops + [f"kind/{kind}"]) + "]", "---"]
    body = [f"# {_oneline(c['title'])}", "", _oneline(c["definition"])]
    extra = KIND_SECTION.get(kind)
    if extra:
        text = (c.get("pseudocode") if kind == "algorithm" else c.get("proof")) or ""
        text = text.strip("\n")
        if kind == "algorithm" and text and not text.lstrip().startswith("```"):
            text = "```\n" + text + "\n```"
        body += ["", f"## {extra}", text or NONE_LINE]
    body += ["", "## Propositions"]
    props = [f"- {_oneline(p['text'])} `src: {p['source']}`" for p in c.get("propositions", [])]
    body += props or [NONE_LINE]
    return "\n".join(fm) + "\n" + "\n".join(body) + "\n"


def _sections(body):
    """-> (title, definition_lines, {section: [raw lines]}, stray_lines). Fenced code blocks are kept verbatim
    (a `# comment` inside pseudocode is not a heading)."""
    title, definition, secs, stray, cur, fence = None, [], {}, [], None, False
    for raw in body.split("\n"):
        l = raw.rstrip()
        if l.lstrip().startswith("```"):
            fence = not fence
            if cur is not None:
                secs[cur].append(l)
            continue
        if fence:
            if cur is not None:
                secs[cur].append(l)
            continue
        if not l.strip():
            if cur is not None and secs[cur]:
                secs[cur].append("")
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
    return title, definition, {k: _trim(v) for k, v in secs.items()}, stray


def _trim(lines):
    while lines and not lines[-1]:
        lines = lines[:-1]
    return lines


def _block(lines):
    text = "\n".join(lines).strip("\n")
    return "" if text == NONE_LINE else text


def parse_concept(text):
    fm, body = split_frontmatter(text)
    title, definition, secs, _ = _sections(body)
    props = []
    for l in secs.get("Propositions", []):
        m = PROP_RE.match(l)
        if m:
            props.append({"text": m.group(1), "source": m.group(2)})
    as_list = lambda v: v if isinstance(v, list) else ([v] if v else [])  # noqa: E731
    req = [m.group(1) for x in as_list(fm.get("prerequisites")) if (m := LINK.search(str(x)))]
    rel = [(m.group(1), m.group(2)) for x in as_list(fm.get("related")) if (m := REL_ITEM.match(str(x).strip()))]
    # v2 sections (read only, for migration)
    req += [m.group(1) for l in secs.get("Requires", []) if (m := _REQ_RE.match(l))]
    pre = [m.group(1) for l in secs.get("Prerequisite of", []) if (m := _PRE_RE.match(l))]
    rel += [(m.group(1), m.group(2)) for l in secs.get("Related", []) if (m := _REL_RE.match(l))]
    c = {"id": fm.get("id"), "title": fm.get("title") or title, "aliases": as_list(fm.get("aliases")),
         "kind": fm.get("kind"), "topics": [str(t) for t in as_list(fm.get("topics"))],
         "epistemic_status": fm.get("epistemic_status"),
         "falsification_conditions": as_list(fm.get("falsification_conditions")),
         "sources": as_list(fm.get("sources")), "definition": " ".join(definition),
         "pseudocode": _block(secs.get("Pseudocode", [])), "proof": _block(secs.get("Proof sketch", [])),
         "propositions": props, "requires": list(dict.fromkeys(req)), "prerequisite_of": pre,
         "related": list(dict.fromkeys(rel)),
         # v2 fields, kept so an unmigrated note still maps to a topic and a course
         "domain": fm.get("domain") or "", "topic": fm.get("topic") or "", "course": fm.get("course") or ""}
    c["topics"] = topics_of(c)
    if c["topics"] and not (c["domain"] and c["topic"]):     # code that still reads domain/topic: the first topic
        c["domain"], _, c["topic"] = c["topics"][0].partition("/")
    pre += [m.group(1) for x in as_list(fm.get("prerequisite_of")) if (m := LINK.search(str(x)))]
    c["prerequisite_of"] = list(dict.fromkeys(pre))
    return c


def validate_concept(text, stem=None, known_ids=None):
    """-> list of errors (empty = valid). `known_ids`: if given, every link must resolve to one of them."""
    errs = []
    if not text.startswith("---\n"):
        return ["missing frontmatter"]
    fm, body = split_frontmatter(text)
    for k in ("id", "type", "title", "kind", "topics", "epistemic_status", "sources"):
        if not fm.get(k):
            errs.append(f"frontmatter: missing '{k}'")
    if fm.get("type") and fm["type"] != "concept":
        errs.append("frontmatter: type must be 'concept'")
    if fm.get("id") and not SLUG.match(str(fm["id"])):
        errs.append(f"id '{fm['id']}' is not snake_case")
    if stem and fm.get("id") and fm["id"] != stem:
        errs.append(f"id '{fm['id']}' does not match the file name '{stem}'")
    kind = fm.get("kind")
    if kind and kind not in KINDS:
        errs.append(f"kind '{kind}' not in {KINDS}")
    tops = fm.get("topics") if isinstance(fm.get("topics"), list) else [fm.get("topics")]
    for t in [x for x in tops if x]:
        if not TOPIC.match(str(t)):
            errs.append(f"topic '{t}' is not 'macro/sub' in kebab-case")
    st = fm.get("epistemic_status")
    if st and st not in STATUSES:
        errs.append(f"epistemic_status '{st}' not in {STATUSES}")
    if st and st not in ("proof", "definition") and not fm.get("falsification_conditions"):
        errs.append(f"epistemic_status '{st}' needs falsification_conditions")
    if not isinstance(fm.get("sources"), list) or not fm.get("sources"):
        errs.append("sources must be a non-empty list")
    links = []
    for x in fm.get("prerequisites") or []:
        m = re.fullmatch(r"\[\[([a-z0-9_]+)\]\]", str(x).strip())
        if not m:
            errs.append(f"prerequisites: malformed item '{str(x)[:60]}'")
        else:
            links.append(m.group(1))
    if kind == "pitfall" and not links:
        errs.append("a pitfall must list the node it is a pitfall of in its prerequisites")
    for x in fm.get("prerequisite_of") or []:
        m = re.fullmatch(r"\[\[([a-z0-9_]+)\]\]", str(x).strip())
        if not m:
            errs.append(f"prerequisite_of: malformed item '{str(x)[:60]}'")
        else:
            links.append(m.group(1))
    for x in fm.get("related") or []:
        m = REL_ITEM.match(str(x).strip())
        if not m:
            errs.append(f"related: malformed item '{str(x)[:60]}' (use '<{'|'.join(RELATED)}> [[slug]]')")
        else:
            links.append(m.group(2))

    title, definition, secs, stray = _sections(body)
    if title is None:
        errs.append("body must start with '# <title>'")
    elif fm.get("title") and title != fm["title"]:
        errs.append(f"H1 '{title}' differs from frontmatter title '{fm['title']}'")
    if len(" ".join(definition)) < 15:
        errs.append("definition paragraph under the title is missing or too short")
    want = ([KIND_SECTION[kind]] if kind in KIND_SECTION else []) + ["Propositions"]
    if list(secs) != want:
        errs.append(f"sections must be exactly {want} in that order, found {list(secs)}")
    if kind == "algorithm" and "```" not in "\n".join(secs.get("Pseudocode", [])):
        errs.append("an algorithm needs its pseudocode in a ``` block")
    for l in stray:
        errs.append(f"unexpected heading '{l}'")
    for l in secs.get("Propositions", []):
        if not l or (l == NONE_LINE and len([x for x in secs["Propositions"] if x]) == 1):
            continue
        if not PROP_RE.match(l):
            errs.append(f"Propositions: malformed line '{l[:80]}'")
    if fm.get("id") in links:
        errs.append("links to itself")
    if known_ids is not None:
        for t in links:
            if t not in known_ids:
                errs.append(f"link [[{t}]] does not resolve")
    return errs
