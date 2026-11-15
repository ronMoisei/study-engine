"""Local embeddings through Ollama, cached in the engine store by (model, text). No cloud tokens.

Vectors are L2-normalised float32, so cosine similarity is a dot product.
"""
import hashlib
import json
import re
import urllib.error
import urllib.request

import numpy as np


class EmbedError(Exception):
    pass


def _key(model, text):
    return hashlib.sha256(f"{model}\x00{text}".encode("utf-8")).hexdigest()


def _unit(m):
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (m / n).astype(np.float32)


class Embedder:
    def __init__(self, cfg, store):
        self.cfg, self.store, self.model = cfg, store, cfg.embed_model

    def _remote(self, texts):
        body = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(self.cfg.ollama_url + "/api/embed", body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())["embeddings"]
        except urllib.error.HTTPError as e:
            raise EmbedError(f"Ollama embed error {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        except urllib.error.URLError as e:
            raise EmbedError(f"Ollama is not reachable at {self.cfg.ollama_url} — start Ollama. ({e.reason})")

    def embed(self, texts, batch=32):
        """-> np.ndarray [len(texts), dim], unit rows. Only texts missing from the cache are sent to the model."""
        texts = list(texts)
        out = [None] * len(texts)
        missing = []
        for i, t in enumerate(texts):
            blob = self.store.get_vec(_key(self.model, t))
            if blob is None:
                missing.append(i)
            else:
                out[i] = np.frombuffer(blob, dtype=np.float32)
        for start in range(0, len(missing), batch):
            idx = missing[start:start + batch]
            vecs = _unit(np.asarray(self._remote([texts[i] for i in idx]), dtype=np.float32))
            self.store.put_vecs((_key(self.model, texts[i]), self.model, vecs.shape[1], v.tobytes())
                                for i, v in zip(idx, vecs))
            for i, v in zip(idx, vecs):
                out[i] = v
        if not out:
            return np.zeros((0, 1), dtype=np.float32)
        return np.vstack(out)


class FakeEmbedder:
    """Deterministic bag-of-words vectors for offline tests (no Ollama). Shared words -> higher cosine."""
    model = "fake"

    def __init__(self, dim=512):
        self.dim = dim

    def embed(self, texts, batch=32):
        m = np.zeros((len(list(texts)), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-zA-Zàèéìòù0-9]{3,}", t.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                m[i, h % self.dim] += 1.0
        return _unit(m) if len(m) else m


def make_embedder(cfg, store):
    return FakeEmbedder() if cfg.provider == "stub" else Embedder(cfg, store)
