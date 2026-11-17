"""Model providers. Every call: one job, one JSON schema, validated output, logged input/output/usage.

AgyProvider  — `agy -p <prompt> --model M --json-schema <file> --output-format json`, run in an EMPTY workdir so
               the Magistrale workspace's skills, hooks and rules are not loaded into each call.
StubProvider — deterministic offline stand-in (tests, development while the agy quota is exhausted).
"""
import json
import re
import subprocess
import threading
import time
from pathlib import Path


class LLMError(Exception):
    pass


# ── minimal JSON-schema validator (the subset our schemas use) ────────────────

def validate(obj, schema, path="$"):
    errs = []
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
    if t and not isinstance(obj, types[t]) or (t in ("integer", "number") and isinstance(obj, bool)):
        return [f"{path}: expected {t}"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} not in {schema['enum']}")
    if t == "object":
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}: missing '{k}'")
        for k, sub in schema.get("properties", {}).items():
            if k in obj:
                errs += validate(obj[k], sub, f"{path}.{k}")
    if t == "array":
        if "maxItems" in schema and len(obj) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "minItems" in schema and len(obj) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        for i, x in enumerate(obj):
            errs += validate(x, schema.get("items", {}), f"{path}[{i}]")
    return errs


def find_conforming(value, schema, depth=0):
    """Search agy's JSON envelope for the first object that satisfies `schema` (envelope shape unknown/unstable)."""
    if depth > 6:
        return None
    if isinstance(value, str):
        s = value.strip()
        m = re.search(r"```(?:json)?\s*(.+?)```", s, re.S)
        s = m.group(1) if m else s
        if s[:1] in "{[":
            try:
                return find_conforming(json.loads(s), schema, depth + 1)
            except json.JSONDecodeError:
                return None
        return None
    if isinstance(value, dict):
        if not validate(value, schema):
            return value
        for v in value.values():
            hit = find_conforming(v, schema, depth + 1)
            if hit is not None:
                return hit
    if isinstance(value, list):
        for v in value:
            hit = find_conforming(v, schema, depth + 1)
            if hit is not None:
                return hit
    return None


