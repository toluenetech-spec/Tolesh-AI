"""Export and deletion of everything Tolesh holds.

Two promises this module keeps:

  * an export is complete enough to be useful — conversations, memory,
    projects, jobs, agent runs, artifact metadata and the files themselves —
    and contains no credentials;
  * a deletion really deletes: rows are removed, attachment and artifact files
    are unlinked, and the result reports what was removed. Provider API keys
    are kept unless the caller asks for them too, because silently dropping
    them would leave the app unable to answer at all.
"""

from __future__ import annotations

import io
import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from .._sqlite import connect

CONFIRM_PHRASE = "DELETE ALL DATA"

# Tables holding user content. Provider configuration and the permission audit
# are not content, and are handled explicitly.
_CONTENT_TABLES = (
    "messages", "conversations", "attachment_chunks", "attachments",
    "artifacts", "agent_runs", "job_runs", "jobs", "notifications",
    "connections", "oauth_states", "projects", "memories", "assistants",
    "prompt_templates", "shares",
)


def export_all(state: Any) -> tuple[bytes, dict[str, Any]]:
    """Return ``(zip_bytes, manifest)`` with no credentials inside."""
    buffer = io.BytesIO()
    counts: dict[str, int] = {}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        conversations = state.conversations.list(include_archived=True)
        counts["conversations"] = len(conversations)
        exported = []
        for meta in conversations:
            exported.append({**meta, "messages": state.conversations.messages(meta["id"], limit=5000)})
        _add(archive, "conversations.json", exported)

        memory = state.memory.list()
        counts["memory"] = len(memory)
        _add(archive, "memory.json", memory)

        projects = state.projects.list()
        counts["projects"] = len(projects)
        _add(archive, "projects.json", projects)

        settings = state.settings.all()["values"]
        _add(archive, "settings.json", settings)

        jobs = state.jobs.list()
        counts["jobs"] = len(jobs)
        _add(archive, "jobs.json", jobs)

        runs = state.agent_history.list(limit=500)
        counts["agent_runs"] = len(runs)
        _add(archive, "agent_runs.json", runs)

        # Connections without tokens: an export must never become a credential
        # dump sitting in a downloads folder.
        connections = state.connectors.connections()
        counts["connections"] = len(connections)
        _add(archive, "connections.json", connections)

        servers = state.mcp.store.list(redact=True)
        counts["mcp_servers"] = len(servers)
        _add(archive, "mcp_servers.json", servers)

        artifacts = state.artifacts.list(limit=500)
        counts["artifacts"] = len(artifacts)
        _add(archive, "artifacts.json", artifacts)

        files = 0
        for meta in artifacts:
            path = state.artifacts.path(meta["id"])
            if path and path.is_file():
                archive.write(path, f"files/artifacts/{meta['id']}-{meta['name']}")
                files += 1
        for attachment in state.attachments.list():
            data = state.attachments.read_bytes(attachment["id"])
            if data is not None:
                archive.write(io.BytesIO(data),
                              f"files/attachments/{attachment['id']}-{attachment['filename']}")
                files += 1
        counts["files"] = files

        _add(archive, "manifest.json", {
            "product": "Tolesh",
            "exported_at": time.time(),
            "counts": counts,
            "note": "No API keys or connection tokens are included.",
        })
    return buffer.getvalue(), {"counts": counts, "bytes": buffer.tell()}


def delete_all(state: Any, confirm: str, *, include_keys: bool = False) -> dict[str, Any]:
    """Erase user content. Returns what was removed."""
    if (confirm or "").strip() != CONFIRM_PHRASE:
        return {"status": "error",
                "message": f"Type {CONFIRM_PHRASE!r} exactly to confirm."}

    removed: dict[str, int] = {}
    db_path = getattr(state, "db_path", "")
    # ``file:`` URIs are the shared in-memory database used by tests; they are
    # still a real database and must be emptied too.
    if db_path:
        conn = connect(db_path)
        try:
            for table in _CONTENT_TABLES:
                try:
                    cur = conn.execute(f"DELETE FROM {table}")
                    removed[table] = cur.rowcount if cur.rowcount > 0 else 0
                except Exception:
                    # A table this build never created is not an error.
                    continue
            if include_keys:
                try:
                    cur = conn.execute("DELETE FROM settings")
                    removed["settings"] = cur.rowcount
                except Exception:
                    pass
            conn.commit()
        finally:
            conn.close()

    files = 0
    for store in ("attachments", "artifacts"):
        root = getattr(getattr(state, store, None), "root", None)
        if not root:
            continue
        for child in Path(root).glob("*"):
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                files += 1
            except OSError:
                continue
    removed["files"] = files
    removed["keys_deleted"] = 1 if include_keys else 0
    return {"status": "ok", "removed": removed,
            "note": ("Provider keys were deleted too; Tolesh cannot answer until "
                     "they are added again." if include_keys else
                     "Provider API keys were kept so Tolesh can still answer.")}


def _add(archive: zipfile.ZipFile, name: str, payload: Any) -> None:
    archive.writestr(name, json.dumps(payload, ensure_ascii=False, indent=2, default=str))
