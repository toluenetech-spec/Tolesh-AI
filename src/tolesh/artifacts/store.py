"""Artifacts: files Tolesh produced, kept so the user can open them later.

An artifact is a real file on disk with a database row describing it. The name
is sanitised and the id is generated, so a model cannot choose where a file
lands; the directory is 0700 and the files 0600.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Any

from .._sqlite import connect, default_data_dir

MAX_BYTES = 25 * 1024 * 1024


def default_root() -> Path:
    override = os.environ.get("TOLESH_ARTIFACTS_DIR")
    if override:
        return Path(override).expanduser()
    return default_data_dir() / "artifacts"


def safe_filename(name: str, fallback: str = "artifact") -> str:
    base = Path(str(name or "").replace("\\", "/")).name
    cleaned = "".join(ch for ch in base if ch.isalnum() or ch in "._- ()")
    cleaned = cleaned.strip(" .")
    return (cleaned or fallback)[:100]


def _guess_kind(mime: str, name: str) -> str:
    lowered = (mime or "").lower()
    suffix = Path(name).suffix.lower()
    if lowered.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".svg", ".webp"}:
        return "image"
    if lowered.startswith("text/html") or suffix == ".html":
        return "report"
    if suffix in {".csv", ".tsv", ".xlsx", ".json"}:
        return "dataset"
    if lowered.startswith("text/") or suffix in {".md", ".txt", ".py"}:
        return "document"
    return "file"


class ArtifactStore:
    def __init__(self, db_path: str = ":memory:", *, root: str | Path | None = None):
        self.root = Path(root) if root else default_root()
        self._conn = connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, name TEXT, kind TEXT, mime TEXT, size INTEGER,
                created_at REAL, conversation_id TEXT, run_id TEXT, meta TEXT
            )"""
        )
        self._conn.commit()

    # ------------------------------------------------------------------ write
    def save_bytes(self, name: str, data: bytes, *, kind: str = "",
                   mime: str = "", conversation_id: str | None = None,
                   run_id: str | None = None,
                   meta: dict[str, Any] | None = None) -> dict[str, Any]:
        if len(data) > MAX_BYTES:
            raise ValueError(f"An artifact may not exceed {MAX_BYTES // (1024 * 1024)} MB.")
        safe = safe_filename(name)
        artifact_id = f"art-{secrets.token_hex(6)}"
        folder = self.root / artifact_id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(folder, 0o700)
        except OSError:
            pass
        target = folder / safe
        target.write_bytes(data)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        self._conn.execute(
            "INSERT INTO artifacts (id, name, kind, mime, size, created_at, "
            "conversation_id, run_id, meta) VALUES (?,?,?,?,?,?,?,?,?)",
            (artifact_id, safe, kind or _guess_kind(mime, safe), mime or "",
             len(data), time.time(), conversation_id or "", run_id or "",
             _dumps(meta or {})))
        self._conn.commit()
        return self.get(artifact_id) or {}

    def save_text(self, name: str, text: str, *, kind: str = "document",
                  mime: str = "text/markdown", **kwargs: Any) -> dict[str, Any]:
        return self.save_bytes(name, text.encode("utf-8"), kind=kind, mime=mime, **kwargs)

    # ------------------------------------------------------------------- read
    def get(self, artifact_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, name, kind, mime, size, created_at, conversation_id, "
            "run_id, meta FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, *, conversation_id: str | None = None, kind: str | None = None,
             limit: int = 100) -> list[dict[str, Any]]:
        sql = ("SELECT id, name, kind, mime, size, created_at, conversation_id, "
               "run_id, meta FROM artifacts")
        clauses, params = [], []
        if conversation_id:
            clauses.append("conversation_id=?")
            params.append(conversation_id)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [self._row(r) for r in self._conn.execute(sql, params).fetchall()]

    def path(self, artifact_id: str) -> Path | None:
        meta = self.get(artifact_id)
        if meta is None:
            return None
        candidate = self.root / artifact_id / meta["name"]
        return candidate if candidate.is_file() else None

    def read_bytes(self, artifact_id: str) -> bytes | None:
        path = self.path(artifact_id)
        return path.read_bytes() if path else None

    def delete(self, artifact_id: str) -> bool:
        meta = self.get(artifact_id)
        if meta is None:
            return False
        self._conn.execute("DELETE FROM artifacts WHERE id=?", (artifact_id,))
        self._conn.commit()
        folder = self.root / artifact_id
        try:
            for child in folder.glob("*"):
                child.unlink(missing_ok=True)
            folder.rmdir()
        except OSError:
            pass
        return True

    def stats(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM artifacts").fetchone()
        return {"count": row[0], "bytes": row[1]}

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {"id": row[0], "name": row[1], "kind": row[2], "mime": row[3],
                "size": row[4], "created_at": row[5],
                "conversation_id": row[6], "run_id": row[7],
                "meta": _loads(row[8]),
                "download_url": f"/api/artifacts/{row[0]}/download"}


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(raw: str) -> dict[str, Any]:
    import json

    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}
