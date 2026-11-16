"""Sync Knowledge v4: full notes -> atomic drafts -> course graph (one proposal per course), resume, rebuild."""
import json
import unittest

from engine import coursesync, ingest, knowledge_sync, syncrun
from engine import graph as G
from engine import graphview
from tests.test_ingest import make_env

RAW1 = """# Lecture one

Gradient descent updates the parameters against the gradient of the loss, with a small learning rate to converge.

Momentum method accumulates a velocity of past gradients. Momentum method builds on gradient descent updates of the parameters.
"""
RAW2 = """# Lecture two

Gradient descent updates the parameters against the gradient of the loss in every single step of training.

Adam optimizer combines adaptive step sizes per parameter with gradient descent updates and a running average.
"""


def course_env():
    cfg, store, llm, emb, _ = make_env()
    cfg.ingest_method = "v4"
    cfg.passage_chars_cloud = 200                       # one passage per paragraph: one stub concept each
    cfg.sync_workers = 2
    c = cfg.material / "01ABCDE - Optimization"
    (c / "01_Study" / "Lectures").mkdir(parents=True)
    for name, text in (("1 - Descent", RAW1), ("2 - Adaptive", RAW2)):
        rn = c / "01_Study" / "Notes" / "Raw Notes" / name
        rn.mkdir(parents=True)
        (rn / f"{name}_Raw_Notes.md").write_text(text, encoding="utf-8")
        (c / "01_Study" / "Lectures" / f"{name}.mp4").write_bytes(b"x")
    return cfg, store, llm, emb, c


def p_domain(cfg, pid):
    return ingest.load(cfg, pid)["domain"]


def raw(c, name):
    return f"{c.name}/01_Study/Notes/Raw Notes/{name}/{name}_Raw_Notes.md"


class CourseGraph(unittest.TestCase):
    def test_notes_first_graph_last(self):
        cfg, store, llm, emb, c = course_env()
        e = syncrun.estimate(cfg, [c.name])
        self.assertEqual(e["method"], "v4")
        self.assertEqual((e["total"]["files"], e["total"]["polish"], e["total"]["graph"]), (2, 2, 3))

        r = syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False)
        self.assertEqual(r["errors"], [])
        self.assertEqual(len(r["proposals"]), 1)                        # ONE proposal for the course
        p = ingest.load(cfg, r["proposals"][0])
        self.assertEqual((p["origin"], p["course"], p["rebuild"]), ("course", c.name, False))

        # stage 2: full notes beside the raw notes, marked as written by the engine
        full = c / "01_Study" / "Notes" / "Full Notes" / "1 - Descent" / "1 - Descent_Full_Notes.md"
        self.assertIn("generated_by: study-engine", full.read_text(encoding="utf-8"))
        # stage 3 -> 4: the concept both lectures draft is merged, its second definition becomes a proposition
        titles = {it["concept"]["title"]: it for it in p["items"]}
        self.assertEqual(sorted(titles), ["Adam optimizer combines", "Gradient descent updates",
                                          "Momentum method accumulates"])
        gd = titles["Gradient descent updates"]
        self.assertEqual(p["stats"]["merged"], 1)
        self.assertEqual(len(gd["lectures"]), 2)
        self.assertTrue(any("every single step" in x["text"] for x in gd["concept"]["propositions"]))
        self.assertTrue(all("Full Notes" in s for s in gd["concept"]["sources"]))
        # links read from the full notes, with the reason and the quote as evidence
        mom = titles["Momentum method accumulates"]
        req = [e for e in mom["edges"] if e["predicate"] == "requires"]
        self.assertEqual([e["to"] for e in req], [gd["slug"]])
        self.assertEqual(req[0]["confidence"], "confident")
        self.assertTrue(req[0]["evidence"][0].startswith("notes:"))

        # state: both lectures graphed by this proposal; nothing left for the next run
        st = coursesync.load_state(cfg, c.name)
        self.assertEqual({x["graphed"] for x in st["lectures"].values()}, {p["id"]})
        row = [x for x in knowledge_sync.inventory(cfg)["courses"] if x["course"] == c.name][0]
        self.assertEqual(row["raw_new"], [])
        self.assertEqual(syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False)["proposals"], [])
        last = json.loads((cfg.shared_dir / "sync" / "last_run.json").read_text(encoding="utf-8"))
        self.assertEqual(last["proposals"], [])

        # a discarded course proposal is redone (from the saved drafts: no polish/atomize again)
        ingest.discard(cfg, p["id"])
        self.assertEqual(syncrun.estimate(cfg, [c.name])["total"]["prepared"], 2)
        self.assertEqual(len(syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False)["proposals"]), 1)

    def test_graph_waits_for_every_lecture(self):
        cfg, store, llm, emb, c = course_env()
        r = syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False, stop=lambda: True)
        self.assertTrue(r["stopped"])
        self.assertEqual(r["proposals"], [])
        self.assertTrue(r["waiting"])

        class Broken:                                    # lecture 2 fails: no course graph yet
            def run(self, job, prompt, schema, session="", retries=0):
                if job == "polish" and "Adam" in prompt:
                    raise RuntimeError("model down")
                return llm.run(job, prompt, schema, session=session)
        r = syncrun.run(cfg, store, Broken(), emb, [c.name], transcribe=False)
        self.assertEqual(r["proposals"], [])
        self.assertEqual(len(r["errors"]), 1)
        self.assertIn("1 lectures failed", r["waiting"][0]["reason"])
        st = coursesync.load_state(cfg, c.name)
        self.assertIsNotNone(st["lectures"][raw(c, "1 - Descent")]["drafts"])       # kept for the next run
        r = syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False)
        self.assertEqual(len(r["proposals"]), 1)

    def test_full_notes_written_by_hand_are_not_overwritten(self):
        cfg, store, llm, emb, c = course_env()
        mine = c / "01_Study" / "Notes" / "Full Notes" / "1 - Descent" / "1 - Descent_Full_Notes.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("# my own notes\n", encoding="utf-8")
        r = syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False)
        self.assertIn("not written by the engine", " ".join(r["errors"]))
        self.assertEqual(mine.read_text(encoding="utf-8"), "# my own notes\n")

    def test_rebuild_replaces_the_course_notes_on_approval(self):
        cfg, store, llm, emb, c = course_env()
        first = syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False)["proposals"][0]
        ingest.apply(cfg, first)
        g = G.Graph.load(cfg)
        old = {i for i, n in g.nodes.items() if n.course == c.name}
        self.assertEqual(len(old), 3)
        # a note captured in a study session for this course is not part of the rebuild
        from engine import notes
        sess = {"id": "learning_rate_schedule", "title": "Learning rate schedule", "aliases": [], "kind": "heuristic",
                "domain": "optimization", "topic": "general", "course": c.name,
                "definition": "Decrease the learning rate over training.", "propositions": [], "requires": [],
                "prerequisite_of": [], "related": [], "sources": ["Learner/sessions/2026-11-16_opt.json"]}
        dom = cfg.graph / p_domain(cfg, first)
        (dom / "learning_rate_schedule.md").write_text(notes.render_concept(sess), encoding="utf-8")

        e = syncrun.estimate(cfg, [c.name], rebuild=[c.name])
        self.assertEqual((e["total"]["files"], e["courses"][0]["retires"]), (2, 3))
        r = syncrun.run(cfg, store, llm, emb, [c.name], transcribe=False, rebuild=[c.name])
        p = ingest.load(cfg, r["proposals"][0])
        self.assertTrue(p["rebuild"])
        self.assertTrue(all(it["decision"] == "new" for it in p["items"]))   # not matched to the notes it replaces
        s = ingest.apply(cfg, p["id"])
        self.assertEqual(s["retired"], 3)
        self.assertEqual(s["errors"], [])
        moved = list((cfg.shared_dir / "retired" / p["id"]).rglob("*.md"))
        self.assertEqual(len(moved), 3)                                    # kept, not deleted
        g = G.Graph.load(cfg)
        self.assertEqual({i for i, n in g.nodes.items() if n.course == c.name},
                         old | {"learning_rate_schedule"})                  # same ids written again; session note kept
        self.assertIn(c.name, json.loads((cfg.shared_dir / "rederived.json").read_text(encoding="utf-8")))


