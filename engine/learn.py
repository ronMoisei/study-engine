"""Self-rated confidence (1-3) + goal -> the message that starts a learning session in agy. Deterministic: built from
the graph, the evidence log and the folder layout, never by a model. Rules: Agents/rules/learning_harness_doctrine.md §11
and the teach skill's STAGE GATES: whatever the level, every stage of the plan opens with a probe of its sub-steps
(the plan is adjusted to what the probe finds) and closes with a check; the level sets how hard they are.
"""
import re
from pathlib import Path

from . import topics
from .material import TEXT_EXT, MaterialError

# level -> (what the learner means, phase in the plan note, skill, what the agent should do)
LEVELS = {
    1: ("No confidence", "teach", "teach (stage gates, level 1)",
        "Level 1 — study plan from my goal. Before EACH stage: EASY probe questions on that stage's sub-steps (do I "
        "remember / can I compute / can I write the code?), then adjust the plan — a gap becomes extra sub-steps taught "
        "first, a verified sub-step is skipped. Teach. Close each stage with a MEDIUM check; end the session with a "
        "HARD check."),
    2: ("A little confidence", "teach", "teach (stage gates, level 2)",
        "Level 2 — study plan from my goal. Before EACH stage: MEDIUM probe questions on that stage's sub-steps, then "
        "adjust the plan (a gap becomes extra sub-steps taught first; a verified sub-step is skipped). Teach. Close "
        "each stage with a HARD check; end the session with a HARD check."),
    3: ("Extreme confidence", "evaluate", "teach (stage gates, level 3)",
        "Level 3 — before any plan, a HARD check on the goal, one question at a time. Right → harder (edge cases, "
        "traps, conditions relaxed). Wrong → fall back to level 2: study plan, medium probe before each stage, teach, "
        "hard checks."),
}
LEVEL_PROMPT = "ask me for it first (1 no confidence · 2 a little confidence · 3 extreme confidence)"
OPEN_ONLY = ("Open questions only (explain, derive, prove, compute; code only if my scope includes it). No multiple "
             "choice. I reply free-form (often dictated): answer, reasoning and my own questions together — no "
             "answer/conf/thinking template. No Obsidian callouts in chat.")
CAPTURE = ("After each unit, capture what was taught (teach skill §4): new concepts into this session's Inbox "
           "proposal with `study.py propose`, easy flashcards with `mcp_anki_add_cards`.")
# Folders inside a course that are not source material for the graph.
SKIP_DIRS = {"Atomic Notes", "flashcards", "images", "Video", "Audio"}
MAX_FILES = 25


def level_of(value):
    """None when not given; ValueError when given but not 1-3."""
    if value in (None, "", "null"):
        return None
    lv = int(value)
    if lv not in LEVELS:
        raise ValueError(f"level must be 1-3, got {value}")
    return lv


GRILL = ("Grill me first: use the `grill-me` skill — one question at a time, pin down my goal contract (deliverable, "
         "the material's tasks in scope, depth, what I already have, what is OUT of scope) and get my yes before any "
         "plan. The contract then binds the plan: every step serves an in-scope task.")


def header(level, goal, grill=False):
    lines = [GRILL] if grill else []
    if level:
        meaning, phase, skill, _ = LEVELS[level]
        lines.append(f"Self-rated confidence: **{level}/3 — {meaning}** → phase `{phase}` (skill: {skill}).")
    else:
        lines.append(f"Self-rated confidence: not given — {LEVEL_PROMPT}.")
    if (goal or "").strip():
        lines.append("My goal / scope, in my words: " + " ".join(goal.split()))
    return lines


def instructions(level, todo, total):
    if not level:
        return [f"{todo} of {total} steps are not yet held. " if total else "",
                "Once you have my level, start in the mode it selects.", OPEN_ONLY, CAPTURE]
    out = [LEVELS[level][3], STAGE_GATE]
    if total:
        out.append(f"{todo} of {total} steps are not yet held: the probes decide what to teach — never skip a "
                   "probe because the evidence says held, and never teach a sub-step the probe verified.")
    out += [OPEN_ONLY, CAPTURE]                          # every level may end up teaching (level 3 falls back)
    return out


STAGE_GATE = ("Stage gates (teach skill): probe questions are about THIS stage's sub-steps only, in the order they "
              "build (can I recall it → compute it → code it → explain why); a gap is taught before anything that "
              "rests on it — never jump to 'why F1 matters' if I cannot compute precision. Record verified and taught "
              "sub-steps in the plan's `done:` and keep `next:` on the stage in progress.")


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
