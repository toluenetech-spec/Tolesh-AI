"""Persistent agent runs.

An agent that forgets what it did is not auditable and cannot be resumed after
an approval or a crash. Every run stores its goal, status, message list and
step log; the message list is what makes resuming possible without replaying
side effects.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from .._sqlite import connect


class AgentHistory:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY, goal TEXT, status TEXT, model TEXT,
                conversation_id TEXT, created_at REAL, updated_at REAL,
                steps INTEGER, messages TEXT, result TEXT, error TEXT,
                approval_id TEXT
            )"""
        )
        self._conn.commit()

    def create(self, goal: str, *, model: str | None = None,
               conversation_id: str | None = None) -> dict[str, Any]:
        run_id = f"run-{secrets.token_hex(6)}"
        now = time.time()
        self._conn.execute(
            "INSERT INTO agent_runs (id, goal, status, model, conversation_id, "
            "created_at, updated_at, steps, messages, result, error, approval_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, goal, "running", model or "", conversation_id or "", now, now,
             0, "[]", "", "", ""))
        self._conn.commit()
        return self.get(run_id) or {}

    def update(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"status", "model", "steps", "messages", "result", "error",
                   "approval_id", "conversation_id"}
        patch = {k: v for k, v in fields.items() if k in allowed}
        if not patch:
            return self.get(run_id)
        if "messages" in patch and not isinstance(patch["messages"], str):
            patch["messages"] = json.dumps(patch["messages"], ensure_ascii=False)
        patch["updated_at"] = time.time()
        columns = ", ".join(f"{k}=?" for k in patch)
        self._conn.execute(f"UPDATE agent_runs SET {columns} WHERE id=?",
                           (*patch.values(), run_id))
        self._conn.commit()
        return self.get(run_id)

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT id, goal, status, model, conversation_id, created_at,
                      updated_at, steps, messages, result, error, approval_id
               FROM agent_runs WHERE id=?""", (run_id,)).fetchone()
        return self._row(row) if row else None

    def messages(self, run_id: str) -> list[dict[str, Any]]:
        run = self.get(run_id)
        if not run:
            return []
        try:
            value = json.loads(run["messages"])
            return value if isinstance(value, list) else []
        except Exception:
            return []

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT id, goal, status, model, conversation_id, created_at,
                      updated_at, steps, messages, result, error, approval_id
               FROM agent_runs ORDER BY created_at DESC LIMIT ?""", (limit,)).fetchall()
        out = []
        for row in rows:
            run = self._row(row)
            # The message list can be large; the list view only needs a preview.
            run.pop("messages", None)
            out.append(run)
        return out

    def delete(self, run_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM agent_runs WHERE id=?", (run_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {"id": row[0], "goal": row[1], "status": row[2], "model": row[3],
                "conversation_id": row[4], "created_at": row[5],
                "updated_at": row[6], "steps": row[7], "messages": row[8],
                "result": row[9], "error": row[10], "approval_id": row[11]}