def find_usage(value, out=None, depth=0):
    """Collect numeric fields whose key mentions tokens (e.g. input_tokens) anywhere in the envelope."""
    out = {} if out is None else out
    if depth > 6:
        return out
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and "token" in k.lower():
                out[k] = out.get(k, 0) + v
            else:
                find_usage(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value:
            find_usage(v, out, depth + 1)
    return out


class AgyProvider:
    name = "agy"

    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        cfg.agy_workdir.mkdir(parents=True, exist_ok=True)

    def run(self, job, prompt, schema, session="", retries=1, model=None):
        model = model or self.cfg.models[job]
        schema_file = self.cfg.agy_workdir / f"schema_{job}_{threading.get_ident()}.json"   # parallel calls
        timeout = int(self.cfg.job_timeout_s.get(job, self.cfg.agy_timeout_s))
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        # A schema with "x-text-field" asks for PLAIN TEXT (no --json-schema): long Markdown with code and LaTeX
        # inside a JSON string must be escaped (models even double-encode it), and one bad escape fails the call.
        text_field = schema.get("x-text-field")
        last_err = None
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(min(90, 20 * attempt))         # a failed call is often a passing hiccup: wait, then retry
            t0 = time.time()
            raw, usage, out, err = "", {}, None, None
            try:
                # The prompt goes in on stdin as one stream-json message: a command-line argument is capped at
                # ~32k characters on Windows, which a batch of pairs exceeds.
                message = json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"
                proc = subprocess.run(
                    [self.cfg.agy, "-p", "", "--input-format", "stream-json", "--output-format", "stream-json",
                     "--model", model] + ([] if text_field else ["--json-schema", str(schema_file)]) +
                    ["--disable-slash-commands", "--print-timeout", f"{timeout}s"],
                    input=message, cwd=self.cfg.agy_workdir, capture_output=True, text=True, encoding="utf-8",
                    timeout=timeout + 30)
                raw = (proc.stdout or "") + ("\n[stderr]\n" + proc.stderr if proc.stderr else "")
                if "RESOURCE_EXHAUSTED" in raw or "quota" in raw.lower() and proc.returncode:
                    raise LLMError("agy quota exhausted — retry later or set provider 'stub' in config.json")
                envelope = None                            # the last {"event":"result","result":{...}} line
                for line in (proc.stdout or "").splitlines():
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(ev, dict) and ev.get("event") == "result":
                        envelope = ev.get("result")
                if not isinstance(envelope, dict):
                    envelope = {}
                if envelope.get("status") not in (None, "SUCCESS") and envelope.get("error"):
                    err = f"agy: {envelope['error']}"
                usage = find_usage(envelope.get("usage", {}))
                # result: {status, response, structured_output, usage, ...}
                if text_field:
                    text = plain_text(envelope.get("response"))
                    out = {text_field: text} if text and not err else None
                    if out is None and not err:
                        err = f"empty answer from agy (exit {proc.returncode}): {(proc.stderr or '').strip()[-200:]}"
                else:
                    structured = envelope.get("structured_output")
                    out = structured if isinstance(structured, dict) and not validate(structured, schema) \
                        else find_conforming(envelope, schema)
                    if out is None:
                        err = (f"no schema-conforming JSON in agy output (exit {proc.returncode})"
                               + (f": {proc.stderr.strip()[-200:]}" if proc.stderr and proc.stderr.strip() else ""))
            except subprocess.TimeoutExpired:
                err = "agy timed out"
            except FileNotFoundError:
                err = f"agy executable not found: {self.cfg.agy}"
            except LLMError as e:
                err = str(e)
                self.store.log_call(session=session, job=job, model=model, prompt=prompt, output=None, raw=raw[:20000],
                                    usage=usage, ok=0, error=err, duration_ms=int((time.time() - t0) * 1000))
                raise
            self.store.log_call(session=session, job=job, model=model, prompt=prompt, output=out, raw=raw[:20000],
                                usage=usage, ok=int(out is not None), error=err,
                                duration_ms=int((time.time() - t0) * 1000))
            if out is not None:
                return out
            last_err = err
        raise LLMError(f"{job}: {last_err}")


class StubProvider:
    """Deterministic, offline. Checklist = sentences of the back; grading = keyword overlap. Not a real grader."""
    name = "stub"

    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store

    @staticmethod
    def _words(s):
        return {w for w in re.findall(r"[a-zA-Zàèéìòù0-9]{4,}", s.lower())}

    def run(self, job, prompt, schema, session="", retries=0, model=None):
        data = json.loads(prompt.split("INPUT_JSON:", 1)[1])
        if job == "checklist":
            parts = [p.strip() for p in re.split(r"(?<=[.;])\s+|\n+", data["back"]) if p.strip()][:5] or [data["back"]]
            out = {"points": [{"point": p, "essential": i == 0} for i, p in enumerate(parts)]}
        elif job == "grade":
            ans = data["answer"]
            items = []
            for i, pt in enumerate(data["checklist"]):
                key = self._words(pt["point"])
                hit = [w for w in self._words(ans) if w in key]
                met = bool(key) and len(hit) / len(key) >= 0.5
                quote = next((m.group(0) for w in hit for m in [re.search(re.escape(w), ans, re.I)] if m), "")
                items.append({"index": i, "met": met, "evidence_quote": quote if met else ""})
            out = {"items": items, "contradiction": False, "contradiction_note": "", "miss_type": "cue",
                   "feedback": "stub grader (keyword overlap) — not a real evaluation"}
        elif job == "remedial":
            out = {"cards": [{"front": f"(remedial) {u}", "back": u} for u in data["unmet_points"][:2]]}
        elif job == "extract":
            sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", data["passage"]) if len(x.split()) >= 4]
            out = {"claims": [{"name": " ".join(x.split()[:4]).strip(".,;:"), "statement": x, "kind": "definition",
                               "quote": x} for x in sents[:8]]}
        elif job == "judge":
            a, b = self._words(data["a"]), self._words(data["b"])
            key = schema["required"][0]                    # same_idea or same_concept
            out = {key: bool(a | b) and len(a & b) / len(a | b) >= 0.6}
        elif job == "group":
            # claims whose names share their first word form one concept; first claim defines it
            groups = {}
            for c in data["claims"]:
                groups.setdefault(c["name"].split()[0].lower(), []).append(c["id"])
            names = {c["id"]: c["name"] for c in data["claims"]}
            out = {"concepts": [{"title": names[ids[0]], "kind": "definition", "topic": "general",
                                 "definition_claim": ids[0], "claims": ids, "aliases": []}
                                for ids in groups.values()],
                   "topics": [{"slug": "general", "title": "General"}]}
        elif job == "structure":
            # sentences of every passage; sentences whose first word matches form one concept (like extract+group)
            groups = {}
            for p in data["passages"]:
                for x in re.split(r"(?<=[.!?])\s+|\n+", p["text"]):
                    x = x.strip()
                    if len(x.split()) >= 4:
                        groups.setdefault(x.split()[0].lower(), []).append((p["index"], x))
            out = {"concepts": [{"title": " ".join(s[0][1].split()[:4]).strip(".,;:"), "kind": "definition",
                                 "topic": "general", "passage": s[0][0], "quote": s[0][1], "requires": []}
                                for s in groups.values()][:40],
                   "topics": [{"slug": "general", "title": "General"}]}
        elif job == "write":
            text = "\n".join(p["text"] for p in data["passages"])
            notes = []
            for c in data["concepts"]:
                first = c["introduced_by"].split()[0].lower()
                same = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text)
                        if len(x.split()) >= 4 and x.split()[0].lower() == first and x.strip() != c["introduced_by"]]
                notes.append({"n": c["n"], "definition": c["introduced_by"], "definition_quote": c["introduced_by"],
                              "aliases": [], "propositions": [{"text": x, "quote": x} for x in same[:6]]})
            out = {"notes": notes}
        elif job == "polish":
            out = {"markdown": data["text"]}
        elif job == "atomize":
            # one concept per passage: its first sentence of 4+ words is the definition and the quote
            cs = []
            for p in data["passages"]:
                sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", p["text"])
                         if len(x.split()) >= 4 and not x.strip().startswith("#")]
                if sents:
                    cs.append({"title": " ".join(sents[0].split()[:3]).strip(".,;:"), "kind": "definition",
                               "definition": sents[0], "quote": sents[0], "aliases": [],
                               "propositions": [{"text": x, "quote": x} for x in sents[1:3]]})
            out = {"concepts": cs[:20]}
        elif job == "consolidate":
            first, dups = {}, []
            for c in data["concepts"]:
                k = c["title"].lower()
                if k in first:
                    dups.append({"keep": first[k], "same": [c["n"]]})
                else:
                    first[k] = c["n"]
            out = {"topics": [{"slug": "general", "title": "General", "members": [c["n"] for c in data["concepts"]]}],
                   "duplicates": dups, "existing": [], "discard": []}
        elif job == "link":
            # a concept of this lecture requires every catalog concept whose title's first word appears just before
            # its own title in the notes; the quote is that span
            low, links = data["notes"].lower(), []
            titles = {c["id"]: c["title"] for c in data["catalog"]}
            for a in data["taught_here"]:
                for b, tb in titles.items():
                    if a != b and tb.lower() in low and titles[a].lower() in low and \
                            low.index(tb.lower()) < low.index(titles[a].lower()):
                        i = low.index(tb.lower())
                        links.append({"from": a, "to": b, "relation": "requires", "because": "stub",
                                      "quote": " ".join(data["notes"][i:].split()[:8])})
                        break
            out = {"links": links}
        elif job == "relate":
            rels = []
            for p in data["pairs"]:
                a, b = p["a"], p["b"]
                head = lambda t: " ".join(t.lower().split()[:2])  # noqa: E731  (first two words of a title)
                wa = self._words(a["title"] + " " + a["definition"])
                wb = self._words(b["title"] + " " + b["definition"])
                if wa and wb and len(wa & wb) / len(wa | wb) >= 0.6:
                    r = "same"
                elif head(b["title"]) in a["definition"].lower():
                    r = "A_requires_B"
                elif head(a["title"]) in b["definition"].lower():
                    r = "B_requires_A"
                else:
                    r = "none"
                rels.append({"pair": p["pair"], "relation": r})
            out = {"relations": rels}
        else:
            raise LLMError(f"stub: unknown job {job}")
        errs = validate(out, schema)
        if errs:
            raise LLMError(f"stub produced invalid output: {errs}")
        self.store.log_call(session=session, job=job, model="stub", prompt=prompt, output=out, raw="", usage={},
                            ok=1, error=None, duration_ms=0)
        return out


