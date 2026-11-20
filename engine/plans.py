"""Study plans (Learner/plans/<goal_slug>.md, written by the agy teaching agent) as live progress for the Teach tab.

A plan note: frontmatter (goal, phase, next, done, course, updated) + a mermaid map whose nodes are numbered steps
grouped in subgraphs:
    subgraph S1 ["1. Tabular Mapping & Audio Basics"]
      S1_1["1.1 Formulation & dev/eval Split"]
A step is DONE when its number is in the frontmatter list `done: ["1.1", "1.2"]` (the agent adds each step as it
completes it), or its label says "(done)", "(held)" or "✓". The CURRENT step is the one `next:` starts with.
"""
import re

from .graph import split_frontmatter

SUBGRAPH = re.compile(r'^\s*subgraph\s+(\w+)\s*\["?(.+?)"?\]\s*$')
NODE = re.compile(r'(\w+)\["((\d+(?:\.\d+)*)\s+([^"]*?))"\]')
DONE_MARK = re.compile(r"\((done|held)\)|✓", re.I)
STEP_ID = re.compile(r"^\s*(\d+(?:\.\d+)+)")


def parse(text):
    fm, body = split_frontmatter(text)
    done = {str(x).strip() for x in (fm.get("done") or [])} if isinstance(fm.get("done"), list) else set()
    nxt = str(fm.get("next") or "")
    m = STEP_ID.match(nxt)
    current = m.group(1) if m else ""
    sections, seen, sec = [], set(), None
    in_mermaid = False
    for line in body.splitlines():
        if line.strip().startswith("```mermaid"):
            in_mermaid = True
            continue
        if in_mermaid and line.strip().startswith("```"):
            in_mermaid = False
            continue
        if not in_mermaid:
            continue
        sm = SUBGRAPH.match(line)
        if sm:
            sec = {"title": re.sub(r"^\d+\.\s*", "", sm.group(2)).strip(), "steps": []}
            sections.append(sec)
            continue
        for nm in NODE.finditer(line):
            sid, label = nm.group(3), nm.group(4)
            if sid in seen:
                continue
            seen.add(sid)
            if sec is None:
                sec = {"title": "", "steps": []}
                sections.append(sec)
            title = DONE_MARK.sub("", label).strip(" —-")
            status = "done" if sid in done or DONE_MARK.search(label) else ("current" if sid == current else "todo")
            sec["steps"].append({"id": sid, "title": title, "status": status})
    steps = [s for x in sections for s in x["steps"]]
    n_done = sum(s["status"] == "done" for s in steps)
    return {"goal": str(fm.get("goal") or ""), "slug": str(fm.get("goal_slug") or ""), "phase": str(fm.get("phase") or ""),
            "next": nxt, "course": str(fm.get("course") or ""), "updated": str(fm.get("updated") or ""),
            "level": fm.get("level"), "sections": [x for x in sections if x["steps"]],
            "progress": {"done": n_done, "total": len(steps)}}


def list_plans(cfg, include_done=False):
    """Plans with a step map, most recently changed first; finished ones (phase: done) only when asked."""
    d = cfg.plans_dir
    out = []
    for f in sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []:
        try:
            p = parse(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not p["progress"]["total"] or (p["phase"] == "done" and not include_done):
            continue
        out.append({**p, "file": f.name})
    return out
