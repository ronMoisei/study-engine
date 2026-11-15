"""Review session state machine.  awaiting_answer --answer()--> answered --next()--> awaiting_answer | done

Guarantees by construction (no prompt needed):
- the back of a card is only returned inside the result of answer();
- evidence is appended BEFORE the card is rated in Anki;
- the rating comes from grading.decide(), never from the model.
"""
import time
import uuid

from . import evidence, grading
from .anki import AnkiError
from .llm import LLMError


class SessionError(Exception):
    pass


class ReviewSession:
    def __init__(self, cfg, anki, store, llm, deck=None, limit=20):
        self.cfg, self.anki, self.store, self.llm = cfg, anki, store, llm
        self.id = time.strftime("%Y-%m-%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
        self.deck = deck
        self.queue = anki.due_card_ids(deck)[: int(limit)]
        self.pos = 0
        self.state = "awaiting_answer" if self.queue else "done"
        self.card = None
        self.results = []

    # ── views ──
    def current(self):
        if self.state == "done":
            return {"state": "done", "summary": self.summary()}
        if self.card is None:
            self.card = self.anki.card(self.queue[self.pos])
        c = self.card
        return {"state": self.state, "session": self.id, "position": self.pos + 1, "total": len(self.queue),
                "card_id": c["card_id"], "deck": c["deck"], "front": c["front"],
                "node": evidence.node_for(c),
                "result": self.results[-1] if self.state == "answered" else None}

    def summary(self):
        counts = {}
        for r in self.results:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        return {"session": self.id, "reviewed": len(self.results), "verdicts": counts,
                "remedial_added": sum(len(r.get("remedial", [])) for r in self.results),
                "usage": self.store.session_usage(self.id)}

    # ── transitions ──
    def answer(self, text, confidence=None, idk=False):
        if self.state != "awaiting_answer":
            raise SessionError(f"cannot answer in state '{self.state}'")
        text = (text or "").strip()
        if not text and not idk:
            raise SessionError("empty answer — type/dictate something, or mark 'I don't know'")
        if confidence is not None and not 0 <= int(confidence) <= 100:
            raise SessionError("confidence must be 0-100")
        c = self.card or self.anki.card(self.queue[self.pos])
        node = evidence.node_for(c)
        checklist = self._checklist(c, node)

        if idk:
            items = [{**p, "met": False, "quote": "", "note": ""} for p in checklist]
            graded = {"contradiction": False, "contradiction_note": "", "miss_type": "cue",
                      "feedback": "Marked as not known — a genuine gap, not a wrong answer."}
        else:
            graded = self.llm.run("grade", grading.prompt(grading.GRADE_PROMPT, {
                "question": c["front"], "card_answer": c["back"], "checklist": checklist, "answer": text}),
                grading.GRADE_SCHEMA, session=self.id)
            items = grading.verify_items(checklist, graded, text)

        d = grading.decide(items, None if confidence is None else int(confidence), idk,
                           bool(graded.get("contradiction")), graded.get("miss_type"))
        result = {"card_id": c["card_id"], "node": node, "verdict": d["verdict"], "ease": d["ease"],
                  "miss": d["miss"], "misconception": d["misconception"], "confidence": confidence,
                  "items": items, "feedback": graded.get("feedback", ""),
                  "contradiction_note": graded.get("contradiction_note", "") if graded.get("contradiction") else "",
                  "back": c["back"], "rated": False, "remedial": [], "errors": []}

        # 1) evidence first
        note = result["feedback"][:300]
        evidence.append(self.cfg.ledger, session=self.id, node=node, verdict=d["verdict"],
                        confidence=None if confidence is None else int(confidence), miss=d["miss"], note=note,
                        card_id=c["card_id"], ease=d["ease"])
        # 2) then the rating
        if self.cfg.dry_run:
            result["errors"].append("dry run: not rated in Anki")
        else:
            try:
                result["rated"] = self.anki.rate(c["card_id"], d["ease"])
            except AnkiError as e:
                result["errors"].append(f"rating failed: {e}")
        # 3) remedial cards on a miss (never blocks the session)
        if d["verdict"] in ("wrong", "partial", "idk"):
            try:
                result["remedial"] = self._remedial(c, node, items, result["contradiction_note"])
            except (LLMError, AnkiError) as e:
                result["errors"].append(f"remedial cards not created: {e}")

        self.store.log_review(session=self.id, card_id=c["card_id"], node=node, answer=text,
                              confidence=confidence, idk=int(bool(idk)), verdict=d["verdict"], ease=d["ease"],
                              result=result)
        self.results.append(result)
        self.state = "answered"
        return result

    def next(self):
        if self.state != "answered":
            raise SessionError(f"cannot advance in state '{self.state}'")
        self.pos += 1
        self.card = None
        self.state = "awaiting_answer" if self.pos < len(self.queue) else "done"
        return self.current()

    # ── model jobs ──
    def _source_note(self, node):
        if node.startswith("anki_note_") or not self.cfg.graph.is_dir():
            return ""
        hits = list(self.cfg.graph.rglob(f"{node}.md"))
        return hits[0].read_text(encoding="utf-8")[:4000] if hits else ""

    def _checklist(self, c, node):
        h = grading.content_hash(c["front"], c["back"])
        cached = self.store.get_checklist(c["card_id"], h)
        if cached:
            return cached
        out = self.llm.run("checklist", grading.prompt(grading.CHECKLIST_PROMPT, {
            "question": c["front"], "back": c["back"], "source_note": self._source_note(node)}),
            grading.CHECKLIST_SCHEMA, session=self.id)
        points = out["points"]
        self.store.put_checklist(c["card_id"], h, points, self.cfg.models.get("checklist", ""))
        return points

    def _remedial(self, c, node, items, false_belief):
        unmet = [it["point"] for it in items if not it["met"]]
        if not unmet:
            return []
        out = self.llm.run("remedial", grading.prompt(grading.REMEDIAL_PROMPT, {
            "question": c["front"], "back": c["back"], "unmet_points": unmet, "false_belief": false_belief}),
            grading.REMEDIAL_SCHEMA, session=self.id)
        cards = out["cards"]
        if self.cfg.dry_run:
            return [{**x, "added": False} for x in cards]
        tags = ["remedial", f"gap::{node}"] + ([f"lh::{node}"] if not node.startswith("anki_note_") else [])
        ids = self.anki.add_cards(c["deck"], cards, self.cfg.note_type, self.cfg.front_field, self.cfg.back_field,
                                  tags, due_now=True)
        return [{**x, "added": bool(i), "note_id": i} for x, i in zip(cards, ids)]
