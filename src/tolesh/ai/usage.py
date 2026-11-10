"""Token / usage accounting for every routed completion."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from .._sqlite import connect


def default_db_path() -> str:
    """Delegates to the shared resolver so every store uses one database."""
    from .._sqlite import default_db_path as _resolve
    return _resolve()


@dataclass
class UsageRecord:
    ts: float
    provider: str
    model: str
    task: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    status: str


class UsageLedger:
    """Small SQLite ledger. One connection guarded by a lock so FastAPI's
    threadpool workers can record concurrently without corruption."""

    def __init__(self, db_path: str | None = None, *, in_memory: bool = False):
        self.path = ":memory:" if in_memory else (db_path or default_db_path())
        self._lock = threading.Lock()
        self._conn = connect(self.path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                task TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok'
            )"""
        )
        self._conn.commit()

    def record(self, *, provider: str, model: str, task: str,
               prompt_tokens: int, completion_tokens: int,
               latency_ms: int, status: str = "ok") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage (ts,provider,model,task,prompt_tokens,completion_tokens,latency_ms,status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), provider, model, task, prompt_tokens,
                 completion_tokens, latency_ms, status),
            )
            self._conn.commit()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.cursor()
            total = cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
                "COALESCE(AVG(latency_ms),0) FROM usage"
            ).fetchone()
            by_provider = [
                {"provider": r[0], "requests": r[1], "tokens": r[2]}
                for r in cur.execute(
                    "SELECT provider, COUNT(*), COALESCE(SUM(prompt_tokens+completion_tokens),0) AS tokens "
                    "FROM usage GROUP BY provider ORDER BY tokens DESC"
                ).fetchall()
            ]
            by_model = [
                {"model": r[0], "requests": r[1], "tokens": r[2]}
                for r in cur.execute(
                    "SELECT model, COUNT(*), COALESCE(SUM(prompt_tokens+completion_tokens),0) AS tokens "
                    "FROM usage GROUP BY model ORDER BY tokens DESC LIMIT 20"
                ).fetchall()
            ]
            errors = cur.execute(
                "SELECT COUNT(*) FROM usage WHERE status != 'ok'"
            ).fetchone()[0]
        return {
            "requests": total[0],
            "prompt_tokens": total[1],
            "completion_tokens": total[2],
            "total_tokens": total[1] + total[2],
            "avg_latency_ms": round(total[3], 1),
            "errors": errors,
            "by_provider": by_provider,
            "by_model": by_model,
        }

    def reset(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM usage")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
