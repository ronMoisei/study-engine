"""Sync Knowledge: v3 ingest (structure -> write -> organize), runs, bulk approval, held conflicts, retiring."""
import json
import unittest

from engine import graph as G
from engine import ingest, knowledge_sync, syncrun
from engine.store import Store
from tests.test_ingest import make_env

RAW = """# Lecture 1

Gradient descent updates the parameters against the gradient of the loss.
Gradient descent needs a learning rate that is small enough to converge.

Momentum accumulates a velocity of past gradients to speed up descent.
Momentum damps oscillations in narrow valleys of the loss surface.
"""


def course_env():
    cfg, store, llm, emb, _ = make_env()
    cfg.ingest_method = "v3"
    c = cfg.material / "01ABCDE - Optimization"
    rn = c / "01_Study" / "Notes" / "Raw Notes" / "Opt 1"
    rn.mkdir(parents=True)
    (c / "01_Study" / "Lectures").mkdir(parents=True)
    (c / "01_Study" / "Lectures" / "Opt 1.mp4").write_bytes(b"x")
    (rn / "Opt 1_Raw_Notes.md").write_text(RAW, encoding="utf-8")
    return cfg, store, llm, emb, c


class V3Ingest(unittest.TestCase):
    def test_structure_write_organize(self):
        cfg, store, llm, emb, c = course_env()
        p = ingest.ingest(cfg, store, llm, emb, c / "01_Study/Notes/Raw Notes/Opt 1/Opt 1_Raw_Notes.md",
                          "optimization", course=c.name)
        titles = {it["concept"]["title"]: it for it in p["items"]}
        self.assertIn("Gradient descent updates the", titles)
        g = titles["Gradient descent updates the"]["concept"]
        self.assertEqual(g["course"], c.name)
        self.assertEqual([x["text"] for x in g["propositions"]],
                         ["Gradient descent needs a learning rate that is small enough to converge."])
        self.assertTrue(all(ingest.quote_supported(x["quote"], RAW) for x in g["propositions"]))
        self.assertEqual(p["stats"]["writer_calls"], 1)

    def test_unquoted_output_is_dropped(self):
        cfg, store, llm, emb, c = course_env()

        class Liar:                                      # the writer invents a definition the source never states
            name = "liar"

            def route(self, job):
                return ("stub", "x")

            def run(self, job, prompt, schema, session="", retries=0, model=None):
                out = llm.run(job, prompt, schema, session=session)
                if job == "write":
                    for n in out["notes"]:
                        n["definition_quote"] = "words that appear nowhere in this lecture at all"
                return out
        p = ingest.ingest(cfg, store, Liar(), emb, c / "01_Study/Notes/Raw Notes/Opt 1/Opt 1_Raw_Notes.md",
                          "optimization", course=c.name)
        self.assertEqual(p["items"], [])
        self.assertEqual(p["stats"]["dropped_unquoted_concepts"], 2)


class Runs(unittest.TestCase):
    def test_estimate_run_resume_approve_retire(self):
        cfg, store, llm, emb, c = course_env()
        atomic = c / "01_Study" / "Notes" / "Atomic Notes"
        atomic.mkdir(parents=True)
        (atomic / "old_note.md").write_text('---\ntitle: "Old note"\neasy_definition: "An old atomic note."\n---\n',
                                            encoding="utf-8")
        cfg.atomic_globs = ["Material/*/01_Study/Notes/Atomic Notes/*.md"]
        self.assertIn("old_note", G.Graph.load(cfg).nodes)

        e = syncrun.estimate(cfg, [c.name])
        self.assertEqual((e["total"]["files"], e["total"]["structure"], e["total"]["relate"]), (1, 1, 1))

        r = syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False)
        self.assertEqual(len(r["proposals"]), 1)
        self.assertFalse(syncrun._lock_file(cfg).exists())              # lock released
        again = syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False)
        self.assertEqual(again["proposals"], [])                         # resume: nothing processed twice

        pid = r["proposals"][0]
        p = ingest.load(cfg, pid)                                        # make one link a conflict
        new = [it for it in p["items"] if it["decision"] == "new"]
        new[1]["edges"] = [{"predicate": "requires", "to": new[0]["slug"], "confidence": "conflict",
                            "evidence": [], "conflict": ["appears later in the lecture"], "accepted": False}]
        ingest.save(cfg, p)
        s = ingest.approve_confident(cfg)
        self.assertEqual((s["proposals"], s["held_conflicts"]), (1, 1))
        g = G.Graph.load(cfg)
        self.assertNotIn(new[0]["slug"], g.requires(new[1]["slug"]))     # held, not written
        out = ingest.accept_conflicts(cfg, pid, [[new[1]["slug"], "requires", new[0]["slug"]]])
        self.assertEqual(len(out["accepted"]), 1)
        self.assertIn(new[0]["slug"], G.Graph.load(cfg).requires(new[1]["slug"]))

        self.assertEqual(syncrun.update_rederived(cfg), [c.name])        # all raw notes approved -> retired
        self.assertNotIn("old_note", G.Graph.load(cfg).nodes)

    def test_one_pc_at_a_time(self):
        cfg, *_ = course_env()
        syncrun.acquire(cfg)
        with self.assertRaises(syncrun.SyncError):
            syncrun.acquire(cfg)
        syncrun.release(cfg)
        syncrun.acquire(cfg)
        syncrun.release(cfg)


if __name__ == "__main__":
    unittest.main()