class OllamaProvider:
    """Local models through Ollama's /api/chat with a JSON schema. temperature 0 + fixed seed: repeatable output."""
    name = "ollama"

    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store

    def run(self, job, prompt, schema, session="", retries=1, model=None):
        body = {"model": model, "stream": False, "format": schema, "think": False,
                "options": {"temperature": 0, "seed": 7, "num_ctx": 8192},
                "messages": [{"role": "user", "content": prompt}]}
        last_err = None
        for _ in range(retries + 1):
            t0 = time.time()
            raw, out, err, usage = "", None, None, {}
            try:
                reply = self._post(body)
                raw = reply.get("message", {}).get("content", "")
                usage = {"local_prompt_tokens": reply.get("prompt_eval_count", 0),
                         "local_output_tokens": reply.get("eval_count", 0)}
                out = find_conforming(raw, schema)
                if out is None:
                    err = "local model output does not match the schema"
            except LLMError as e:
                err = str(e)
            self.store.log_call(session=session, job=job, model=f"ollama:{model}", prompt=prompt, output=out,
                                raw=raw[:20000], usage=usage, ok=int(out is not None), error=err,
                                duration_ms=int((time.time() - t0) * 1000))
            if out is not None:
                return out
            last_err = err
            if err and "not reachable" in err:
                break
        raise LLMError(f"{job}: {last_err}")

    def _post(self, body):
        import urllib.error
        import urllib.request
        url = self.cfg.ollama_url + "/api/chat"
        for attempt in (0, 1):
            try:
                req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=600) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if e.code == 400 and "think" in body and attempt == 0:      # model without a thinking switch
                    body = {k: v for k, v in body.items() if k != "think"}
                    continue
                raise LLMError(f"Ollama error {e.code}: {detail}")
            except urllib.error.URLError as e:
                raise LLMError(f"Ollama is not reachable at {self.cfg.ollama_url} — start Ollama. ({e.reason})")


