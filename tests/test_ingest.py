"""Ingest pipeline offline: temp vault, stub model, bag-of-words embeddings. No Ollama, no agy, no real vault."""
import tempfile
import unittest
from pathlib import Path

from engine import graph as G
from engine import ingest, material
from engine.config import Config
from engine.embed import FakeEmbedder
from engine.llm import Router
from engine.store import Store

GRAPH_NOTE = """---
id: graph_laplacian
type: proposition
kind: definition
domain: spectral
epistemic_status: definition
sources:
  - "old.pdf#page=1"
aliases: []
---
# Graph Laplacian

The graph Laplacian equals degree matrix minus adjacency matrix.
"""

ATOMIC_NOTE = """---
id: X.01.act1.degree_matrix
title: "Degree Matrix"
aliases:
  - "Diagonal degree matrix"
type: primitive
macro_topic: "graphs"
prerequisites:
 []
implications:
  - "[[graph_laplacian]]"
contrasts_with: []
easy_definition: |
  The diagonal matrix whose entries are the vertex degrees
  of the graph.
easy_pitfall: |
  Confusing it with adjacency.
---
## Core Statement
body
"""

MATERIAL = """The graph Laplacian equals degree matrix minus adjacency matrix.

The Fiedler value is the second smallest eigenvalue of the Graph Laplacian.

Course logistics.

The Fiedler value is the second smallest eigenvalue of the Graph Laplacian.

Because the Fiedler value is positive exactly for connected graphs it measures connectivity.
"""


def make_env():
    tmp = Path(tempfile.mkdtemp())
    vault = tmp / "Obsidian Notes"
    (vault / "Graph" / "spectral").mkdir(parents=True)
    (vault / "Graph" / "spectral" / "graph_laplacian.md").write_text(GRAPH_NOTE, encoding="utf-8")
    (vault / "Course" / "atomic").mkdir(parents=True)
    (vault / "Course" / "atomic" / "degree_matrix.md").write_text(ATOMIC_NOTE, encoding="utf-8")
    (tmp / "Material").mkdir()
    src = tmp / "Material" / "lecture.md"
    src.write_text(MATERIAL, encoding="utf-8")
    cfg = Config(magistrale=tmp, provider="stub", db_file=str(tmp / "e.sqlite3"), sim_floor=0.1, passage_chars=5000)
    store = Store(cfg.db_path)
    return cfg, store, Router(cfg, store), FakeEmbedder(), src


class Index(unittest.TestCase):
    def test_loads_graph_and_atomic_notes(self):
        cfg, *_ = make_env()
        g = G.Graph.load(cfg)
        self.assertEqual(sorted(g.nodes), ["degree_matrix", "graph_laplacian"])
        d = g.nodes["degree_matrix"]
        self.assertTrue(d.readonly)
        self.assertEqual(d.statement, "The diagonal matrix whose entries are the vertex degrees of the graph.")
        self.assertEqual(d.aliases, ["Diagonal degree matrix"])
        # "implications: [[graph_laplacian]]" on the atomic note means graph_laplacian requires degree_matrix
        self.assertIn(("requires", "degree_matrix"), g.nodes["graph_laplacian"].edges)
        self.assertEqual(g.study_order(["graph_laplacian"])[0], ["degree_matrix", "graph_laplacian"])

    def test_loop_detection_includes_uncommitted_nodes(self):
        g = G.Graph({})
        extra = {"a": ["b"]}                       # new a requires new b (b has no dependencies of its own)
        self.assertTrue(g.would_cycle("b", "a", extra))
        self.assertFalse(g.would_cycle("c", "a", extra))
        self.assertTrue(g.would_cycle("a", "a", {}))

    def test_slugs(self):
        self.assertEqual(G.slugify("Numero di condizionamento κ(A) è"), "numero_di_condizionamento_a_e")
        self.assertEqual(G.unique_slug("Graph Laplacian", {"graph_laplacian"}), "graph_laplacian_2")


