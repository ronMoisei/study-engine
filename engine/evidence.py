"""Append to Magistrale/Learner/evidence.jsonl — same record schema as Agents/skills/learning-harness/log_evidence.py."""
import datetime as dt
import json
import re
from pathlib import Path

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def node_for(card) -> str:
    """Graph slug from the card's lh::<slug> tag; otherwise a stable per-note id."""
    for t in card.get("tags", []):
        if t.lower().startswith("lh::"):
            slug = t[4:].strip().lower()
            if SLUG.match(slug):
                return slug
    return f"anki_note_{card['note_id']}"


def append(ledger: Path, *, session, node, verdict, confidence, miss, note, card_id, ease):
    rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "session": session, "agent": "anki",
           "node": node, "role": "anki", "mode": None, "verdict": verdict,
           "confidence": None if verdict == "idk" else confidence, "hint_rung": None, "miss": miss,
           "note": note, "card": card_id, "ease": ease}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec
