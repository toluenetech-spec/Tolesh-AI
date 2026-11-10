"""Persistent conversations.

Kept separate from long-term memory on purpose: a conversation is the *current*
context window; ``memory_ext.LayeredMemory`` holds cross-conversation facts.
Both are SQLite, both are exportable and deletable, and the same schema can be
served by the shared backend for future web and Android clients.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any

from .._sqlite import connect


class ConversationManager:
    def __init__(self, db_path: str = ":memory:"):
        self._lock = threading.Lock()
        self._conn = connect(db_path)
        self._conn.executescript(
            """CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL,
                model TEXT, tokens INTEGER DEFAULT 0, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);"""
        )
        self._conn.commit()

    def create(self, title: str = "New conversation") -> dict[str, Any]:
        cid = secrets.token_hex(8)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id,title,created_at,updated_at) VALUES (?,?,?,?)",
                (cid, title, now, now),
            )
            self._conn.commit()
        return {"id": cid, "title": title, "created_at": now, "updated_at": now}

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id,c.title,c.created_at,c.updated_at,COUNT(m.id) "
                "FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id "
                "GROUP BY c.id ORDER BY c.updated_at DESC"
            ).fetchall()
        return [{"id": r[0], "title": r[1], "created_at": r[2],
                 "updated_at": r[3], "message_count": r[4]} for r in rows]

    def add_message(self, conversation_id: str, role: str, content: str,
                    *, model: str | None = None, tokens: int = 0) -> dict[str, Any]:
        mid = secrets.token_hex(8)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (id,conversation_id,role,content,model,tokens,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (mid, conversation_id, role, content, model, tokens, now),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
            self._conn.commit()
        return {"id": mid, "conversation_id": conversation_id, "role": role,
                "content": content, "model": model, "tokens": tokens,
                "created_at": now}

    def messages(self, conversation_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id,role,content,model,tokens,created_at FROM messages "
                "WHERE conversation_id=? ORDER BY created_at ASC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [{"id": r[0], "role": r[1], "content": r[2], "model": r[3],
                 "tokens": r[4], "created_at": r[5]} for r in rows]

    def window(self, conversation_id: str, limit: int = 20) -> list[dict[str, str]]:
        """The last N turns in the exact shape the provider expects.

        ``messages()`` deliberately returns the whole transcript in ascending
        order for the UI. A model context window is the opposite query: take the
        newest N turns, then restore chronological order. Using rowid (not the
        float ``created_at``) keeps the order stable when several messages are
        written within the same clock tick — otherwise a long conversation
        would silently drop its most recent turns.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT role, content FROM (
                       SELECT rowid, role, content FROM messages
                       WHERE conversation_id=? AND role IN ('user','assistant')
                       ORDER BY rowid DESC LIMIT ?
                   ) ORDER BY rowid ASC""",
                (conversation_id, limit),
            ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]

    def rename(self, conversation_id: str, title: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                (title, time.time(), conversation_id))
            self._conn.commit()
        return cur.rowcount > 0

    def delete(self, conversation_id: str) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            cur = self._conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
