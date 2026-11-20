"""Self-rated level (1-5) + goal -> the message that starts a learning session in agy. Deterministic: built from
the graph, the evidence log and the folder layout, never by a model. Rules: Agents/rules/learning_harness_doctrine.md §11.
"""
import re
from pathlib import Path

from . import topics
from .material import TEXT_EXT, MaterialError

# level -> (what the learner means, phase in the plan note, skill, what the agent should do)
LEVELS = {
    1: ("I don't know anything", "teach", "teach (level-1 section)",
        "Teach it from scratch: a short introduction to the topic and where my material covers it, then let me derive "
        "the concepts myself through guided exercises and proofs, foundations first. Skip the probe."),
    2: ("I know parts of it", "teach", "teach (levels 1-2 section)",
        "Teach it, no evaluation first: compress the terminology I already recognise and spend the time on the "
        "medium/hard reasoning, guided exercises and proofs."),
    3: ("I know it", "evaluate", "practice (evaluation kind, medium)",
        "Evaluate me: medium problems on this topic alone, one at a time. Grade strictly; a structural miss gets a "
        "short repair, then the next problem."),
    4: ("I think I know everything", "evaluate", "practice (evaluation kind, hard)",
        "Evaluate me hard: non-trivial derivations, edge cases and computations with a trap, on this topic alone, one "
        "at a time, no warm-up. Grade strictly; a structural miss gets a short repair, then the next problem."),
    5: ("I master it", "project", "project",
        "Project mode: propose three real, complex projects that use this topic together with others I hold, each "
        "with a one-line CV description and a first milestone that fits one session. Wait for my pick."),
}
OPEN_ONLY = ("Open questions only (explain, derive, prove, compute; code only if my scope includes it). No multiple "
             "choice. I reply free-form (often dictated): answer, reasoning and my own questions together — no "
             "answer/conf/thinking template. No Obsidian callouts in chat.")
CAPTURE = ("After each unit, capture what was taught (teach skill §4): new concepts into this session's Inbox "
           "proposal with `study.py propose`, easy flashcards with `mcp_anki_add_cards`.")
# Folders inside a course that are not source material for the graph.
SKIP_DIRS = {"Atomic Notes", "flashcards", "images", "Video", "Audio"}
MAX_FILES = 25


def level_of(value):
    """None when not given; ValueError when given but not 1-5."""
    if value in (None, "", "null"):
        return None
    lv = int(value)
    if lv not in LEVELS:
        raise ValueError(f"level must be 1-5, got {value}")
    return lv


GRILL = ("Grill me first: use the `grill-me` skill — one question at a time, pin down my goal contract (deliverable, "
         "the material's tasks in scope, depth, what I already have, what is OUT of scope) and get my yes before any "
         "plan. The contract then binds the plan: every step serves an in-scope task.")


def header(level, goal, grill=False):
    lines = [GRILL] if grill else []
    if level:
        meaning, phase, skill, _ = LEVELS[level]
        lines.append(f"Self-rated level: **{level}/5 — {meaning}** → phase `{phase}` (skill: {skill}).")
    else:
        lines.append("Self-rated level: not given — ask me for it first (1 nothing · 2 parts · 3 I know it · "
                     "4 everything · 5 mastery).")
    if (goal or "").strip():
        lines.append("My goal / scope, in my words: " + " ".join(goal.split()))
    return lines


def instructions(level, todo, total):
    if not level:
        return [f"{todo} of {total} steps are not yet held. " if total else "",
                "Once you have my level, start in the mode it selects.", OPEN_ONLY, CAPTURE]
    out = [LEVELS[level][3]]
    if level <= 2 and total:
        out.append(f"{todo} of {total} steps are not yet held; do not re-teach what the evidence shows as held "
                   "unless a question contradicts it.")
    out.append(OPEN_ONLY)
    if level in (1, 2, 5):                               # modes that introduce new concepts
        out.append(CAPTURE)
    return out


# ── material targets ──