class Material(unittest.TestCase):
    def test_text_passages_have_line_anchors(self):
        cfg, _, _, _, src = make_env()
        ps, info = material.parse(src, 120, root=cfg.magistrale / "Material")
        self.assertEqual(info["name"], "lecture.md")
        self.assertTrue(all(p.anchor.startswith("lecture.md#L") for p in ps))
        self.assertGreater(len(ps), 1)

    def test_unsupported_type(self):
        cfg, *_ = make_env()
        f = cfg.magistrale / "x.docx"
        f.write_text("x")
        with self.assertRaises(material.MaterialError):
            material.parse(f)


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.cfg, self.store, self.llm, self.emb, self.src = make_env()
        self.p = ingest.ingest(self.cfg, self.store, self.llm, self.emb, self.src, "Spectral")

    def by_decision(self, d):
        return [it for it in self.p["items"] if it["decision"] == d]

    def test_decisions(self):
        self.assertEqual(len(self.by_decision("existing")), 1)
        self.assertEqual(self.by_decision("existing")[0]["match"], "graph_laplacian")
        self.assertEqual(len(self.by_decision("duplicate")), 1)
        new = self.by_decision("new")
        self.assertEqual([it["slug"] for it in new], ["the_fiedler_value_is", "because_the_fiedler_value"])
        self.assertEqual(self.p["domain"], "spectral")
        self.assertEqual(self.p["stats"]["new"], 2)

    def test_nothing_written_before_approval(self):
        self.assertEqual(sorted(p.name for p in self.cfg.graph.rglob("*.md")), ["graph_laplacian.md"])

    def test_edges_and_anchors(self):
        fiedler, conn = self.by_decision("new")
        self.assertIn({"predicate": "requires", "to": "graph_laplacian"}, fiedler["edges"])
        self.assertIn({"predicate": "requires", "to": "the_fiedler_value_is"}, conn["edges"])
        self.assertTrue(fiedler["claim"]["anchor"].startswith("lecture.md#L"))
        self.assertEqual(len(fiedler["also_at"]), 1)            # the repeated statement

    def test_extraction_is_cached(self):
        before = self.store.db.execute("SELECT count(*) FROM llm_calls WHERE job='extract'").fetchone()[0]
        ingest.ingest(self.cfg, self.store, self.llm, self.emb, self.src, "Spectral")
        after = self.store.db.execute("SELECT count(*) FROM llm_calls WHERE job='extract'").fetchone()[0]
        self.assertEqual(before, after)

    def test_same_input_same_proposal(self):
        again = ingest.ingest(self.cfg, self.store, self.llm, self.emb, self.src, "Spectral")
        strip = lambda p: [(i["decision"], i["match"], i["slug"], i["edges"]) for i in p["items"]]  # noqa: E731
        self.assertEqual(strip(self.p), strip(again))

    def test_apply_writes_notes_and_plan(self):
        new = self.by_decision("new")
        summary = ingest.apply(self.cfg, self.p["id"], reject=[new[1]["index"]])
        self.assertEqual(summary["written"], ["spectral/the_fiedler_value_is"])
        self.assertEqual(summary["sources_added"], ["graph_laplacian"])
        note = (self.cfg.graph / "spectral" / "the_fiedler_value_is.md").read_text(encoding="utf-8")
        self.assertIn("id: the_fiedler_value_is", note)
        self.assertIn("- requires:: [[graph_laplacian]]", note)
        self.assertEqual(note.count("lecture.md#L"), 2)         # both places it was stated
        old = (self.cfg.graph / "spectral" / "graph_laplacian.md").read_text(encoding="utf-8")
        self.assertIn("old.pdf#page=1", old)
        self.assertIn("lecture.md#L", old)
        g = G.Graph.load(self.cfg)
        self.assertIn(("requires", "graph_laplacian"), g.nodes["the_fiedler_value_is"].edges)
        self.assertFalse((self.cfg.graph / "spectral" / "because_the_fiedler_value.md").exists())
        self.assertEqual(ingest.load(self.cfg, self.p["id"])["status"], "done")

        plan = ingest.build_plan(self.cfg, self.p["id"])
        self.assertEqual([s["id"] for s in plan["steps"]], ["degree_matrix", "graph_laplacian", "the_fiedler_value_is"])
        self.assertFalse(plan["steps"][0]["in_material"])       # pulled in as a prerequisite
        text = Path(plan["file"]).read_text(encoding="utf-8")
        self.assertIn("graph_laplacian --> the_fiedler_value_is", text)
        self.assertIn("phase: probe", text)

    def test_edge_to_rejected_node_is_dropped(self):
        new = self.by_decision("new")
        summary = ingest.apply(self.cfg, self.p["id"], accept=[new[1]["index"]])
        self.assertEqual(summary["dropped_edges"][0]["to"], "the_fiedler_value_is")
        note = (self.cfg.graph / "spectral" / "because_the_fiedler_value.md").read_text(encoding="utf-8")
        self.assertNotIn("the_fiedler_value_is]]", note)


