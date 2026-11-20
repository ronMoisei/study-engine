"""Sync Knowledge — what is new, course by course (read-only inventory, no model calls).

The pipeline it feeds (stages):
  1. offline   lecture video (or audio) + slides -> Raw Notes/<lecture>/<lecture>_Raw_Notes.md
               (the local lecture pipeline: Whisper + slide matching + local vision)
  2. polish    Hydrogen (high) turns each raw note into full notes that keep every derivation and reason
  3. atomize   Hydrogen (medium) writes the atomic concepts of each full note (no links yet)
  4. graph     Argon, once every lecture is done: the course's chapters and duplicates, then the links,
               read from the reasoning in the full notes (engine/coursesync.py)
Stage 4 ends in ONE Inbox proposal per course; nothing enters Graph/ without approval.
This module answers "what would Sync process?": lectures not yet transcribed, raw notes and slide decks
not yet in the graph.
"""
import os
from pathlib import Path

from . import ingest
from .graph import Graph

VIDEO = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
AUDIO = {".m4a", ".wav", ".mp3", ".ogg", ".flac"}
NOTE_DIRS = {"Raw Notes", "Atomic Notes", "Full Notes"}


SKIPPED_BY_PIPELINE = {"03_other", "test"}     # the lecture pipeline ignores recordings in these folders


def _walk(course_dir):
    """(file, skipped_by_pipeline) for a course, skipping the notes folders (they are output, not material)."""
    for root, dirs, files in os.walk(course_dir):
        dirs[:] = [d for d in dirs if d not in NOTE_DIRS and d != "Notes" and not d.startswith(".")]
        rel = Path(root).relative_to(course_dir).parts
        skipped = any(p.lower() in SKIPPED_BY_PIPELINE for p in rel)
        for f in files:
            yield Path(root) / f, skipped


def raw_notes_dirs(course_dir):
    """Every 'Raw Notes' folder of a course: at the course root (Material/<course>/Raw Notes) or nested, as in
    Material/<course>/01_Study/Notes/Raw Notes. Lecture folders inside are <lecture>/<lecture>_Raw_Notes.md."""
    out = []
    for root, dirs, _ in os.walk(course_dir):
        if Path(root).name == "Raw Notes":
            out.append(Path(root))
            dirs[:] = []                                 # lecture folders inside are not searched further
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("images", "Atomic Notes", "Full Notes")
                   and d.lower() not in ("lectures", "lezioni", "slides", "video", "audio")]
    return sorted(out)


def _rel(cfg, p):
    try:
        return Path(p).resolve().relative_to(cfg.material.resolve()).as_posix()
    except (ValueError, OSError):
        return str(p)


def _ingested(cfg, graph):
    """Material files (relative to Material/) already analysed: a proposal was made from them, or a graph note
    cites them as a source."""
    from . import coursesync
    from . import lecturesync
    seen = {p["source"] for p in ingest.list_proposals(cfg)} | coursesync.graphed(cfg) | lecturesync.graphed(cfg)
    for n in graph.nodes.values():
        for s in n.sources or []:
            path = str(s).strip("[]").split("#")[0].replace("\\", "/")
            seen.add(path[len("Material/"):] if path.startswith("Material/") else path)
    return seen


def inventory(cfg):
    graph = Graph.load(cfg)
    done = _ingested(cfg, graph)
    per_course = {}
    for n in graph.nodes.values():
        if n.course:
            per_course[n.course] = per_course.get(n.course, 0) + 1
    courses, totals = [], {"lectures": 0, "to_transcribe": 0, "raw_new": 0, "slides_new": 0, "misrouted": 0}
    if not cfg.material.is_dir():
        return {"courses": [], "totals": totals}
    scanned = []
    for c in sorted(d for d in cfg.material.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))):
        videos, audios, slides, other = set(), set(), [], set()
        for f, skipped in _walk(c):
            ext = f.suffix.lower()
            if skipped and (ext in VIDEO or ext in AUDIO):
                other.add(f.stem)                       # e.g. 03_Other/: the pipeline never transcribes these
            elif ext in VIDEO:
                videos.add(f.stem)
            elif ext in AUDIO and not f.stem.endswith("_enhanced"):
                audios.add(f.stem)
            elif ext == ".pdf" and "slide" in f.as_posix().lower():
                slides.append(f)
        raw = {}
        for rn in raw_notes_dirs(c):
            for d in rn.iterdir():
                f = d / f"{d.name}_Raw_Notes.md"
                if d.is_dir() and f.is_file():
                    raw.setdefault(d.name, f)
        scanned.append((c, videos | (audios - videos), len(audios - videos), len(videos), slides, raw,
                        sorted(other - videos - audios)))
    owner = {}                                          # lecture name -> course whose material holds the recording
    for c, lectures, *_ in scanned:
        for name in lectures:
            owner.setdefault(name, c.name)
    for c, lectures, audio_only, n_video, slides, raw, other in scanned:
        # raw notes of a lecture recorded in ANOTHER course: the lecture pipeline routed them to the wrong folder
        misrouted = sorted((name, owner[name]) for name in raw if name not in lectures and name in owner)
        raw = {k: v for k, v in raw.items() if not (k not in lectures and k in owner)}
        transcribed_here = set(raw) | {n for cc, *r in scanned for n in r[4] if owner.get(n) == c.name}
        to_transcribe = sorted(lectures - transcribed_here)
        raw_new = sorted(_rel(cfg, f) for f in raw.values() if _rel(cfg, f) not in done)
        slides_new = sorted(_rel(cfg, f) for f in slides if _rel(cfg, f) not in done)
        row = {"course": c.name, "lectures": len(lectures), "video": n_video, "audio_only": audio_only,
               "transcribed": len(lectures) - len(to_transcribe), "to_transcribe": to_transcribe, "raw_new": raw_new,
               "slides_new": slides_new, "concepts": per_course.get(c.name, 0),
               "raw_all": sorted(_rel(cfg, f) for f in raw.values()),          # a rebuild processes all of them
               "slides_all": sorted(_rel(cfg, f) for f in slides),
               "misrouted": [{"lecture": n, "belongs_to": o} for n, o in misrouted],
               "skipped_recordings": other,              # in 03_Other/ or test/: not transcribed by the pipeline
               "source": "lectures" if lectures else ("slides" if slides else "none")}
        courses.append(row)
        totals["lectures"] += len(lectures)
        totals["to_transcribe"] += len(to_transcribe)
        totals["raw_new"] += len(raw_new)
        totals["slides_new"] += len(slides_new)
        totals["misrouted"] += len(misrouted)
    return {"courses": courses, "totals": totals}
