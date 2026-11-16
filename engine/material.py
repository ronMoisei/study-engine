"""Material -> passages with anchors. Pure code, no model.

PDF:  consecutive pages packed up to the size limit; each page keeps its own anchor "<file>#page=N"
text: (md, txt) paragraphs packed up to the size limit, anchor "<file>#L<start>-<end>"
"""
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

TEXT_EXT = {".md", ".txt", ".markdown"}
MIN_CHARS = 80          # shorter pages/blocks (title slides, page numbers) carry no claims
WORD = re.compile(r"\w{3,}")


@dataclass
class Passage:
    index: int
    anchor: str                                     # anchor of the first part
    text: str
    parts: list = field(default_factory=list)       # [(anchor, text)] — e.g. one entry per PDF page

    def locate(self, quote):
        """Anchor of the part that best contains `quote` (falls back to the passage anchor)."""
        words = set(WORD.findall(quote.lower()))
        best, score = self.anchor, 0
        for anchor, text in self.parts:
            hit = len(words & set(WORD.findall(text.lower())))
            if hit > score:
                best, score = anchor, hit
        return best


class MaterialError(Exception):
    pass


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _clean(text):
    text = text.replace("\x00", " ").replace("\r", "")
    text = re.sub(r"-\n(?=[a-zà-ù])", "", text)             # de-hyphenate line breaks
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _split_long(text, limit):
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?;])\s+", text):
        if cur and len(cur) + len(sent) + 1 > limit:
            parts.append(cur)
            cur = ""
        while len(sent) > limit:
            parts.append(sent[:limit])
            sent = sent[limit:]
        cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur)
    return parts


def _pack(blocks, limit):
    """Greedy pack (text, first_line, last_line) blocks into chunks of at most `limit` chars."""
    out, cur, a, b = [], "", None, None
    for text, l0, l1 in blocks:
        if cur and len(cur) + len(text) + 2 > limit:
            out.append((cur, a, b))
            cur, a = "", None
        if len(text) > limit:                                 # one oversized block: split on sentences
            out += [(part, l0, l1) for part in _split_long(text, limit)]
            continue
        cur = f"{cur}\n\n{text}" if cur else text
        a = l0 if a is None else a
        b = l1
    if cur:
        out.append((cur, a, b))
    return out


def _pdf(path, name, limit):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise MaterialError("pypdf is missing: pip install pypdf")
    try:
        reader = PdfReader(str(path))
    except Exception as e:  # noqa: BLE001
        raise MaterialError(f"cannot read PDF: {e}")
    pages = []
    for n, page in enumerate(reader.pages, 1):
        try:
            text = _clean(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            text = ""
        if len(text) >= MIN_CHARS:
            pages.append((n, text))
    out, cur = [], []                                         # cur: [(anchor, text)] of consecutive pages
    for n, text in pages:
        if len(text) > limit:                                 # a long page stands alone, split by paragraphs
            if cur:
                out.append(cur)
                cur = []
            paras = [(p, n, n) for p in re.split(r"\n\n+", text) if p.strip()]
            out += [[(f"{name}#page={n}", chunk)] for chunk, _, _ in _pack(paras, limit) if len(chunk) >= MIN_CHARS]
            continue
        if cur and sum(len(t) for _, t in cur) + len(text) > limit:
            out.append(cur)
            cur = []
        cur.append((f"{name}#page={n}", text))
    if cur:
        out.append(cur)
    return out, len(reader.pages)


def _text(path, name, limit):
    lines = path.read_text(encoding="utf-8", errors="replace").replace("\r", "").split("\n")
    if lines and lines[0].strip() == "---":                   # skip YAML frontmatter
        try:
            end = lines.index("---", 1)
            lines = [""] * (end + 1) + lines[end + 1:]
        except ValueError:
            pass
    blocks, cur, start = [], [], None
    for i, line in enumerate(lines, 1):
        if line.strip():
            if not cur:
                start = i
            cur.append(line)
        elif cur:
            blocks.append(("\n".join(cur), start, i - 1))
            cur = []
    if cur:
        blocks.append(("\n".join(cur), start, len(lines)))
    out, cur, size = [], [], 0                                # cur: [(anchor, text)] — one part per paragraph
    for text, a, b in blocks:
        text = _clean(text)
        pieces = [text] if len(text) <= limit else _split_long(text, limit)
        for piece in pieces:
            if cur and size + len(piece) > limit:
                out.append(cur)
                cur, size = [], 0
            cur.append((f"{name}#L{a}-{b}", piece))
            size += len(piece) + 2
    if cur:
        out.append(cur)
    out = [parts for parts in out if sum(len(t) for _, t in parts) >= MIN_CHARS]
    return out, None


def parse(path, limit=1800, root=None):
    """-> (passages, info). `root`: anchors are written relative to it when the file is inside it."""
    path = Path(path)
    if not path.is_file():
        raise MaterialError(f"file not found: {path}")
    name = path.name
    if root:
        try:
            name = path.resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    ext = path.suffix.lower()
    if ext == ".pdf":
        raw, pages = _pdf(path, name, limit)
    elif ext in TEXT_EXT:
        raw, pages = _text(path, name, limit)
    else:
        raise MaterialError(f"unsupported file type '{ext}' — supported: .pdf, .md, .txt")
    passages = [Passage(i, parts[0][0], "\n\n".join(t for _, t in parts), parts) for i, parts in enumerate(raw)]
    return passages, {"file": str(path), "name": name, "hash": file_hash(path), "pages": pages,
                      "passages": len(passages), "chars": sum(len(p.text) for p in passages)}
