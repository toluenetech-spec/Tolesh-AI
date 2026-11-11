"""Tolesh's layered personal memory.

Five layers, because the brief requires distinguishing them:

  * ``conversation`` — context that belongs to one thread of work
  * ``preference``   — how the user likes things done
  * ``personal``     — stable facts about the person
  * ``project``      — a body of work and its decisions
  * ``task``         — ongoing activity

Everything lives in one readable SQLite table on the same database as the rest
of Tolesh, so an export or a deletion covers it. Deleting is a real ``DELETE``.
Superseding is not: when a fact is corrected the old row is kept with
``status='superseded'`` and a pointer to its replacement, because "I used to
think X, you corrected me to Y" is information the user may want to audit — and
because silently overwriting would make the correction impossible to verify.

The store is deliberately dumb about *what* to remember. Extraction lives in
:mod:`tolesh.memory_ext.extract`, ranking in :mod:`tolesh.memory_ext.retrieve`,
and the orchestration (background worker, settings, prompt budget) in
:mod:`tolesh.memory_ext.service`.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .._sqlite import connect
from . import text as mtext

CATEGORIES = ("conversation", "preference", "personal", "project", "task")

_CATEGORY_LABELS = {
    "conversation": "Conversation context",
    "preference": "Preference",
    "personal": "Personal info",
    "project": "Project",
    "task": "Task / ongoing activity",
}

# Statuses a row can hold. ``active`` is the only one retrieval will ever see.
STATUSES = ("active", "superseded")

# How similar two memories must be before the second one is treated as the
# same fact. High on purpose: merging two genuinely different preferences is
# worse than storing a near-duplicate the user can delete.
DUPLICATE_THRESHOLD = 0.86

# An explicit correction ("actually I use Postgres now") replaces its target at
# a lower similarity, because the user has told us it is the same slot.
CORRECTION_THRESHOLD = 0.42


@dataclass
class MemoryItem:
    id: str
    category: str
    content: str
    source: str
    pinned: bool
    created_at: float
    updated_at: float
    confidence: float = 1.0
    importance: float = 0.5
    project_id: str | None = None
    conversation_id: str | None = None
    origin: str = "manual"
    status: str = "active"
    superseded_by: str | None = None
    disabled: bool = False
    use_count: int = 0
    last_used_at: float | None = None
    key: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "category": self.category,
            "category_label": _CATEGORY_LABELS.get(self.category, self.category),
            "content": self.content, "source": self.source,
            "pinned": self.pinned, "created_at": self.created_at,
            "updated_at": self.updated_at,
            "confidence": round(self.confidence, 3),
            "importance": round(self.importance, 3),
            "project_id": self.project_id,
            "conversation_id": self.conversation_id,
            "origin": self.origin,
            "status": self.status,
            "superseded_by": self.superseded_by,
            "disabled": self.disabled,
            "use_count": self.use_count,
            "last_used_at": self.last_used_at,
            "key": self.key,
            "evidence": self.evidence,
        }


_COLUMNS = """id, category, content, source, pinned, created_at, updated_at,
              confidence, importance, project_id, conversation_id, origin, status,
              superseded_by, disabled, use_count, last_used_at, key, evidence"""

# Columns added after the first release. Added with ALTER TABLE so an existing
# database keeps every row it already had — the alternative (drop and recreate)
# would delete the user's memory to upgrade the schema.
_ADDED_COLUMNS = (
    ("confidence", "REAL NOT NULL DEFAULT 1.0"),
    ("importance", "REAL NOT NULL DEFAULT 0.5"),
    ("project_id", "TEXT"),
    ("conversation_id", "TEXT"),
    ("origin", "TEXT NOT NULL DEFAULT 'manual'"),
    ("status", "TEXT NOT NULL DEFAULT 'active'"),
    ("superseded_by", "TEXT"),
    ("disabled", "INTEGER NOT NULL DEFAULT 0"),
    ("use_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_used_at", "REAL"),
    ("key", "TEXT"),
    ("evidence", "TEXT NOT NULL DEFAULT '{}'"),
)


class LayeredMemory:
    """One table, five categories, honest deletion, auditable corrections."""

    def __init__(self, db_path: str = ":memory:"):
        self._new_id = lambda: secrets.token_hex(8)
        self._lock = threading.RLock()
        self._conn = connect(db_path)
        self._migrate()

    # ------------------------------------------------------------- schema
    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, category TEXT NOT NULL, content TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'user', pinned INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                )"""
            )
            existing = {row[1] for row in
                        self._conn.execute("PRAGMA table_info(memories)").fetchall()}
            for name, ddl in _ADDED_COLUMNS:
                if name not in existing:
                    self._conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {ddl}")
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS memory_suppressions (
                    id TEXT PRIMARY KEY, phrase TEXT NOT NULL, created_at REAL NOT NULL,
                    conversation_id TEXT
                )"""
            )
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS memory_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
                    kind TEXT NOT NULL, memory_id TEXT, detail TEXT
                )"""
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, category)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at)"
            )
            self._conn.commit()

    # -------------------------------------------------------------- write
    def add(self, category: str, content: str, *, source: str = "user",
            pinned: bool = False, confidence: float = 1.0,
            importance: float = 0.5, project_id: str | None = None,
            conversation_id: str | None = None, origin: str = "manual",
            key: str | None = None, evidence: dict[str, Any] | None = None,
            dedupe: bool = True) -> dict[str, Any]:
        """Store one memory, merging it into an existing near-duplicate.

        ``dedupe=False`` is for the tests and for the "add exactly what I typed"
        path in the UI, where a user who deliberately adds a second copy should
        get a second copy.
        """
        if category not in CATEGORIES:
            raise ValueError(f"unknown category '{category}'; expected one of {CATEGORIES}")
        content = str(content).strip()
        if not content:
            raise ValueError("memory content must not be empty")
        now = time.time()

        if dedupe:
            twin = self.find_duplicate(content, category=category, project_id=project_id)
            if twin is not None:
                merged = self._merge_into(twin, content=content,
                                          confidence=confidence, source=source,
                                          evidence=evidence)
                self._event("merged", merged["id"], {"with": twin["id"]})
                return merged

        mid = self._new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories (id,category,content,source,pinned,created_at,"
                "updated_at,confidence,importance,project_id,conversation_id,origin,"
                "status,disabled,key,evidence) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'active',0,?,?)",
                (mid, category, content, source, int(pinned), now, now,
                 float(confidence), float(importance), project_id, conversation_id,
                 origin, key, json.dumps(evidence or {}, ensure_ascii=False)),
            )
            self._conn.commit()
        self._event("created", mid, {"origin": origin, "category": category})
        return self.get(mid)  # type: ignore[return-value]

    def _merge_into(self, existing: dict[str, Any], *, content: str,
                    confidence: float, source: str,
                    evidence: dict[str, Any] | None) -> dict[str, Any]:
        """Keep the better-formed wording, raise confidence, refresh the clock."""
        now = time.time()
        keep_new = len(content) > len(existing["content"]) + 8
        merged_evidence = dict(existing.get("evidence") or {})
        if evidence:
            merged_evidence.update(evidence)
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET content=?, updated_at=?, confidence=?, evidence=?, "
                "source=CASE WHEN ?='user' THEN 'user' ELSE source END WHERE id=?",
                (content if keep_new else existing["content"], now,
                 max(float(existing.get("confidence") or 0.0), float(confidence)),
                 json.dumps(merged_evidence, ensure_ascii=False), source, existing["id"]),
            )
            self._conn.commit()
        return self.get(existing["id"])  # type: ignore[return-value]

    def update(self, item_id: str, **fields: Any) -> dict[str, Any] | None:
        """Edit a memory in place. Only the listed fields are writable.

        Editing content resets ``confidence`` to 1.0: a human has just confirmed
        the fact, which is the strongest evidence the store can hold.
        """
        allowed = {"category", "content", "pinned", "confidence", "importance",
                   "project_id", "disabled", "status", "key"}
        changes = {k: v for k, v in fields.items() if k in allowed}
        if not changes:
            return self.get(item_id)
        if "category" in changes and changes["category"] not in CATEGORIES:
            raise ValueError(f"unknown category '{changes['category']}'")
        if "content" in changes:
            content = str(changes["content"]).strip()
            if not content:
                raise ValueError("memory content must not be empty")
            changes["content"] = content
            if "confidence" not in changes:
                changes["confidence"] = 1.0
        if "pinned" in changes:
            changes["pinned"] = int(bool(changes["pinned"]))
        if "disabled" in changes:
            changes["disabled"] = int(bool(changes["disabled"]))
        changes["updated_at"] = time.time()
        assignments = ", ".join(f"{name}=?" for name in changes)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE memories SET {assignments} WHERE id=?",
                (*changes.values(), item_id),
            )
            self._conn.commit()
        if cur.rowcount == 0:
            return None
        self._event("updated", item_id, {"fields": sorted(changes)})
        return self.get(item_id)

    def supersede(self, old_id: str, new_id: str) -> dict[str, Any] | None:
        """Mark *old_id* as replaced by *new_id* without deleting it.

        Retrieval ignores superseded rows, so the corrected fact is the only one
        the model sees; the user can still see both, and which replaced which.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memories SET status='superseded', superseded_by=?, updated_at=? "
                "WHERE id=? AND status='active'",
                (new_id, time.time(), old_id),
            )
            self._conn.commit()
        if cur.rowcount == 0:
            return None
        self._event("superseded", old_id, {"by": new_id})
        return self.get(old_id)

    def delete(self, item_id: str) -> bool:
        """Honour a deletion request: physically remove the row."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE id=?", (item_id,))
            self._conn.commit()
        if cur.rowcount:
            self._event("deleted", item_id, {})
        return cur.rowcount > 0

    def forget_everything(self, confirm: str) -> dict[str, Any]:
        """Wipe all memory. Requires the literal confirmation phrase so a stray
        request can never nuke the user's data."""
        if confirm.strip().upper() != "FORGET EVERYTHING":
            return {"status": "error",
                    "message": "Confirmation phrase must be exactly 'FORGET EVERYTHING'."}
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            self._conn.execute("DELETE FROM memories")
            self._conn.execute("DELETE FROM memory_suppressions")
            self._conn.commit()
        self._event("wiped", None, {"deleted": count})
        return {"status": "ok", "deleted": count}

    def mark_used(self, ids: Iterable[str], *, now: float | None = None) -> None:
        """Record that these memories were put in front of the model.

        Used for ranking feedback and so the UI can show which memories are
        actually earning their place.
        """
        stamp = now or time.time()
        rows = [(stamp, mid) for mid in ids if mid]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE memories SET use_count=use_count+1, last_used_at=? WHERE id=?", rows)
            self._conn.commit()

    # --------------------------------------------------------------- read
    def get(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                f"SELECT {_COLUMNS} FROM memories WHERE id=?", (item_id,)
            ).fetchone()
        return self._row(r) if r else None

    def list(self, category: str | None = None, limit: int = 200, *,
             project_id: str | None = None, include_superseded: bool = False,
             include_disabled: bool = True, status: str | None = None,
             origin: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category=?")
            params.append(category)
        if status:
            clauses.append("status=?")
            params.append(status)
        elif not include_superseded:
            clauses.append("status='active'")
        if not include_disabled:
            clauses.append("disabled=0")
        if project_id is not None:
            clauses.append("project_id IS ?")
            params.append(project_id)
        if origin:
            clauses.append("origin=?")
            params.append(origin)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM memories {where} "
                "ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row(r) for r in rows]

    def active(self, *, project_id: str | None = None) -> list[dict[str, Any]]:
        """Everything retrieval is allowed to see: active and not switched off."""
        return self.list(limit=10000, project_id=project_id, include_disabled=False)

    def search(self, query: str, limit: int = 10, *,
               include_superseded: bool = False) -> list[dict[str, Any]]:
        """Rank by relevance rather than by "contains the substring".

        ``LIKE '%q%'`` was the old behaviour and it could not answer "what is my
        stack" against "prefers Python". This scores every active row and keeps
        the ones that actually share content words.
        """
        query = (query or "").strip()
        if not query:
            return []
        rows = self.list(limit=10000, include_superseded=include_superseded)
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            score = mtext.similarity(query, row["content"])
            if row["pinned"]:
                score += 0.05
            if score > 0.02:
                scored.append((score, row))
        scored.sort(key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)
        out = []
        for score, row in scored[:limit]:
            row = dict(row)
            row["score"] = round(score, 4)
            out.append(row)
        return out

    def find_duplicate(self, content: str, *, category: str | None = None,
                       project_id: str | None = None,
                       threshold: float = DUPLICATE_THRESHOLD) -> dict[str, Any] | None:
        best: tuple[float, dict[str, Any]] | None = None
        for row in self.list(limit=10000, include_superseded=False):
            if category and row["category"] != category:
                continue
            if (row["project_id"] or None) != (project_id or None):
                continue
            score = mtext.similarity(content, row["content"])
            if score >= threshold and (best is None or score > best[0]):
                best = (score, row)
        return best[1] if best else None

    def find_correction_target(self, content: str, *, category: str,
                               project_id: str | None = None,
                               threshold: float = CORRECTION_THRESHOLD) -> dict[str, Any] | None:
        """The active memory an explicit correction is most likely replacing.

        Narrower than :meth:`find_duplicate`: same category, same project scope,
        and a real content overlap. A correction that matches nothing is stored
        as a new memory instead of guessing which fact to overwrite.
        """
        best: tuple[float, dict[str, Any]] | None = None
        for row in self.list(limit=10000, category=category, include_superseded=False):
            if (row["project_id"] or None) != (project_id or None):
                continue
            if row["pinned"] and row["origin"] == "user":
                # A fact the user typed and pinned is not silently replaced by
                # an inferred correction; the user edits it themselves.
                continue
            score = mtext.similarity(content, row["content"])
            if score >= threshold and (best is None or score > best[0]):
                best = (score, row)
        return best[1] if best else None

    # -------------------------------------------------------- suppressions
    def add_suppression(self, phrase: str, *, conversation_id: str | None = None) -> dict[str, Any]:
        """Record "do not remember this", so retrieval and extraction both stop.

        A deletion alone is not enough: the next conversation would extract the
        same fact again. This is the list that makes forgetting stick.
        """
        phrase = (phrase or "").strip()
        if not phrase:
            raise ValueError("suppression phrase must not be empty")
        sid = self._new_id()
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO memory_suppressions (id, phrase, created_at, conversation_id) "
                "VALUES (?,?,?,?)", (sid, phrase, now, conversation_id))
            self._conn.commit()
        self._event("suppressed", sid, {"phrase": phrase[:120]})
        return {"id": sid, "phrase": phrase, "created_at": now}

    def suppressions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, phrase, created_at, conversation_id FROM memory_suppressions "
                "ORDER BY created_at DESC").fetchall()
        return [{"id": r[0], "phrase": r[1], "created_at": r[2],
                 "conversation_id": r[3]} for r in rows]

    def remove_suppression(self, sid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memory_suppressions WHERE id=?", (sid,))
            self._conn.commit()
        return cur.rowcount > 0

    def is_suppressed(self, content: str, *, threshold: float = 0.55) -> bool:
        for row in self.suppressions():
            if mtext.similarity(content, row["phrase"]) >= threshold:
                return True
        return False

    def forget_matching(self, phrase: str, *, limit: int = 20) -> dict[str, Any]:
        """Delete every memory that matches *phrase*, and block its return.

        Superseded rows are included: "forget that I live in Lagos" has to
        remove the replaced copy too, or the user deletes a fact and still finds
        it in the panel's history.
        """
        matches = [row for row in self.list(limit=10000, include_superseded=True)
                   if mtext.similarity(phrase, row["content"]) >= 0.45]
        matches.sort(key=lambda row: mtext.similarity(phrase, row["content"]), reverse=True)
        matches = matches[:limit]
        for row in matches:
            self.delete(row["id"])
        suppression = self.add_suppression(phrase)
        return {"deleted": [row["id"] for row in matches], "suppression": suppression}

    # -------------------------------------------------------------- events
    def _event(self, kind: str, memory_id: str | None, detail: dict[str, Any]) -> None:
        """A short audit trail: what the store did, without storing content twice."""
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO memory_events (ts, kind, memory_id, detail) VALUES (?,?,?,?)",
                    (time.time(), kind, memory_id,
                     json.dumps(detail, ensure_ascii=False)[:2000]))
                self._conn.commit()
        except sqlite3.Error:
            # An audit row must never be the reason a memory write fails.
            pass

    def events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, kind, memory_id, detail FROM memory_events "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "kind": r[1], "memory_id": r[2],
                 "detail": json.loads(r[3] or "{}")} for r in rows]

    # ------------------------------------------------------------- context
    def build_context(self, max_chars: int = 1200) -> str:
        """Transparent prompt injection: exactly what is stored, grouped by
        category. Used as the fallback when no query is available (the voice
        daemon and the "what do you know" path), never as the chat default —
        dumping the whole store into every turn is what the retrieval path
        exists to avoid."""
        items = self.list(limit=10000, include_disabled=False)
        if not items:
            return ""
        grouped: dict[str, list[str]] = {c: [] for c in CATEGORIES}
        for it in items:
            grouped.setdefault(it["category"], []).append(it["content"])
        lines: list[str] = []
        for cat in CATEGORIES:
            if grouped.get(cat):
                lines.append(f"[{_CATEGORY_LABELS[cat]}]")
                lines.extend(f"- {c}" for c in grouped[cat])
        return "\n".join(lines)[:max_chars]

    def export(self) -> dict[str, Any]:
        return {"categories": list(CATEGORIES), "items": self.list(limit=10000),
                "suppressions": self.suppressions()}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
            superseded = self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE status='superseded'").fetchone()[0]
            by_category = dict(self._conn.execute(
                "SELECT category, COUNT(*) FROM memories WHERE status='active' "
                "GROUP BY category").fetchall())
            auto = self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE origin IN ('auto','correction') "
                "AND status='active'").fetchone()[0]
            suppressed = self._conn.execute(
                "SELECT COUNT(*) FROM memory_suppressions").fetchone()[0]
            last = self._conn.execute("SELECT MAX(updated_at) FROM memories").fetchone()[0]
        return {
            "total": total,
            "superseded": superseded,
            "by_category": {c: by_category.get(c, 0) for c in CATEGORIES},
            "automatic": auto,
            "manual": total - auto,
            "suppressions": suppressed,
            "last_updated": last,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -------------------------------------------------------------- helper
    @staticmethod
    def _row(r) -> dict[str, Any]:
        try:
            evidence = json.loads(r[18] or "{}")
        except (json.JSONDecodeError, TypeError):
            evidence = {}
        return MemoryItem(
            id=r[0], category=r[1], content=r[2], source=r[3],
            pinned=bool(r[4]), created_at=r[5], updated_at=r[6],
            confidence=float(r[7] if r[7] is not None else 1.0),
            importance=float(r[8] if r[8] is not None else 0.5),
            project_id=r[9], conversation_id=r[10],
            origin=r[11] or "manual", status=r[12] or "active",
            superseded_by=r[13], disabled=bool(r[14]),
            use_count=int(r[15] or 0), last_used_at=r[16],
            key=r[17], evidence=evidence,
        ).as_dict()
