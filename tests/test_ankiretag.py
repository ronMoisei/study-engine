"""Existing Anki cards brought in line with the graph: lh:: (merges followed) + the node's topics only."""
import json
import unittest

from engine import ankiretag, notes
from tests.test_ingest import CONCEPT, make_env


class FakeAnki:
    def __init__(self, notes_):
        self.notes = {n["noteId"]: n for n in notes_}

    def call(self, action, **kw):
        if action == "findNotes":
            return list(self.notes)
        if action == "notesInfo":
            return [self.notes[i] for i in kw["notes"]]
        if action == "multi":
            for a in kw["actions"]:
                self.notes[a["params"]["note"]]["tags"] = a["params"]["tags"]
            return [None] * len(kw["actions"])


class Retag(unittest.TestCase):
    def test_lh_follows_merges_topics_from_the_note_dag_tags_removed(self):
        cfg, *_ = make_env()
        (cfg.graph / "graph_laplacian.md").write_text(
            notes.render_concept({**CONCEPT, "topics": ["graph-theory/spectral"]}), encoding="utf-8")
        for f in cfg.graph.glob("*/graph_laplacian.md"):
            f.unlink()
        d = cfg.shared_dir / "migrate"
        d.mkdir(parents=True)
        (d / "redirects.json").write_text(json.dumps({"laplacian_matrix": "graph_laplacian"}), encoding="utf-8")
        anki = FakeAnki([
            {"noteId": 1, "tags": ["lh::laplacian_matrix", "node::laplacian", "req::degree-matrix", "remedial",
                                   "gap::laplacian_matrix", "leech"]},
            {"noteId": 2, "tags": ["lh::graph_laplacian", "graph-theory::spectral"]},
            {"noteId": 3, "tags": ["chunking"]},
            {"noteId": 4, "tags": ["lh::gone_node"]}])
        r = ankiretag.retag(cfg, anki, dry_run=True)
        self.assertEqual((r["changed"], r["unchanged"]), (1, 1))
        self.assertEqual(anki.notes[1]["tags"][0], "lh::laplacian_matrix")          # dry run: nothing written
        ankiretag.retag(cfg, anki)
        self.assertEqual(anki.notes[1]["tags"], ["lh::graph_laplacian", "graph-theory::spectral", "remedial", "leech",
                                                 "gap::graph_laplacian"])
        self.assertEqual(anki.notes[3]["tags"], ["chunking"])                         # no lh:: -> left alone
        self.assertEqual(r["orphans"], ["gone_node"])


if __name__ == "__main__":
    unittest.main()
