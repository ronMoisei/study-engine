"""One-time move to the flat, topic-organised graph: dry run, then the staged plan (stub models)."""
import unittest

from engine import migrate
from tests.test_ingest import make_env

OLD = """---
id: X.01.act1.gradient_descent
title: "Gradient descent"
aliases:
  - "GD"
type: mechanism
macro_topic: optimisation
prerequisites:
  - "[[2_Full Notes/X/atomic/graph_laplacian]]"
implications: []
contrasts_with: []
easy_definition: |
  Step against the gradient of the loss with a learning rate.
easy_pitfall: |
  A learning rate that is too large makes the iterates diverge.
---
## Core Statement
Gradient descent updates parameters against the gradient.
"""


def env():
    cfg, store, llm, _, _ = make_env()
    d = cfg.material / "01ABCDE - Optimization" / "01_Study" / "Notes" / "Atomic Notes"
    d.mkdir(parents=True)
    (d / "gradient_descent.md").write_text(OLD, encoding="utf-8")
    return cfg, store, llm


class Migrate(unittest.TestCase):
    def test_report_is_a_dry_run(self):
        cfg, *_ = env()
        before = sorted(p.as_posix() for p in cfg.magistrale.rglob("*.md"))
        r = migrate.report(cfg)
        self.assertEqual((r["engine"], r["old"], r["collisions"]), (1, 2, {}))
        self.assertEqual(sorted(p.as_posix() for p in cfg.magistrale.rglob("*.md")), before)   # nothing written

    def test_prepare_splits_assigns_and_stages(self):
        cfg, store, llm = env()
        plan = migrate.prepare(cfg, store, llm, say=lambda *_: None)
        n = plan["notes"]
        gd = n["gradient_descent"]                                   # the main note keeps the old stem (lh:: links)
        self.assertEqual((gd["kind"], gd["requires"]), ("definition", ["graph_laplacian"]))
        self.assertEqual(gd["sources"], [".study/retired/atomic-notes/01ABCDE - Optimization/gradient_descent.md"])
        pit = [c for c in n.values() if c.get("extra_of") == "gradient_descent"]
        self.assertEqual([(c["kind"], c["requires"]) for c in pit], [("pitfall", ["gradient_descent"])])
        # the course note "degree_matrix" says graph_laplacian rests on it: the dependent now stores the link
        self.assertIn("degree_matrix", n["graph_laplacian"]["requires"])
        self.assertEqual(set(plan["assigned"]), set(n))              # every note has a topic from the list
        self.assertTrue(all(a["topics"][0] in {t["slug"] for t in plan["taxonomy"]} for a in plan["assigned"].values()))
        self.assertTrue((migrate.plan_dir(cfg) / "plan.json").is_file())
        self.assertFalse((cfg.graph / "gradient_descent.md").exists())                  # Graph/ untouched
        s = migrate.summary(plan)
        self.assertEqual((s["notes"], s["extras"], s["unassigned"]), (4, 1, []))

    def test_apply_writes_a_flat_graph_and_archives_the_rest(self):
        from engine import graph as G
        from engine import notes, topics
        cfg, store, llm = env()
        plan = migrate.prepare(cfg, store, llm, say=lambda *_: None)
        plan["merges"] = [{"keep": "graph_laplacian", "same": ["degree_matrix"], "why": "test"}]
        r = migrate.apply(cfg, plan, say=lambda *_: None)
        self.assertEqual(r["problems"], [])
        self.assertEqual(sorted(p.name for p in cfg.graph.iterdir() if p.is_file()),
                         ["_topics.md", "gradient_descent.md", "gradient_descent_pitfall.md", "graph_laplacian.md"])
        for f in cfg.graph.glob("[!_]*.md"):
            self.assertEqual(notes.validate_concept(f.read_text(encoding="utf-8"), f.stem), [], f.name)
        lap = notes.parse_concept((cfg.graph / "graph_laplacian.md").read_text(encoding="utf-8"))
        self.assertIn("Degree Matrix", lap["aliases"])                                  # merged note
        self.assertNotIn("degree_matrix", lap["requires"])
        self.assertEqual(r["redirects"], {"degree_matrix": "graph_laplacian"})
        self.assertTrue(topics.load_all(cfg))
        self.assertFalse(list(cfg.material.rglob("Atomic Notes/*.md")))                 # originals moved...
        self.assertTrue((cfg.magistrale / ".study/retired/atomic-notes/01ABCDE - Optimization/gradient_descent.md").is_file())
        self.assertEqual(sorted(G.Graph.load(cfg).nodes), ["gradient_descent", "gradient_descent_pitfall",
                                                           "graph_laplacian"])          # ...and not loaded twice


if __name__ == "__main__":
    unittest.main()
