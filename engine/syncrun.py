"""Sync Knowledge runs, course by course.

  stage 1   offline lecture pipeline on THIS PC (pc.json: lecture_pipeline, lecture_python), for lectures without
            raw notes; resumable through the pipeline's own checkpoints
  stage 2-4 for each new raw note (or, for a course without recordings, each slide deck):
            ingest v3 = structure (Argon) -> atomic concepts (Hydrogen) -> organize links (Argon) -> one proposal
  approval  approve_confident() in ingest.py; when every raw note of a course has an approved proposal, the
            course's old atomic notes are retired (they were re-derived): .study/rederived.json

Resume: a file that already has a proposal is not processed again (knowledge_sync.inventory). Only one PC runs a
Sync at a time (.study/sync.lock).
"""
import json
import math
import os
import socket
import subprocess
import time
from pathlib import Path

from . import ingest, knowledge_sync, learn

LOCK_STALE_S = 6 * 3600


class SyncError(Exception):
    pass


# ── lock ──

def _lock_file(cfg):
    return cfg.shared_dir / "sync.lock"


def acquire(cfg):
    f = _lock_file(cfg)
    if f.is_file():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            d = {}
        if time.time() - d.get("started", 0) < LOCK_STALE_S:
            raise SyncError(f"a Sync is already running on {d.get('pc', '?')} since "
                            f"{time.strftime('%H:%M', time.localtime(d.get('started', 0)))}")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"pc": socket.gethostname(), "pid": os.getpid(), "started": time.time()}), encoding="utf-8")


def release(cfg):
    try:
        _lock_file(cfg).unlink()
    except OSError:
        pass


# ── what a run would do ──

def lecture_pipeline(cfg):
    """(python, ingest.py) of the offline lecture pipeline on this PC, or None."""
    script = Path(cfg.lecture_pipeline) / "ingest.py" if cfg.lecture_pipeline else None
    py = Path(cfg.lecture_python) if cfg.lecture_python else None
    if script and script.is_file() and py and py.is_file():
        return py, script
    return None


def _files(row):
    """Files stages 2-4 process for a course: new raw notes; slide decks only when there are no recordings."""
    return list(row["raw_new"]) + (list(row["slides_new"]) if row["source"] == "slides" else [])


def _chars(cfg, rel):
    f = cfg.material / rel
    if f.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            return 1200 * len(PdfReader(str(f)).pages)
        except Exception:  # noqa: BLE001
            return 20000
    try:
        return f.stat().st_size
    except OSError:
        return 0


def estimate(cfg, courses):
    """Per course: lectures to transcribe (stage 1) and the cloud calls stages 2-4 would make."""
    inv = {r["course"]: r for r in knowledge_sync.inventory(cfg)["courses"]}
    out, total = [], {"files": 0, "structure": 0, "write": 0, "relate": 0, "transcribe": 0}
    for c in courses:
        row = inv.get(c)
        if not row:
            continue
        files = _files(row)
        chars = [_chars(cfg, f) for f in files]
        write = sum(max(1, math.ceil(n / ingest.WRITE_BATCH_CHARS)) for n in chars)
        e = {"course": c, "files": len(files), "transcribe": len(row["to_transcribe"]),
             "structure": len(files), "write": write, "relate": len(files),       # ~1 relate call (<=60 pairs) per file
             "chars": sum(chars)}
        out.append(e)
        for k in total:
            total[k] += e[k]
    total["calls"] = total["structure"] + total["write"] + total["relate"]
    return {"courses": out, "total": total, "stage1_available": lecture_pipeline(cfg) is not None,
            "models": {k: cfg.models.get(k) for k in ("structure", "write", "relate")}}


# ── a run ──

def run(cfg, store, llm, embedder, courses, transcribe=True, progress=None, stop=None):
    """Run Sync for `courses`. progress(dict) gets updates; stop() -> True ends the run between files."""
    report = {"courses": [], "proposals": [], "errors": [], "transcribed": []}
    say = progress or (lambda d: None)
    acquire(cfg)
    try:
        inv = {r["course"]: r for r in knowledge_sync.inventory(cfg)["courses"]}
        lp = lecture_pipeline(cfg)
        for c in courses:
            row = inv.get(c)
            if not row:
                report["errors"].append(f"{c}: not a course folder")
                continue
            # stage 1 — offline transcription on this PC
            if transcribe and row["to_transcribe"]:
                if not lp:
                    report["errors"].append(f"{c}: {len(row['to_transcribe'])} lectures not transcribed "
                                            "(the lecture pipeline is not set up on this PC)")
                else:
                    say({"course": c, "stage": "transcribe", "done": 0, "total": len(row["to_transcribe"])})
                    code = c.split(" ")[0]
                    r = subprocess.run([str(lp[0]), str(lp[1]), "--course", code, "--all", "--yes"],
                                       cwd=str(lp[1].parent), capture_output=True, text=True, encoding="utf-8",
                                       errors="replace")
                    if r.returncode != 0:
                        report["errors"].append(f"{c}: lecture pipeline exited {r.returncode}: "
                                                f"{(r.stderr or r.stdout).strip()[-300:]}")
                    report["transcribed"].append(c)
                    row = {x["course"]: x for x in knowledge_sync.inventory(cfg)["courses"]}[c]
            # stages 2-4 — one proposal per new file
            files = _files(row)
            domain = learn.default_domain(cfg, cfg.material / c)
            for k, rel in enumerate(files):
                if stop and stop():
                    report["stopped"] = True
                    return report
                say({"course": c, "stage": "structure", "file": rel, "done": k, "total": len(files)})

                def sub(stage, done, total, _k=k, _rel=rel):
                    say({"course": c, "stage": stage, "file": _rel, "done": _k, "total": len(files),
                         "step": done, "steps": total})
                try:
                    p = ingest.ingest(cfg, store, llm, embedder, cfg.material / rel, domain, sub, course=c)
                    report["proposals"].append(p["id"])
                except Exception as e:  # noqa: BLE001  one bad file does not stop the run
                    report["errors"].append(f"{rel}: {type(e).__name__}: {e}")
            report["courses"].append(c)
        return report
    finally:
        release(cfg)


# ── after approval: retire re-derived atomic notes ──

def update_rederived(cfg):
    """A course whose raw notes ALL have an approved (done) proposal is re-derived: its old atomic notes are hidden
    from the graph from now on. Returns the list of retired courses."""
    f = cfg.shared_dir / "rederived.json"
    try:
        retired = set(json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        retired = set()
    done = {p["source"] for p in ingest.list_proposals(cfg) if p["status"] == "done"}
    for c in cfg.material.iterdir() if cfg.material.is_dir() else []:
        if not c.is_dir() or c.name in retired:
            continue
        raw = [knowledge_sync._rel(cfg, d / f"{d.name}_Raw_Notes.md")
               for rn in knowledge_sync.raw_notes_dirs(c) for d in rn.iterdir()
               if d.is_dir() and (d / f"{d.name}_Raw_Notes.md").is_file()]
        if raw and all(r in done for r in raw):
            retired.add(c.name)
    if retired:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(sorted(retired), ensure_ascii=False, indent=1), encoding="utf-8")
    return sorted(retired)
