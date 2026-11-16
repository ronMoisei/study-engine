"""Concepts from a teaching session -> one Inbox proposal per session (deterministic, no model call)."""
import unittest

from engine import graph as G
from engine import ingest, session
from tests.test_ingest import make_env


def payload(extra=None):
    concepts = [
        {"title": "Fiedler vector", "kind": "definition", "topic": "spectral clustering",
         "definition": "The eigenvector of the second smallest eigenvalue of the Laplacian $L$.",
         "propositions": ["Its sign pattern splits the vertices in two."],
         "requires": ["Graph Laplacian", "something never taught"]},
        {"title": "graph laplacian", "kind": "definition",                   # existing concept: extends it
         "definition": "A brand new statement about L that the note does not hold yet."},
        {"title": "Degree Matrix", "kind": "definition", "definition": "D = diag(d)."},   # read-only course note
    ]
    return {"session": "2026-11-16_spectral", "domain": "spectral", "concepts": concepts + (extra or [])}


class SessionProposals(unittest.TestCase):
    def setUp(self):
        self.cfg, *_ = make_env()

    def test_identity_links_and_approval(self):
        p = session.propose(self.cfg, payload())
        by = {it["concept"]["title"]: it for it in p["items"]}
        self.assertEqual(p["id"], "session-2026_11_16_spectral")
        self.assertEqual(by["Fiedler vector"]["decision"], "new")
        self.assertEqual(by["Fiedler vector"]["slug"], "fiedler_vector")
        self.assertEqual([e["to"] for e in by["Fiedler vector"]["edges"]], ["graph_laplacian"])
        self.assertTrue(all(e["accepted"] for e in by["Fiedler vector"]["edges"]))   # the Inbox pre-ticks them
        self.assertEqual(by["Fiedler vector"]["dropped_edges"][0]["reason"], "unknown concept")
        self.assertEqual((by["graph laplacian"]["decision"], by["graph laplacian"]["match"]), ("extends", "graph_laplacian"))
        self.assertEqual(by["Degree Matrix"]["decision"], "covered")
        s = ingest.apply(self.cfg, p["id"])
        self.assertEqual(s["written"], ["spectral/fiedler_vector"])
        self.assertEqual(s["extended"], ["graph_laplacian"])
        g = G.Graph.load(self.cfg)
        self.assertIn("graph_laplacian", g.requires("fiedler_vector"))

    def test_rerun_keeps_decisions_and_grows(self):
        p = session.propose(self.cfg, payload())
        ingest.apply(self.cfg, p["id"], reject=[0])                          # learner rejects Fiedler vector
        extra = [{"title": "Spectral gap", "kind": "definition", "definition": "$\\lambda_2 - \\lambda_1$.",
                  "requires": ["Fiedler vector"]}]
        p2 = session.propose(self.cfg, payload(extra))
        by = {it["concept"]["title"]: it for it in p2["items"]}
        self.assertEqual(p2["id"], p["id"])                                  # same proposal, updated
        self.assertEqual(by["Fiedler vector"]["status"], "rejected")
        self.assertEqual(by["graph laplacian"]["status"], "approved")
        self.assertEqual(by["Spectral gap"]["status"], "pending")
        self.assertEqual(len(ingest.list_proposals(self.cfg)), 1)

    def test_course_graph(self):
        from engine import graphview, notes
        (self.cfg.material / "01VSCWS - Data science and machine learning").mkdir()
        data = {"session": "s", "course": "Data science and machine",                 # a unique part is enough
                "concepts": [{"title": "Gini impurity", "kind": "definition",
                              "definition": "One minus the sum of squared class proportions of a node."}]}
        p = session.propose(self.cfg, data)
        self.assertEqual(p["domain"], "data-science-and-machine-learning")
        ingest.apply(self.cfg, p["id"])
        f = self.cfg.graph / "data-science-and-machine-learning" / "gini_impurity.md"
        self.assertEqual(notes.parse_concept(f.read_text(encoding="utf-8"))["course"],
                         "01VSCWS - Data science and machine learning")
        groups = graphview.graph_payload(self.cfg, group="01VSCWS - Data science and machine learning")
        self.assertEqual([n["id"] for n in groups["nodes"]], ["gini_impurity"])     # a course graph, created
        # an older note without the field still lands in its course through its source path
        old = notes.render_concept({"id": "wardrop", "title": "Wardrop equilibrium", "kind": "definition",
                                    "domain": "hw1-tutorial", "topic": "traffic", "aliases": [],
                                    "definition": "Equal travel time on all used paths.", "propositions": [],
                                    "requires": [], "related": [],
                                    "sources": ["01VSCWS - Data science and machine learning/HW1/hw1.pdf#page=4"]})
        (self.cfg.graph / "hw1-tutorial").mkdir()
        (self.cfg.graph / "hw1-tutorial" / "wardrop.md").write_text(old, encoding="utf-8")
        g = graphview.graph_payload(self.cfg)["groups"]
        self.assertEqual(g["01VSCWS - Data science and machine learning"], 2)
        self.assertNotIn("hw1-tutorial", g)

    def test_invalid_input(self):
        with self.assertRaises(session.SessionError):
            session.propose(self.cfg, {"session": "x", "domain": "d", "concepts": [{"title": "T", "kind": "blob",
                                                                                    "definition": "x"}]})
        with self.assertRaises(session.SessionError):
            session.propose(self.cfg, {"session": "x", "concepts": [{"title": "T", "definition": "x"}]})   # no domain


if __name__ == "__main__":
    unittest.main()
