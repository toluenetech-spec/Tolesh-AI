"""Tolesh's layered personal memory.

The brief requires distinguishing:
  * current conversation context  (owned by conversation.Manager)
  * long-term user preferences     -> "preference"
  * important personal info        -> "personal"
  * project information            -> "project"
  * tasks and ongoing activities   -> "task"

Everything is stored in one readable SQLite table, is listed in the UI, and can
be deleted individually or wiped entirely. Deletion is a real DELETE, not a
soft flag. Nothing is collected automatically from files, mic or camera.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from .._sqlite import connect

CATEGORIES = ("conversation", "preference", "personal", "project", "task")

_CATEGORY_LABELS = {
    "conversation": "Conversation context",
    "preference": "Preference",
    "personal": "Personal info",
    "project": "Project",
    "task": "Task / ongoing activity",
}


@dataclass
class MemoryItem:
    id: str
    category: str
    content: str
    source: str
    pinned: bool
    created_at: float
    updated_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "category": self.category,
            "category_label": _CATEGORY_LABELS.get(self.category, self.category),
            "content": self.content, "source": self.source,
            "pinned": self.pinned, "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class LayeredMemory:
    def __init__(self, db_path: str = ":memory:"):
        import secrets
        self._new_id = lambda: secrets.token_hex(8)
        self._lock = threading.Lock()
        self._conn = connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, category TEXT NOT NULL, content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'user', pinned INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            )"""
        )
        self._conn.commit()

    def add(self, category: str, content: str, *, source: str = "user",
            pinned: bool = False) -> dict[str, Any]:
        if category not in CATEGORIES:
            raise ValueError(f"unknown category '{category}'; expected one of {CATEGORIES}")
        content = str(content).strip()
        if not content:
            raise ValueError("memory content must not be empty")
        now = time.time()
        mid = self._new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories (id,category,content,source,pinned,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (mid, category, content, source, int(pinned), now, now),
            )
            self._conn.commit()
        return self.get(mid)  # type: ignore[return-value]

    def get(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT id,category,content,source,pinned,created_at,updated_at "
                "FROM memories WHERE id=?", (item_id,),
            ).fetchone()
        return self._row(r) if r else None

    def list(self, category: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            if category:
                rows = self._conn.execute(
                    "SELECT id,category,content,source,pinned,created_at,updated_at "
                    "FROM memories WHERE category=? ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                    (category, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id,category,content,source,pinned,created_at,updated_at "
                    "FROM memories ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row(r) for r in rows]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = f"%{query.strip()}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT id,category,content,source,pinned,created_at,updated_at "
                "FROM memories WHERE content LIKE ? ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                (q, limit),
            ).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, item_id: str) -> bool:
        """Honour a deletion request: physically remove the row."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE id=?", (item_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def forget_everything(self, confirm: str) -> dict[str, Any]:
        """Wipe all memory. Requires the literal confirmation phrase so a stray
        request can never nuke the user's data."""
        if confirm.strip().upper() != "FORGET EVERYTHING":
            return {"status": "error",
                    "message": "Confirmation phrase must be exactly 'FORGET EVERYTHING'."}
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            self._conn.execute("DELETE FROM memories")
            self._conn.commit()
        return {"status": "ok", "deleted": count}

    def export(self) -> dict[str, Any]:
        return {"categories": list(CATEGORIES), "items": self.list(limit=10000)}

    def build_context(self, max_chars: int = 1200) -> str:
        """Transparent prompt injection: exactly what is stored, grouped by
        category. The user can compare this against the Memory panel."""
        items = self.list(limit=10000)
        if not items:
            return ""
        grouped: dict[str, list[str]] = {c: [] for c in CATEGORIES}
        for it in items:
            grouped.setdefault(it["category"], []).append(it["content"])
        lines: list[str] = []
        for cat in CATEGORIES:
            if grouped.get(cat):
                lines.append(f"[{_CATEGORY_LABELS[cat]}]")
                lines.extend(f"- {c}" for c in grouped[cat])
        text = "\n".join(lines)
        return text[:max_chars]

    def stats(self) -> dict[str, Any]:
        items = self.list(limit=10000)
        return {
            "total": len(items),
            "by_category": {c: sum(1 for i in items if i["category"] == c)
                            for c in CATEGORIES},
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(r) -> dict[str, Any]:
        return MemoryItem(
            id=r[0], category=r[1], content=r[2], source=r[3],
            pinned=bool(r[4]), created_at=r[5], updated_at=r[6],
        ).as_dict()
