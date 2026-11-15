"""End-to-end review flow with a fake Anki and the stub grader: no network, no model, no real ledger."""
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from engine.config import Config
from engine.llm import StubProvider
from engine.review import ReviewSession, SessionError
from engine.server import Engine, make_handler
from engine.store import Store


class FakeAnki:
    def __init__(self, ledger):
        self.ledger = ledger
        self.cards = {
            1: {"card_id": 1, "note_id": 11, "deck": "Graph::sgt", "tags": ["lh::graph_laplacian"], "model": "Basilare",
                "front": "Define the graph Laplacian.", "back": "The Laplacian is L = D - A. D is the diagonal degree matrix."},
            2: {"card_id": 2, "note_id": 22, "deck": "Graph::sgt", "tags": [], "model": "Basilare",
                "front": "Dimension of the kernel of L for a connected graph?", "back": "One, spanned by the all-ones vector."},
            3: {"card_id": 3, "note_id": 33, "deck": "Graph::sgt", "tags": [], "model": "Basilare",
                "front": "What is the Fiedler value?", "back": "The second smallest eigenvalue of L."},
        }
        self.rated, self.added = [], []

    def call(self, action, **p):
        return 6

    def decks(self):
        return ["Graph::sgt"]

    def due_card_ids(self, deck):
        return [1, 2, 3]

    def card(self, cid):
        return dict(self.cards[cid])

    def rate(self, cid, ease):
        lines = self.ledger.read_text(encoding="utf-8").splitlines() if self.ledger.exists() else []
        logged = any(json.loads(l).get("card") == cid for l in lines)
        self.rated.append((cid, ease, logged))
        return True

    def add_cards(self, deck, cards, note_type, ff, bf, tags, due_now=True):
        self.added.append((deck, cards, tags, due_now))
        return [900 + i for i, _ in enumerate(cards)]


def make_env():
    tmp = Path(tempfile.mkdtemp())
    cfg = Config(magistrale=tmp, provider="stub", db_file=str(tmp / "engine.sqlite3"))
    store = Store(cfg.db_path)
    anki = FakeAnki(cfg.ledger)
    return cfg, store, anki, StubProvider(cfg, store)


class Flow(unittest.TestCase):
    def setUp(self):
        self.cfg, self.store, self.anki, self.llm = make_env()
        self.s = ReviewSession(self.cfg, self.anki, self.store, self.llm, deck="Graph::sgt")

    def ledger(self):
        return [json.loads(l) for l in self.cfg.ledger.read_text(encoding="utf-8").splitlines()]

    def test_front_only_until_answered(self):
        v = self.s.current()
        self.assertNotIn("back", v)
        self.assertNotIn("Laplacian is L = D - A", json.dumps(v))

    def test_full_session(self):
        r1 = self.s.answer("The Laplacian is L = D - A, where D is the diagonal degree matrix", 95)
        self.assertEqual((r1["verdict"], r1["ease"]), ("correct", 4))
        self.assertIn("L = D - A", r1["back"])
        self.assertEqual(r1["node"], "graph_laplacian")
        self.s.next()
        r2 = self.s.answer("It is two-dimensional because of the trace", 85)
        self.assertEqual((r2["verdict"], r2["ease"], r2["miss"]), ("wrong", 1, "structural"))
        self.assertTrue(r2["remedial"])
        self.s.next()
        r3 = self.s.answer("", None, idk=True)
        self.assertEqual((r3["verdict"], r3["ease"]), ("idk", 1))
        end = self.s.next()
        self.assertEqual(end["state"], "done")
        self.assertEqual(end["summary"]["verdicts"], {"correct": 1, "wrong": 1, "idk": 1})
        # evidence BEFORE rating, every time
        self.assertEqual([(c, e) for c, e, _ in self.anki.rated], [(1, 4), (2, 1), (3, 1)])
        self.assertTrue(all(logged for _, _, logged in self.anki.rated))
        led = self.ledger()
        self.assertEqual([x["verdict"] for x in led], ["correct", "wrong", "idk"])
        self.assertEqual(led[1]["node"], "anki_note_22")
        self.assertIsNone(led[2]["confidence"])
        # remedial: due now, tagged with the gap
        deck, cards, tags, due_now = self.anki.added[0]
        self.assertTrue(due_now)
        self.assertIn("gap::anki_note_22", tags)

    def test_state_guards(self):
        with self.assertRaises(SessionError):
            self.s.next()                       # cannot skip without answering
        with self.assertRaises(SessionError):
            self.s.answer("", 50)               # empty answer
        self.s.answer("L = D - A", 60)
        with self.assertRaises(SessionError):
            self.s.answer("again", 60)          # cannot re-answer the same card

    def test_checklist_cached(self):
        self.s.answer("L = D - A", 60)
        s2 = ReviewSession(self.cfg, self.anki, self.store, self.llm, deck="Graph::sgt")
        s2.answer("L = D - A", 60)
        jobs = [r[0] for r in self.store.db.execute("SELECT job FROM llm_calls WHERE job='checklist'")]
        self.assertEqual(len(jobs), 1)

    def test_dry_run_never_writes_to_anki(self):
        self.cfg.dry_run = True
        r = self.s.answer("wrong", 90)
        self.assertEqual(self.anki.rated, [])
        self.assertEqual(self.anki.added, [])
        self.assertFalse(r["rated"])


class Server(unittest.TestCase):
    def test_http_roundtrip(self):
        from http.server import ThreadingHTTPServer
        cfg, store, anki, llm = make_env()
        engine = Engine(cfg, anki=anki, llm=llm)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def req(method, path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            r = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(r) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        self.assertEqual(req("GET", "/health")[1]["provider"], "stub")
        st, v = req("POST", "/review/start", {"deck": "Graph::sgt"})
        self.assertEqual((st, v["position"]), (200, 1))
        self.assertNotIn("back", v)
        sid = v["session"]
        self.assertEqual(req("POST", f"/review/{sid}/next")[0], 409)
        st, r = req("POST", f"/review/{sid}/answer", {"answer": "The Laplacian is L = D - A, D diagonal degree matrix", "confidence": 70})
        self.assertEqual((st, r["verdict"]), (200, "correct"))
        self.assertEqual(req("POST", f"/review/{sid}/next")[1]["position"], 2)
        self.assertEqual(req("GET", "/review/nope")[0], 409)
        httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
