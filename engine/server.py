"""Local JSON API on 127.0.0.1 for the Obsidian Study panel (stdlib only).

GET  /health                      engine + Anki status
GET  /decks                       Anki deck names
POST /review/start   {deck, limit}            -> current view
GET  /review/<sid>                            -> current view (front only until answered)
POST /review/<sid>/answer {answer, confidence, idk} -> grading result (includes the back)
POST /review/<sid>/next                       -> next view / summary
GET  /review/<sid>/summary
POST /ingest {path, domain}                   -> job (background)      GET /jobs/<id>  -> progress
GET  /proposals                               GET /proposals/<id>
POST /proposals/<id>/apply {accept, reject}   write approved items to the vault
POST /proposals/<id>/plan                     build the study plan note
POST /proposals/<id>/discard
"""
import json
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ingest as ingest_mod
from .anki import Anki, AnkiError
from .embed import EmbedError, make_embedder
from .llm import LLMError, make_provider
from .material import MaterialError
from .review import ReviewSession, SessionError
from .store import Store


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
        """Run the ingest pipeline in a background thread; poll /jobs/<id>."""
        import time
        import uuid
        path = ingest_mod.resolve_path(self.cfg, path)
        if not path.is_file():
            raise MaterialError(f"file not found: {path}")
        if not (domain or "").strip():
            raise MaterialError("a domain is required (e.g. deep-nlp, ml-security)")
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "state": "running", "stage": "parse", "done": 0, "total": 0, "file": str(path),
               "proposal": None, "error": None, "started": time.time()}
        self.jobs[jid] = job

        def progress(stage, done, total):
            job.update(stage=stage, done=done, total=total)

        def work():
            try:
                p = ingest_mod.ingest(self.cfg, self.store, self.llm, self.embedder, path, domain, progress)
                job.update(state="done", proposal=p["id"], stats=p["stats"])
            except Exception as e:  # noqa: BLE001
                job.update(state="error", error=f"{type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()
        return job

    def session(self, sid):
        s = self.sessions.get(sid)
        if not s:
            raise SessionError(f"unknown session {sid}")
        return s


def make_handler(engine):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):        # quiet
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def _route(self, method):
            path = self.path.split("?")[0].rstrip("/")
            try:
                if method == "GET" and path == "/health":
                    return self._send(200, engine.health())
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
                            return self._send(200, ingest_mod.apply(engine.cfg, pid, b.get("accept"), b.get("reject")))
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
            except KeyError as e:
                return self._send(404, {"error": str(e).strip("'\"")})
            except MaterialError as e:
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


def serve(cfg, engine=None):
    engine = engine or Engine(cfg)
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(engine))
    print(f"study-engine on http://{cfg.host}:{cfg.port}  provider={engine.llm.name}  dry_run={cfg.dry_run}")
    print(f"ledger: {cfg.ledger}")
    httpd.serve_forever()