PROVIDERS = {"agy": AgyProvider, "ollama": OllamaProvider, "stub": StubProvider}


class Router:
    """Sends each job to the provider named in cfg.models[job] ("provider:model"; bare model -> cfg.provider)."""

    def __init__(self, cfg, store):
        self.cfg, self.store, self._cache = cfg, store, {}

    @property
    def name(self):
        return self.cfg.provider if self.cfg.provider == "stub" else "router"

    def route(self, job):
        spec = self.cfg.models.get(job)
        if spec is None:
            raise LLMError(f"no model configured for job '{job}'")
        if self.cfg.provider == "stub":
            return "stub", "stub"
        head, _, rest = spec.partition(":")
        return (head, rest) if head in PROVIDERS and rest else (self.cfg.provider, spec)

    def run(self, job, prompt, schema, session="", retries=1):
        prov, model = self.route(job)
        if prov not in self._cache:
            self._cache[prov] = PROVIDERS[prov](self.cfg, self.store)
        out = self._cache[prov].run(job, prompt, schema, session=session, retries=retries, model=model)
        return repair_latex(unwrap_double(out, schema))


def plain_text(response):
    """A plain-text answer: the model's text, without a code fence wrapped around ALL of it."""
    if not isinstance(response, str):
        return ""
    t = response.strip()
    m = re.fullmatch(r"```(?:markdown|md)?\n(.*)\n```", t, re.S)        # only a fence around the whole note
    return (m.group(1) if m else t).strip()


