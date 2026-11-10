"""Persistent conversations.

Kept separate from long-term memory on purpose: a conversation is the *current*
context window; ``memory_ext.LayeredMemory`` holds cross-conversation facts.
Both are SQLite, both are exportable and deletable, and the same schema can be
served by the shared backend for future web and Android clients.

Schema changes are additive and applied in place: an existing ``tolesh.db``
from an earlier build keeps every row, and the new columns (``pinned``,
``archived``, ``project_id``) default to the behaviour that build already had.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from typing import Any, Iterable

from .._sqlite import connect

# Sentinels: ``None`` is a meaningful filter value ("no project"), so the
# "don't filter by project at all" case needs its own object.
_UNSET: Any = object()


def _escape_like(term: str) -> str:
    """Make a user query literal inside ``LIKE``.

    Without this, searching for ``100%`` matches every row and ``a_b`` matches
    ``axb`` — a search box that quietly returns everything is worse than one
    that returns nothing.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
        self._migrate()
        self._conn.commit()

    # ------------------------------------------------------------- schema
    def _migrate(self) -> None:
        """Add the columns a newer build needs, keeping existing rows."""
        have = {row[1] for row in self._conn.execute("PRAGMA table_info(conversations)")}
        for column, ddl in (
            ("pinned", "ALTER TABLE conversations ADD COLUMN pinned INTEGER DEFAULT 0"),
            ("archived", "ALTER TABLE conversations ADD COLUMN archived INTEGER DEFAULT 0"),
            ("project_id", "ALTER TABLE conversations ADD COLUMN project_id TEXT"),
        ):
            if column not in have:
                self._conn.execute(ddl)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_project "
            "ON conversations(project_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_updated "
            "ON conversations(updated_at)"
        )

    # -------------------------------------------------------------- write
    def create(self, title: str = "New conversation",
               *, project_id: str | None = None) -> dict[str, Any]:
        cid = secrets.token_hex(8)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id,title,created_at,updated_at,pinned,archived,"
                "project_id) VALUES (?,?,?,?,0,0,?)",
                (cid, title, now, now, project_id),
            )
            self._conn.commit()
        return {"id": cid, "title": title, "created_at": now, "updated_at": now,
                "pinned": False, "archived": False, "project_id": project_id}

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

    def rename(self, conversation_id: str, title: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                (title, time.time(), conversation_id))
            self._conn.commit()
        return cur.rowcount > 0

    def set_pinned(self, conversation_id: str, pinned: bool) -> bool:
        return self._flag("pinned", conversation_id, pinned)

    def set_archived(self, conversation_id: str, archived: bool) -> bool:
        return self._flag("archived", conversation_id, archived)

    def set_project(self, conversation_id: str, project_id: str | None) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE conversations SET project_id=? WHERE id=?",
                (project_id, conversation_id))
            self._conn.commit()
        return cur.rowcount > 0

    def _flag(self, column: str, conversation_id: str, value: bool) -> bool:
        # ``column`` is never user input: both call sites are named methods.
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE conversations SET {column}=? WHERE id=?",
                (1 if value else 0, conversation_id))
            self._conn.commit()
        return cur.rowcount > 0

    def delete(self, conversation_id: str) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            cur = self._conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            self._conn.commit()
        return cur.rowcount > 0

    # --------------------------------------------------------------- read
    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id,title,created_at,updated_at,pinned,archived,project_id "
                "FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if row is None:
            return None
        return {"id": row[0], "title": row[1], "created_at": row[2], "updated_at": row[3],
                "pinned": bool(row[4]), "archived": bool(row[5]), "project_id": row[6]}

    def list(self, *, include_archived: bool = False,
             project_id: Any = _UNSET, query: str | None = None,
             limit: int = 200) -> list[dict[str, Any]]:
        """Conversations, pinned first, then most recently touched.

        ``query`` searches titles and every message body, and returns a snippet
        from the first matching message so the result list can show *why* a
        conversation matched instead of only its title.
        """
        where: list[str] = []
        params: list[Any] = []
        if not include_archived:
            where.append("COALESCE(c.archived,0)=0")
        if project_id is not _UNSET:
            if project_id is None:
                where.append("c.project_id IS NULL")
            else:
                where.append("c.project_id=?")
                params.append(project_id)

        term = (query or "").strip()
        if term:
            like = f"%{_escape_like(term)}%"
            where.append(
                "(c.title LIKE ? ESCAPE '\\' OR EXISTS ("
                "  SELECT 1 FROM messages m WHERE m.conversation_id=c.id "
                "  AND m.content LIKE ? ESCAPE '\\'))"
            )
            params.extend([like, like])

        sql = (
            "SELECT c.id,c.title,c.created_at,c.updated_at,COUNT(m.id),"
            "COALESCE(c.pinned,0),COALESCE(c.archived,0),c.project_id "
            "FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id "
            + ("WHERE " + " AND ".join(where) + " " if where else "")
            + "GROUP BY c.id ORDER BY COALESCE(c.pinned,0) DESC, c.updated_at DESC LIMIT ?"
        )
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()

        items = [{"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3],
                  "message_count": r[4], "pinned": bool(r[5]), "archived": bool(r[6]),
                  "project_id": r[7]} for r in rows]
        if term:
            for item in items:
                item["snippet"] = self._snippet(item["id"], term)
        return items

    def _snippet(self, conversation_id: str, term: str, width: int = 90) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM messages WHERE conversation_id=? "
                "AND content LIKE ? ESCAPE '\\' ORDER BY rowid ASC LIMIT 1",
                (conversation_id, f"%{_escape_like(term)}%")).fetchone()
        if row is None:
            return ""
        text = " ".join(str(row[0]).split())
        at = text.lower().find(term.lower())
        if at < 0:
            return text[:width]
        start = max(0, at - width // 3)
        return ("…" if start else "") + text[start:start + width].strip() + "…"

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

    # ------------------------------------------------------------- export
    def export(self, conversation_id: str, fmt: str = "markdown") -> str:
        """Export one conversation. Raises ``KeyError`` for an unknown id."""
        meta = self.get(conversation_id)
        if meta is None:
            raise KeyError(conversation_id)
        rows = self.messages(conversation_id, limit=10_000)
        if fmt in ("json", "application/json"):
            return json.dumps({"conversation": meta, "messages": rows},
                              ensure_ascii=False, indent=2)
        if fmt not in ("markdown", "md", "text"):
            raise ValueError(f"Unsupported export format: {fmt}")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta["created_at"]))
        lines = [f"# {meta['title']}", "", f"*Exported from Tolesh · {when}*", ""]
        for row in rows:
            who = {"user": "You", "assistant": "Tolesh"}.get(row["role"], row["role"])
            suffix = f" · {row['model']}" if row.get("model") else ""
            lines.append(f"**{who}**{suffix}")
            lines.append("")
            lines.append(row["content"].strip())
            lines.append("")
        return "\n".join(lines)

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            archived = self._conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE COALESCE(archived,0)=1").fetchone()[0]
            messages = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return {"conversations": total, "archived": archived, "messages": messages}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def transcripts(manager: ConversationManager,
                ids: Iterable[str]) -> list[dict[str, Any]]:
    """Convenience for bulk reads (used by project export)."""
    return [{"conversation": manager.get(cid), "messages": manager.messages(cid)}
            for cid in ids]
