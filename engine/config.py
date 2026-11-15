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
        "link": "agy:gemini-3.8-flash-low",      # one call per file; local models get prerequisite direction wrong
    })
    ollama_url: str = "http://127.0.0.1:11434"
    embed_model: str = "qwen3-embedding:8b"
    # Matching thresholds (cosine). Calibrated on bench/embed_pairs.py: unrelated <= 0.35, same idea >= 0.65.
    sim_floor: float = 0.45                     # below: unrelated -> new node, no model call
    top_k: int = 5                              # candidates judged per claim
    link_k: int = 8                             # neighbours offered to the link job
    passage_chars: int = 1800                   # passage size for a local extract model
    passage_chars_cloud: int = 6000             # agy adds ~13k tokens of overhead per call: fewer, larger passages
    # Read-only node sources inside the vault (course atomic notes); first file per slug wins.
    atomic_globs: list = field(default_factory=lambda: ["2_Full Notes/*/atomic/*.md", "*/atomic/*.md"])
    note_type: str = "Basilare"
    front_field: str = "Fronte"
    back_field: str = "Retro"
    dry_run: bool = False                       # True: never rate/add in Anki (testing)
    db_file: str = ""                           # override engine DB location (tests); "" = default below

    @property
    def vault(self) -> Path:
        return self.magistrale / "Obsidian Notes"

    @property
    def graph(self) -> Path:
        return self.vault / "Graph"

    @property
    def ledger(self) -> Path:
        return self.vault / "Learner" / "evidence.jsonl"

    @property
    def data_dir(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "study-engine"

    @property
    def proposals_dir(self) -> Path:
        return (Path(self.db_file).parent if self.db_file else self.data_dir) / "proposals"

    @property
    def plans_dir(self) -> Path:
        return self.vault / "Learner" / "plans"

    @property
    def db_path(self) -> Path:
        if self.db_file:
            return Path(self.db_file)
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "study-engine" / "engine.sqlite3"


def load(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or REPO / "config.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        for k, v in data.items():
            if not hasattr(cfg, k):
                raise ValueError(f"config.json: unknown key '{k}'")
            cur = getattr(cfg, k)
            if isinstance(cur, Path):
                v = Path(v)
            elif isinstance(cur, dict):
                v = {**cur, **v}
            elif isinstance(cur, float):
                v = float(v)
            setattr(cfg, k, v)
    return cfg
