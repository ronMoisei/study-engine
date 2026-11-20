"""Concept ingest offline: temp vault, stub models, bag-of-words embeddings. No Ollama, no agy, no real vault."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from engine import graph as G
from engine import ingest, material, notes, topics
from engine.config import Config
from engine.embed import FakeEmbedder
from engine.llm import Router, repair_latex
from engine.store import Store
from engine.terms import TermIndex, defined_before_use

CONCEPT = {
    "id": "graph_laplacian", "title": "Graph Laplacian", "aliases": ["Laplacian matrix"], "kind": "definition",
    "domain": "spectral", "topic": "graph-matrices",
    "definition": "The graph Laplacian equals degree matrix minus adjacency matrix.",
    "propositions": [{"text": "Its quadratic form is $x^\\top L x = \\sum_{(u,v)} (x_u - x_v)^2$.",
                      "source": "old.pdf#page=2"}],
    "requires": ["degree_matrix"], "related": [], "sources": ["old.pdf#page=1"]}

ATOMIC_NOTE = """---
id: X.01.act1.degree_matrix
title: "Degree Matrix"
aliases:
  - "Diagonal degree matrix"
type: primitive
macro_topic: "graphs"
math_symbols:
  - 'D = \\mathrm{diag}(d_1, \\dots, d_n)'
prerequisites:
 []
implications:
  - "[[graph_laplacian]]"
contrasts_with: []
easy_definition: |
  The diagonal matrix whose entries are the vertex degrees
  of the graph.
---
## Core Statement
body
"""

# Material: Fiedler claims share the first word -> the stub groups them into one concept.
MATERIAL = """The graph Laplacian equals degree matrix minus adjacency matrix.

Fiedler value is the second smallest eigenvalue of the Graph Laplacian.

Course logistics.

Fiedler value is positive exactly for connected graphs so it measures connectivity.

