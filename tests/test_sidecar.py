"""Side-panel backend: layout, graph payload, teach message, sidecar token and static-file safety."""
import json
import os
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from engine import graphview, ingest, layout, sidecar
from engine.server import Engine, make_handler
from tests.test_ingest import make_env


class Layout(unittest.TestCase):
    DEPS = {"c": ["b"], "b": ["a"], "d": ["a"], "e": ["c", "d"], "a": [], "x": []}

    def test_levels_and_direction(self):
        pos = layout.layout(self.DEPS, self.DEPS)
        self.assertEqual({n: p["level"] for n, p in pos.items()},
                         {"a": 0, "x": 0, "b": 1, "d": 1, "c": 2, "e": 3})
        for n, ds in self.DEPS.items():                  # every concept is drawn above what it requires
            for d in ds:
                self.assertLess(pos[n]["y"], pos[d]["y"])

    def test_deterministic_and_order_independent(self):
        a = layout.layout(self.DEPS, self.DEPS)
        b = layout.layout(list(reversed(list(self.DEPS))), dict(reversed(list(self.DEPS.items()))))
        self.assertEqual(a, b)

    def test_clusters_as_blocks(self):
        deps = {"a2": ["a1"], "a1": [], "b3": ["b2"], "b2": ["b1", "a2"], "b1": []}  # b2 also needs a2
        pos, blocks = layout.layout_clusters(deps, deps, lambda n: n[0], aspect=10)  # wide: one band
        a, b = [n for n in deps if n[0] == "a"], [n for n in deps if n[0] == "b"]
        xa, xb = {pos[n]["x"] for n in a}, {pos[n]["x"] for n in b}
        self.assertTrue(max(xa) < min(xb) or max(xb) < min(xa))      # one column block per cluster
        self.assertLess(pos["b3"]["y"], pos["b2"]["y"])               # inside a cluster: above what it requires
        self.assertEqual(pos["a1"]["y"], pos["b1"]["y"])              # foundations share the band's bottom row
        self.assertEqual({x["id"] for x in blocks}, {"a", "b"})
        self.assertEqual(layout.layout_clusters(deps, deps, lambda n: n[0]), (pos, blocks))   # deterministic

    def test_concept_clusters(self):
        from engine import clusters
        topic = {"x1": "attention", "x2": "attention", "x3": "attention", "y1": "softmax", "z1": "sql-joins",
                 "w1": "lonely"}
        label, names = clusters.cluster(topic, [("y1", "x1"), ("z1", "z1")], topic.get, min_size=3)
        self.assertEqual(label["y1"], "attention")                    # small topic folded into its linked cluster
        self.assertEqual((label["z1"], label["w1"]), ("unlinked", "unlinked"))
        self.assertEqual(names["attention"], "Attention")
        self.assertEqual(clusters.title_of("nosql_patterns"), "NoSQL patterns")

    def test_loop_does_not_hang(self):
        pos = layout.layout(["p", "q"], {"p": ["q"], "q": ["p"]})
        self.assertEqual(set(pos), {"p", "q"})

    def test_sweeps_reduce_crossings(self):
        deps = {"t1": ["b2"], "t2": ["b1"], "b1": [], "b2": []}   # initial alphabetical order crosses
        layout_mod = layout
        old = layout_mod.SWEEPS
        try:
            layout_mod.SWEEPS = 0
            before = layout_mod.crossings(layout_mod.layout(deps, deps), deps)
            layout_mod.SWEEPS = old
            after = layout_mod.crossings(layout_mod.layout(deps, deps), deps)
        finally:
            layout_mod.SWEEPS = old
        self.assertEqual((before, after), (1, 0))


