"""Custom assistants and reusable prompt templates.

An assistant is a saved system prompt with a name, an optional model and an
optional greeting — the same idea as a custom GPT, kept small enough to reason
about. A template is a prompt body the composer can insert.

Both are content, so both are covered by export and deletion.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from .._sqlite import connect

MAX_INSTRUCTIONS = 8000


class AssistantStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS assistants (
                id TEXT PRIMARY KEY, name TEXT, description TEXT,
                instructions TEXT, model TEXT, greeting TEXT, tools TEXT,
                icon TEXT, created_at REAL, updated_at REAL, use_count INTEGER
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS prompt_templates (
                id TEXT PRIMARY KEY, name TEXT, body TEXT, category TEXT,
                created_at REAL, updated_at REAL, use_count INTEGER
            )"""
        )
        self._conn.commit()

    # ------------------------------------------------------------ assistants
    def create_assistant(self, name: str, *, instructions: str = "",
                         description: str = "", model: str = "",
                         greeting: str = "", tools: list[str] | None = None,
                         icon: str = "") -> dict[str, Any]:
        if not (name or "").strip():
            raise ValueError("An assistant needs a name.")
        if len(instructions) > MAX_INSTRUCTIONS:
            raise ValueError("Instructions are limited to 8000 characters.")
        now = time.time()
        assistant_id = f"asst-{secrets.token_hex(5)}"
        self._conn.execute(
            """INSERT INTO assistants (id, name, description, instructions, model,
                greeting, tools, icon, created_at, updated_at, use_count)
               VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
            (assistant_id, name.strip()[:80], description[:500], instructions,
             model[:200], greeting[:2000], _dumps(tools or []), icon[:16], now, now))
        self._conn.commit()
        return self.get_assistant(assistant_id) or {}

    def update_assistant(self, assistant_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "description", "instructions", "model", "greeting",
                   "tools", "icon"}
        fields = {k: v for k, v in patch.items() if k in allowed}
        if not fields:
            raise ValueError("No supported fields to update.")
        if "instructions" in fields and len(str(fields["instructions"])) > MAX_INSTRUCTIONS:
            raise ValueError("Instructions are limited to 8000 characters.")
        if "tools" in fields:
            fields["tools"] = _dumps(list(fields["tools"] or []))
        fields["updated_at"] = time.time()
        if self.get_assistant(assistant_id) is None:
            raise KeyError(assistant_id)
        columns = ", ".join(f"{k}=?" for k in fields)
        self._conn.execute(f"UPDATE assistants SET {columns} WHERE id=?",
                           (*fields.values(), assistant_id))
        self._conn.commit()
        return self.get_assistant(assistant_id) or {}

    def get_assistant(self, assistant_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT id, name, description, instructions, model, greeting, tools,
                      icon, created_at, updated_at, use_count
               FROM assistants WHERE id=?""", (assistant_id,)).fetchone()
        return self._assistant(row) if row else None

    def assistants(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT id, name, description, instructions, model, greeting, tools,
                      icon, created_at, updated_at, use_count
               FROM assistants ORDER BY created_at ASC""").fetchall()
        return [self._assistant(r) for r in rows]

    def delete_assistant(self, assistant_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM assistants WHERE id=?", (assistant_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def touch_assistant(self, assistant_id: str) -> None:
        self._conn.execute(
            "UPDATE assistants SET use_count=use_count+1 WHERE id=?", (assistant_id,))
        self._conn.commit()

    # ------------------------------------------------------------- templates
    def create_template(self, name: str, body: str, *,
                        category: str = "") -> dict[str, Any]:
        if not (name or "").strip() or not (body or "").strip():
            raise ValueError("A template needs a name and a body.")
        now = time.time()
        template_id = f"tpl-{secrets.token_hex(5)}"
        self._conn.execute(
            """INSERT INTO prompt_templates (id, name, body, category, created_at,
                updated_at, use_count) VALUES (?,?,?,?,?,?,0)""",
            (template_id, name.strip()[:80], body[:8000], category[:40], now, now))
        self._conn.commit()
        return self.get_template(template_id) or {}

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT id, name, body, category, created_at, updated_at, use_count
               FROM prompt_templates WHERE id=?""", (template_id,)).fetchone()
        return self._template(row) if row else None

    def templates(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT id, name, body, category, created_at, updated_at, use_count
               FROM prompt_templates ORDER BY created_at ASC""").fetchall()
        return [self._template(r) for r in rows]

    def delete_template(self, template_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM prompt_templates WHERE id=?",
                                 (template_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def touch_template(self, template_id: str) -> None:
        self._conn.execute(
            "UPDATE prompt_templates SET use_count=use_count+1 WHERE id=?",
            (template_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _assistant(row: Any) -> dict[str, Any]:
        return {"id": row[0], "name": row[1], "description": row[2],
                "instructions": row[3], "model": row[4], "greeting": row[5],
                "tools": _loads(row[6]), "icon": row[7], "created_at": row[8],
                "updated_at": row[9], "use_count": row[10]}

    @staticmethod
    def _template(row: Any) -> dict[str, Any]:
        return {"id": row[0], "name": row[1], "body": row[2], "category": row[3],
                "created_at": row[4], "updated_at": row[5], "use_count": row[6]}


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str) -> list[str]:
    import json

    try:
        value = json.loads(raw or "[]")
        return [str(v) for v in value] if isinstance(value, list) else []
    except Exception:
        return []
