"""Local JSON API on 127.0.0.1 for the Study side panel (stdlib only).

GET  /health                      engine + Anki status
GET  /decks                       Anki deck names
POST /review/start   {deck, limit}            -> current view
GET  /review/<sid>                            -> current view (front only until answered)
POST /review/<sid>/answer {answer, confidence, idk} -> grading result (includes the back)
POST /review/<sid>/next                       -> next view / summary
GET  /review/<sid>/summary
POST /ingest {path, domain}                   -> job (background)      GET /jobs/<id>  -> progress
                                                 path: file, folder or course; domain defaults to the course
GET  /courses                                 course folders in Material/
GET  /sync[?recheck=1]                        this PC's setup (launcher, MCP servers, deps, deploy); recheck repairs
GET  /proposals                               GET /proposals/<id>
POST /proposals/<id>/apply {accept, reject}   write approved items to the vault
POST /proposals/<id>/plan                     build the study plan note
POST /proposals/<id>/discard
GET  /graph?group=&focus=&around=1            laid-out prerequisite graph with learner status
GET  /node/<id>                               one concept: definition, links, path, file
GET  /teach-message?id=&level=&goal=         the message "Teach me this" would send (level 1-5 picks the mode)
POST /teach {id, level, goal, conversationId} send it to that conversation (or start a new one)
GET  /cluster/<id>                            a concept cluster as a lecture: order, outside prerequisites
     /teach-message?cluster=… and POST /teach {cluster, …}   "Prepare lecture" for a whole cluster
GET  /learn-message?target=&level=&goal=     Inbox "Start learning": node, file/folder/course, or a topic in words
POST /learn {target, level, goal, conversationId}
GET  /                                        the side-panel UI (sidecar_ui/)

Security: binds 127.0.0.1 only; no CORS headers (no web page may call it). In sidecar mode every request except
the UI's static files must carry the token the app provides (X-Sidecar-Token header or ?token=).
"""
import json
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mimetypes
import urllib.parse
from pathlib import Path

from . import graphview, learn, sidecar
from . import ingest as ingest_mod
from .anki import Anki, AnkiError
from .embed import EmbedError, make_embedder
from .llm import LLMError, make_provider
from .material import MaterialError
from .review import ReviewSession, SessionError
from .store import Store

# Windows' registry can map .js to text/plain; fix the types the panel needs.
STATIC_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2",
                ".svg": "image/svg+xml"}


class Engine:
    def __init__(self, cfg, anki=None, llm=None):
        self.cfg = cfg
        self.store = Store(cfg.db_path)
        self.anki = anki or Anki(cfg.anki_url)
        self.llm = llm or make_provider(cfg, self.store)
        self.embedder = make_embedder(cfg, self.store)
        self.sessions = {}
        self.jobs = {}
        self.lock = threading.Lock()
        self.ingest_lock = threading.Lock()
        self.sync_report = None               # devicesync.check() result for this PC

    def health(self):
        try:
            self.anki.call("version")
            anki = True
        except AnkiError:
            anki = False
        return {"ok": True, "provider": self.llm.name, "anki": anki, "dry_run": self.cfg.dry_run,
                "ledger": str(self.cfg.ledger)}

    def start(self, deck=None, limit=20):
        s = ReviewSession(self.cfg, self.anki, self.store, self.llm, deck=deck or None, limit=limit)
        with self.lock:
            self.sessions[s.id] = s
        return s.current()

    def start_ingest(self, path, domain):
        """Run the ingest pipeline in a background thread; poll /jobs/<id>. `path`: a file, a folder (every
        .pdf/.md/.txt inside, one proposal per file) or a course name. Domain defaults to the course folder."""
        import time
        import uuid
        target = learn.find_material(self.cfg, path) or ingest_mod.resolve_path(self.cfg, path)
        files = learn.material_files(self.cfg, target)
        if not files:
            raise MaterialError(f"no .pdf, .md or .txt files in {target}")
        if len(files) > learn.MAX_FILES:
            raise MaterialError(f"{len(files)} files in {target.name}: pick a subfolder (at most {learn.MAX_FILES} "
                                "per analysis, each costs cloud calls)")
        domain = (domain or "").strip() or learn.default_domain(self.cfg, target)
        if not domain:
            raise MaterialError("a domain is required for material outside Material/<course>/ (e.g. deep-nlp)")
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "state": "running", "stage": "parse", "done": 0, "total": 0, "file": str(target),
               "files": len(files), "file_index": 0, "domain": domain, "proposal": None, "proposals": [],
               "errors": [], "error": None, "started": time.time()}
        self.jobs[jid] = job

        def progress(stage, done, total):
            job.update(stage=stage, done=done, total=total)

        def work():
            for k, f in enumerate(files):
                job.update(file_index=k, current=f.name, stage="parse", done=0, total=0)
                try:
                    with self.ingest_lock:               # one file at a time: later files see earlier concepts
                        p = ingest_mod.ingest(self.cfg, self.store, self.llm, self.embedder, f, domain, progress)
                    job["proposals"].append(p["id"])
                    job.update(proposal=p["id"], stats=p["stats"])
                except Exception as e:  # noqa: BLE001
                    job["errors"].append(f"{f.name}: {type(e).__name__}: {e}")
            if job["proposals"]:
                job.update(state="done")
            else:
                job.update(state="error", error="; ".join(job["errors"]) or "nothing analysed")

        threading.Thread(target=work, daemon=True).start()
        return job

    def session(self, sid):
        s = self.sessions.get(sid)
        if not s:
            raise SessionError(f"unknown session {sid}")
        return s