class Payload(unittest.TestCase):
    def setUp(self):
        self.cfg, store, llm, emb, src = make_env()
        p = ingest.ingest(self.cfg, store, llm, emb, src, "spectral")
        ingest.apply(self.cfg, p["id"])

    def test_groups_and_layout(self):
        d = graphview.graph_payload(self.cfg)
        self.assertEqual(d["groups"], {"Course": 1, "spectral": 3})
        ids = {n["id"]: n for n in d["nodes"]}
        self.assertTrue(ids["degree_matrix"]["readonly"])
        self.assertLess(ids["fiedler_value_is_the"]["y"], ids["graph_laplacian"]["y"])
        self.assertIn({"from": "graph_laplacian", "to": "degree_matrix", "type": "requires"}, d["edges"])
        self.assertEqual(d["status_counts"], {"untested": 4})

    def test_knowledge_view_macro_nodes(self):
        d = graphview.graph_payload(self.cfg)                      # no group, no focus: the all-knowledge view
        self.assertTrue(d["clusters"])
        for c in d["clusters"]:
            self.assertTrue({"mx", "my", "w", "h", "foundations", "status_counts", "size"} <= set(c))
        ids = {c["id"] for c in d["clusters"]}
        for n in d["nodes"]:
            self.assertIn(n["cluster"], ids)
            c = next(c for c in d["clusters"] if c["id"] == n["cluster"])
            self.assertTrue(0 <= n["lx"] < c["w"] and 0 <= n["ly"] < c["h"])   # local position inside its block
        for e in d["cluster_edges"]:                               # aggregated, between different clusters only
            self.assertNotEqual(e["from"], e["to"])
            self.assertGreater(e["count"], 0)
        self.assertEqual(graphview.graph_payload(self.cfg, group="Course")["clusters"], [])

    def test_cluster_lecture(self):
        d = graphview.graph_payload(self.cfg)
        cid = next(n["cluster"] for n in d["nodes"] if n["id"] == "fiedler_value_is_the")
        c = graphview.cluster_detail(self.cfg, cid)
        ids = [x["id"] for x in c["order"]]
        self.assertIn("fiedler_value_is_the", ids)
        for x in c["outside"]:                                     # borrowed prerequisites are not in the cluster
            self.assertNotIn(x["id"], ids)
        if "graph_laplacian" in ids:                              # foundations first
            self.assertLess(ids.index("graph_laplacian"), ids.index("fiedler_value_is_the"))
        m = graphview.cluster_message(self.cfg, cid, 1, "theory only")
        self.assertIn(f"the concept cluster **{c['name']}**", m)
        self.assertIn("Prepare it as one lecture", m)
        self.assertIn("phase `teach`", m)
        self.assertIn("No multiple choice", m)
        with self.assertRaises(KeyError):
            graphview.cluster_detail(self.cfg, "nope")

    def test_group_and_focus_filters(self):
        self.assertEqual({n["id"] for n in graphview.graph_payload(self.cfg, group="Course")["nodes"]}, {"degree_matrix"})
        f = graphview.graph_payload(self.cfg, focus="graph_laplacian", around=1)
        self.assertEqual({n["id"] for n in f["nodes"]}, {"degree_matrix", "graph_laplacian", "fiedler_value_is_the"})
        self.assertTrue(next(n for n in f["nodes"] if n["id"] == "graph_laplacian")["focus"])

    def test_status_from_evidence(self):
        self.cfg.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.ledger.write_text(json.dumps({"ts": "2026-11-15T10:00", "node": "graph_laplacian", "verdict": "wrong",
                                               "confidence": 90}) + "\n", encoding="utf-8")
        ids = {n["id"]: n for n in graphview.graph_payload(self.cfg)["nodes"]}
        self.assertEqual(ids["graph_laplacian"]["status"], "misconception")

    def test_node_detail_and_teach_message(self):
        n = graphview.node_detail(self.cfg, "fiedler_value_is_the")
        self.assertEqual([x["id"] for x in n["path"]], ["degree_matrix", "graph_laplacian", "fiedler_value_is_the"])
        self.assertEqual([x["id"] for x in n["required_by"]], ["spectral_clustering_partitions_vertices"])
        vault = urllib.parse.quote(self.cfg.magistrale.name)
        self.assertTrue(n["obsidian"].startswith(f"obsidian://open?vault={vault}&file=Graph/fiedler"))
        self.assertEqual(n["file"], "Graph/fiedler_value_is_the.md")                  # one flat folder
        course = graphview.node_detail(self.cfg, "degree_matrix")
        self.assertEqual(course["group"], "Course")
        self.assertEqual(course["obsidian"], f"obsidian://open?vault={vault}&file=Material/Course/Atomic%20Notes/degree_matrix")
        self.assertEqual(course["file"], "Material/Course/Atomic Notes/degree_matrix.md")
        msg = graphview.teach_message(self.cfg, "fiedler_value_is_the")
        self.assertIn("learning-harness", msg)
        self.assertIn("1. Degree Matrix (`degree_matrix`) — untested", msg)
        self.assertIn("3 of 3 steps are not yet held", msg)
        with self.assertRaises(KeyError):
            graphview.node_detail(self.cfg, "nope")

    def test_level_changes_the_mode(self):
        modes = {lv: graphview.teach_message(self.cfg, "fiedler_value_is_the", lv, "theory only, no code")
                 for lv in range(1, 4)}
        self.assertIn("phase `teach`", modes[1]); self.assertIn("EASY probe", modes[1]); self.assertIn("MEDIUM check", modes[1])
        self.assertIn("phase `teach`", modes[2]); self.assertIn("MEDIUM probe", modes[2]); self.assertIn("HARD check", modes[2])
        self.assertIn("phase `evaluate`", modes[3]); self.assertIn("HARD check on the goal", modes[3])
        self.assertIn("fall back to level 2", modes[3])
        for m in modes.values():                               # every level: stage gates, open questions, capture
            self.assertIn("Stage gates", m)
            self.assertIn("No multiple choice", m)
            self.assertIn("theory only, no code", m)
            self.assertIn("3 of 3 steps are not yet held", m)
        self.assertIn("Spectral clustering partitions vertices", modes[3])   # harder questions use what it touches
        for lv in (1, 2):
            self.assertNotIn("Connected topics", modes[lv])
        self.assertIn("ask me for it first", graphview.teach_message(self.cfg, "graph_laplacian"))
        for bad in (4, 5, 7):                                  # 1-3 only (2026-11-20)
            with self.assertRaises(ValueError):
                graphview.teach_message(self.cfg, "graph_laplacian", bad)

    def test_learn_message_targets(self):
        m = graphview.learn_message(self.cfg, "Graph Laplacian", 2, "")                 # node by name
        self.assertIn("graph node `graph_laplacian`", m)
        m = graphview.learn_message(self.cfg, "Course", 1, "I know nothing")           # course by name
        self.assertIn("the course `Material/Course`", m)
        self.assertIn("Degree Matrix (`degree_matrix`)", m)                            # its graph nodes
        m = graphview.learn_message(self.cfg, "lecture.md", 3, "")                     # a file
        self.assertIn("the file `Material/lecture.md`", m)
        self.assertIn("Fiedler", m)                                                     # concepts from that file
        m = graphview.learn_message(self.cfg, "reinforcement learning with an LLM head", 3, "know RL and LLMs apart")
        self.assertIn("Topic: **reinforcement learning with an LLM head**", m)
        with self.assertRaises(KeyError):
            graphview.learn_message(self.cfg, "", 1, "")


