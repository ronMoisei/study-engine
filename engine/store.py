"""Local SQLite store (outside Google Drive): checklist cache, LLM call log, review log. Safe to delete."""
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS checklists (
  card_id INTEGER, content_hash TEXT, checklist TEXT, model TEXT, created REAL,
  PRIMARY KEY (card_id, content_hash));
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, session TEXT, job TEXT, model TEXT,
  prompt TEXT, output TEXT, raw TEXT, usage TEXT, ok INTEGER, error TEXT, duration_ms INTEGER);
CREATE TABLE IF NOT EXISTS embeddings (
  key TEXT PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB);
CREATE TABLE IF NOT EXISTS extractions (
  key TEXT PRIMARY KEY, model TEXT, claims TEXT, created REAL);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, session TEXT, card_id INTEGER, node TEXT,
  answer TEXT, confidence INTEGER, idk INTEGER, verdict TEXT, ease INTEGER, result TEXT);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def get_checklist(self, card_id, content_hash):
        with self.lock:
            row = self.db.execute("SELECT checklist FROM checklists WHERE card_id=? AND content_hash=?",
                                  (card_id, content_hash)).fetchone()
        return json.loads(row[0]) if row else None

    def put_checklist(self, card_id, content_hash, checklist, model):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO checklists VALUES (?,?,?,?,?)",
                            (card_id, content_hash, json.dumps(checklist, ensure_ascii=False), model, time.time()))

    def get_vec(self, key):
        with self.lock:
            row = self.db.execute("SELECT vec FROM embeddings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put_vecs(self, rows):
        """rows: iterable of (key, model, dim, blob)."""
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)", list(rows))

    def get_extraction(self, key):
        with self.lock:
            row = self.db.execute("SELECT claims FROM extractions WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_extraction(self, key, model, claims):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO extractions VALUES (?,?,?,?)",
                            (key, model, json.dumps(claims, ensure_ascii=False), time.time()))

    def log_call(self, **row):
        row.setdefault("ts", time.time())
        for k in ("output", "usage"):
            if row.get(k) is not None and not isinstance(row[k], str):
                row[k] = json.dumps(row[k], ensure_ascii=False)
        cols = ",".join(row)
        with self.lock, self.db:
            self.db.execute(f"INSERT INTO llm_calls ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))

    def log_review(self, **row):
        row.setdefault("ts", time.time())
        row["result"] = json.dumps(row.get("result"), ensure_ascii=False)
        cols = ",".join(row)
        with self.lock, self.db:
            self.db.execute(f"INSERT INTO reviews ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))

    def session_usage(self, session):
        """Sum every numeric field whose name contains 'token' across this session's calls."""
        with self.lock:
            rows = self.db.execute("SELECT usage, ok FROM llm_calls WHERE session=?", (session,)).fetchall()
        totals, calls = {}, len(rows)
        for usage, _ in rows:
            for k, v in (json.loads(usage) if usage else {}).items():
                if isinstance(v, (int, float)):
                    totals[k] = totals.get(k, 0) + v
        return {"calls": calls, "tokens": totals}
