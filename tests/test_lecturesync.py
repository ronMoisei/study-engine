"""Sync Knowledge v5: raw notes -> typed notes + cards per lecture (stub models) -> graph by code -> Inbox
proposal; approval writes Graph/<slug>.md and sends the approved notes' cards to the course deck."""
import unittest
from unittest import mock

from engine import ingest, lecturesync, notes, syncrun, topics
from tests.test_ingest import make_env

RAW1 = """# Lecture one

Gradient descent updates the parameters against the gradient of the loss.

Momentum method accumulates a velocity of past gradients for faster convergence.
"""
RAW2 = """# Lecture two

Adam optimizer combines adaptive step sizes per parameter with momentum estimates.
"""


class FakeAnki:
    def __init__(self):
        self.notes = []

    def call(self, action, **kw):
        if action == "addNotes":
            self.notes += kw["notes"]
            return list(range(1, len(kw["notes"]) + 1))
        return None


def env():
    cfg, store, llm, emb, _ = make_env()
    cfg.ingest_method = "v5"
    cfg.sync_workers = 1
    topics.write_all(cfg, [{"macro": "optimization", "macro_title": "Optimization", "sub": "first-order",
                            "title": "First-order methods"}])
    c = cfg.material / "01ABCDE - Optimization"
    (c / "01_Study" / "Lectures").mkdir(parents=True)
    for name, text in (("1 - Descent", RAW1), ("2 - Adaptive", RAW2)):
        rn = c / "01_Study" / "Notes" / "Raw Notes" / name
        rn.mkdir(parents=True)
        (rn / f"{name}_Raw_Notes.md").write_text(text, encoding="utf-8")
        (c / "01_Study" / "Lectures" / f"{name}.mp4").write_bytes(b"x")
    return cfg, store, llm, emb, c