UI_DIR = Path(__file__).resolve().parents[1] / "sidecar_ui"


def make_handler(engine, token=None):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):        # quiet
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

        def _static(self, path):
            name = "index.html" if path in ("", "/") else path[len("/ui/"):]
            f = (UI_DIR / name).resolve()
            if UI_DIR.resolve() not in f.parents or not f.is_file():
                return self._send(404, {"error": "not found"})
            body = f.read_bytes()
            self.send_response(200)
            ctype = STATIC_TYPES.get(f.suffix) or mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            self.send_header("Content-Type", ctype
                             + ("; charset=utf-8" if f.suffix in (".html", ".js", ".css") else ""))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self, query):
            if not token:
                return True
            return self.headers.get("X-Sidecar-Token") == token or query.get("token", [None])[0] == token

        def _route(self, method):
            raw_path, _, qs = self.path.partition("?")
            query = urllib.parse.parse_qs(qs)
            path = raw_path.rstrip("/")
            if method == "GET" and (path == "" or path.startswith("/ui/")):
                return self._static(path)
            if not self._authorised(query):
                return self._send(401, {"error": "unauthorized"})
            q = lambda k, d=None: query.get(k, [d])[0]  # noqa: E731
            try:
                if method == "GET" and path == "/graph":
                    return self._send(200, graphview.graph_payload(engine.cfg, q("group") or None, q("focus") or None,
                                                                   int(q("around", "1"))))
                m = re.fullmatch(r"/node/([\w-]+)", path)
                if m and method == "GET":
                    return self._send(200, graphview.node_detail(engine.cfg, m.group(1)))
                m = re.fullmatch(r"/cluster/([\w-]+)", path)
                if m and method == "GET":
                    return self._send(200, graphview.cluster_detail(engine.cfg, m.group(1)))
                if method == "GET" and path == "/teach-message":
                    if q("cluster"):
                        return self._send(200, {"message": graphview.cluster_message(engine.cfg, q("cluster"),
                                                                                     q("level"), q("goal"))})
                    return self._send(200, {"message": graphview.teach_message(engine.cfg, q("id", ""), q("level"),
                                                                               q("goal"))})
                if method == "GET" and path == "/learn-message":
                    return self._send(200, {"message": graphview.learn_message(engine.cfg, q("target", ""),
                                                                               q("level"), q("goal"))})
                if method == "GET" and path == "/courses":
                    return self._send(200, {"courses": learn.courses(engine.cfg)})
                if method == "POST" and path in ("/teach", "/learn"):
                    b = self._body()
                    if path == "/learn":
                        msg = graphview.learn_message(engine.cfg, b.get("target", ""), b.get("level"), b.get("goal"))
                    elif b.get("cluster"):
                        msg = graphview.cluster_message(engine.cfg, b["cluster"], b.get("level"), b.get("goal"))
                    else:
                        msg = graphview.teach_message(engine.cfg, b.get("id", ""), b.get("level"), b.get("goal"))
                    if b.get("conversationId"):
                        out = sidecar.send_message(b["conversationId"], msg)
                        return self._send(200, {"sent_to": b["conversationId"], "result": out, "message": msg})
                    out = sidecar.new_conversation(msg, model=engine.cfg.teach_model,
                                                   project_id=engine.cfg.agy_project or None,
                                                   title=sidecar.title_of(msg))
                    return self._send(200, {"new_conversation": True, "result": out, "message": msg,
                                            "project": engine.cfg.agy_project or "panel's project"})
                if method == "GET" and path == "/health":
                    return self._send(200, engine.health())
                if method == "GET" and path == "/sync":
                    if q("recheck") or engine.sync_report is None:
                        from . import devicesync
                        engine.sync_report = devicesync.check(engine.cfg, fix=bool(q("recheck")))
                    return self._send(200, engine.sync_report)
                if method == "GET" and path == "/decks":
                    return self._send(200, {"decks": engine.anki.decks()})
                if method == "POST" and path == "/review/start":
                    b = self._body()
                    return self._send(200, engine.start(b.get("deck"), int(b.get("limit") or 20)))
                if method == "POST" and path == "/ingest":
                    b = self._body()
                    return self._send(200, engine.start_ingest(b.get("path", ""), b.get("domain", "")))
                m = re.fullmatch(r"/jobs/(\w+)", path)
                if m and method == "GET":
                    job = engine.jobs.get(m.group(1))
                    return self._send(200, job) if job else self._send(404, {"error": "unknown job"})
                if method == "GET" and path == "/proposals":
                    return self._send(200, {"proposals": ingest_mod.list_proposals(engine.cfg)})
                m = re.fullmatch(r"/proposals/([\w-]+)(?:/(apply|plan|discard))?", path)
                if m:
                    pid, act = m.group(1), m.group(2)
                    if method == "GET" and act is None:
                        return self._send(200, ingest_mod.load(engine.cfg, pid))
                    if method == "POST" and act == "apply":
                        b = self._body()
                        with engine.lock:
                            return self._send(200, ingest_mod.apply(
                                engine.cfg, pid, b.get("accept"), b.get("reject"), b.get("reject_edges"),
                                b.get("accept_edges")))
                    if method == "POST" and act == "plan":
                        return self._send(200, ingest_mod.build_plan(engine.cfg, pid))
                    if method == "POST" and act == "discard":
                        ingest_mod.discard(engine.cfg, pid)
                        return self._send(200, {"discarded": pid})
                m = re.fullmatch(r"/review/([\w-]+)(?:/(answer|next|summary))?", path)
                if m:
                    s = engine.session(m.group(1))
                    act = m.group(2)
                    with engine.lock:
                        if method == "GET" and act is None:
                            return self._send(200, s.current())
                        if method == "GET" and act == "summary":
                            return self._send(200, s.summary())
                        if method == "POST" and act == "answer":
                            b = self._body()
                            return self._send(200, s.answer(b.get("answer", ""), b.get("confidence"),
                                                            bool(b.get("idk"))))
                        if method == "POST" and act == "next":
                            return self._send(200, s.next())
                return self._send(404, {"error": f"no route {method} {path}"})
            except SessionError as e:
                return self._send(409, {"error": str(e)})
            except sidecar.AgentApiError as e:
                return self._send(502, {"error": str(e)})
            except KeyError as e:
                return self._send(404, {"error": str(e).strip("'\"")})
            except (MaterialError, ValueError) as e:
                return self._send(400, {"error": str(e)})
            except EmbedError as e:
                return self._send(503, {"error": str(e)})
            except AnkiError as e:
                return self._send(503, {"error": str(e)})
            except LLMError as e:
                return self._send(502, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return H


RESTART_EXIT = 3          # the launcher's restart_policy "on-failure" restarts the engine on a non-zero exit


def _start_device_sync(engine, as_sidecar):
    """Every start: check (and repair) this PC's setup in the background; when running as the panel from the
    deployed copy, watch for a newer deploy made on another PC and restart onto it."""
    import os
    import time

    from . import devicesync

    def run_check():
        try:
            engine.sync_report = devicesync.check(engine.cfg, fix=True)
        except Exception as e:  # noqa: BLE001
            engine.sync_report = {"ok": False, "items": [{"item": "sync check", "ok": False, "detail": str(e)}]}
        for i in engine.sync_report["items"]:
            if i.get("fixed") or not i["ok"]:
                print(f"[sync] {i['item']}: {i['detail']}", flush=True)

    threading.Thread(target=run_check, daemon=True).start()
    from . import config as config_mod
    if not (as_sidecar and config_mod._workspace_above(config_mod.REPO)):
        return
    first = devicesync.deploy_stamp(engine.cfg)

    def watch():
        while True:
            time.sleep(20)
            now = devicesync.deploy_stamp(engine.cfg)
            if now and first and now != first:
                print(f"[sync] new engine deployed ({now}); restarting onto it", flush=True)
                time.sleep(2)                       # let Drive finish writing the copy
                os._exit(RESTART_EXIT)

    threading.Thread(target=watch, daemon=True).start()


def serve(cfg, engine=None, as_sidecar=False):
    engine = engine or Engine(cfg)
    _start_device_sync(engine, as_sidecar)
    token, port = None, cfg.port
    if as_sidecar:
        e = sidecar.env()
        if not e["port"]:
            raise SystemExit("--sidecar: ANTIGRAVITY_SIDECAR_WEB_PORT is not set; the Antigravity app starts this")
        port, token = int(e["port"]), e["token"]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine, token))
    print(f"study-engine on http://127.0.0.1:{port}  sidecar={as_sidecar}  provider={engine.llm.name}  "
          f"dry_run={cfg.dry_run}", flush=True)
    print(f"ledger: {cfg.ledger}")
    httpd.serve_forever()