def unwrap_double(out, schema):
    """A model sometimes answers a one-string schema with the whole JSON inside the string:
    {"markdown": "{\\"markdown\\": \\"## ...\\"}"}. Return the inner object when it conforms to the schema."""
    if not isinstance(out, dict) or len(out) != 1:
        return out
    (k, v), = out.items()
    if not isinstance(v, str) or not v.lstrip().startswith("{") or f'"{k}"' not in v[:200]:
        return out
    text = v.strip()
    for attempt in (text, re.sub(r'\\(?![\\"/bfnrtu])', r"\\\\", text)):   # LaTeX \alpha: not a JSON escape
        try:
            inner = json.loads(attempt, strict=False)
        except ValueError:
            continue
        if isinstance(inner, dict) and not validate(inner, schema):
            return inner
    return out


# A model that writes LaTeX with single backslashes inside JSON gets them read as escapes: "\times" arrives as
# TAB + "imes", "\frac" as FORM FEED + "rac", "\nabla" as NEWLINE + "abla". Put the backslash back when the
# control character is directly followed by the rest of a LaTeX command.
_LATEX_TAILS = {
    "\t": "imes|au|heta|ext|extbf|extit|ilde|op|anh|frac|riangle|o",
    "\f": "rac|orall|lat",
    "\b": "eta|ar|ig|egin|f|m|oldsymbol|ullet|inom",
    "\r": "ho|ight|ightarrow|m|angle|ceil|floor",
    "\n": "abla|eq|u|ot|orm|ewline|i|exists|ormalsize",
    "\v": "ec|arepsilon|arphi|dots",
    "\a": "lpha|pprox",
}
_LATEX_RE = {ch: re.compile(re.escape(ch) + r"(?=(?:" + tails + r")(?![a-z]))") for ch, tails in _LATEX_TAILS.items()}
_MATH = re.compile(r"\$\$.+?\$\$|\$[^$]+\$", re.S)
# The opposite mistake: escaping twice, so "\\sum" arrives. Inside math a doubled backslash directly before a
# command letter or a spacing mark is never a line break, so it becomes a single one.
_DOUBLED = re.compile(r"\\\\(?=[A-Za-z,;:!])")


def collapse_doubled(text):
    return _MATH.sub(lambda m: _DOUBLED.sub(lambda _: "\\", m.group(0)), text)


def repair_latex(obj):
    if isinstance(obj, str):
        if "$" not in obj and "\\" not in obj:
            return obj
        for ch, rx in _LATEX_RE.items():
            if ch == "\n":                               # only inside math, newlines are real text otherwise
                obj = _MATH.sub(lambda m: rx.sub(r"\\" + "n", m.group(0)), obj)
            else:
                obj = rx.sub(lambda m, c=ch: "\\" + {"\t": "t", "\f": "f", "\b": "b", "\r": "r", "\v": "v",
                                                     "\a": "a"}[c], obj)
        return collapse_doubled(obj)
    if isinstance(obj, list):
        return [repair_latex(x) for x in obj]
    if isinstance(obj, dict):
        return {k: repair_latex(v) for k, v in obj.items()}
    return obj


def make_provider(cfg, store):
    return Router(cfg, store)
