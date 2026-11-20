"""Topic skeleton per domain: Graph/<domain>/_topics.md, an ordered list you can edit by hand.

    ---
    type: topics
    domain: deep-nlp
    ---
    # Topics — deep-nlp

    1. `word-embeddings` — Word embeddings
    2. `attention-mechanisms` — Attention mechanisms

Order matters: a concept's prerequisites may not come from a LATER topic (used as a veto on links).
New topics proposed by the grouping step are appended in the order they first appear in the material,
and only when you approve concepts that use them. Reorder the lines to change the teaching order.
"""
import re
import unicodedata

LINE = re.compile(r"^\s*\d+\.\s+`([a-z0-9-]+)`\s+[—-]\s+(.+?)\s*$")
MACRO = re.compile(r"^##\s+(.+?)\s+`([a-z0-9-]+)`\s*$")
ALL_HEADER = """Macro topics (the colours of the knowledge graph) and their subtopics, in teaching order. A note's
`topics` and a card's tags (`macro::sub`) come from this list only. Reorder or rename freely; a renamed slug must
also be renamed in the notes that use it."""


# ── the shared topic list: Graph/_topics.md (every note, every course) ──

def all_path(cfg):
    return cfg.graph / "_topics.md"


def load_all(cfg):
    """[{slug: "macro/sub", macro, macro_title, sub, title}] in teaching order."""
    f = all_path(cfg)
    if not f.is_file():
        return []
    out, macro, seen = [], None, set()
    for line in f.read_text(encoding="utf-8").splitlines():
        m = MACRO.match(line)
        if m:
            macro = {"macro": m.group(2), "macro_title": m.group(1)}
            continue
        m = LINE.match(line)
        if m and macro:
            slug = f"{macro['macro']}/{m.group(1)}"
            if slug not in seen:
                seen.add(slug)
                out.append({"slug": slug, **macro, "sub": m.group(1), "title": m.group(2)})
    return out


def macros(items):
    """[(macro, macro_title)] in order."""
    return list(dict.fromkeys((t["macro"], t["macro_title"]) for t in items))


def write_all(cfg, items):
    f = all_path(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: topics", "---", "# Topics", "", ALL_HEADER]
    for macro, title in macros(items):
        lines += ["", f"## {title} `{macro}`", ""]
        lines += [f"{i}. `{t['sub']}` — {t['title']}" for i, t in enumerate([t for t in items if t["macro"] == macro], 1)]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_all(cfg, new):
    """Append topics not yet listed (each {macro, macro_title, sub, title}); -> the ones added."""
    have = load_all(cfg)
    slugs = {t["slug"] for t in have}
    add = []
    for t in new:
        slug = f"{t['macro']}/{t['sub']}"
        if slug not in slugs:
            slugs.add(slug)
            add.append({**t, "slug": slug})
    if add:
        # keep each new subtopic with its macro: insert after the macro's last subtopic, new macros at the end
        out = list(have)
        for t in add:
            last = max((i for i, x in enumerate(out) if x["macro"] == t["macro"]), default=None)
            out.insert(last + 1 if last is not None else len(out), t)
        write_all(cfg, out)
    return add


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48].strip("-")


def path(cfg, domain):
    return cfg.graph / domain / "_topics.md"


def load(cfg, domain):
    f = path(cfg, domain)
    if not f.is_file():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m and m.group(1) not in {t["slug"] for t in out}:
            out.append({"slug": m.group(1), "title": m.group(2)})
    return out


def write(cfg, domain, items):
    f = path(cfg, domain)
    f.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: topics", f"domain: {domain}", "---", f"# Topics — {domain}", "",
             "Teaching order of the chapters in this domain. Reorder the lines to change it; a concept may not",
             "require a concept from a later topic.", ""]
    lines += [f"{i}. `{t['slug']}` — {t['title']}" for i, t in enumerate(items, 1)]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append(cfg, domain, new):
    have = load(cfg, domain)
    slugs = {t["slug"] for t in have}
    add = [t for t in new if t["slug"] not in slugs]
    if add or not path(cfg, domain).is_file():
        write(cfg, domain, have + add)
    return add
