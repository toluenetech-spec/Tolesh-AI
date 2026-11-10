"""Projects: named workspaces that group conversations and carry instructions.

The ChatGPT analogue is a "Project": a folder with its own context that every
conversation inside it inherits. Tolesh keeps the same idea but stores it in the
one shared SQLite file, so a project survives a restart, a provider change and
the desktop/web switch without a separate service.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any

from .._sqlite import connect

MAX_NAME = 80
MAX_INSTRUCTIONS = 4000


class ProjectStore:
    def __init__(self, db_path: str = ":memory:"):
        self._lock = threading.Lock()
        self._conn = connect(db_path)
        self._conn.executescript(
            """CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                instructions TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );"""
        )
        self._conn.commit()

    def create(self, name: str, *, description: str = "",
               instructions: str = "") -> dict[str, Any]:
        clean = (name or "").strip()
        if not clean:
            raise ValueError("A project needs a name.")
        if len(clean) > MAX_NAME:
            raise ValueError(f"Project name is limited to {MAX_NAME} characters.")
        pid = secrets.token_hex(8)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects (id,name,description,instructions,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (pid, clean, description[:2000], instructions[:MAX_INSTRUCTIONS], now, now),
            )
            self._conn.commit()
        return self.get(pid) or {}

    def update(self, project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if "name" in patch and patch["name"] is not None:
            name = str(patch["name"]).strip()
            if not name:
                raise ValueError("A project needs a name.")
            if len(name) > MAX_NAME:
                raise ValueError(f"Project name is limited to {MAX_NAME} characters.")
            fields["name"] = name
        if "description" in patch and patch["description"] is not None:
            fields["description"] = str(patch["description"])[:2000]
        if "instructions" in patch and patch["instructions"] is not None:
            fields["instructions"] = str(patch["instructions"])[:MAX_INSTRUCTIONS]
        if not fields:
            raise ValueError("No fields to update")
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE projects SET {sets}, updated_at=? WHERE id=?",
                (*fields.values(), time.time(), project_id))
            self._conn.commit()
        if cur.rowcount == 0:
            raise KeyError(project_id)
        return self.get(project_id) or {}

    def get(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id,name,description,instructions,created_at,updated_at "
                "FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                return None
            count = self._conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE project_id=?", (project_id,)
            ).fetchone()[0]
        return {"id": row[0], "name": row[1], "description": row[2],
                "instructions": row[3], "created_at": row[4], "updated_at": row[5],
                "conversation_count": count}

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.id,p.name,p.description,p.instructions,p.created_at,p.updated_at,"
                "(SELECT COUNT(*) FROM conversations c WHERE c.project_id=p.id) "
                "FROM projects p ORDER BY p.updated_at DESC").fetchall()
        return [{"id": r[0], "name": r[1], "description": r[2], "instructions": r[3],
                 "created_at": r[4], "updated_at": r[5], "conversation_count": r[6]}
                for r in rows]

    def delete(self, project_id: str) -> bool:
        """Delete a project and detach its conversations.

        The conversations themselves are *not* deleted: losing a chat because a
        folder was removed is data loss the user did not ask for. They become
        unfiled instead.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET project_id=NULL WHERE project_id=?", (project_id,))
            cur = self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def instructions_for(self, project_id: str | None) -> str:
        if not project_id:
            return ""
        project = self.get(project_id)
        return (project or {}).get("instructions", "") or ""

    def close(self) -> None:
        with self._lock:
            self._conn.close()
