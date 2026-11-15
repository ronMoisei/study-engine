"""Learner state per node, derived from Obsidian Notes/Learner/evidence.jsonl. Categorical on purpose.

  misconception  last graded answer wrong with confidence >= 70, or classed structural
  fragile        last graded answer not correct, or correct with confidence < 50, or needing hint rung >= R3
  held           last two graded answers correct (confidence >= 50, hint <= R1) on different days
  in_progress    any other evidence
  untested       no evidence
"""
import json


def _rung(v):
    try:
        return int(str(v).upper().lstrip("R"))
    except (TypeError, ValueError):
        return None


def status_of(events):
    graded = [e for e in events if e.get("verdict") in ("correct", "wrong", "partial", "idk")]
    if not graded:
        return "in_progress" if events else "untested"
    last = graded[-1]
    conf = last.get("confidence")
    conf = conf if isinstance(conf, (int, float)) else 50
    r = _rung(last.get("hint_rung"))
    if last.get("miss") == "structural" or (last["verdict"] == "wrong" and conf >= 70):
        return "misconception"
    if last["verdict"] != "correct" or conf < 50 or (r is not None and r >= 3):
        return "fragile"

    def ok(e):
        c = e.get("confidence")
        return e["verdict"] == "correct" and (c if isinstance(c, (int, float)) else 50) >= 50 \
            and (_rung(e.get("hint_rung")) or 0) <= 1

    if len(graded) >= 2:
        prev = graded[-2]
        if ok(last) and ok(prev) and str(last.get("ts", ""))[:10] != str(prev.get("ts", ""))[:10]:
            return "held"
    return "in_progress"


def statuses(ledger):
    """-> {node_id: status} for every node that has evidence."""
    events = {}
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("node"):
                events.setdefault(e["node"], []).append(e)
    for evs in events.values():
        evs.sort(key=lambda e: str(e.get("ts", "")))
    return {n: status_of(evs) for n, evs in events.items()}
