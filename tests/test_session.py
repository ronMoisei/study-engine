"""Concepts from a teaching session -> one Inbox proposal per session (deterministic, no model call)."""
import unittest

from engine import graph as G
from engine import ingest, session
from tests.test_ingest import make_env


def topics_list(cfg):
    from engine import topics
    return topics.load_all(cfg)


def payload(extra=None):
    concepts = [
        {"title": "Fiedler vector", "kind": "definition", "topics": ["graph-theory/spectral"],
         "definition": "The eigenvector of the second smallest eigenvalue of the Laplacian $L$.",
         "propositions": ["Its sign pattern splits the vertices in two."],
         "requires": ["Graph Laplacian", "something never taught"]},
        {"title": "graph laplacian", "kind": "definition", "topics": ["graph-theory/spectral"],      # existing concept: extends it
         "definition": "A brand new statement about L that the note does not hold yet."},
        {"title": "Degree Matrix", "kind": "definition", "topics": ["graph-theory/spectral"], "definition": "D = diag(d)."},   # read-only
    ]
    return {"session": "2026-11-16_spectral", "concepts": concepts + (extra or [])}


class SessionProposals(unittest.TestCase):
    def setUp(self):
        from engine import topics
        self.cfg, *_ = make_env()
        topics.write_all(self.cfg, [{"macro": "graph-theory", "macro_title": "Graph theory", "sub": "spectral",
                                     "title": "Spectral graph theory"}])

    def test_topics_are_required_and_checked(self):
        bad = payload()
        bad["concepts"][0]["topics"] = ["graph-theory/unknown"]
        with self.assertRaises(session.SessionError) as e:
            session.propose(self.cfg, bad)
        self.assertIn("unknown: graph-theory/unknown", str(e.exception))
        bad["new_topics"] = [{"macro": "graph-theory", "macro_title": "Graph theory", "sub": "unknown", "title": "New"}]
        p = session.propose(self.cfg, bad)                                    # declared: accepted, added on approval
        self.assertEqual([t["sub"] for t in p["new_topics"]], ["unknown"])
        ingest.apply(self.cfg, p["id"])
        self.assertIn("graph-theory/unknown", {t["slug"] for t in topics_list(self.cfg)})

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
        self.assertEqual(s["written"], ["fiedler_vector"])
        self.assertEqual(s["extended"], ["graph_laplacian"])
        g = G.Graph.load(self.cfg)
        self.assertIn("graph_laplacian", g.requires("fiedler_vector"))

    def test_rerun_keeps_decisions_and_grows(self):
        p = session.propose(self.cfg, payload())
        ingest.apply(self.cfg, p["id"], reject=[0])                          # learner rejects Fiedler vector
        extra = [{"title": "Spectral gap", "kind": "definition", "topics": ["graph-theory/spectral"],
                  "definition": "$\\lambda_2 - \\lambda_1$.",
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
                "concepts": [{"title": "Gini impurity", "kind": "definition", "topics": ["graph-theory/spectral"],
                              "definition": "One minus the sum of squared class proportions of a node."}]}
        p = session.propose(self.cfg, data)
        ingest.apply(self.cfg, p["id"])
        f = self.cfg.graph / "gini_impurity.md"
        text = f.read_text(encoding="utf-8")
        self.assertNotIn("\ncourse:", text)                       # notes carry no course (2026-11-20): topics only
        self.assertEqual(notes.parse_concept(text)["topics"], ["graph-theory/spectral"])
        # a note whose source lies in a course folder still knows that course (derived, never stored)
        old = notes.render_concept({"id": "wardrop", "title": "Wardrop equilibrium", "kind": "definition",
                                    "domain": "hw1-tutorial", "topic": "traffic", "aliases": [],
                                    "definition": "Equal travel time on all used paths.", "propositions": [],
                                    "requires": [], "related": [],
                                    "sources": ["01VSCWS - Data science and machine learning/HW1/hw1.pdf#page=4"]})
        (self.cfg.graph / "hw1-tutorial").mkdir()
        (self.cfg.graph / "hw1-tutorial" / "wardrop.md").write_text(old, encoding="utf-8")
        from engine import graph as G
        self.assertEqual(G.Graph.load(self.cfg).nodes["wardrop"].course, "01VSCWS - Data science and machine learning")
        g = graphview.graph_payload(self.cfg)["groups"]                  # the graph groups by macro topic
        self.assertEqual((g["hw1-tutorial"], g["graph-theory"]), (1, 1))
        self.assertNotIn("01VSCWS - Data science and machine learning", g)

    def test_invalid_input(self):
        with self.assertRaises(session.SessionError):
            session.propose(self.cfg, {"session": "x", "domain": "d", "concepts": [{"title": "T", "kind": "blob",
                                                                                    "definition": "x"}]})
        with self.assertRaises(session.SessionError):
            session.propose(self.cfg, {"session": "x", "concepts": [{"title": "T", "definition": "x"}]})   # no domain


if __name__ == "__main__":
    unittest.main()
