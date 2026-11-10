"""Encrypted storage for external connection tokens.

Tokens are encrypted with Fernet before they touch SQLite. The key lives in
its own 0600 file outside the database, so a copy of ``tolesh.db`` alone is
not enough to use someone's GitHub or Google account. Nothing in this module
ever returns a plaintext token to a caller outside the connector layer.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from .._sqlite import connect

try:
    from cryptography.fernet import Fernet, InvalidToken

    CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment,misc]
    CRYPTO_AVAILABLE = False


def default_key_path() -> Path:
    override = os.environ.get("TOLESH_CONNECTOR_KEY_PATH")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("TOLESH_CONFIG_DIR")
    root = Path(base).expanduser() if base else Path.home() / ".config" / "tolesh"
    return root / "connector.key"


class VaultError(RuntimeError):
    pass


class TokenVault:
    """Symmetric encryption with a per-install key file."""

    def __init__(self, key_path: str | Path | None = None):
        self.key_path = Path(key_path) if key_path else default_key_path()
        self._fernet: Any = None

    def _cipher(self) -> Any:
        if self._fernet is not None:
            return self._fernet
        if not CRYPTO_AVAILABLE:  # pragma: no cover
            raise VaultError(
                "The 'cryptography' package is required to store connection "
                "tokens. Install it, or use an API token per request instead.")
        if not self.key_path.exists():
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            self.key_path.write_bytes(key)
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass
        key = self.key_path.read_bytes().strip()
        try:
            self._fernet = Fernet(key)
        except Exception as exc:
            raise VaultError(
                f"The connector key at {self.key_path} is not a valid Fernet key.") from exc
        return self._fernet

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        return self._cipher().encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._cipher().decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise VaultError(
                "A stored connection token could not be decrypted. The key file "
                "changed or the record is damaged; reconnect the account.") from exc


class ConnectorStore:
    def __init__(self, db_path: str = ":memory:", *, vault: TokenVault | None = None):
        self.vault = vault or TokenVault()
        self._conn = connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS connections (
                id TEXT PRIMARY KEY, provider TEXT, label TEXT, account TEXT,
                scopes TEXT, access_token TEXT, refresh_token TEXT,
                token_type TEXT, expires_at REAL, status TEXT, error TEXT,
                created_at REAL, updated_at REAL, meta TEXT
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY, provider TEXT, redirect_uri TEXT,
                code_verifier TEXT, client_id TEXT, client_secret TEXT,
                scopes TEXT, created_at REAL, expires_at REAL, base_url TEXT
            )"""
        )
        # Additive migration for databases created before ``base_url`` existed.
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(oauth_states)")}
        if "base_url" not in columns:
            self._conn.execute("ALTER TABLE oauth_states ADD COLUMN base_url TEXT")
        self._conn.commit()

    # ----------------------------------------------------------- connections
    def upsert(self, *, connection_id: str, provider: str, access_token: str,
               refresh_token: str = "", token_type: str = "Bearer",
               expires_at: float | None = None, scopes: list[str] | None = None,
               account: str = "", label: str = "",
               meta: dict[str, Any] | None = None) -> dict[str, Any]:
        now = time.time()
        existing = self.get(connection_id)
        self._conn.execute(
            """INSERT INTO connections
               (id, provider, label, account, scopes, access_token, refresh_token,
                token_type, expires_at, status, error, created_at, updated_at, meta)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 provider=excluded.provider, label=excluded.label,
                 account=excluded.account, scopes=excluded.scopes,
                 access_token=excluded.access_token,
                 refresh_token=excluded.refresh_token,
                 token_type=excluded.token_type, expires_at=excluded.expires_at,
                 status=excluded.status, error='', updated_at=excluded.updated_at,
                 meta=excluded.meta""",
            (connection_id, provider, label or provider, account,
             json.dumps(scopes or []), self.vault.encrypt(access_token),
             self.vault.encrypt(refresh_token), token_type, expires_at,
             "connected", "", existing["created_at"] if existing else now, now,
             json.dumps(meta or {})),
        )
        self._conn.commit()
        return self.get(connection_id) or {}

    def get(self, connection_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT id, provider, label, account, scopes, access_token,
                      refresh_token, token_type, expires_at, status, error,
                      created_at, updated_at, meta
               FROM connections WHERE id=?""", (connection_id,)).fetchone()
        return self._public(row) if row else None

    def secrets(self, connection_id: str) -> dict[str, Any] | None:
        """Decrypted tokens — only the connector layer may call this."""
        row = self._conn.execute(
            """SELECT id, provider, access_token, refresh_token, token_type,
                      expires_at, scopes FROM connections WHERE id=?""",
            (connection_id,)).fetchone()
        if row is None:
            return None
        return {"id": row[0], "provider": row[1],
                "access_token": self.vault.decrypt(row[2] or ""),
                "refresh_token": self.vault.decrypt(row[3] or ""),
                "token_type": row[4] or "Bearer", "expires_at": row[5],
                "scopes": json.loads(row[6] or "[]")}

    def list(self, provider: str | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT id, provider, label, account, scopes, access_token, "
               "refresh_token, token_type, expires_at, status, error, created_at, "
               "updated_at, meta FROM connections")
        params: tuple[Any, ...] = ()
        if provider:
            sql += " WHERE provider=?"
            params = (provider,)
        sql += " ORDER BY created_at ASC"
        return [self._public(row) for row in self._conn.execute(sql, params).fetchall()]

    def delete(self, connection_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM connections WHERE id=?", (connection_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def set_status(self, connection_id: str, status: str, error: str = "") -> None:
        self._conn.execute(
            "UPDATE connections SET status=?, error=?, updated_at=? WHERE id=?",
            (status, error[:500], time.time(), connection_id))
        self._conn.commit()

    # ---------------------------------------------------------- oauth states
    def save_state(self, state: str, *, provider: str, redirect_uri: str,
                   code_verifier: str, client_id: str, client_secret: str,
                   scopes: list[str], ttl: float = 600.0,
                   base_url: str = "") -> None:
        now = time.time()
        self._conn.execute(
            """INSERT OR REPLACE INTO oauth_states
               (state, provider, redirect_uri, code_verifier, client_id,
                client_secret, scopes, created_at, expires_at, base_url)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (state, provider, redirect_uri, code_verifier, client_id,
             self.vault.encrypt(client_secret), json.dumps(scopes), now,
             now + ttl, base_url))
        self._conn.commit()

    def take_state(self, state: str) -> dict[str, Any] | None:
        """Consume a state exactly once, and only while it is fresh."""
        row = self._conn.execute(
            """SELECT state, provider, redirect_uri, code_verifier, client_id,
                      client_secret, scopes, expires_at, base_url FROM oauth_states
               WHERE state=?""", (state,)).fetchone()
        if row is None:
            return None
        self._conn.execute("DELETE FROM oauth_states WHERE state=?", (state,))
        self._conn.commit()
        if row[7] < time.time():
            return None
        return {"state": row[0], "provider": row[1], "redirect_uri": row[2],
                "code_verifier": row[3], "client_id": row[4],
                "client_secret": self.vault.decrypt(row[5] or ""),
                "scopes": json.loads(row[6] or "[]"), "base_url": row[8] or ""}

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _public(row: Any) -> dict[str, Any]:
        expires_at = row[8]
        expired = bool(expires_at and expires_at < time.time())
        return {
            "id": row[0], "provider": row[1], "label": row[2], "account": row[3],
            "scopes": json.loads(row[4] or "[]"),
            # Presence, never the value.
            "has_token": bool(row[5]), "has_refresh": bool(row[6]),
            "token_type": row[7], "expires_at": expires_at,
            "expired": expired,
            "status": "expired" if expired and row[9] == "connected" else row[9],
            "error": row[10], "created_at": row[11], "updated_at": row[12],
            "meta": json.loads(row[13] or "{}"),
        }


def new_connection_id(provider: str) -> str:
    return f"{provider}-{secrets.token_hex(4)}"


def pkce_pair() -> tuple[str, str]:
    """RFC 7636 verifier/challenge pair (S256)."""
    import hashlib

    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge
