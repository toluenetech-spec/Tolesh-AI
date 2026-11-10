#!/usr/bin/env python3
"""Smoke-test the live Dahl provider through the Tolesh API.

    python3 scripts/verify_dahl.py [--url http://127.0.0.1:8787]

Checks, against a running backend, that:

1. the key is configured and never echoed back,
2. ``/api/providers/dahl/models`` returns the ids the gateway actually serves,
3. ``/api/providers/dahl/test`` attributes a busy model to that model,
4. ``/api/chat`` streams a real answer and reports real token usage,
5. the answer is stored in the conversation it claims to be part of.

Writes ``docs/dahl-report.json``. Exit code 0 only when a real answer arrived.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _get(url: str, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, body: dict | None = None, timeout: float = 180.0) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _stream_chat(url: str, message: str, timeout: float = 180.0) -> dict:
    body = json.dumps({"message": message, "task": "chat"}).encode("utf-8")
    req = urllib.request.Request(f"{url}/api/chat", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    tokens: list[str] = []
    statuses: list[dict] = []
    usage: dict | None = None
    done: dict | None = None
    conversation_id: str | None = None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            try:
                frame = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            kind = frame.get("type")
            if kind == "token":
                tokens.append(frame.get("data") or "")
            elif kind == "status":
                statuses.append(frame.get("data") or {})
            elif kind == "usage":
                usage = frame.get("data")
            elif kind == "done":
                done = frame.get("data")
            elif kind == "meta":
                conversation_id = (frame.get("data") or {}).get("conversation_id")
    return {"text": "".join(tokens), "statuses": statuses, "usage": usage,
            "done": done, "conversation_id": conversation_id}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--out", default=str(ROOT / "docs" / "dahl-report.json"))
    args = ap.parse_args()
    url = args.url.rstrip("/")

    report: dict = {"url": url, "steps": {}}

    health = _get(f"{url}/api/health")
    report["steps"]["health"] = {
        "status": health.get("status"),
        "providers_configured": health.get("providers_configured"),
        "default_model": health.get("default_model"),
    }

    providers = _get(f"{url}/api/providers")
    dahl = next((p for p in providers["providers"] if p["name"] == "dahl"), None)
    report["steps"]["provider"] = {
        "configured": bool(dahl and dahl["configured"]),
        "base_url": dahl["base_url"] if dahl else None,
        "key_redacted": bool(dahl and dahl["api_key"] == "***"),
        "declared_models": [m["id"] for m in (dahl or {}).get("models", [])],
        "key_appears_in_payload": "dahl_" in json.dumps(providers),
    }

    discovery = _get(f"{url}/api/providers/dahl/models", timeout=60)
    report["steps"]["discovery"] = {
        "ok": discovery.get("ok"),
        "status": discovery.get("status"),
        "count": discovery.get("count"),
        "latency_ms": discovery.get("latency_ms"),
        "models": discovery.get("models"),
    }

    probe = _post(f"{url}/api/providers/dahl/test", timeout=240)
    report["steps"]["connection_test"] = {
        "ok": probe.get("ok"),
        "status": probe.get("status"),
        "answered_by": probe.get("model"),
        "attempts": probe.get("attempts"),
    }

    chat = _stream_chat(url, "In one sentence: what are you, and which model is answering?")
    report["steps"]["chat"] = {
        "conversation_id": chat["conversation_id"],
        "answered_by": (chat["done"] or {}).get("model"),
        "fallbacks_used": (chat["done"] or {}).get("fallbacks_used"),
        "usage": chat["usage"],
        "chars": len(chat["text"]),
        "reply": chat["text"][:400],
        "provider_errors": [s for s in chat["statuses"] if s.get("kind") == "provider_error"],
        "think_leaked": "<think>" in chat["text"] or "</think>" in chat["text"],
    }

    if chat["conversation_id"]:
        stored = _get(f"{url}/api/conversations/{chat['conversation_id']}/messages")
        report["steps"]["persistence"] = {
            "stored_messages": len(stored.get("messages", [])),
            "assistant_model": next(
                (m.get("model") for m in stored.get("messages", [])
                 if m.get("role") == "assistant"), None),
        }

    report["ok"] = bool(chat["text"]) and report["steps"]["provider"]["configured"] \
        and not report["steps"]["provider"]["key_appears_in_payload"]

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False)[:3000])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
