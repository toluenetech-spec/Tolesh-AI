"""Uploaded files: stored, text-extracted, chunked and searched locally.

Everything here is local. The bytes live next to the database (mode 0600) and
the extracted text is chunked so a 300-page PDF does not have to be pasted into
a model context window to be useful — only the chunks that actually match the
question are sent.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from .._sqlite import connect, default_data_dir

MAX_BYTES = 20 * 1024 * 1024          # 20 MB per file
MAX_TEXT = 400_000                     # characters kept per file
CHUNK_CHARS = 1100
CHUNK_OVERLAP = 120

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log",
                 ".py", ".js", ".ts", ".html", ".htm", ".xml", ".yaml", ".yml",
                 ".ini", ".cfg", ".sql", ".sh", ".rst"}
SUPPORTED = TEXT_SUFFIXES | {".pdf", ".docx"}


def attachments_dir() -> Path:
    path = default_data_dir() / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def _strip_html(raw: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            # One unreadable page must not throw away the other 299.
            parts.append("")
    return "\n\n".join(parts)


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", "replace")
    # Paragraph and line breaks become newlines before tags are stripped,
    # otherwise the whole document collapses into one unreadable line.
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def extract_text(filename: str, data: bytes, mime: str = "") -> str:
    """Best-effort plain text for a supported file.

    Raises ``ValueError`` with a message the UI can show when the format is not
    supported — never returns an empty string pretending success.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(
            f"{suffix or 'That file type'} is not supported. "
            f"Supported: {', '.join(sorted(SUPPORTED))}"
        )
    try:
        if suffix == ".pdf":
            text = _pdf_text(data)
        elif suffix == ".docx":
            text = _docx_text(data)
        elif suffix in (".html", ".htm"):
            text = _strip_html(data.decode("utf-8", "replace"))
        else:
            text = data.decode("utf-8", "replace")
    except ValueError:
        raise
    except Exception as exc:
        # A truncated or encrypted file is the user's problem to see, not a
        # 500 from a parser they never called.
        raise ValueError(f"{filename} could not be read as {suffix}: {exc}") from exc

    if not text.strip():
        # A scanned PDF has pages but no text layer; saying "empty" is honest,
        # silently indexing nothing is not.
        raise ValueError(
            f"{filename} contains no extractable text. "
            "Scanned PDFs need OCR before they can be read."
        )
    return text[:MAX_TEXT]