def course_of(cfg, path):
    """Course folder name when `path` is inside Material/<course>/ (or is that folder), else None."""
    try:
        rel = Path(path).resolve().relative_to(cfg.material.resolve())
    except (ValueError, OSError):
        return None
    return rel.parts[0] if rel.parts and (cfg.material / rel.parts[0]).is_dir() else None


def default_domain(cfg, path):
    """'01TXLWS - Network Dynamics and Learning' -> 'network-dynamics-and-learning' (course code dropped)."""
    course = course_of(cfg, path)
    if not course:
        return ""
    name = re.sub(r"^\s*[0-9A-Z]{5,9}\s*[-–]\s*", "", course)
    return topics.slugify(name or course)


def courses(cfg):
    if not cfg.material.is_dir():
        return []
    return sorted(d.name for d in cfg.material.iterdir() if d.is_dir() and not d.name.startswith("."))


def find_material(cfg, target):
    """A file/folder path (absolute or relative to Material/), or a course named by a unique part of its folder name."""
    t = str(target or "").strip().strip('"')
    if not t:
        return None
    p = Path(t)
    p = p if p.is_absolute() else cfg.material / p
    if p.exists():
        return p
    hits = [c for c in courses(cfg) if t.lower() in c.lower()]
    return cfg.material / hits[0] if len(hits) == 1 else None


def material_files(cfg, path):
    """Files the ingest pipeline can read (.pdf, .md, .txt) at `path`, a file or a folder (recursive)."""
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise MaterialError(f"not found: {path}")
    out = []
    for f in sorted(path.rglob("*")):
        rel = f.relative_to(path).parts
        if any(x in SKIP_DIRS or x.startswith(".") for x in rel[:-1]) or f.name.startswith("."):
            continue
        if f.is_file() and f.suffix.lower() in TEXT_EXT | {".pdf"}:
            out.append(f)
    return out


def rel_to(cfg, p):
    try:
        return Path(p).resolve().relative_to(cfg.magistrale.resolve()).as_posix()
    except ValueError:
        return str(p)


def material_message(cfg, path, level, goal, graph_nodes=None, grill=False):
    """Start learning from a file / folder / course that may not be in the graph yet."""
    path = Path(path)
    course = course_of(cfg, path)
    what = "course" if course and path.resolve() == (cfg.material / course).resolve() else \
        ("folder" if path.is_dir() else "file")
    lines = ["Use the `learning-harness` skill.",
             f"Topic: the {what} `{rel_to(cfg, path)}`" + (f" (course **{course}**)" if course and what != "course" else "") + "."]
    lines += header(level, goal, grill)
    if path.is_dir():
        files = material_files(cfg, path)
        lines += ["", f"Readable material there ({len(files)} files; read them yourself, don't ask me to paste):"]
        lines += [f"- `{rel_to(cfg, f)}`" for f in files[:15]]
        if len(files) > 15:
            lines.append(f"- … and {len(files) - 15} more")
    if graph_nodes:
        lines += ["", f"The study graph already holds {len(graph_nodes)} concepts from here, foundations first:"]
        lines += [f"{i}. {n['name']} (`{n['id']}`) — {n['status']}" for i, n in enumerate(graph_nodes[:20], 1)]
    lines += [""] + instructions(level, 0, 0)
    return "\n".join(x for x in lines if x is not None)


def topic_message(level, goal, topic, related=None, grill=False):
    """Start learning a topic that is neither a graph node nor a path: plain words."""
    lines = ["Use the `learning-harness` skill.", f"Topic: **{topic.strip()}**" if topic.strip() else
             "Topic: see my goal below."]
    lines += header(level, goal, grill)
    if related:
        lines += ["", "Possibly related concepts already in the study graph:"]
        lines += [f"- {n['name']} (`{n['id']}`) — {n['status']}" for n in related]
    lines += ["", "Find the material in `Material/` yourself (search by name); if there is none, say so and use "
                  "`ground-truth` for sources."] + instructions(level, 0, 0)
    return "\n".join(lines)