class DoubleEncoded(unittest.TestCase):
    def test_json_inside_the_string_is_unwrapped(self):
        from engine.llm import unwrap_double
        schema = coursesync.POLISH_SCHEMA
        inner = r'{"markdown": "## Gradient\n\nThe step is $\alpha \nabla f$."}'      # as the model wrote it
        out = unwrap_double({"markdown": inner}, schema)["markdown"]
        self.assertTrue(out.startswith("## Gradient\n\nThe step is"))
        plain = {"markdown": "## Gradient\n\nnot JSON"}
        self.assertIs(unwrap_double(plain, schema), plain)
        broken = {"markdown": '{"markdown": "## cut off'}
        self.assertIs(unwrap_double(broken, schema), broken)


class CourseView(unittest.TestCase):
    def test_wide_levels_wrap_into_topic_blocks(self):
        cfg, store, llm, emb, c = course_env()
        dom = cfg.graph / "optimization"
        dom.mkdir(parents=True)
        from engine import notes
        for k in range(30):                              # 30 unlinked concepts in one topic
            notes_c = {"id": f"c{k:02d}", "title": f"Concept {k}", "aliases": [], "kind": "definition",
                       "domain": "optimization", "topic": "basics", "course": c.name,
                       "definition": f"Concept number {k} of the course.", "propositions": [], "requires": [],
                       "prerequisite_of": [], "related": [], "sources": []}
            (dom / f"c{k:02d}.md").write_text(notes.render_concept(notes_c), encoding="utf-8")
        d = graphview.graph_payload(cfg, group=c.name)
        xs = {n["x"] for n in d["nodes"]}
        self.assertLessEqual(len(xs), graphview.COURSE_ROW)
        self.assertEqual([s["id"] for s in d["sections"]], ["basics"])
        self.assertEqual(d["sections"][0]["size"], 30)


if __name__ == "__main__":
    unittest.main()