class Guards(unittest.TestCase):
    def run_with(self, overrides):
        cfg, store, llm, emb, src = make_env()

        class Patched:
            name = "patched"

            def run(self, job, prompt, schema, session="", retries=1):
                if job in overrides:
                    return overrides[job](prompt)
                return llm.run(job, prompt, schema, session=session)
        return cfg, ingest.ingest(cfg, store, Patched(), emb, src, "spectral")

    def test_claim_with_invented_quote_is_dropped(self):
        claims = {"claims": [
            {"name": "Real", "statement": "The Fiedler value is the second smallest eigenvalue.", "kind": "definition",
             "quote": "The Fiedler value is the second smallest eigenvalue"},
            {"name": "Invented", "statement": "Cheeger's inequality bounds the conductance.", "kind": "theorem",
             "quote": "Cheeger's inequality bounds the conductance of every cut"}]}
        _, p = self.run_with({"extract": lambda _: claims})
        self.assertEqual([it["claim"]["name"] for it in p["items"]], ["Real"])
        self.assertEqual(p["stats"]["dropped_unquoted"], 1)

    def test_bad_edges_are_rejected_by_code(self):
        def link(prompt_text):
            new = __import__("json").loads(prompt_text.split("INPUT_JSON:", 1)[1])["new"][0]["id"]
            return {"edges": [
                {"from": new, "to": "graph_laplacian", "predicate": "requires"},
                {"from": "graph_laplacian", "to": new, "predicate": "requires"},          # closes a loop
                {"from": new, "to": "no_such_node", "predicate": "requires"},
                {"from": "graph_laplacian", "to": "degree_matrix", "predicate": "requires"}]}
        _, p = self.run_with({"link": link})
        item = next(it for it in p["items"] if it["decision"] == "new")
        self.assertEqual(item["edges"], [{"predicate": "requires", "to": "graph_laplacian"}])
        reasons = sorted(d["reason"] for d in item["dropped_edges"])
        self.assertEqual(reasons, ["edge does not involve a new node", "unknown node id",
                                   "would create a prerequisite loop"])

    def test_edge_into_readonly_course_note_is_rejected(self):
        def link(prompt_text):
            new = __import__("json").loads(prompt_text.split("INPUT_JSON:", 1)[1])["new"][0]["id"]
            return {"edges": [{"from": "degree_matrix", "to": new, "predicate": "requires"}]}
        _, p = self.run_with({"link": link})
        item = next(it for it in p["items"] if it["decision"] == "new")
        self.assertEqual(item["incoming"], [])
        self.assertEqual(item["dropped_edges"][0]["reason"], "would edit a read-only course note")

    def test_existing_writable_note_can_gain_an_edge(self):
        def link(prompt_text):
            new = __import__("json").loads(prompt_text.split("INPUT_JSON:", 1)[1])["new"][0]["id"]
            return {"edges": [{"from": "graph_laplacian", "to": new, "predicate": "requires"}]}
        cfg, p = self.run_with({"link": link})
        item = next(it for it in p["items"] if it["decision"] == "new")
        self.assertEqual(item["incoming"], [{"from": "graph_laplacian", "predicate": "requires"}])
        summary = ingest.apply(cfg, p["id"], accept=[item["index"]])
        self.assertEqual(summary["edges_added"], [f"graph_laplacian requires {item['slug']}"])
        self.assertIn(f"- requires:: [[{item['slug']}]]",
                      (cfg.graph / "spectral" / "graph_laplacian.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class Api(unittest.TestCase):
    def test_ingest_job_proposal_apply_plan_over_http(self):
        import json
        import threading
        import time
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer

        from engine.server import Engine, make_handler
        cfg, store, llm, emb, src = make_env()
        engine = Engine(cfg, anki=object(), llm=llm)
        engine.embedder = emb
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

        self.assertEqual(req("POST", "/ingest", {"path": "missing.pdf", "domain": "x"})[0], 400)
        self.assertEqual(req("POST", "/ingest", {"path": "lecture.md", "domain": ""})[0], 400)
        st, job = req("POST", "/ingest", {"path": "lecture.md", "domain": "spectral"})   # relative to Material/
        self.assertEqual(st, 200)
        for _ in range(100):
            st, job = req("GET", f"/jobs/{job['id']}")
            if job["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(job["state"], "done", job.get("error"))
        pid = job["proposal"]
        self.assertEqual(req("GET", "/proposals")[1]["proposals"][0]["id"], pid)
        self.assertEqual(req("GET", f"/proposals/{pid}")[1]["stats"]["new"], 2)
        st, summary = req("POST", f"/proposals/{pid}/apply", {})
        self.assertEqual(len(summary["written"]), 2)
        st, plan = req("POST", f"/proposals/{pid}/plan")
        self.assertEqual(plan["steps"][-1]["id"], "because_the_fiedler_value")
        self.assertEqual(req("POST", f"/proposals/{pid}/discard")[0], 200)
        self.assertEqual(req("GET", f"/proposals/{pid}")[0], 404)
        httpd.shutdown()
        httpd.server_close()
