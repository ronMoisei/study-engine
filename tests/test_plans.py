"""Study plans as live progress: steps from the mermaid map, done from `done:` or a (done) mark, current from next."""
import unittest

from engine import plans
from tests.test_ingest import make_env

PLAN = '''---
goal: "Master classification for Lab 7"
goal_slug: lab7
phase: teach
next: "2.1 Basic Frequency Features — predict pending"
done: ["1.2"]
updated: 2026-11-20
---
# Study Plan

```mermaid
graph TD
  subgraph S1 ["1. Tabular Mapping"]
    S1_1["1.1 Formulation (done)"]
    S1_2["1.2 Fixed-Dimension Features"]
    S1_1 --> S1_2
  end
  subgraph S2 ["2. Spectral Features"]
    S2_1["2.1 Basic Frequency Features"]
    S2_2["2.2 Normalization"]
    S1_2 --> S2_1 --> S2_2
  end
```
'''


class Plans(unittest.TestCase):
    def test_steps_and_progress(self):
        p = plans.parse(PLAN)
        self.assertEqual([s["title"] for s in p["sections"]], ["Tabular Mapping", "Spectral Features"])
        st = {s["id"]: s["status"] for x in p["sections"] for s in x["steps"]}
        self.assertEqual(st, {"1.1": "done", "1.2": "done", "2.1": "current", "2.2": "todo"})
        self.assertEqual(p["progress"], {"done": 2, "total": 4})
        self.assertEqual(p["sections"][0]["steps"][0]["title"], "Formulation")

    def test_list_skips_finished_and_mapless(self):
        cfg, *_ = make_env()
        cfg.plans_dir.mkdir(parents=True)
        (cfg.plans_dir / "lab7.md").write_text(PLAN, encoding="utf-8")
        (cfg.plans_dir / "old.md").write_text(PLAN.replace("phase: teach", "phase: done"), encoding="utf-8")
        (cfg.plans_dir / "nomap.md").write_text("---\ngoal: x\n---\nno map\n", encoding="utf-8")
        self.assertEqual([p["file"] for p in plans.list_plans(cfg)], ["lab7.md"])
        self.assertEqual(len(plans.list_plans(cfg, include_done=True)), 2)


class Grill(unittest.TestCase):
    def test_grill_line_only_when_asked(self):
        from engine import graphview, learn
        cfg, *_ = make_env()
        on = graphview.learn_message(cfg, "graph_laplacian", 2, "", grill=True)
        off = graphview.learn_message(cfg, "graph_laplacian", 2, "")
        self.assertIn("`grill-me` skill", on)
        self.assertNotIn("grill-me", off)
        self.assertIn(learn.GRILL, graphview.learn_message(cfg, "some topic in words", 1, "", grill=True))


if __name__ == "__main__":
    unittest.main()
