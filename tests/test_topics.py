"""The shared topic list Graph/_topics.md: macro topics (graph colours) and their subtopics, in teaching order."""
import unittest

from engine import topics
from engine import graph as G
from tests.test_ingest import make_env


class TopicList(unittest.TestCase):
    def test_write_load_and_add_keep_each_subtopic_with_its_macro(self):
        cfg, *_ = make_env()
        topics.write_all(cfg, [
            {"macro": "optimization", "macro_title": "Optimization", "sub": "first-order", "title": "First-order"},
            {"macro": "graph-theory", "macro_title": "Graph theory", "sub": "spectral", "title": "Spectral"}])
        added = topics.add_all(cfg, [
            {"macro": "optimization", "macro_title": "Optimization", "sub": "second-order", "title": "Second-order"},
            {"macro": "optimization", "macro_title": "Optimization", "sub": "first-order", "title": "dup"},
            {"macro": "game-theory", "macro_title": "Game theory", "sub": "equilibria", "title": "Equilibria"}])
        self.assertEqual([t["slug"] for t in added], ["optimization/second-order", "game-theory/equilibria"])
        self.assertEqual([t["slug"] for t in topics.load_all(cfg)],
                         ["optimization/first-order", "optimization/second-order", "graph-theory/spectral",
                          "game-theory/equilibria"])
        self.assertEqual(topics.macros(topics.load_all(cfg))[0], ("optimization", "Optimization"))
        text = topics.all_path(cfg).read_text(encoding="utf-8")
        self.assertIn("## Graph theory `graph-theory`", text)
        self.assertNotIn("_topics", [n for n in G.Graph.load(cfg).nodes])          # never loaded as a note


if __name__ == "__main__":
    unittest.main()