class SyncV5(unittest.TestCase):
    def test_lectures_to_proposal_to_notes_and_cards(self):
        cfg, store, llm, emb, c = env()
        e = syncrun.estimate(cfg, [c.name])
        self.assertEqual((e["method"], e["total"]["notes"], e["total"]["exercises"]), ("v5", 2, 2))
        r = syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False)
        self.assertEqual(r["errors"], [])
        p = ingest.load(cfg, r["proposals"][0])
        self.assertEqual((p["method"], p["deck"]), ("v5", "Data Science::Optimization"))
        by = {it["concept"]["title"]: it for it in p["items"]}
        self.assertEqual(sorted(by), ["Adam optimizer combines", "Gradient descent updates", "Momentum method accumulates"])
        mom, gd = by["Momentum method accumulates"], by["Gradient descent updates"]
        self.assertEqual([e["to"] for e in mom["edges"]], [gd["slug"]])     # the stub's "previous note" premise
        self.assertEqual({c_["kind"] for it in p["items"] for c_ in it["cards"]}, {"easy", "medium"})
        self.assertEqual(p["stats"]["new"], 3)
        # approval: flat notes, valid, cards to Anki with lh:: + topic tags; one card unticked
        fake = FakeAnki()
        with mock.patch("engine.anki.Anki", return_value=fake):
            s = ingest.apply(cfg, p["id"], reject_cards=[[gd["index"], 0]])
        self.assertEqual(s["errors"], [])
        for it in p["items"]:
            f = cfg.graph / f"{it['slug']}.md"
            self.assertEqual(notes.validate_concept(f.read_text(encoding="utf-8"), f.stem), [], f.name)
        self.assertEqual(notes.parse_concept((cfg.graph / f"{mom['slug']}.md").read_text(encoding="utf-8"))["requires"],
                         [gd["slug"]])
        self.assertTrue(fake.notes)
        self.assertTrue(all(n["tags"][0].startswith("lh::") and "optimization::first-order" in n["tags"]
                            for n in fake.notes))
        self.assertTrue(all(n["deckName"] == "Data Science::Optimization" for n in fake.notes))
        p = ingest.load(cfg, p["id"])
        gd = next(it for it in p["items"] if it["slug"] == gd["slug"])
        self.assertEqual(gd["cards"][0]["status"], "rejected")                 # unticked: never sent
        self.assertTrue(all(c_["status"] == "sent" for it in p["items"] for c_ in it["cards"][1 if it is gd else 0:]))
        # nothing left to do for this course
        self.assertEqual(syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False)["proposals"], [])

    def test_existing_node_gains_the_lecture_instead_of_a_duplicate(self):
        cfg, store, llm, emb, c = env()
        st, errs = lecturesync.prepare_all(cfg, store, llm, c.name,
                                           [f"{c.name}/01_Study/Notes/Raw Notes/1 - Descent/1 - Descent_Raw_Notes.md"])
        rel = next(iter(st["lectures"]))
        st["lectures"][rel]["notes"][0].update(action="integrate", target="graph_laplacian",   # the repeat check
                                               add=["A new fact from this lecture."])
        p = lecturesync.build(cfg, c.name, [rel], st)
        ext = [it for it in p["items"] if it["decision"] == "extends"]
        self.assertEqual([it["match"] for it in ext], ["graph_laplacian"])
        self.assertEqual([x["text"] for x in ext[0]["new_propositions"]], ["A new fact from this lecture."])
        self.assertTrue(ext[0]["cards"])                                       # its cards go to the existing node

    def test_lectures_in_order_see_earlier_drafts_and_existing_titles(self):
        cfg, store, llm, emb, c = env()
        seen = []
        real = lecturesync._cached

        def spy(cfg_, store_, llm_, job, version, template, data, schema, session):
            if job == "notes":
                seen.append((data["lecture"], [e["title"] for e in data["earlier_lectures"]]))
            out = real(cfg_, store_, llm_, job, version, template, data, schema, session)
            if job == "notes" and data["lecture"] == "2 - Adaptive":       # lecture 2 repeats lecture 1 and the graph
                out = {**out, "notes": [{**out["notes"][0], "title": "Gradient descent updates"},
                                        {**out["notes"][0], "key": "n9", "title": "Graph Laplacian"}]}
            return out
        rels = [f"{c.name}/01_Study/Notes/Raw Notes/{n}/{n}_Raw_Notes.md" for n in ("2 - Adaptive", "1 - Descent")]
        with mock.patch.object(lecturesync, "_cached", spy):
            st, errs = lecturesync.prepare_all(cfg, store, llm, c.name, rels)
        self.assertEqual(errs, [])
        self.assertEqual([s[0] for s in seen], ["1 - Descent", "2 - Adaptive"])          # lecture order
        self.assertEqual(seen[1][1], ["Gradient descent updates", "Momentum method accumulates"])
        p = lecturesync.build(cfg, c.name, rels, st)
        gd = next(it for it in p["items"] if it["concept"]["title"] == "Gradient descent updates")
        self.assertEqual(len(gd["lectures"]), 2)                       # the lab's note merged into lecture 1's
        ext = [it for it in p["items"] if it["decision"] == "extends"]
        self.assertEqual([it["match"] for it in ext], ["graph_laplacian"])   # same name as an existing node

    def test_near_twin_topics_are_merged(self):
        cfg, store, llm, emb, c = env()
        rel = f"{c.name}/01_Study/Notes/Raw Notes/1 - Descent/1 - Descent_Raw_Notes.md"
        st, _ = lecturesync.prepare_all(cfg, store, llm, c.name, [rel])
        x = st["lectures"][rel]
        x["new_topics"] = [{"macro": "optimization", "macro_title": "Optimization", "sub": "bayesian-and-naive-bayes",
                            "title": "Bayes"},
                           {"macro": "optimization", "macro_title": "Optimization", "sub": "naive-bayes-and-bayesian",
                            "title": "Bayes again"}]
        x["notes"][0]["topics"] = ["optimization/naive-bayes-and-bayesian"]
        p = lecturesync.build(cfg, c.name, [rel], st)
        self.assertEqual([t["sub"] for t in p["new_topics"]], ["bayesian-and-naive-bayes"])
        first = next(it for it in p["items"] if it["concept"]["title"] == x["notes"][0]["title"])
        self.assertEqual(first["concept"]["topics"], ["optimization/bayesian-and-naive-bayes"])

    def test_anki_closed_keeps_cards_pending(self):
        cfg, store, llm, emb, c = env()
        r = syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False, limit=1)
        p = ingest.load(cfg, r["proposals"][0])
        self.assertEqual(p["stats"]["lectures"], 1)                            # a test run on the first lecture
        from engine.anki import AnkiError

        class Closed:
            def call(self, *a, **k):
                raise AnkiError("Anki is not reachable")
        with mock.patch("engine.anki.Anki", return_value=Closed()):
            s = ingest.apply(cfg, p["id"])
        self.assertTrue(s["cards"]["error"])
        p = ingest.load(cfg, p["id"])
        self.assertTrue(all(c_["status"] == "pending" for it in p["items"] for c_ in it["cards"]))
        fake = FakeAnki()
        out = ingest.send_cards(cfg, p, fake)                                  # Anki open again: sent
        self.assertEqual(out["pending"], 0)
        self.assertTrue(fake.notes)


if __name__ == "__main__":
    unittest.main()
