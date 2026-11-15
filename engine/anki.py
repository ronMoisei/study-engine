"""AnkiConnect client (add-on 2055492159). Everything stays on 127.0.0.1."""
import html
import json
import re
import urllib.error
import urllib.request


class AnkiError(Exception):
    pass


def to_markdown(field_html: str) -> str:
    """Anki field HTML -> Obsidian-renderable markdown: strip scripts/styles/tags, MathJax delimiters -> $ / $$."""
    s = re.sub(r"<(style|script)\b.*?</\1>", "", field_html or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</div>|</p>|</li>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = s.replace("\\[", "$$").replace("\\]", "$$").replace("\\(", "$").replace("\\)", "$")
    return re.sub(r"\n{3,}", "\n\n", s).strip()


class Anki:
    def __init__(self, url: str):
        self.url = url

    def call(self, action, **params):
        body = json.dumps({"action": action, "version": 6, "params": params}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url, body), timeout=15) as r:
                reply = json.loads(r.read())
        except urllib.error.URLError as e:
            raise AnkiError(f"Anki is not reachable at {self.url} — open desktop Anki (AnkiConnect). ({e.reason})")
        if reply.get("error"):
            raise AnkiError(f"AnkiConnect {action}: {reply['error']}")
        return reply.get("result")

    # ── reads ──
    def decks(self) -> list[str]:
        return sorted(self.call("deckNames"))

    def due_card_ids(self, deck: str | None) -> list[int]:
        q = "is:due" + (f' "deck:{deck}"' if deck else "")
        return self.call("findCards", query=q) or []

    def card(self, card_id: int) -> dict:
        """Normalized card: id, note, deck, tags, front/back markdown (from the note's first two fields)."""
        info = self.call("cardsInfo", cards=[card_id])
        if not info:
            raise AnkiError(f"card {card_id} not found")
        c = info[0]
        fields = sorted(c["fields"].items(), key=lambda kv: kv[1].get("order", 0))
        front = to_markdown(fields[0][1]["value"]) if fields else to_markdown(c["question"])
        back = to_markdown(fields[1][1]["value"]) if len(fields) > 1 else to_markdown(c["answer"])
        return {"card_id": c["cardId"], "note_id": c["note"], "deck": c["deckName"], "tags": c.get("tags", []),
                "model": c.get("modelName"), "front": front, "back": back}

    # ── writes ──
    def rate(self, card_id: int, ease: int) -> bool:
        return bool(self.call("answerCards", answers=[{"cardId": card_id, "ease": ease}])[0])

    def add_cards(self, deck, cards, note_type, front_field, back_field, tags, due_now=True) -> list:
        notes = [{"deckName": deck, "modelName": note_type,
                  "fields": {front_field: c["front"], back_field: c["back"]},
                  "tags": tags, "options": {"allowDuplicate": False}} for c in cards]
        ids = self.call("addNotes", notes=notes)
        added = [i for i in ids if i]
        if added and due_now:
            cids = self.call("findCards", query=" OR ".join(f"nid:{n}" for n in added))
            if cids:
                self.call("setDueDate", cards=cids, days="0")
        return ids