def chunk_text(text: str, size: int = CHUNK_CHARS,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on paragraph boundaries, with a little overlap.

    Overlap matters for retrieval: a fact split across a chunk boundary would
    otherwise be invisible to both chunks.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for para in paragraphs:
        while len(para) > size:                 # a single huge paragraph
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.append(para[:size])
            para = para[size - overlap:]
        if len(buffer) + len(para) + 2 <= size:
            buffer = f"{buffer}\n\n{para}" if buffer else para
        else:
            if buffer:
                chunks.append(buffer)
            buffer = para
    if buffer:
        chunks.append(buffer)
    return chunks


_WORD = re.compile(r"[a-z0-9]{3,}")


def _terms(query: str) -> list[str]:
    return list(dict.fromkeys(_WORD.findall((query or "").lower())))


class AttachmentStore:
    def __init__(self, db_path: str = ":memory:", *, root: Path | None = None):
        self._lock = threading.Lock()
        self._conn = connect(db_path)
        self.root = Path(root) if root else attachments_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._conn.executescript(
            """CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                filename TEXT NOT NULL,
                mime TEXT DEFAULT '',
                size INTEGER DEFAULT 0,
                sha256 TEXT DEFAULT '',
                text TEXT DEFAULT '',
                path TEXT DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attachment_chunks (
                id TEXT PRIMARY KEY,
                attachment_id TEXT NOT NULL,
                ord INTEGER NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_attachments_conv
                ON attachments(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_att
                ON attachment_chunks(attachment_id);"""
        )
        self._conn.commit()

    # -------------------------------------------------------------- write
    def add(self, filename: str, data: bytes, *, conversation_id: str | None = None,
            mime: str = "") -> dict[str, Any]:
        if not data:
            raise ValueError("The file is empty.")
        if len(data) > MAX_BYTES:
            raise ValueError(
                f"{filename} is {len(data) / 1e6:.1f} MB; the limit is "
                f"{MAX_BYTES / 1e6:.0f} MB."
            )
        text = extract_text(filename, data, mime)
        aid = secrets.token_hex(8)
        suffix = Path(filename).suffix.lower()
        target = self.root / f"{aid}{suffix}"
        target.write_bytes(data)
        try:
            target.chmod(0o600)
        except OSError:
            pass

        digest = hashlib.sha256(data).hexdigest()
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO attachments (id,conversation_id,filename,mime,size,sha256,"
                "text,path,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (aid, conversation_id, filename, mime, len(data), digest, text,
                 str(target), now),
            )
            for i, chunk in enumerate(chunk_text(text)):
                self._conn.execute(
                    "INSERT INTO attachment_chunks (id,attachment_id,ord,text) "
                    "VALUES (?,?,?,?)",
                    (secrets.token_hex(8), aid, i, chunk),
                )
            self._conn.commit()
        return self.get(aid) or {}

    def link(self, attachment_id: str, conversation_id: str) -> bool:
        """File an upload under the conversation that ended up using it.

        An upload happens before the first message, so it has no conversation
        yet; without this the file would be orphaned in the list.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE attachments SET conversation_id=? WHERE id=?",
                (conversation_id, attachment_id))
            self._conn.commit()
        return cur.rowcount > 0

    def delete(self, attachment_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT path FROM attachments WHERE id=?", (attachment_id,)).fetchone()
            if row is None:
                return False
            self._conn.execute("DELETE FROM attachment_chunks WHERE attachment_id=?",
                               (attachment_id,))
            self._conn.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
            self._conn.commit()
        if row[0]:
            try:
                Path(row[0]).unlink(missing_ok=True)
            except OSError:
                pass
        return True

    # --------------------------------------------------------------- read
    def get(self, attachment_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id,conversation_id,filename,mime,size,sha256,created_at,"
                "(SELECT COUNT(*) FROM attachment_chunks c WHERE c.attachment_id=a.id) "
                "FROM attachments a WHERE id=?", (attachment_id,)).fetchone()
        if row is None:
            return None
        return {"id": row[0], "conversation_id": row[1], "filename": row[2],
                "mime": row[3], "size": row[4], "sha256": row[5],
                "created_at": row[6], "chunks": row[7]}

    def list(self, conversation_id: str | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT id,conversation_id,filename,mime,size,sha256,created_at "
               "FROM attachments")
        params: tuple[Any, ...] = ()
        if conversation_id is not None:
            sql += " WHERE conversation_id=?"
            params = (conversation_id,)
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [{"id": r[0], "conversation_id": r[1], "filename": r[2], "mime": r[3],
                 "size": r[4], "sha256": r[5], "created_at": r[6]} for r in rows]

    def text_of(self, attachment_id: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT text FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        return row[0] if row else ""

    def retrieve(self, query: str, *, attachment_ids: list[str] | None = None,
                 limit: int = 4) -> list[dict[str, Any]]:
        """Rank chunks against a question by term overlap.

        Deliberately lexical, not embedding-based: it needs no model, no network
        and no index build, and for "what does this document say about X" it is
        accurate enough to be useful. The interface is the same shape a vector
        search would return, so swapping it later is contained here.
        """
        terms = _terms(query)
        if not terms:
            return []
        with self._lock:
            if attachment_ids:
                marks = ",".join("?" for _ in attachment_ids)
                rows = self._conn.execute(
                    f"SELECT c.text, a.id, a.filename FROM attachment_chunks c "
                    f"JOIN attachments a ON a.id=c.attachment_id "
                    f"WHERE c.attachment_id IN ({marks})", tuple(attachment_ids)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT c.text, a.id, a.filename FROM attachment_chunks c "
                    "JOIN attachments a ON a.id=c.attachment_id").fetchall()

        scored: list[tuple[float, dict[str, Any]]] = []
        for text, aid, filename in rows:
            low = text.lower()
            hits = sum(low.count(t) for t in terms)
            if not hits:
                continue
            coverage = sum(1 for t in terms if t in low) / len(terms)
            score = hits * (0.5 + coverage) / (len(text) ** 0.5)
            scored.append((score, {"attachment_id": aid, "filename": filename,
                                   "text": text.strip(), "score": round(score, 4)}))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def context_block(self, query: str, *, attachment_ids: list[str] | None = None,
                      max_chars: int = 6000) -> str:
        """A prompt-ready block of the most relevant file content."""
        hits = self.retrieve(query, attachment_ids=attachment_ids, limit=6)
        if not hits:
            return ""
        parts: list[str] = []
        used = 0
        for hit in hits:
            body = hit["text"][: max(0, max_chars - used)]
            if not body:
                break
            parts.append(f"[{hit['filename']}]\n{body}")
            used += len(body)
        return "\n\n".join(parts)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def describe(items: list[dict[str, Any]]) -> str:
    """One-line summary used by the API and the tests."""
    return json.dumps([{"id": i["id"], "filename": i["filename"]} for i in items])
