"""Create raw notes: the offline lecture pipeline on THIS PC, with short progress updates from a small model.

  1. audio     enhance_all.py: a cleaned-up <stem>_enhanced.mp3 for every recording that has none (ffmpeg, about
               30 s per lecture). Whisper then transcribes the cleaned audio.
  2. lectures  ingest.py --all-courses --yes: Whisper, keyframes, slide matching and local vision (Ollama) for every
               lecture without raw notes. Resumable: a stopped lecture continues from its checkpoint next time.

Progress (lecture k of N, phase, current step) is read from the pipeline's output: no model needed. After each
lecture, and when something fails, the "narrate" model (Hydrogen low) turns that lecture's log into one to three
plain sentences; if the model call fails the update keeps the facts parsed from the log. The full log is kept in
%LOCALAPPDATA%/study-engine/raw_notes/. One run at a time across PCs (the Sync lock).
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import knowledge_sync, syncrun

NARRATE_SCHEMA = {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}},
                  "x-text-field": "text"}
NARRATE_PROMPT = """You report on a long offline job to the student who started it: their university lectures are \
being turned into raw notes on their PC (Whisper speech-to-text, keyframes from the video, slide matching, a local \
vision model reading the professor's handwriting).

Below is what the job printed for ONE step. Write one to three short plain sentences for the student: what was just \
done, the numbers that matter (lecture length, speech segments, keyframes, slides matched, frames read by the \
vision model), and anything that looks wrong or unusual (errors, no speech found, no keyframes, an unexpected \
language, raw audio used although cleaned audio should exist, a failed or skipped lecture). Say plainly when all \
went fine. No preamble, no headings, no lists, no Markdown. Answer with the sentences only.

INPUT_JSON:
"""
LOG_CHARS = 9000            # log excerpt sent per update (head + tail of the lecture's output)


def pipeline_env(py):
    """Environment ingest.cmd sets for the conda Python (CUDA DLLs, OpenMP clash, unbuffered UTF-8 output)."""
    env = dict(os.environ)
    conda = Path(py).parent
    env["PATH"] = os.pathsep.join(str(p) for p in (conda / "bin", conda / "Library" / "bin", conda / "Scripts")) \
        + os.pathsep + env.get("PATH", "")
    env.update(KMP_DUPLICATE_LIB_OK="TRUE", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
               HF_HUB_DISABLE_SYMLINKS_WARNING="1")
    return env


def ollama_up(cfg, timeout=3):
    try:
        with urllib.request.urlopen(cfg.ollama_url.rstrip("/") + "/api/tags", timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def start_ollama(cfg, wait_s=40):
    """Start the local Ollama server if it is not answering (the pipeline's vision step needs it). -> True if up."""
    if ollama_up(cfg):
        return True
    exe = shutil.which("ollama") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
    if not Path(exe).is_file():
        return False
    subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     | getattr(subprocess, "DETACHED_PROCESS", 0))
    end = time.time() + wait_s
    while time.time() < end:
        if ollama_up(cfg):
            return True
        time.sleep(1)
    return False


def excerpt(lines, limit=LOG_CHARS):
    """The informative part of a lecture's log: per-keyframe lines are summarised by their count."""
    keep, frames = [], 0
    for ln in lines:
        if re.match(r"\s*\[\d+/\d+\]", ln):              # one line per keyframe: count them, keep a few
            frames += 1
            if frames > 6 and not re.search(r"error|fail|timeout", ln, re.I):
                continue
        keep.append(ln)
    text = "\n".join(keep)
    if frames > 6:
        text += f"\n({frames} keyframe lines in total; only the first ones are shown)"
    if len(text) > limit:
        text = text[: limit // 3] + "\n[...]\n" + text[-(limit - limit // 3):]
    return text


def facts(lines):
    """Numbers parsed from one lecture's log, for an update that needs no model."""
    t = "\n".join(lines)
    out = {}
    for key, pat in (("duration_s", r"Duration:\s*([\d.]+)s"), ("segments", r"Total Audio Segments:\s*(\d+)"),
                     ("keyframes", r"Total Keyframes:\s*(\d+)"), ("language", r"Detected language:\s*'(\w+)'")):
        m = re.search(pat, t)
        if m:
            out[key] = m.group(1)
    out["cleaned_audio"] = "Found existing DSP enhanced audio" in t
    out["errors"] = [ln.strip() for ln in lines if re.search(r"\[ERROR\]|Traceback|Error:", ln)][:5]
    return out


def plain(f, lecture):
    bits = []
    if f.get("duration_s"):
        bits.append(f"{float(f['duration_s']) / 60:.0f} min")
    if f.get("segments"):
        bits.append(f"{f['segments']} speech segments")
    if f.get("keyframes"):
        bits.append(f"{f['keyframes']} keyframes")
    head = f"{lecture}: " + (", ".join(bits) if bits else "done")
    return head + (". Problems: " + "; ".join(f["errors"]) if f.get("errors") else ".")


class RawNotesRun:
    """One run. `state` is what the panel polls (GET /knowledge/raw-notes)."""

    def __init__(self, cfg, llm, courses=None):
        self.cfg, self.llm = cfg, llm
        self.courses = list(courses) if courses else None          # course folder names, or None: every course
        self.codes = ",".join(c.split(" ")[0] for c in self.courses) if self.courses else None
        if not self.courses:
            scope = "all courses"
        elif len(self.courses) == 1:
            scope = self.courses[0]
        else:
            scope = f"{len(self.courses)} courses: " + ", ".join(c.split(" ")[0] for c in self.courses)
        self.proc = None
        self.stopping = False
        self._lecture_lines, self._ended_lecture = [], False
        self.narrator = ThreadPoolExecutor(max_workers=1)        # updates in order, never blocking the log reader
        self.state = {"state": "running", "phase": "starting", "done": 0, "total": 0, "course": "", "lecture": "",
                      "step": "", "detail": "", "updates": [], "errors": [], "started": time.time(), "ended": None,
                      "log": "", "model": cfg.models.get("narrate", ""), "scope": scope}
        self.lock = threading.Lock()

    # ── updates ──
    def add(self, text, kind="info", lecture=""):
        with self.lock:
            self.state["updates"].append({"t": time.strftime("%H:%M"), "kind": kind, "lecture": lecture, "text": text})

    def narrate(self, event, lecture, lines, fallback, kind="info"):
        """Queue a model-written update for one step; the parsed facts stand in if the call fails."""
        data = {"event": event, "lecture": lecture, "course": self.state["course"],
                "position": f"{self.state['done']}/{self.state['total']}", "log": excerpt(lines)}

        def work():
            text = None
            try:
                out = self.llm.run("narrate", NARRATE_PROMPT + json.dumps(data, ensure_ascii=False, indent=1),
                                   NARRATE_SCHEMA, session="rawnotes")
                text = (out.get("text") or "").strip() or None
            except Exception as e:  # noqa: BLE001  an update is never worth stopping the run
                text = f"{fallback} (no model update: {type(e).__name__})"
            self.add(text or fallback, kind, lecture)
        self.narrator.submit(work)

    # ── the run ──
    def run(self):
        cfg, st = self.cfg, self.state
        lp = syncrun.lecture_pipeline(cfg)
        logdir = cfg.db_path.parent / "raw_notes"                  # %LOCALAPPDATA%/study-engine (this PC only)
        logdir.mkdir(parents=True, exist_ok=True)
        log = logdir / time.strftime("%Y-%m-%d_%H%M%S.log")
        st["log"] = str(log)
        try:
            syncrun.acquire(cfg)
        except syncrun.SyncError as e:
            st.update(state="error", error=str(e), ended=time.time())
            return
        try:
            if not lp:
                raise RuntimeError("the lecture pipeline is not set up on this PC (pc.json: lecture_pipeline, "
                                   "lecture_python)")
            py, script = lp
            todo = self.left()
            only = ["--course", self.codes] if self.codes else []
            self.add(f"Starting ({st['scope']}): {todo} lectures without raw notes. First the audio clean-up, then "
                     "the lectures one by one (about 10 minutes each).")
            with open(log, "w", encoding="utf-8") as fh:
                enhance = script.parent / "enhance_all.py"
                if enhance.is_file():
                    st.update(phase="audio", step="cleaning up lecture audio")
                    self._stream([str(py), "-X", "utf8", "-u", str(enhance), "--workers", "3"] + only, script.parent,
                                 fh, self._on_audio, "the audio clean-up")
                else:
                    self.add("No enhance_all.py next to the pipeline: Whisper uses the raw audio.", "warn")
                if self.stopping:
                    return
                st.update(phase="lectures", step="looking for lectures", done=0, total=todo)
                if not start_ollama(cfg):
                    self.add("Ollama is not running and could not be started: the slides and the handwriting "
                             "will not be read (the speech is still transcribed).", "warn")
                self._stream([str(py), "-X", "utf8", "-u", str(script), "--all-courses", "--yes"] + only,
                             script.parent, fh, self._on_lecture, "the lecture pipeline")
                if not self.stopping:
                    self._close_lecture("finished")
        except Exception as e:  # noqa: BLE001
            st["errors"].append(f"{type(e).__name__}: {e}")
            self.add(f"The run failed: {type(e).__name__}: {e}", "error")
        finally:
            syncrun.release(cfg)
            self.narrator.shutdown(wait=True)                    # last updates in before the run is reported over
            left = self.left()
            if self.stopping:
                st["state"] = "stopped"
                self.add(f"Stopped. {left} lectures still without raw notes; the one in progress continues from "
                         "its checkpoint next time.", "warn")
            elif st["state"] == "running":
                st["state"] = "error" if st["errors"] else "done"
                self.add(f"Finished: {st['done']} lectures processed, {left} still without raw notes"
                         + (f", {len(st['errors'])} problems." if st["errors"] else "."),
                         "error" if st["errors"] else "done")
            st.update(ended=time.time(), detail="", step="")

    def left(self):
        """Lectures still without raw notes, in this run's scope."""
        inv = knowledge_sync.inventory(self.cfg)
        if not self.courses:
            return inv["totals"]["to_transcribe"]
        return sum(len(r["to_transcribe"]) for r in inv["courses"] if r["course"] in self.courses)

    def _stream(self, cmd, cwd, fh, on_line, what):
        self.proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=pipeline_env(cmd[0]), text=True,
                                     encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with self.proc.stdout:
            for raw in self.proc.stdout:
                line = raw.rstrip("\r\n")
                fh.write(line + "\n")
                fh.flush()
                if line.strip():
                    on_line(line)
        code = self.proc.wait()
        self.proc = None
        if code and not self.stopping:
            self.state["errors"].append(f"{what} exited with code {code}")
            self.add(f"{what[0].upper() + what[1:]} exited with code {code}; see the log: {self.state['log']}",
                     "error")

    def _on_audio(self, line):
        st = self.state
        m = re.match(r"(\d+) lectures already have enhanced audio; (\d+) to do", line)
        if m:
            n = int(m.group(2))
            st.update(done=0, total=n, detail="")
            self.add(f"Audio: {n} recordings to clean up ({m.group(1)} already done)." if n
                     else "Audio: every recording already has cleaned-up audio.")
            return
        m = re.match(r"\[(\d+)/(\d+)\] (OK|FAILED) \[(\w+)\] (.+?)(?: \(|:|$)", line)
        if m:
            st.update(done=int(m.group(1)), total=int(m.group(2)), course=m.group(4), detail=m.group(5))
            if m.group(3) == "FAILED":
                st["errors"].append(line.strip())
                self.add(f"Audio clean-up failed for {m.group(5)}: Whisper will use its raw audio.", "warn")
        elif line.startswith("ENHANCE DONE"):
            self.add("Audio: " + line.split(":", 1)[1].strip() + ".")

    def _on_lecture(self, line):
        st = self.state
        m = re.search(r"FULL BATCH RUN: (\d+) genuinely pending", line)
        if m:
            st["total"] = int(m.group(1))
            return
        if "already ingested" in line:
            self.add("Every lecture already has raw notes: nothing to transcribe.", "done")
            return
        m = re.search(r"BATCH PROGRESS: \[(\d+)/(\d+)\] (\S+) - (.+)$", line)
        if m:
            self._close_lecture("finished")
            st.update(done=int(m.group(1)) - 1, total=int(m.group(2)), course=m.group(3),
                      lecture=m.group(4).strip(), step="starting", detail="")
            self._lecture_lines, self._ended_lecture = [], False
            return
        if st["lecture"]:
            self._lecture_lines.append(line)
        m = re.match(r">>> PHASE (\d): (.+)", line.strip())
        if m:
            st.update(step=f"{m.group(1)}/4 · {m.group(2).split('(')[0].strip()}", detail="")
        elif re.match(r"\s*\[\d+/\d+\]", line) or line.startswith("[Audio]"):
            st["detail"] = line.strip()[:160]
        if re.search(r"\[ERROR\]|Traceback", line):
            st["errors"].append(f"{st['lecture']}: {line.strip()[:200]}")
        if "FULL BATCH COMPLETE" in line:
            self._close_lecture("finished")

    def _close_lecture(self, event):
        """The lecture in progress is over (the next one started, or the batch ended): queue its update."""
        st = self.state
        if not st["lecture"] or self._ended_lecture or not self._lecture_lines:
            return
        self._ended_lecture = True
        lines = list(self._lecture_lines)
        f = facts(lines)
        failed = bool(f["errors"])
        st["done"] += 1
        self.narrate("lecture failed" if failed else "lecture " + event, st["lecture"], lines,
                     plain(f, st["lecture"]), "error" if failed else "lecture")

    def stop(self):
        """Stop now: the pipeline is killed (its checkpoints let the lecture in progress resume next time)."""
        self.stopping = True
        p = self.proc
        if p and p.poll() is None:
            if os.name == "nt":                                     # the whole tree: python, ffmpeg children
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            else:
                p.terminate()

    def view(self):
        with self.lock:
            return {**self.state, "updates": list(self.state["updates"])}


def preview(cfg):
    """What the button would do, without running anything."""
    lp = syncrun.lecture_pipeline(cfg)
    inv = knowledge_sync.inventory(cfg)
    return {"available": lp is not None, "to_transcribe": inv["totals"]["to_transcribe"],
            "enhance": bool(lp and (lp[1].parent / "enhance_all.py").is_file()),
            "model": cfg.models.get("narrate", "")}
