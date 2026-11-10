"""Read-only share links for a conversation.

Sharing is off by default. When it is switched on, a link carries a random
token, can expire, can be revoked, and shows exactly one conversation — never
the rest of the workspace. Viewing a link does not require the API token
because the link *is* the credential; that is the point, and it is why creating
one is a deliberate act with a visible warning.
"""

from __future__ import annotations

import html
import secrets
import time
from typing import Any

from .._sqlite import connect


class ShareStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS shares (
                token TEXT PRIMARY KEY, conversation_id TEXT, created_at REAL,
                expires_at REAL, revoked INTEGER, views INTEGER, title TEXT
            )"""
        )
        self._conn.commit()

    def create(self, conversation_id: str, *, title: str = "",
               ttl_hours: float | None = None) -> dict[str, Any]:
        token = secrets.token_urlsafe(18)
        now = time.time()
        expires = now + ttl_hours * 3600 if ttl_hours else None
        self._conn.execute(
            """INSERT INTO shares (token, conversation_id, created_at, expires_at,
                revoked, views, title) VALUES (?,?,?,?,0,0,?)""",
            (token, conversation_id, now, expires, title[:200]))
        self._conn.commit()
        return self.get(token) or {}

    def get(self, token: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT token, conversation_id, created_at, expires_at, revoked, views,
                      title FROM shares WHERE token=?""", (token,)).fetchone()
        if row is None:
            return None
        return {"token": row[0], "conversation_id": row[1], "created_at": row[2],
                "expires_at": row[3], "revoked": bool(row[4]), "views": row[5],
                "title": row[6],
                "url": f"/share/{row[0]}",
                "active": not row[4] and (row[3] is None or row[3] > time.time())}

    def for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT token FROM shares WHERE conversation_id=? ORDER BY created_at DESC""",
            (conversation_id,)).fetchall()
        return [self.get(r[0]) for r in rows if self.get(r[0])]

    def list(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT token FROM shares ORDER BY created_at DESC").fetchall()
        return [self.get(r[0]) for r in rows if self.get(r[0])]

    def revoke(self, token: str) -> bool:
        cur = self._conn.execute("UPDATE shares SET revoked=1 WHERE token=?", (token,))
        self._conn.commit()
        return cur.rowcount > 0

    def delete(self, token: str) -> bool:
        cur = self._conn.execute("DELETE FROM shares WHERE token=?", (token,))
        self._conn.commit()
        return cur.rowcount > 0

    def record_view(self, token: str) -> None:
        self._conn.execute("UPDATE shares SET views=views+1 WHERE token=?", (token,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def render_page(share: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    """A standalone page for one shared conversation."""
    title = html.escape(share.get("title") or "Shared conversation")
    blocks = []
    for message in messages:
        role = message.get("role", "user")
        if role not in ("user", "assistant"):
            continue
        body = html.escape(str(message.get("content", "")))
        label = "You" if role == "user" else "Tolesh"
        blocks.append(f'<article class="{role}"><div class="who">{label}</div>'
                      f'<div class="body">{body}</div></article>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title} — Tolesh</title>
<style>
:root{{--ink:#22201d;--muted:#6b635a;--line:#e6e0d6;--paper:#fbf8f3;--accent:#8a5a2b}}
body{{margin:0;background:var(--paper);color:var(--ink);
font:16px/1.65 ui-sans-serif,system-ui,-apple-system,sans-serif}}
main{{max-width:44rem;margin:0 auto;padding:2.5rem 1.25rem 5rem}}
header{{border-bottom:1px solid var(--line);padding-bottom:1rem;margin-bottom:1.5rem}}
h1{{font-size:1.35rem;margin:0 0 .3rem;letter-spacing:-.01em}}
.meta{{color:var(--muted);font-size:12.5px}}
article{{margin:1.4rem 0;padding:1rem 1.15rem;border-radius:12px;background:#fff;
border:1px solid var(--line)}}
article.user{{background:#f4efe6}}
.who{{font-size:11px;text-transform:uppercase;letter-spacing:.08em;
color:var(--muted);margin-bottom:.4rem}}
.body{{white-space:pre-wrap;word-wrap:break-word}}
footer{{margin-top:3rem;color:var(--muted);font-size:12px;text-align:center}}
</style></head><body><main>
<header><h1>{title}</h1>
<div class="meta">Shared read-only from Tolesh · {len(blocks)} messages</div></header>
{''.join(blocks)}
<footer>This page shows one conversation. The owner can revoke this link at any time.</footer>
</main></body></html>"""


def render_error(message: str) -> str:
    safe = html.escape(message)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="robots" content="noindex"><title>Tolesh — link unavailable</title>
<style>body{{font-family:system-ui,sans-serif;background:#fbf8f3;color:#22201d;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
div{{max-width:28rem;padding:2rem;background:#fff;border-radius:12px;
border-top:4px solid #a3341f;box-shadow:0 10px 30px rgba(0,0,0,.07)}}
h1{{font-size:1.1rem;margin:0 0 .5rem;color:#a3341f}}p{{margin:0;color:#4a453f}}</style>
</head><body><div><h1>This link is not available</h1><p>{safe}</p></div></body></html>"""