class SidecarServer(unittest.TestCase):
    def setUp(self):
        cfg, store, llm, emb, src = make_env()
        p = ingest.ingest(cfg, store, llm, emb, src, "spectral")
        ingest.apply(cfg, p["id"])
        engine = Engine(cfg, anki=object(), llm=llm)
        engine.embedder = emb
        self.engine = engine
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine, token="secret"))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def req(self, method, path, body=None, token="secret"):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Sidecar-Token"] = token
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_token_required_for_api_but_not_ui(self):
        self.assertEqual(self.req("GET", "/graph", token=None)[0], 401)
        self.assertEqual(self.req("GET", "/graph", token="wrong")[0], 401)
        self.assertEqual(self.req("GET", "/graph")[0], 200)
        self.assertEqual(self.req("GET", "/graph?token=secret", token=None)[0], 200)
        st, body, headers = self.req("GET", "/", token=None)
        self.assertEqual(st, 200)
        self.assertIn(b"<title>Study</title>", body)
        self.assertEqual(self.req("GET", "/ui/app.js", token=None)[0], 200)

    def test_no_cors_and_no_traversal(self):
        _, _, headers = self.req("GET", "/graph")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1])
        c.request("GET", "/ui/../engine/config.py")
        self.assertEqual(c.getresponse().status, 404)
        c.close()

    def test_teach_outside_the_app_reports_cleanly(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTIGRAVITY_AGENTAPI_EXE", None)
            st, body, _ = self.req("POST", "/teach", {"id": "graph_laplacian", "conversationId": "c1"})
        self.assertEqual(st, 502)
        self.assertIn(b"not running inside the Antigravity app", body)

    def test_teach_sends_to_the_conversation(self):
        calls = []
        with mock.patch.object(sidecar, "agentapi", side_effect=lambda args, **kw: calls.append(args) or {"ok": 1}):
            st, body, _ = self.req("POST", "/teach", {"id": "graph_laplacian", "conversationId": "conv-9"})
            self.assertEqual(st, 200)
            self.assertEqual(calls[0][:2], ["send-message", "conv-9"])
            self.assertIn("Graph Laplacian", calls[0][2])
            st, body, _ = self.req("POST", "/teach", {"id": "graph_laplacian"})
            self.assertEqual(calls[1][:3], ["new-conversation", "--model", "flash"])

    def test_teach_carries_level_and_goal(self):
        calls = []
        with mock.patch.object(sidecar, "agentapi", side_effect=lambda args, **kw: calls.append(args) or {"ok": 1}):
            st, _, _ = self.req("POST", "/teach", {"id": "graph_laplacian", "level": 3, "goal": "exam", "conversationId": "c"})
            self.assertEqual(st, 200)
            self.assertIn("phase `evaluate`", calls[0][2])
            self.assertIn("HARD check on the goal", calls[0][2])
            st, _, _ = self.req("POST", "/learn", {"target": "Course", "level": 1, "conversationId": "c"})
            self.assertEqual(st, 200)
            self.assertIn("Material/Course", calls[1][2])
        self.assertEqual(self.req("GET", "/teach-message?id=graph_laplacian&level=9")[0], 400)
        self.assertEqual(self.req("GET", "/learn-message?target=Course&level=5&goal=cv")[0], 400)   # 1-3 only now
        st, body, _ = self.req("GET", "/learn-message?target=Course&level=2&goal=exam")
        self.assertIn("MEDIUM probe", json.loads(body)["message"])
        self.assertEqual(json.loads(self.req("GET", "/courses")[1])["courses"], ["Course"])

    def test_folder_ingest_uses_course_domain(self):
        import time
        cfg = self.engine.cfg
        (cfg.material / "Course" / "Slides").mkdir(parents=True)
        (cfg.material / "Course" / "Slides" / "a.md").write_text(
            "Graph Laplacian is L = D - A built from the degree matrix and the adjacency matrix.\n", encoding="utf-8")
        (cfg.material / "Course" / "Slides" / "skip.mp4").write_bytes(b"x")
        st, body, _ = self.req("POST", "/ingest", {"path": "Course"})
        self.assertEqual(st, 200, body)
        job = json.loads(body)
        self.assertEqual((job["files"], job["domain"]), (1, "course"))         # Atomic Notes/ and media skipped
        for _ in range(100):
            j = json.loads(self.req("GET", f"/jobs/{job['id']}")[1])
            if j["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(j["state"], "done", j)
        self.assertEqual(len(j["proposals"]), 1)

    def test_manifest(self):
        m = sidecar.manifest(None, "python", "C:/x/study.py")
        self.assertEqual(m["args"], ["C:/x/study.py", "serve", "--sidecar"])
        self.assertTrue(m["has_web_ui"])
        self.assertEqual(m["ui_config"]["views"][0]["entrypoint"], "SIDECAR_UI_ENTRYPOINT_AUX_PANE")


if __name__ == "__main__":
    unittest.main()