Spectral clustering partitions vertices using the Fiedler value of the graph.
"""


def make_env(material_text=MATERIAL):
    tmp = Path(tempfile.mkdtemp())
    vault = tmp                                          # Magistrale is the vault
    (vault / "Graph" / "spectral").mkdir(parents=True)
    (vault / "Graph" / "spectral" / "graph_laplacian.md").write_text(notes.render_concept(CONCEPT), encoding="utf-8")
    (tmp / "Material" / "Course" / "Atomic Notes").mkdir(parents=True)
    (tmp / "Material" / "Course" / "Atomic Notes" / "degree_matrix.md").write_text(ATOMIC_NOTE, encoding="utf-8")
    src = tmp / "Material" / "lecture.md"
    src.write_text(material_text, encoding="utf-8")
    cfg = Config(magistrale=tmp, provider="stub", db_file=str(tmp / "e.sqlite3"), sim_floor=0.1, passage_chars=5000,
                 ingest_method="v2")                     # the extract/group pipeline these tests were written for
    store = Store(cfg.db_path)
    return cfg, store, Router(cfg, store), FakeEmbedder(), src


class Format(unittest.TestCase):
    def test_render_parse_validate_roundtrip(self):
        text = notes.render_concept(CONCEPT)
        self.assertEqual(notes.validate_concept(text, "graph_laplacian", {"degree_matrix"}), [])
        back = notes.parse_concept(text)
        for k in ("id", "title", "aliases", "kind", "topic", "definition", "requires", "sources"):
            self.assertEqual(back[k], CONCEPT[k], k)
        self.assertEqual(back["propositions"], CONCEPT["propositions"])          # LaTeX backslashes survive

    def test_typed_notes_round_trip(self):
        algo = {**CONCEPT, "id": "power_iteration", "title": "Power iteration", "kind": "algorithm",
                "topics": ["linear-algebra/eigenvalues"], "requires": ["graph_laplacian"],
                "related": [("contrasts_with", "graph_laplacian")],
                "pseudocode": "x = random()\n# normalise every step\nrepeat: x = A x / |A x|"}
        text = notes.render_concept(algo)
        self.assertEqual(notes.validate_concept(text, "power_iteration"), [])
        self.assertIn("topics: [\"linear-algebra/eigenvalues\"]", text)
        self.assertIn("```\nx = random()\n# normalise every step", text)       # a # comment is not a heading
        back = notes.parse_concept(text)
        self.assertEqual((back["kind"], back["topics"], back["requires"], back["related"]),
                         ("algorithm", ["linear-algebra/eigenvalues"], ["graph_laplacian"],
                          [("contrasts_with", "graph_laplacian")]))
        self.assertEqual((back["domain"], back["topic"]), ("linear-algebra", "eigenvalues"))
        self.assertEqual(notes.render_concept(back), text)
        thm = {**algo, "id": "t", "kind": "theorem", "proof": "By induction on k."}
        self.assertEqual(notes.parse_concept(notes.render_concept(thm))["proof"], "By induction on k.")
        pit = {**algo, "id": "p", "kind": "pitfall", "pseudocode": ""}
        self.assertEqual(notes.validate_concept(notes.render_concept(pit), "p"), [])

    def test_v2_notes_are_still_read(self):
        v2 = ("---\nid: a\ntype: concept\ntitle: \"A\"\naliases: []\nkind: definition\ndomain: spectral\n"
              "topic: graph-matrices\ncourse: \"01ABCDE - X\"\nepistemic_status: definition\nsources:\n  - \"s\"\n---\n"
              "# A\n\nA is the first thing we define.\n\n## Propositions\n*none*\n\n## Requires\n"
              "- requires:: [[b]]\n\n## Prerequisite of\n- prerequisite_of:: [[c]]\n\n## Related\n"
              "- contrasts_with:: [[d]]\n")
        c = notes.parse_concept(v2)
        self.assertEqual((c["topics"], c["requires"], c["prerequisite_of"], c["related"], c["course"]),
                         (["spectral/graph-matrices"], ["b"], ["c"], [("contrasts_with", "d")], "01ABCDE - X"))
        self.assertEqual(notes.validate_concept(notes.render_concept(c), "a"), [])     # rewritten in the new format

    def test_validator_rejects_malformed_notes(self):
        good = notes.render_concept(CONCEPT)
        cases = {
            "file name 'x'": (good, "x", None),
            "kind": (good.replace("kind: definition", "kind: blob"), "graph_laplacian", None),
            "sections": (good.replace("## Propositions", "## See also"), "graph_laplacian", None),
            "malformed item": (good.replace('"[[degree_matrix]]"', '"needs degree_matrix"'), "graph_laplacian", None),
            "macro/sub": (good.replace('"spectral/graph-matrices"', '"graph-matrices"'), "graph_laplacian", None),
            "pseudocode": (good.replace("kind: definition", "kind: algorithm").replace(
                "## Propositions", "## Pseudocode\nrepeat: step\n\n## Propositions"), "graph_laplacian", None),
            "pitfall of": (good.replace("kind: definition", "kind: pitfall").replace(
                'prerequisites: ["[[degree_matrix]]"]', "prerequisites: []"), "graph_laplacian", None),
            "does not resolve": (good, "graph_laplacian", set()),
            "H1": (good.replace("# Graph Laplacian", "# Laplacian"), "graph_laplacian", None),
            "falsification": (good.replace("epistemic_status: definition", "epistemic_status: unverified"),
                              "graph_laplacian", None),
            "unexpected heading": (good + "\n### Extra\n", "graph_laplacian", None),
        }
        for want, (text, stem, known) in cases.items():
            errs = notes.validate_concept(text, stem, known)
            self.assertTrue(any(want in e for e in errs), f"{want}: {errs}")


class Index(unittest.TestCase):
    def test_loads_concepts_and_atomic_notes(self):
        cfg, *_ = make_env()
        g = G.Graph.load(cfg)
        self.assertEqual(sorted(g.nodes), ["degree_matrix", "graph_laplacian"])
        lap, deg = g.nodes["graph_laplacian"], g.nodes["degree_matrix"]
        self.assertTrue(lap.is_concept and deg.readonly)
        self.assertEqual(lap.pos, ("old.pdf", (1,)))
        self.assertEqual(deg.pos, ("X", (1, 1)))
        self.assertEqual(deg.symbols, ["D = \\mathrm{diag}(d_1, \\dots, d_n)"])
        self.assertEqual(g.study_order(["graph_laplacian"])[0], ["degree_matrix", "graph_laplacian"])

    def test_order_conflict_and_shortcuts(self):
        a = G.Node("a", "A", "x", pos=("f", (5,)))
        b = G.Node("b", "B", "x", pos=("f", (9,)))
        c = G.Node("c", "C", "x", pos=("g", (1,)), edges=[("requires", "a")])
        g = G.Graph({"a": a, "b": b, "c": c})
        self.assertTrue(g.order_conflict("a", "b"))        # a requires b, but b appears later in file f
        self.assertFalse(g.order_conflict("b", "a"))
        self.assertFalse(g.order_conflict("c", "b"))       # different sequences: no opinion
        self.assertTrue(g.transitive_redundant("x", "a", {"x": ["c", "a"]}))   # x->c->a makes x->a a shortcut

    def test_loop_detection_includes_uncommitted_nodes(self):
        g = G.Graph({})
        self.assertTrue(g.would_cycle("b", "a", {"a": ["b"]}))
        self.assertFalse(g.would_cycle("c", "a", {"a": ["b"]}))

    def test_defined_before_use(self):
        cfg, *_ = make_env()
        g = G.Graph.load(cfg)
        ix = TermIndex(g)
        self.assertEqual(ix.uses("graph_laplacian", "degree_matrix"), ["degree matrix"])
        w = defined_before_use(["graph_laplacian", "degree_matrix"], ix, set(g.nodes))
        self.assertEqual([(x["id"], x["uses"], x["problem"]) for x in w],
                         [("graph_laplacian", "degree_matrix", "taught later")])

    def test_topics_file(self):
        cfg, *_ = make_env()
        topics.append(cfg, "spectral", [{"slug": "graph-matrices", "title": "Graph matrices"}])
        topics.append(cfg, "spectral", [{"slug": "clustering", "title": "Clustering"},
                                        {"slug": "graph-matrices", "title": "dup"}])
        self.assertEqual([t["slug"] for t in topics.load(cfg, "spectral")], ["graph-matrices", "clustering"])
        self.assertNotIn("_topics", G.Graph.load(cfg).nodes)


class Latex(unittest.TestCase):
    def test_repairs_json_escapes_eaten_from_latex(self):
        broken = "A $A \\in \\mathbb{R}^{n \times n}$, $\frac{a}{b}$, $\beta$, $\rho$, $\nabla f$\nnext line"
        fixed = repair_latex(broken)
        self.assertIn("\\times", fixed)
        self.assertIn("\\frac{a}{b}", fixed)
        self.assertIn("\\beta", fixed)
        self.assertIn("\\rho", fixed)
        self.assertIn("\\nabla f", fixed)
        self.assertIn("$\nnext line", fixed)                 # a real newline outside math stays

    def test_untouched_when_clean(self):
        s = "Plain text with a\ttab and no math."
        self.assertEqual(repair_latex(s), s)

    def test_doubled_backslashes_collapse_inside_math_only(self):
        s = "potential $\\\\sum_{e \\\\in E} \\\\int_0^{f} \\\\, ds$ and $$a \\\\ b$$ outside \\\\x"
        out = repair_latex(s)
        self.assertIn("$\\sum_{e \\in E} \\int_0^{f} \\, ds$", out)
        self.assertIn("a \\\\ b", out)                      # a real line break (before a space) stays
        self.assertTrue(out.endswith("outside \\\\x"))      # nothing outside math changes


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.cfg, self.store, self.llm, self.emb, self.src = make_env()
        self.p = ingest.ingest(self.cfg, self.store, self.llm, self.emb, self.src, "Spectral")

    def items(self, decision):
        return [it for it in self.p["items"] if it["decision"] == decision]

    def test_concepts_and_decisions(self):
        self.assertEqual(self.p["stats"]["concepts"], 3)
        self.assertEqual([it["match"] for it in self.items("covered")], ["graph_laplacian"])
        new = self.items("new")
        self.assertEqual([it["slug"] for it in new], ["fiedler_value_is_the", "spectral_clustering_partitions_vertices"])
        fiedler = new[0]["concept"]
        self.assertEqual(len(fiedler["propositions"]), 1)          # two claims grouped into one concept
        self.assertEqual(fiedler["topic"], "general")
        self.assertEqual(self.p["new_topics"], [{"slug": "general", "title": "General"}])

    def test_links_with_evidence_and_shortcut_removal(self):
        fiedler, clustering = self.items("new")
        self.assertEqual([(e["to"], e["confidence"]) for e in fiedler["edges"]], [("graph_laplacian", "confident")])
        self.assertIn("definition uses 'graph laplacian'", fiedler["edges"][0]["evidence"])
        # clustering -> fiedler is kept; clustering -> laplacian would be a shortcut
        self.assertEqual([e["to"] for e in clustering["edges"]], ["fiedler_value_is_the"])

    def test_nothing_written_before_approval(self):
        self.assertEqual(sorted(p.name for p in self.cfg.graph.rglob("*.md")), ["graph_laplacian.md"])

    def test_cached_and_repeatable(self):
        again = ingest.ingest(self.cfg, self.store, self.llm, self.emb, self.src, "spectral")
        strip = lambda p: [(i["decision"], i["match"], i["slug"], i["edges"]) for i in p["items"]]  # noqa: E731
        self.assertEqual(strip(self.p), strip(again))
        n = lambda job: self.store.db.execute(f"SELECT count(*) FROM llm_calls WHERE job='{job}'").fetchone()[0]  # noqa: E731
        self.assertEqual((n("extract"), n("group")), (1, 1))

    def test_apply_writes_valid_notes_topics_and_plan(self):
        s = ingest.apply(self.cfg, self.p["id"])
        self.assertEqual(s["errors"], [])
        self.assertEqual(sorted(s["written"]), ["fiedler_value_is_the", "spectral_clustering_partitions_vertices"])
        for f in self.cfg.graph.rglob("*.md"):
            if not f.name.startswith("_"):
                self.assertEqual(notes.validate_concept(f.read_text(encoding="utf-8"), f.stem), [], f.name)
        self.assertEqual([t["slug"] for t in topics.load(self.cfg, "spectral")], ["general"])
        plan = ingest.build_plan(self.cfg, self.p["id"])
        self.assertEqual([s["id"] for s in plan["steps"]],
                         ["degree_matrix", "graph_laplacian", "fiedler_value_is_the", "spectral_clustering_partitions_vertices"])
        self.assertIn("graph_laplacian --> fiedler_value_is_the", Path(plan["file"]).read_text(encoding="utf-8"))

    def test_rejected_links_and_items(self):
        fiedler, clustering = self.items("new")
        s = ingest.apply(self.cfg, self.p["id"], accept=[clustering["index"]])
        self.assertEqual(s["dropped_edges"][0]["reason"], "target not approved")
        note = notes.parse_concept((self.cfg.graph / f"{clustering['slug']}.md").read_text(encoding="utf-8"))
        self.assertEqual(note["requires"], [])
        cfg2, store2, llm2, emb2, src2 = make_env()
        p2 = ingest.ingest(cfg2, store2, llm2, emb2, src2, "spectral")
        f2 = next(it for it in p2["items"] if it["decision"] == "new")
        ingest.apply(cfg2, p2["id"], accept=[f2["index"]], reject_edges=[[f2["slug"], "requires", "graph_laplacian"]])
        note = notes.parse_concept((cfg2.graph / f"{f2['slug']}.md").read_text(encoding="utf-8"))
        self.assertEqual(note["requires"], [])


class Vetoes(unittest.TestCase):
    """Patch the relate answers to force each veto."""

    def run_with(self, relate_answer, material_text=MATERIAL, extra_setup=None):
        cfg, store, llm, emb, src = make_env(material_text)
        if extra_setup:
            extra_setup(cfg)

        class Patched:
            name = "patched"

            def route(self, job):
                return llm.route(job)

            def run(self, job, prompt_text, schema, session="", retries=1):
                if job == "relate":                         # the test's answer, else the stub's
                    data = json.loads(prompt_text.split("INPUT_JSON:", 1)[1])
                    base = {r["pair"]: r["relation"] for r in llm.run(job, prompt_text, schema)["relations"]}
                    return {"relations": [{"pair": p["pair"], "relation": relate_answer(p) or base[p["pair"]]}
                                          for p in data["pairs"]]}
                return llm.run(job, prompt_text, schema, session=session)
        return cfg, ingest.ingest(cfg, store, Patched(), emb, src, "spectral")

    def edges(self, p):
        return {(it["slug"], e["to"]): e for it in p["items"] if it["decision"] == "new" for e in it["edges"]}

    def test_later_prerequisite_is_a_conflict(self):
        # "Fiedler (line 3) requires Spectral clustering (line 7)": the prerequisite appears later -> conflict
        def ans(p):
            return "A_requires_B" if (p["a"]["title"].startswith("Fiedler") and p["b"]["title"].startswith("Spectral")) \
                else "B_requires_A" if (p["b"]["title"].startswith("Fiedler") and p["a"]["title"].startswith("Spectral")) \
                else "none"
        cfg, p = self.run_with(ans)
        e = self.edges(p)[("fiedler_value_is_the", "spectral_clustering_partitions_vertices")]
        self.assertEqual(e["confidence"], "conflict")
        self.assertFalse(e["accepted"])
        s = ingest.apply(cfg, p["id"])
        note = notes.parse_concept((cfg.graph / "fiedler_value_is_the.md").read_text(encoding="utf-8"))
        self.assertEqual(note["requires"], [])                          # conflicts are not written by default
        self.assertEqual(s["errors"], [])

    def test_conflict_written_when_explicitly_accepted(self):
        def ans(p):
            return "A_requires_B" if (p["a"]["title"].startswith("Fiedler") and p["b"]["title"].startswith("Spectral")) \
                else "B_requires_A" if (p["b"]["title"].startswith("Fiedler") and p["a"]["title"].startswith("Spectral")) \
                else "none"
        cfg, p = self.run_with(ans)
        ingest.apply(cfg, p["id"], accept_edges=[["fiedler_value_is_the", "requires",
                                                  "spectral_clustering_partitions_vertices"]])
        note = notes.parse_concept((cfg.graph / "fiedler_value_is_the.md").read_text(encoding="utf-8"))
        self.assertEqual(note["requires"], ["spectral_clustering_partitions_vertices"])

    def test_shortcut_link_is_removed(self):
        # clustering -> fiedler -> laplacian, plus clustering -> laplacian: the last one is implied, so dropped
        rank = {"Spectral": 2, "Fiedler": 1, "Graph": 0}

        def ans(p):
            ra, rb = rank.get(p["a"]["title"].split()[0]), rank.get(p["b"]["title"].split()[0])
            if ra is None or rb is None:
                return None
            return "A_requires_B" if ra > rb else "B_requires_A" if rb > ra else "none"
        cfg, p = self.run_with(ans)
        clustering = next(it for it in p["items"] if it["slug"] == "spectral_clustering_partitions_vertices")
        self.assertEqual([e["to"] for e in clustering["edges"]], ["fiedler_value_is_the"])
        self.assertIn("already implied by a longer prerequisite path",
                      [d["reason"] for d in clustering["dropped_edges"]])

    def test_course_note_link_that_closes_a_loop_is_dropped(self):
        def ans(p):
            pair = {p["a"]["title"], p["b"]["title"]}
            if "Degree Matrix" in pair and any(t.startswith("Fiedler") for t in pair):
                return "B_requires_A" if p["b"]["title"] == "Degree Matrix" else "A_requires_B"
            return None                     # stub: Fiedler requires Laplacian, which requires degree matrix
        _, p = self.run_with(ans)
        fiedler = next(it for it in p["items"] if it["slug"] == "fiedler_value_is_the")
        self.assertEqual(fiedler["required_by"], [])
        self.assertIn("would create a prerequisite loop", [d["reason"] for d in fiedler["dropped_edges"]])

    def test_model_only_link_is_marked(self):
        def ans(p):
            pair = {p["a"]["title"].split()[0], p["b"]["title"].split()[0]}
            if pair == {"Spectral", "Graph"}:
                return "A_requires_B" if p["a"]["title"].startswith("Spectral") else "B_requires_A"
            return None if "The" in pair or "Graph" in pair else "none"
        _, p = self.run_with(ans)
        e = self.edges(p)[("spectral_clustering_partitions_vertices", "graph_laplacian")]
        self.assertEqual((e["confidence"], e["accepted"]), ("model", True))

    def test_course_note_requiring_new_concept_is_stored_in_the_new_note(self):
        def ans(p):
            pair = {p["a"]["title"], p["b"]["title"]}
            if "Degree Matrix" in pair and any(t.startswith("Fiedler") for t in pair):
                return "B_requires_A" if p["b"]["title"] == "Degree Matrix" else "A_requires_B"
            return "none"                  # no Fiedler -> Laplacian -> degree path, or this link would be a loop
        cfg, p = self.run_with(ans)
        fiedler = next(it for it in p["items"] if it["slug"] == "fiedler_value_is_the")
        self.assertEqual([e["from"] for e in fiedler["required_by"]], ["degree_matrix"])
        course_file = cfg.material / "Course" / "Atomic Notes" / "degree_matrix.md"
        before = course_file.read_text(encoding="utf-8")
        ingest.apply(cfg, p["id"])
        self.assertEqual(course_file.read_text(encoding="utf-8"), before)          # course note untouched
        note = notes.parse_concept((cfg.graph / "fiedler_value_is_the.md").read_text(encoding="utf-8"))
        self.assertEqual(note["prerequisite_of"], ["degree_matrix"])
        self.assertIn(("requires", "fiedler_value_is_the"), G.Graph.load(cfg).nodes["degree_matrix"].edges)

    def test_existing_concept_gains_new_propositions(self):
        text = MATERIAL + "\nThe graph Laplacian has the all-ones vector in its kernel for every graph.\n"

        def ans(p):
            return None
        cfg, p = self.run_with(ans, text)
        ext = [it for it in p["items"] if it["decision"] == "extends"]
        self.assertEqual(len(ext), 1)
        self.assertEqual(len(ext[0]["new_propositions"]), 1)
        s = ingest.apply(cfg, p["id"])
        self.assertEqual(s["extended"], ["graph_laplacian"])
        note = notes.parse_concept((cfg.graph / "spectral" / "graph_laplacian.md").read_text(encoding="utf-8"))
        self.assertEqual(len(note["propositions"]), 2)
        self.assertEqual(note["requires"], ["degree_matrix"])


class Api(unittest.TestCase):
    def test_ingest_job_proposal_apply_plan_over_http(self):
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
        st, job = req("POST", "/ingest", {"path": "lecture.md", "domain": "spectral"})
        for _ in range(100):
            st, job = req("GET", f"/jobs/{job['id']}")
            if job["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(job["state"], "done", job.get("error"))
        pid = job["proposal"]
        self.assertEqual(req("GET", f"/proposals/{pid}")[1]["stats"]["new"], 2)
        st, summary = req("POST", f"/proposals/{pid}/apply", {"reject_edges": []})
        self.assertEqual((st, len(summary["written"])), (200, 2))
        st, plan = req("POST", f"/proposals/{pid}/plan")
        self.assertEqual(plan["steps"][-1]["id"], "spectral_clustering_partitions_vertices")
        self.assertEqual(req("POST", f"/proposals/{pid}/discard")[0], 200)
        self.assertEqual(req("GET", f"/proposals/{pid}")[0], 404)
        httpd.shutdown()
        httpd.server_close()


class Material(unittest.TestCase):
    def test_text_passages_have_line_anchors(self):
        cfg, _, _, _, src = make_env()
        ps, info = material.parse(src, 120, root=cfg.magistrale / "Material")
        self.assertEqual(info["name"], "lecture.md")
        self.assertTrue(all(p.anchor.startswith("lecture.md#L") for p in ps))


if __name__ == "__main__":
    unittest.main()
