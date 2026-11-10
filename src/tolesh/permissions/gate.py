"""Confirmation gate + audit log for computer control.

Design rules (from the Tolesh brief):
  * permissions, confirmations and safety controls BEFORE consequential actions
  * the user sees exactly what will happen and approves with a token
  * requests expire; nothing executes silently; every decision is auditable
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, asdict
from typing import Any

from .._sqlite import connect
from .policy import RiskLevel, classify


@dataclass
class PendingAction:
    id: str
    token: str
    tool: str
    args: dict[str, Any]
    risk: str
    reason: str
    status: str            # pending | approved | denied | expired
    created_at: float
    expires_at: float

    def public(self) -> dict[str, Any]:
        # The confirmation token is shown to the human in the local UI so they
        # can approve the exact request they can see. It is not a credential:
        # real access control is TOLESH_API_TOKEN plus the explicit human click.
        return asdict(self)


class PermissionGate:
    def __init__(self, db_path: str = ":memory:", *, ttl_sec: float = 300.0):
        self.ttl = ttl_sec
        self._lock = threading.Lock()
        self._conn = connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS permissions (
                id TEXT PRIMARY KEY, token TEXT, tool TEXT, args TEXT,
                risk TEXT, reason TEXT, status TEXT,
                created_at REAL, expires_at REAL, decided_at REAL
            )"""
        )
        self._conn.commit()

    # ------------------------------------------------------------- requesting
    def request(self, tool: str, args: dict[str, Any] | None = None,
                reason: str = "") -> dict[str, Any]:
        args = args or {}
        risk = classify(tool, args)
        if risk is RiskLevel.SAFE:
            # Safe actions are allowed immediately but still audited.
            self._audit(tool, args, risk.value, "auto-approved", reason)
            return {"status": "auto-approved", "risk": risk.value,
                    "tool": tool, "args": args, "token": None, "id": None}

        now = time.time()
        aid = secrets.token_hex(8)
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._conn.execute(
                "INSERT INTO permissions (id,token,tool,args,risk,reason,status,created_at,expires_at,decided_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                (aid, token, tool, _dumps(args), risk.value, reason, "pending",
                 now, now + self.ttl),
            )
            self._conn.commit()
        return {
            "status": "pending", "risk": risk.value, "tool": tool, "args": args,
            "id": aid, "token": token, "expires_at": now + self.ttl,
            "reason": reason,
        }

    # -------------------------------------------------------------- decisions
    def pending(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT id,token,tool,args,risk,reason,status,created_at,expires_at "
                "FROM permissions WHERE status='pending' ORDER BY created_at DESC"
            ).fetchall()
            expired = [r[0] for r in rows if r[8] < now]
            for rid in expired:
                self._conn.execute(
                    "UPDATE permissions SET status='expired', decided_at=? WHERE id=?",
                    (now, rid))
            if expired:
                self._conn.commit()
        out = []
        for r in rows:
            if r[6] != "pending" or r[8] < now:
                continue
            out.append(PendingAction(
                id=r[0], token=r[1], tool=r[2], args=_loads(r[3]), risk=r[4],
                reason=r[5], status=r[6], created_at=r[7], expires_at=r[8],
            ).public())
        return out

    def decide(self, action_id: str, token: str, approve: bool) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT id,token,tool,args,risk,reason,status,expires_at FROM permissions WHERE id=?",
                (action_id,),
            ).fetchone()
            if row is None:
                return {"status": "error", "message": "Unknown action id."}
            if row[6] != "pending":
                return {"status": "error", "message": f"Action already {row[6]}."}
            if row[7] < now:
                self._conn.execute(
                    "UPDATE permissions SET status='expired', decided_at=? WHERE id=?",
                    (now, action_id))
                self._conn.commit()
                return {"status": "error", "message": "Action expired."}
            if not secrets.compare_digest(str(row[1]), str(token)):
                return {"status": "error", "message": "Invalid approval token."}
            status = "approved" if approve else "denied"
            self._conn.execute(
                "UPDATE permissions SET status=?, decided_at=? WHERE id=?",
                (status, now, action_id))
            self._conn.commit()
        self._audit(row[2], _loads(row[3]), row[4], status, row[5])
        return {"status": status, "tool": row[2], "args": _loads(row[3]),
                "risk": row[4], "id": action_id}

    def approve(self, action_id: str, token: str) -> dict[str, Any]:
        return self.decide(action_id, token, True)

    def deny(self, action_id: str, token: str) -> dict[str, Any]:
        return self.decide(action_id, token, False)

    def _audit(self, tool: str, args: dict, risk: str, status: str, reason: str) -> None:
        # Kept in the same table so the full decision history is queryable.
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO permissions "
                "(id,token,tool,args,risk,reason,status,created_at,expires_at,decided_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"audit-{secrets.token_hex(6)}", "", tool, _dumps(args), risk,
                 reason, status, time.time(), time.time(), time.time()),
            )
            self._conn.commit()

    def audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id,tool,args,risk,status,created_at FROM permissions "
                "WHERE status != 'pending' ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"id": r[0], "tool": r[1], "args": _loads(r[2]), "risk": r[3],
                 "status": r[4], "created_at": r[5]} for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _loads(raw: str) -> dict[str, Any]:
    import json
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}
