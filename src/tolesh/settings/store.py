"""User settings that are not provider configuration.

Stored in the same database as everything else so an export or a deletion
covers them. Values are validated on write: a setting that accepts anything is
a setting that fails silently later.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .._sqlite import connect

# Every setting has a default and a type. ``telemetry`` is listed on purpose:
# Tolesh sends none, and the UI says so instead of offering a switch that would
# imply otherwise.
DEFAULTS: dict[str, Any] = {
    "memory_enabled": True,
    "memory_auto_extract": True,
    "memory_retrieval": True,
    "memory_categories": ["preference", "personal", "project", "task"],
    "memory_extraction_mode": "rules",
    "memory_extraction_model": "",
    "memory_retrieval_limit": 6,
    "web_access": True,
    "store_web_content": True,
    "code_execution": True,
    "require_approval_for_writes": True,
    "share_links_enabled": False,
    "agent_max_steps": 8,
    "audit_retention_days": 90,
    "telemetry": False,
    "default_landing": "chat",
}

_TYPES = {key: type(value) for key, value in DEFAULTS.items()}

DESCRIPTIONS = {
    "memory_enabled": "Let Tolesh keep long-term notes about you.",
    "memory_auto_extract": "Learn from conversations in the background, without being asked.",
    "memory_retrieval": "Put relevant memories into the prompt when answering.",
    "memory_categories": "Which kinds of memory may be stored automatically.",
    "memory_extraction_mode": "'rules' is deterministic and free; 'model' asks a model to read the conversation.",
    "memory_extraction_model": "Model used for extraction when the mode is 'model'. Empty lets the router choose.",
    "memory_retrieval_limit": "How many memories one answer may see (1-12).",
    "web_access": "Allow reading web pages and searching when a message asks for it.",
    "store_web_content": "Keep retrieved page text with the conversation.",
    "code_execution": "Allow the sandboxed Python runner.",
    "require_approval_for_writes": "Ask before any action that changes data outside Tolesh.",
    "share_links_enabled": "Allow creating read-only public links to a conversation.",
    "agent_max_steps": "How many tool steps one agent run may take (1-40).",
    "audit_retention_days": "How long audit entries are kept (1-3650).",
    "telemetry": "Tolesh sends no telemetry; this cannot be switched on.",
    "default_landing": "Which panel opens first.",
}

# Categories the memory layer understands. Kept here as a literal rather than an
# import so the settings module stays dependency-free.
MEMORY_CATEGORIES = ("conversation", "preference", "personal", "project", "task")


class SettingsError(ValueError):
    pass


class SettingsStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, "
            "updated_at REAL)"
        )
        self._conn.commit()

    def all(self) -> dict[str, Any]:
        rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        stored: dict[str, Any] = {}
        for key, raw in rows:
            try:
                stored[key] = json.loads(raw)
            except Exception:
                continue
        merged = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
        return {"values": merged,
                "descriptions": DESCRIPTIONS,
                "telemetry_note": "No telemetry is collected or sent by Tolesh."}

    def get(self, key: str) -> Any:
        return self.all()["values"].get(key, DEFAULTS.get(key))

    def patch(self, changes: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in (changes or {}).items():
            if key not in DEFAULTS:
                raise SettingsError(f"Unknown setting: {key}")
            if key == "telemetry":
                if value:
                    raise SettingsError("Tolesh has no telemetry to enable.")
                cleaned[key] = False
                continue
            expected = _TYPES[key]
            if expected is bool and not isinstance(value, bool):
                raise SettingsError(f"{key} must be true or false.")
            if expected is int and (not isinstance(value, int) or isinstance(value, bool)):
                raise SettingsError(f"{key} must be a whole number.")
            if expected is str and not isinstance(value, str):
                raise SettingsError(f"{key} must be text.")
            if expected is list:
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise SettingsError(f"{key} must be a list of category names.")
                unknown = [v for v in value if v not in MEMORY_CATEGORIES]
                if unknown:
                    raise SettingsError(f"{key} contains an unknown category: {unknown[0]}")
            if key == "agent_max_steps" and not 1 <= value <= 40:
                raise SettingsError("agent_max_steps must be between 1 and 40.")
            if key == "audit_retention_days" and not 1 <= value <= 3650:
                raise SettingsError("audit_retention_days must be between 1 and 3650.")
            if key == "memory_retrieval_limit" and not 1 <= value <= 12:
                raise SettingsError("memory_retrieval_limit must be between 1 and 12.")
            if key == "memory_extraction_mode" and value not in ("rules", "model"):
                raise SettingsError("memory_extraction_mode must be 'rules' or 'model'.")
            if key == "memory_extraction_model" and len(value) > 200:
                raise SettingsError("memory_extraction_model is too long.")
            if key == "default_landing" and value not in ("chat", "workspace"):
                raise SettingsError("default_landing must be 'chat' or 'workspace'.")
            cleaned[key] = value
        now = time.time()
        for key, value in cleaned.items():
            self._conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(value), now))
        self._conn.commit()
        return self.all()

    def reset(self) -> dict[str, Any]:
        self._conn.execute("DELETE FROM settings")
        self._conn.commit()
        return self.all()

    def close(self) -> None:
        self._conn.close()
