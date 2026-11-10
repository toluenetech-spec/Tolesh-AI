"""Ask an OpenAI-compatible endpoint which models it actually serves.

The settings UI needs a real answer, not a hard-coded guess: model ids on a
gateway change without notice (Dahl, for example, publishes three). This module
performs exactly one ``GET {base_url}/models`` and reports what came back —
including the HTTP status when it fails, so "no models" is never confused with
"the endpoint refused us".
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .registry import ProviderSpec


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        return (response.text or "").strip()[:300] or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = err.get("message") or err.get("code")
            if message:
                return str(message)[:300]
        if body.get("detail"):
            return str(body["detail"])[:300]
    return (response.text or "").strip()[:300] or f"HTTP {response.status_code}"


def discover_models(spec: ProviderSpec, *, timeout: float = 15.0) -> dict[str, Any]:
    """Return ``{ok, status, models, detail, latency_ms}`` for one provider.

    ``models`` holds the raw wire ids (``vendor/Model-Name``) in the order the
    endpoint listed them. Nothing is invented: an endpoint that answers with no
    usable entries yields ``ok: False`` and ``status: "no_models"``.
    """
    if spec.kind != "openai_compatible" or not spec.base_url:
        return {"ok": False, "status": "unsupported", "models": [], "count": 0,
                "detail": f"{spec.label} has no OpenAI-compatible base URL."}

    url = spec.base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if spec.api_key:
        headers["Authorization"] = f"Bearer {spec.api_key}"

    started = time.time()
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)
    except Exception as exc:  # transport: DNS, TLS, timeout, refused
        return {"ok": False, "status": "error", "models": [], "count": 0,
                "detail": f"{type(exc).__name__}: {exc}"[:300],
                "latency_ms": int((time.time() - started) * 1000)}

    latency_ms = int((time.time() - started) * 1000)
    if response.status_code >= 400:
        return {"ok": False, "status": f"http_{response.status_code}", "models": [],
                "count": 0, "detail": _error_detail(response), "latency_ms": latency_ms}

    try:
        payload = response.json()
    except Exception:
        return {"ok": False, "status": "bad_json", "models": [], "count": 0,
                "detail": "The endpoint did not return JSON.", "latency_ms": latency_ms}

    rows = payload.get("data") if isinstance(payload, dict) else None
    ids: list[str] = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("id"):
            mid = str(row["id"]).strip()
            if mid and mid not in ids:
                ids.append(mid)
    if not ids:
        return {"ok": False, "status": "no_models", "models": [], "count": 0,
                "detail": "The endpoint answered but listed no models.",
                "latency_ms": latency_ms}

    return {"ok": True, "status": "ok", "models": ids, "count": len(ids),
            "latency_ms": latency_ms}
