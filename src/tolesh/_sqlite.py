"""Shared SQLite connection helper for Tolesh's persistent stores.

The desktop application, the embedded API server and the inherited voice
daemon can all hold the same database file open at once. SQLite's default
rollback journal lets only one writer lock the whole file, which surfaces as
``sqlite3.OperationalError: database is locked`` as soon as two of those
processes write at the same moment.

Write-Ahead Logging plus a busy timeout lets one writer and many readers
coexist across processes instead of failing. Both pragmas are cheap and are
skipped for ``:memory:`` databases (tests, in-memory AppState).

This module deliberately does not change ``row_factory``: the stores in this
package read columns positionally, and changing the row type here would
silently alter their behaviour.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

# How long a writer waits for the lock before raising, in milliseconds.
BUSY_TIMEOUT_MS = 5000

# How long a statement keeps retrying a table lock, in seconds. Shared-cache
# in-memory databases (used by tests and by an in-memory AppState) take
# table-level locks that the busy handler does not retry, so two stores writing
# at once surfaced as "database table is locked" — a real failure, not a slow
# query. File databases use WAL and rarely reach this path.
_LOCKED_RETRY_SECONDS = 2.0
_LOCKED_MESSAGES = ("database is locked", "database table is locked")


class _RetryingConnection(sqlite3.Connection):
    """Connection that retries a statement whose table was momentarily locked.

    Only single statements are retried, which is what the stores issue. A
    transaction spanning several statements is not replayed, because replaying
    half of one would be worse than the error.
    """

    def _retry(self, operation, *args, **kwargs):
        deadline = time.monotonic() + _LOCKED_RETRY_SECONDS
        delay = 0.005
        while True:
            try:
                return operation(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if not any(text in message for text in _LOCKED_MESSAGES):
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.1)

    def execute(self, *args, **kwargs):  # type: ignore[override]
        return self._retry(super().execute, *args, **kwargs)

    def executemany(self, *args, **kwargs):  # type: ignore[override]
        return self._retry(super().executemany, *args, **kwargs)


def default_data_dir() -> Path:
    """Single source of truth for Tolesh's mutable user-data directory.

    ``TOLESH_DATA_DIR`` wins so tests and portable installs can redirect every
    store at once; then ``XDG_DATA_HOME``; then the Linux default. The
    desktop app, the embedded API server and the inherited daemon all resolve
    their database through here, which is what keeps them on one file.
    """
    override = os.environ.get("TOLESH_DATA_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "tolesh"
    return Path.home() / ".local" / "share" / "tolesh"


def default_db_path() -> str:
    """Path of the shared SQLite database, honouring ``TOLESH_DB_PATH``."""
    override = os.environ.get("TOLESH_DB_PATH")
    if override:
        return str(Path(override).expanduser())
    base = default_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "tolesh.db")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open *path* with cross-process-safe pragmas applied.

    Parent directories are created for file-backed databases so a fresh
    install works without a manual ``mkdir``.

    ``file:`` URIs are passed through with ``uri=True``. That is how several
    stores share one in-memory database: plain ``":memory:"`` gives every
    connection its own empty database, so a second store would not see the
    first one's tables.
    """
    target = str(path)
    is_uri = target.startswith("file:")
    if not is_uri and target != ":memory:":
        Path(target).expanduser().parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        target,
        uri=is_uri,
        check_same_thread=False,
        timeout=BUSY_TIMEOUT_MS / 1000,
        factory=_RetryingConnection,
    )
    if not is_uri and target != ":memory:":
        try:
            # WAL is persistent once set on the file, but re-issuing it is
            # harmless and covers databases created before this helper.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            # A read-only or exotic filesystem must not stop Tolesh from
            # opening its own database; fall back to the default journal.
            pass
    return conn
