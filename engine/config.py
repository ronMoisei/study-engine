"""Configuration: config.json next to study.py (falls back to defaults below)."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@dataclass
class Config:
    magistrale: Path = Path(r"G:\My Drive\Università\Magistrale")
    anki_url: str = "http://127.0.0.1:8765"
    host: str = "127.0.0.1"
    port: int = 8770
    provider: str = "agy"                       # "agy" | "stub"
    agy: str = "agy"
    agy_timeout_s: int = 180
    # Empty folder for agy -p calls, so the workspace's skills/hooks/rules are NOT loaded into each call.
    agy_workdir: Path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "study-engine" / "agy-work"
    # job -> "provider:model". Providers: agy (cloud, uses quota), ollama (local, free), stub (offline tests).
    # A bare model name uses `provider` above.
    models: dict = field(default_factory=lambda: {
        "checklist": "gemini-3.8-flash-low",
        "grade": "gemini-3.8-flash-low",
        "remedial": "gemini-3.8-flash-medium",
        "extract": "agy:gemini-3.8-flash-low",   # once per file, cached; local alternative: ollama:qwen2.5:14b
        "judge": "ollama:qwen3.5:9b",
        "group": "agy:gemini-3.8-flash-low",     # claims -> concepts + topics, one call per file, cached
        # Sync Knowledge (ingest_method v3): a smart model maps the document, a medium one writes the atomic notes,
        # the smart one decides identity and links (stage 4, across every course).
        "structure": "agy:gemini-4-argon-medium",
        "write": "agy:gemini-4-hydrogen-medium",
        "relate": "agy:gemini-4-argon-medium",   # prerequisite direction for candidate pairs, batched
        # Sync Knowledge v4: notes first, graph last (engine/coursesync.py)
        "polish": "agy:gemini-4-hydrogen-high",       # raw lecture notes -> full notes (keeps every reason)
        "atomize": "agy:gemini-4-hydrogen-medium",    # full notes -> atomic concept drafts
        "consolidate": "agy:gemini-4-argon-medium",   # a course's drafts -> chapters, duplicates (once per course)
        "link": "agy:gemini-4-argon-medium",          # one lecture's full notes -> prerequisite links with reasons
        "narrate": "agy:gemini-4-hydrogen-low",       # Create raw notes: one short progress update per lecture
        # Sync Knowledge v5 (engine/lecturesync.py): raw notes -> typed atomic notes + easy cards; medium exercises
        "notes": "agy:gemini-4-hydrogen-high",
        "exercises": "agy:gemini-4-hydrogen-high",
        "reconcile": "agy:gemini-4-hydrogen-medium",  # a draft vs the notes it may repeat: integrate/skip/sub/new
        # one-time move to the flat, topic-organised graph (engine/migrate.py)
        "split": "agy:gemini-4-hydrogen-medium",      # an older rich atomic note -> typed atomic notes
        "taxonomy": "agy:gemini-4-argon-medium",      # every note's title -> macro topics and subtopics
        "assign": "agy:gemini-4-argon-medium",        # notes -> their topics (and a kind where it changed)
        "dedupe": "agy:gemini-4-argon-medium",        # notes of one subtopic -> the ones that are the same concept
    })
    # "v4": polish -> atomize -> course graph (Sync Knowledge); "v3": structure -> write -> organize per file;
    # "v2": extract -> group -> relate. Analysing a single file in the Inbox always uses the per-file pipeline.
    ingest_method: str = "v5"                    # v5: raw notes -> notes + cards -> graph by code (lecturesync)
    sync_workers: int = 3                       # lectures polished/atomized in parallel during a Sync
    job_timeout_s: dict = field(default_factory=lambda: {"polish": 900, "atomize": 420, "consolidate": 600,
                                                          "link": 600, "split": 600, "taxonomy": 900, "assign": 600,
                                                          "dedupe": 600, "notes": 1200, "exercises": 900,
                                                          "reconcile": 600})
    # Stage 1 (offline lecture pipeline) on THIS PC — set in %LOCALAPPDATA%/study-engine/pc.json, never synced
    lecture_pipeline: str = ""                  # folder holding the pipeline's ingest.py
    lecture_python: str = ""                    # its Python (CUDA, Whisper)
    ollama_url: str = "http://127.0.0.1:11434"
    embed_model: str = "qwen3-embedding:8b"
    # Matching thresholds (cosine). Calibrated on bench/embed_pairs.py: unrelated <= 0.35, same idea >= 0.65.
    sim_floor: float = 0.45                     # below: unrelated -> new node, no model call
    top_k: int = 5                              # candidates judged per claim
    link_k: int = 8                             # neighbours offered to the link job
    passage_chars: int = 1800                   # passage size for a local extract model
    passage_chars_cloud: int = 6000             # agy adds ~13k tokens of overhead per call: fewer, larger passages
    # Read-only node sources (course atomic notes), relative to the Magistrale folder; first file per slug wins.
    # Notes live in Material/<course>/01_Study/Notes/{Raw,Atomic,Full} Notes since 2026-11-16 (the old course-root
    # layout is still read so nothing disappears if a folder is left behind).
    atomic_globs: list = field(default_factory=lambda: ["Material/*/01_Study/Notes/Atomic Notes/*.md",
                                                         "Material/*/Atomic Notes/*.md"])
    note_type: str = "Basilare"
    front_field: str = "Fronte"
    back_field: str = "Retro"
    dry_run: bool = False                       # True: never rate/add in Anki (testing)
    teach_model: str = "flash"                  # model for a NEW conversation started by "Teach me this"
    # Antigravity project for conversations the panel starts (e.g. the online "Magistrale" project, reachable from
    # other devices). "" = the panel's own project. Usually set once in <workspace>/.study/settings.json.
    agy_project: str = ""
    db_file: str = ""                           # override engine DB location (tests); "" = default below

    @property
    def vault(self) -> Path:
        """Magistrale itself is the Obsidian vault: course notes, the graph and the learner record in one place."""
        return self.magistrale

    @property
    def material(self) -> Path:
        return self.magistrale / "Material"

    @property
    def graph(self) -> Path:
        return self.magistrale / "Graph"

    @property
    def learner(self) -> Path:
        return self.magistrale / "Learner"

    @property
    def ledger(self) -> Path:
        return self.learner / "evidence.jsonl"

    @property
    def data_dir(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "study-engine"

    @property
    def shared_dir(self) -> Path:
        """Engine state that must follow you across PCs (synced by Google Drive; a dot folder Obsidian ignores)."""
        return self.magistrale / ".study"

    @property
    def proposals_dir(self) -> Path:
        if self.db_file:                                    # tests: next to their temporary database
            return Path(self.db_file).parent / "proposals"
        return self.shared_dir / "proposals"

    @property
    def plans_dir(self) -> Path:
        return self.learner / "plans"

    @property
    def db_path(self) -> Path:
        if self.db_file:
            return Path(self.db_file)
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "study-engine" / "engine.sqlite3"


def _workspace_above(start: Path):
    """The Magistrale folder, when this code runs from a copy deployed inside it (Magistrale/Agents/study-engine)."""
    for p in start.parents:
        if (p / "Material").is_dir() and (p / "Agents").is_dir():
            return p
    return None


# The pipeline steps whose model the Sync tab lets you choose, and the models agy offers (`agy models`).
PIPELINE_JOBS = {"notes": "Atomic notes + easy cards", "exercises": "Medium exercises",
                 "reconcile": "Repeat check (integrate / skip)", "narrate": "Raw-notes progress updates"}
AGY_MODELS = ["gemini-4-hydrogen-xhigh", "gemini-4-hydrogen-high", "gemini-4-hydrogen-medium", "gemini-4-hydrogen-low",
              "gemini-4-argon-high", "gemini-4-argon-medium", "gemini-4-argon-low",
              "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
              "claude-opus-5-5-high", "claude-opus-5-5-medium", "claude-opus-5-5-low",
              "claude-sonnet-5-5-high", "claude-sonnet-5-5-medium", "claude-sonnet-5-5-low", "gpt-oss-120b-medium"]


def save_shared_models(cfg, changes):
    """Set models for pipeline jobs in the synced settings (.study/settings.json, every PC) and in `cfg`."""
    bad = [j for j in changes if j not in PIPELINE_JOBS]
    bad += [m for m in changes.values() if m.split(":", 1)[-1] not in AGY_MODELS]
    if bad:
        raise ValueError(f"unknown job or model: {bad}")
    f = cfg.shared_dir / "settings.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    want = {j: m if ":" in m else f"agy:{m}" for j, m in changes.items()}
    data["models"] = {**data.get("models", {}), **want}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    cfg.models.update(want)
    return {j: cfg.models[j] for j in PIPELINE_JOBS}


# Settings that must be the same on every PC live in the synced workspace, not in a per-PC config.json.
SHARED_KEYS = {"agy_project", "teach_model", "models", "dry_run", "ingest_method", "sync_workers", "job_timeout_s"}
PC_KEYS = {"lecture_pipeline", "lecture_python"}             # paths that exist on one PC only


def _apply(cfg, data, source, allowed=None):
    for k, v in data.items():
        if not hasattr(cfg, k) or (allowed is not None and k not in allowed):
            raise ValueError(f"{source}: unknown or not allowed key '{k}'")
        cur = getattr(cfg, k)
        if isinstance(cur, Path):
            v = Path(v)
        elif isinstance(cur, dict):
            v = {**cur, **v}
        elif isinstance(cur, float):
            v = float(v)
        setattr(cfg, k, v)


def load(path: Path | None = None) -> Config:
    cfg = Config()
    found = _workspace_above(REPO)
    if found:                                               # deployed copy: works on any PC, whatever the Drive path
        cfg.magistrale = found
    path = path or REPO / "config.json"
    if path.is_file():                                      # this PC only (not deployed, not synced)
        _apply(cfg, json.loads(path.read_text(encoding="utf-8")), "config.json")
    shared = cfg.shared_dir / "settings.json"               # every PC (synced with the workspace)
    if shared.is_file():
        _apply(cfg, json.loads(shared.read_text(encoding="utf-8")), str(shared), SHARED_KEYS)
    pc = cfg.data_dir / "pc.json"                           # this PC only, outside the repo and the Drive folder
    if pc.is_file() and not cfg.db_file:
        _apply(cfg, json.loads(pc.read_text(encoding="utf-8")), str(pc), PC_KEYS)
    return cfg
