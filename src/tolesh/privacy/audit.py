"""One audit trail across the capability layers.

The gate, the MCP client, the agent history and the job scheduler each keep
their own record because each owns its own failure modes. This module reads
them into a single time-ordered list, which is what "what has this assistant
done" actually means. It deliberately does not write a second copy: two logs
that can disagree are worse than one.
"""

from __future__ import annotations

import time
from typing import Any


def collect(state: Any, limit: int = 100) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    try:
        for entry in state.gate.audit_log(limit=limit):
            events.append({
                "kind": "permission",
                "action": entry.get("tool", ""),
                "status": entry.get("status", ""),
                "detail": entry.get("args", {}),
                "risk": entry.get("risk", ""),
                "at": entry.get("created_at", 0),
            })
    except Exception:
        pass

    try:
        for call in state.mcp.calls(limit):
            events.append({
                "kind": "mcp",
                "action": f"{call.get('server')}:{call.get('tool')}",
                "status": call.get("status", ""),
                "detail": {"args": call.get("args", {}), "result": call.get("detail", "")},
                "risk": "",
                "at": call.get("at", 0),
            })
    except Exception:
        pass

    try:
        for run in state.agent_history.list(limit=limit):
            events.append({
                "kind": "agent",
                "action": run.get("goal", "")[:200],
                "status": run.get("status", ""),
                "detail": {"steps": run.get("steps"), "result": run.get("result", "")[:300],
                           "error": run.get("error", "")[:300]},
                "risk": "",
                "at": run.get("updated_at", 0),
            })
    except Exception:
        pass

    try:
        for job in state.jobs.list(limit=limit):
            events.append({
                "kind": "job",
                "action": job.get("name", ""),
                "status": job.get("status", ""),
                "detail": {"runs": job.get("run_count"), "error": job.get("error", "")[:300]},
                "risk": "",
                "at": job.get("updated_at", 0),
            })
    except Exception:
        pass

    events.sort(key=lambda item: item.get("at") or 0, reverse=True)
    return events[:limit]


def summary(state: Any) -> dict[str, Any]:
    """Counts by kind and outcome, for the privacy panel's overview."""
    events = collect(state, limit=500)
    by_kind: dict[str, int] = {}
    denied = 0
    for event in events:
        by_kind[event["kind"]] = by_kind.get(event["kind"], 0) + 1
        if event.get("status") in ("denied", "error", "failed", "cancelled"):
            denied += 1
    return {"total": len(events), "by_kind": by_kind,
            "not_successful": denied, "generated_at": time.time()}
