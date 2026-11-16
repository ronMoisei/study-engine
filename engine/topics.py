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
