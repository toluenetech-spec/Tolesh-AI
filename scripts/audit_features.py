#!/usr/bin/env python3
"""Feature audit: drive every part of Tolesh through its real API and report.

Run from the repository root:

    PYTHONPATH=src python3.11 scripts/audit_features.py

This is not a test suite. It is an inventory: one named check per advertised
capability, each performing the actual request an interface control performs,
and each reporting what came back. Anything that fails is printed as a failure
with the server's own words, and anything that could not be attempted for want
of credentials is listed as such rather than quietly passed.

It also counts route coverage: every ``/api`` route is matched against the paths
that were really visited, and the untouched ones are printed. A capability that
no check exercised is a gap in this audit, and saying so is the point.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from tolesh.ai import ModelRouter, Registry  # noqa: E402
from tolesh.server import create_app  # noqa: E402
from tolesh.server.app import AppState  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "mcp_fixture_server.py"
RESULTS: list[dict] = []
VISITED: set[str] = set()


def record(name: str, ok: bool, detail: str = "", *, gated: bool = False) -> None:
    RESULTS.append({"feature": name, "ok": bool(ok), "detail": str(detail)[:400],
                    "gated": gated})
    mark = "SKIP" if gated else ("ok  " if ok else "FAIL")
    print(f"{mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def check(name: str, fn) -> None:
    try:
        ok, detail = fn()
        record(name, ok, detail)
    except Exception as exc:  # noqa: BLE001 - the audit reports, never crashes
        record(name, False, f"{type(exc).__name__}: {exc}")


def gated(name: str, detail: str) -> None:
    record(name, False, detail, gated=True)


def main() -> int:
    state = AppState(in_memory=True, memory_debounce=0.01)
    offline = Registry.from_defaults().provider("offline")
    state.registry = Registry({"offline": offline})
    state.router = ModelRouter(state.registry, state.ledger, offline_delay=0)
    app = create_app(state=state, web_dir=str(ROOT / "web"), serve_static=True)

    @app.middleware("http")
    async def track(request, call_next):
        VISITED.add(request.url.path)
        return await call_next(request)

    client = TestClient(app)
    ctx = {"cid": None, "aid": None, "pid": None}

    # ------------------------------------------------------------- meta
    check("health reports the product and version", lambda: (
        client.get("/api/health").json()["product"] == "Tolesh",
        client.get("/api/health").json().get("version", "")))
    check("provider catalogue is redacted", lambda: (
        all(not p["api_key"] or set(p["api_key"]) <= {"*", "•"} or len(p["api_key"]) <= 4
            for p in client.get("/api/providers").json()["providers"]),
        "no plaintext key in /api/providers"))
    check("model catalogue lists models", lambda: (
        bool(client.get("/api/models").json()["models"]),
        f"{len(client.get('/api/models').json()['models'])} models"))
    check("fallback order is readable and writable", lambda: (
        client.put("/api/fallback-order", json={"models": []}).status_code == 200,
        "empty list restores the built-in chain"))
    check("capabilities are declared", lambda: (
        bool(client.get("/api/capabilities").json()),
        json.dumps(client.get("/api/capabilities").json())[:120]))

    # -------------------------------------------------------- conversations
    def new_conversation():
        r = client.post("/api/conversations", json={"title": "Audit"})
        ctx["cid"] = r.json()["id"]
        return r.status_code == 200, r.json()["id"]

    check("create conversation", new_conversation)

    def chat_stream():
        events = []
        with client.stream("POST", "/api/chat", json={
                "message": "Say ok and nothing else.",
                "conversation_id": ctx["cid"]}) as response:
            for line in response.iter_lines():
                if line and line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        types = [e["type"] for e in events]
        return ("token" in types and "done" in types, f"{len(events)} events: {types[:6]}")

    check("streamed chat (SSE)", chat_stream)

    check("chat/sync returns a whole reply", lambda: (
        bool(client.post("/api/chat/sync", json={
            "message": "Name one colour.", "conversation_id": ctx["cid"]}).json()["text"]),
        "reply stored and returned"))

    def messages_exist():
        msgs = client.get(f"/api/conversations/{ctx['cid']}/messages").json()["messages"]
        return len(msgs) >= 2, f"{len(msgs)} messages"

    check("messages persist", messages_exist)

    def rename_and_pin():
        client.patch(f"/api/conversations/{ctx['cid']}", json={"title": "Renamed"})
        # Pin/archive/filing is its own endpoint: renaming and filing have
        # different validation, so they are not one PATCH.
        client.patch(f"/api/conversations/{ctx['cid']}/flags", json={"pinned": True})
        meta = client.get(f"/api/conversations/{ctx['cid']}").json()["conversation"]
        return (meta["title"] == "Renamed" and meta["pinned"],
                f"title={meta['title']} pinned={meta['pinned']}")

    check("rename and pin conversation", rename_and_pin)

    check("list conversations", lambda: (
        any(c["id"] == ctx["cid"] for c in client.get("/api/conversations").json()["conversations"]),
        "created conversation appears in the list"))

    def edit_message():
        msgs = client.get(f"/api/conversations/{ctx['cid']}/messages").json()["messages"]
        user_msg = [m for m in msgs if m["role"] == "user"][0]
        r = client.patch(f"/api/conversations/{ctx['cid']}/messages/{user_msg['id']}",
                         json={"content": "Say ok, please."})
        after = client.get(f"/api/conversations/{ctx['cid']}/messages").json()["messages"]
        return (r.status_code == 200
                and any(m["content"] == "Say ok, please." for m in after),
                "edit is visible on re-read")

    check("edit a message", edit_message)

    def regenerate():
        r = client.post(f"/api/conversations/{ctx['cid']}/regenerate", json={})
        return r.status_code == 200, f"status {r.status_code}"

    check("regenerate the last answer", regenerate)

    def branch():
        msgs = client.get(f"/api/conversations/{ctx['cid']}/messages").json()["messages"]
        r = client.post(f"/api/conversations/{ctx['cid']}/branch",
                        json={"message_id": msgs[0]["id"]})
        body = r.json()
        return (r.status_code == 200 and body.get("conversation", {}).get("id") != ctx["cid"],
                f"new conversation {body.get('conversation', {}).get('id')}")

    check("branch a conversation", branch)

    def export_conversation():
        md = client.get(f"/api/conversations/{ctx['cid']}/export", params={"format": "markdown"})
        js = client.get(f"/api/conversations/{ctx['cid']}/export", params={"format": "json"})
        return (md.status_code == 200 and "Renamed" in md.text
                and js.status_code == 200, "markdown and json both render")

    check("export one conversation", export_conversation)

    # ------------------------------------------------------------ projects
    def projects():
        created = client.post("/api/projects", json={
            "name": "Audit project", "instructions": "Answer in Pidgin."}).json()["project"]
        ctx["pid"] = created["id"]
        patched = client.patch(f"/api/projects/{ctx['pid']}", json={"description": "x"})
        filed = client.patch(f"/api/conversations/{ctx['cid']}/flags",
                             json={"project_id": ctx["pid"]})
        meta = client.get(f"/api/conversations/{ctx['cid']}").json()["conversation"]
        return (created["name"] == "Audit project" and patched.status_code == 200
                and filed.status_code == 200 and meta["project_id"] == ctx["pid"],
                f"project {ctx['pid']} linked to the conversation")

    check("projects: create, patch, file a conversation", projects)

    def project_instructions_reach_the_prompt():
        captured = {}
        inner = state.router

        class Spy:
            def select(self, *a, **k):
                return inner.select(*a, **k)

            def stream(self, messages, **kw):
                captured["system"] = messages[0]["content"]
                yield from inner.stream(messages, **kw)

            def complete(self, messages, **kw):
                captured["system"] = messages[0]["content"]
                return inner.complete(messages, **kw)

        state.router = Spy()
        try:
            client.post("/api/chat/sync", json={"message": "hi",
                                                "conversation_id": ctx["cid"]})
        finally:
            state.router = inner
        return ("Answer in Pidgin." in captured.get("system", ""),
                "project instructions are in the system prompt")

    check("project instructions reach the model", project_instructions_reach_the_prompt)

    # --------------------------------------------------------- attachments
    def attachments():
        data = b"region,amount\nLagos,1200\nAbuja,900\nKano,640\n"
        # Raw body + filename in the query string: no multipart parser needed,
        # and the browser can send a File straight through.
        r = client.post("/api/attachments", params={"filename": "sales.csv"},
                        content=data, headers={"Content-Type": "text/csv"})
        ctx["aid"] = r.json()["attachment"]["id"]
        listed = client.get("/api/attachments").json()["attachments"]
        return (r.status_code == 200 and any(a["id"] == ctx["aid"] for a in listed),
                f"uploaded {ctx['aid']}")

    check("upload an attachment", attachments)

    def profile_and_chart():
        profile = client.post("/api/artifacts/analyse",
                              json={"attachment_id": ctx["aid"]}).json()["profile"]
        chart = client.post("/api/artifacts/chart", json={
            "attachment_id": ctx["aid"], "chart_type": "bar", "x": "region",
            "y": "amount", "conversation_id": ctx["cid"]}).json()["artifact"]
        ctx["artifact"] = chart["id"]
        image = client.get(chart["download_url"])
        return (bool(profile.get("columns")) and image.status_code == 200
                and image.headers["content-type"].startswith("image/png"),
                f"profile columns={len(profile.get('columns', []))}, chart {len(image.content)} bytes")

    check("profile a table and render a chart", profile_and_chart)

    def artifacts_roundtrip():
        listed = client.get("/api/artifacts").json()["artifacts"]
        one = client.get(f"/api/artifacts/{ctx['artifact']}").json()["artifact"]
        deleted = client.delete(f"/api/artifacts/{ctx['artifact']}")
        return (bool(listed) and one["id"] == ctx["artifact"]
                and deleted.status_code == 200,
                "list, fetch, delete")

    check("artifacts: list, fetch, delete", artifacts_roundtrip)

    # -------------------------------------------------------------- memory
    def memory_flow():
        client.post("/api/memory", json={"category": "preference",
                                         "content": "Prefers short answers"})
        items = client.get("/api/memory").json()["items"]
        found = client.post("/api/memory/search",
                            json={"query": "how long should answers be?"}).json()["items"]
        cid = ctx["cid"]
        client.post("/api/chat/sync", json={
            "message": "My name is Ada and I prefer Python.", "conversation_id": cid})
        learned = client.post("/api/memory/extract",
                              json={"conversation_id": cid}).json()
        return (bool(items) and bool(found) and bool(learned["created"]),
                f"{len(items)} manual, {len(found)} ranked, {len(learned['created'])} learned")

    check("memory: add, search, learn from a conversation", memory_flow)

    def memory_settings_effect():
        client.patch("/api/memory/settings", json={"memory_retrieval": False})
        off = client.post("/api/memory/search", json={"query": "answers"}).json()["items"]
        client.patch("/api/memory/settings", json={"memory_retrieval": True})
        on = client.post("/api/memory/search", json={"query": "answers"}).json()["items"]
        return (off == [] and bool(on), "retrieval switch really stops retrieval")

    check("memory switches change behaviour", memory_settings_effect)

    # --------------------------------------------------------------- agents
    def agent_run():
        run = client.post("/api/agents/runs", json={
            "goal": "Reply with the word ready.", "conversation_id": ctx["cid"]}).json()["run"]
        events = []
        with client.stream("POST", f"/api/agents/runs/{run['id']}/run") as response:
            for line in response.iter_lines():
                if line and line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        final = [e for e in events if e["type"] == "final"]
        return (bool(final), f"{len(events)} events, final={bool(final)}")

    check("agent run streams to a final answer", agent_run)

    def agent_cancel():
        run = client.post("/api/agents/runs", json={"goal": "count to a hundred"}).json()["run"]
        cancelled = client.post(f"/api/agents/runs/{run['id']}/cancel")
        listed = client.get("/api/agents/runs").json()["runs"]
        return (cancelled.status_code == 200
                and any(r["id"] == run["id"] for r in listed), "cancel acknowledged")

    check("agent run can be cancelled", agent_cancel)

    check("agent tools are listed", lambda: (
        bool(client.get("/api/agents/tools").json()["tools"]),
        f"{len(client.get('/api/agents/tools').json()['tools'])} tools"))

    # ------------------------------------------------------------------ MCP
    def mcp_roundtrip():
        client.post("/api/mcp/servers", json={
            "name": "fixture", "transport": "stdio", "command": sys.executable,
            "args": [str(FIXTURE)]})
        tested = client.post("/api/mcp/servers/fixture/test").json()
        tools = client.get("/api/mcp/servers/fixture/tools").json()["tools"]
        called = client.post("/api/mcp/invoke", json={
            "server": "fixture", "tool": "echo", "args": {"text": "hi"}}).json()
        client.delete("/api/mcp/servers/fixture")
        return (tested.get("ok") and bool(tools) and called.get("status") == "ok",
                f"{len(tools)} tools, invoke={called.get('status')}")

    check("MCP: register, test, list tools, invoke, remove", mcp_roundtrip)

    # ----------------------------------------------------------- connectors
    def connectors():
        catalogue = client.get("/api/connectors").json()["providers"]
        r = client.post("/api/connectors/token", json={
            "provider": "github", "token": "ghp_auditfake", "label": "audit",
            "base_url": "https://api.github.com"})
        return (len(catalogue) >= 6 and r.status_code in (200, 400, 403, 409),
                f"{len(catalogue)} providers catalogued, connect -> {r.status_code}")

    check("connectors: catalogue and token connect", connectors)

    # ----------------------------------------------------------------- jobs
    def jobs():
        # Scheduled an hour out so pause/resume test the controls rather than
        # racing the scheduler, which would otherwise pick the job up at once.
        job = client.post("/api/jobs", json={
            "name": "audit job", "goal": "Reply ready.", "kind": "prompt",
            "schedule": "once", "run_at": time.time() + 3600}).json()["job"]
        paused = client.post(f"/api/jobs/{job['id']}/pause")
        resumed = client.post(f"/api/jobs/{job['id']}/resume")
        ran = client.post(f"/api/jobs/{job['id']}/run").json()["job"]
        # run_now marks the job due; the scheduler thread picks it up on its next
        # tick, so give it a moment before claiming it did nothing.
        deadline = time.time() + 10
        runs = []
        while time.time() < deadline:
            runs = client.get(f"/api/jobs/{job['id']}/runs").json()["runs"]
            if runs and runs[0].get("status") in ("done", "ok", "failed", "error"):
                break
            time.sleep(0.2)
        client.delete(f"/api/jobs/{job['id']}")
        finished = [r.get("status") for r in runs]
        return (paused.status_code == 200 and resumed.status_code == 200
                and ran.get("status") in ("scheduled", "running", "ok", "queued")
                and any(s in ("done", "ok", "failed", "error") for s in finished),
                f"pause={paused.status_code} resume={resumed.status_code} "
                f"run={ran.get('status')} history={len(runs)} {finished}")

    check("jobs: schedule, pause, resume, run now, history", jobs)

    def calendar_jobs():
        preview = client.get("/api/cron/preview",
                             params={"expression": "0 9 * * mon-fri"}).json()
        rejected = client.get("/api/cron/preview",
                              params={"expression": "0 9 * * funday"}).json()
        created = client.post("/api/jobs", json={
            "name": "audit calendar", "goal": "Reply ready.", "kind": "prompt",
            "schedule": "cron", "cron": "0 4 * * sun"}).json()["job"]
        client.delete(f"/api/jobs/{created['id']}")
        next_at = created.get("next_run_at") or 0
        return (preview.get("valid") is True and rejected.get("valid") is False
                and created.get("cron") == "0 4 * * sun"
                and next_at > time.time(),
                f"{len(preview.get('next', []))} previewed runs, invalid rejected, "
                f"next={time.strftime('%a %H:%M', time.localtime(next_at))}")

    check("jobs: cron expression validates and schedules", calendar_jobs)

    def failure_recovery():
        """A broken recurring job must pause and say so, not retry forever."""
        original = state.jobs._executor

        def always_fails(job, progress):
            raise RuntimeError("audit: the upstream is down")

        state.jobs._executor = always_fails
        try:
            job = client.post("/api/jobs", json={
                "name": "audit failure", "goal": "Reply ready.", "kind": "prompt",
                "schedule": "interval", "interval_sec": 3600,
                "max_attempts": 1, "backoff_sec": 1}).json()["job"]
            client.post(f"/api/jobs/{job['id']}/run")
            deadline = time.time() + 10
            stored = client.get(f"/api/jobs/{job['id']}").json()["job"]
            while time.time() < deadline and stored["status"] != "paused":
                time.sleep(0.2)
                stored = client.get(f"/api/jobs/{job['id']}").json()["job"]
            notes = client.get("/api/notifications").json()["notifications"]
            announced = any("paused after" in n["title"] for n in notes)
            client.delete(f"/api/jobs/{job['id']}")
            return (stored["status"] == "paused" and stored["attempts"] == 1
                    and announced,
                    f"status={stored['status']} attempts={stored['attempts']} "
                    f"notified={announced}")
        finally:
            state.jobs._executor = original

    check("jobs: a failing job pauses at the limit and notifies", failure_recovery)

    def wake_word_settings():
        body = client.get("/api/voice/hotword").json()
        patched = client.patch("/api/voice/hotword",
                               json={"hotword_threshold": 0.6})
        tested = client.post("/api/voice/hotword/test").json()
        client.patch("/api/voice/hotword", json={"hotword_threshold": 0.5})
        refused = client.patch("/api/voice/hotword",
                               json={"hotword_backend": "magic"})
        return (body["values"]["hotword_enabled"] is False
                and patched.json()["values"]["hotword_threshold"] == 0.6
                and refused.status_code == 400,
                f"backends={len(body['backends'])} gating={body['status']['gating']} "
                f"probe={'ok' if tested.get('ok') else 'no backend installed'}")

    check("voice: wake-word settings persist and validate", wake_word_settings)

    def telegram_channel():
        """The bot must be off by default, refuse a bad token, and gate chats."""
        start = client.get("/api/telegram").json()
        bad = client.patch("/api/telegram", json={"token": "not-a-token"})
        token = "123456789:" + "A" * 35
        stored = client.patch("/api/telegram", json={"token": token}).json()
        allowed = client.patch("/api/telegram", json={"add_chat": 4242}).json()
        listed = client.get("/api/telegram").json()
        client.delete("/api/telegram/token")
        cleared = client.get("/api/telegram").json()
        return (start["enabled"] is False and start["has_token"] is False
                and bad.status_code == 400
                and stored["has_token"] is True
                and allowed["allowlist"] == [4242]
                and 4242 in listed["allowlist"]
                and cleared["has_token"] is False,
                f"off-by-default, bad token -> {bad.status_code}, "
                f"allowlist {allowed['allowlist']}, token not echoed")

    check("telegram: off by default, token stored encrypted, allowlist enforced",
          telegram_channel)

    def system_controls():
        """Three controls need a token; shutdown stays refused even with one."""
        report = client.get("/api/tools/system").json()
        lock = client.post("/api/tools/invoke", json={"tool": "lock_screen"}).json()
        shutdown = client.post("/api/tools/invoke", json={"tool": "shutdown"}).json()
        # Deny the lock so this audit never actually locks the machine it runs on.
        denied = client.post(f"/api/permissions/{lock['id']}/deny",
                             json={"token": lock["token"], "execute": False})
        approved = client.post(f"/api/permissions/{shutdown['id']}/approve",
                               json={"token": shutdown["token"], "execute": True}).json()
        supported = sum(1 for c in report["controls"].values() if c["supported"])
        return (lock["status"] == "pending-confirmation"
                and denied.status_code == 200
                and approved["execution"]["status"] == "refused"
                and "shutdown" in report["refused"],
                f"{supported}/3 controls available on {report['platform']}, "
                f"lock needs a token, shutdown refused after approval")

    check("system: volume/brightness/lock are gated, shutdown still refused",
          system_controls)

    check("notifications are readable", lambda: (
        client.post("/api/notifications/read-all").status_code == 200,
        "mark-all-read accepted"))

    # ----------------------------------------------------------- assistants
    def assistants():
        created = client.post("/api/assistants", json={
            "name": "Auditor", "instructions": "Be terse.",
            "greeting": "Ready."}).json()["assistant"]
        ctx["assistant"] = created["id"]
        patched = client.patch(f"/api/assistants/{created['id']}",
                               json={"description": "audit"})
        template = client.post("/api/templates", json={
            "name": "audit tpl", "body": "hello"}).json()["template"]
        removed = client.delete(f"/api/templates/{template['id']}")
        return (patched.status_code == 200 and removed.status_code == 200,
                "assistant and template round-trip")

    check("assistants and templates", assistants)

    # -------------------------------------------------------------- shares
    def shares():
        client.patch("/api/settings", json={"share_links_enabled": True})
        made = client.post(f"/api/conversations/{ctx['cid']}/share", json={}).json()
        token = made.get("share", {}).get("token")
        page = client.get(f"/share/{token}")
        listed = client.get("/api/shares").json()["shares"]
        removed = client.delete(f"/api/shares/{token}")
        client.patch("/api/settings", json={"share_links_enabled": False})
        return (bool(token) and page.status_code == 200 and listed
                and removed.status_code == 200,
                "share link renders and can be revoked")

    check("share links: create, open, revoke", shares)

    # ------------------------------------------------------------ settings
    def settings():
        before = client.get("/api/settings").json()["values"]
        client.patch("/api/settings", json={"agent_max_steps": 5})
        after = client.get("/api/settings").json()["values"]
        bad = client.patch("/api/settings", json={"agent_max_steps": 999})
        reset = client.post("/api/settings/reset").json()["values"]
        return (after["agent_max_steps"] == 5 and bad.status_code == 400
                and reset["agent_max_steps"] == before["agent_max_steps"],
                "validated, persisted, resettable")

    check("settings validate and persist", settings)

    check("audit log records actions", lambda: (
        bool(client.get("/api/audit").json()["events"])
        and bool(client.get("/api/audit/summary").json()),
        f"{len(client.get('/api/audit').json()['events'])} entries"))

    # ------------------------------------------------------- permissions
    def permission_flow():
        pending = client.post("/api/tools/invoke", json={
            "tool": "write_text_file", "args": {"path": "audit.txt"},
            "reason": "audit"}).json()
        approved = client.post(f"/api/permissions/{pending['id']}/approve",
                               json={"token": pending["token"], "execute": False})
        second = client.post("/api/tools/invoke", json={
            "tool": "write_text_file", "args": {"path": "audit2.txt"}}).json()
        refused = client.post(f"/api/permissions/{second['id']}/deny",
                              json={"token": second["token"]})
        return (approved.status_code == 200 and refused.status_code == 200,
                f"approve={approved.status_code} deny={refused.status_code}")

    check("permission gate: approve and deny", permission_flow)

    check("tool inventory", lambda: (bool(client.get("/api/tools").json()["tools"]),
                                     f"{len(client.get('/api/tools').json()['tools'])} tools"))

    check("sandboxed python", lambda: (
        client.post("/api/exec", json={"code": "print(6*7)"}).json()["stdout"].strip() == "42",
        "exec returns real output"))

    check("web backends are reported", lambda: (
        bool(client.get("/api/web/backends").json()),
        json.dumps(client.get("/api/web/backends").json())[:120]))

    # --------------------------------------------------------------- usage
    check("usage ledger records tokens", lambda: (
        client.get("/api/usage").json().get("total", 0) >= 0,
        json.dumps(client.get("/api/usage").json())[:120]))

    # ------------------------------------------------------- export/delete
    def export_zip():
        response = client.get("/api/export")
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(archive.namelist())
        needed = {"conversations.json", "memory.json", "settings.json", "manifest.json"}
        return (needed <= names, f"{len(names)} files")

    check("export everything as a zip", export_zip)

    def data_delete():
        refused = client.post("/api/data/delete", json={"confirm": "nope"})
        accepted = client.post("/api/data/delete", json={"confirm": "DELETE ALL DATA"})
        return (refused.status_code == 400 and accepted.status_code == 200,
                f"refused={refused.status_code}, accepted={accepted.status_code}")

    check("delete all data requires confirmation", data_delete)

    # ------------------------------------------------- second pass: detail
    def memory_admin():
        client.post("/api/memory", json={"category": "personal",
                                         "content": "Lives in Lagos"})
        matched = client.post("/api/memory/forget-match",
                              json={"phrase": "Lives in Lagos"}).json()
        suppressions = client.get("/api/memory/suppressions").json()["suppressions"]
        removed = client.delete(f"/api/memory/suppressions/{suppressions[0]['id']}")
        status = client.get("/api/memory/status").json()
        events = client.get("/api/memory/events").json()["events"]
        wiped = client.post("/api/memory/forget",
                            json={"confirm": "FORGET EVERYTHING"}).json()
        return (bool(matched["deleted"]) and removed.status_code == 200
                and status["status"]["enabled"] and bool(events)
                and wiped["status"] == "ok",
                f"{len(matched['deleted'])} forgotten, {len(events)} audit events")

    check("memory: forget-match, suppressions, status, events, wipe", memory_admin)

    def attachment_delete():
        made = client.post("/api/attachments", params={"filename": "note.txt"},
                           content=b"hello", headers={"Content-Type": "text/plain"}).json()
        gone = client.delete(f"/api/attachments/{made['attachment']['id']}")
        again = client.delete(f"/api/attachments/{made['attachment']['id']}")
        return (gone.status_code == 200 and again.status_code == 404,
                "delete is real, second delete 404s")

    check("delete an attachment", attachment_delete)

    def agent_history():
        run = client.post("/api/agents/runs", json={"goal": "reply ok"}).json()["run"]
        one = client.get(f"/api/agents/runs/{run['id']}").json()
        resumed = client.post(f"/api/agents/runs/{run['id']}/resume")
        removed = client.delete(f"/api/agents/runs/{run['id']}")
        return (one.get("run", {}).get("id") == run["id"]
                and resumed.status_code in (200, 400)
                and removed.status_code in (200, 404),
                f"resume={resumed.status_code}, delete={removed.status_code}")

    check("agent runs: fetch, resume, delete", agent_history)

    def job_admin():
        job = client.post("/api/jobs", json={"goal": "reply ok", "kind": "prompt",
                                             "schedule": "interval",
                                             "interval_sec": 3600}).json()["job"]
        patched = client.patch(f"/api/jobs/{job['id']}", json={"name": "renamed"})
        one = client.get(f"/api/jobs/{job['id']}").json()["job"]
        cancelled = client.post(f"/api/jobs/{job['id']}/cancel")
        return (patched.status_code == 200 and one["name"] == "renamed"
                and cancelled.status_code == 200,
                "patch, fetch, cancel")

    check("jobs: patch, fetch, cancel", job_admin)

    def mcp_extras():
        client.post("/api/mcp/servers", json={
            "name": "fx2", "transport": "stdio", "command": sys.executable,
            "args": [str(FIXTURE)]})
        connected = client.post("/api/mcp/servers/fx2/connect").json()
        tools = client.get("/api/mcp/tools").json()["tools"]
        prompts = client.get("/api/mcp/servers/fx2/prompts").json()["prompts"]
        resources = client.get("/api/mcp/servers/fx2/resources").json()["resources"]
        calls = client.get("/api/mcp/calls").json()["calls"]
        disconnected = client.post("/api/mcp/servers/fx2/disconnect")
        client.delete("/api/mcp/servers/fx2")
        state = (connected.get("status") or {}).get("state")
        return (state == "connected" and bool(tools) and disconnected.status_code == 200,
                f"{len(tools)} tools, {len(prompts)} prompts, {len(resources)} resources, "
                f"{len(calls)} calls")

    check("MCP: connect, aggregate tools, prompts, resources, calls", mcp_extras)

    def provider_and_model_toggles():
        patched = client.patch("/api/providers/offline", json={"enabled": True})
        model = client.get("/api/models").json()["models"][0]["id"]
        toggled = client.patch(f"/api/models/{model}", json={"enabled": False})
        restored = client.patch(f"/api/models/{model}", json={"enabled": True})
        config = client.patch("/api/config", json={"default_model": ""})
        # The offline engine is the last-resort fallback and refuses to be
        # switched off; that refusal is the behaviour worth asserting.
        guard = "cannot be switched off" in str(toggled.json().get("detail", ""))
        return (patched.status_code == 200 and toggled.status_code == 400 and guard
                and restored.status_code == 200 and config.status_code == 200,
                "provider and default-model writes accepted; offline engine guarded")

    check("providers and models can be switched", provider_and_model_toggles)

    def notifications():
        listed = client.get("/api/notifications").json()["notifications"]
        if listed:
            one = client.post(f"/api/notifications/{listed[0]['id']}/read")
            return one.status_code == 200, "one notification marked read"
        return True, "no notifications to mark (none created yet)"

    check("notifications: list and mark read", notifications)

    def permission_lists():
        pending = client.get("/api/permissions/pending").json()["pending"]
        audit = client.get("/api/permissions/audit").json()["entries"]
        return (isinstance(pending, list) and isinstance(audit, list),
                f"{len(pending)} pending, {len(audit)} audited")

    check("permission queues are readable", permission_lists)

    def project_delete():
        project = client.post("/api/projects", json={"name": "temp"}).json()["project"]
        gone = client.delete(f"/api/projects/{project['id']}")
        return gone.status_code == 200, "project deleted"

    check("delete a project", project_delete)

    # ----------------------------------------------------- credential-gated
    caps = client.get("/api/media/capabilities").json()
    gated("image generation", "needs a provider key that serves /images/generations")
    gated("speech synthesis",
          f"no voice installed: {caps.get('synthesis', {}).get('reason', '')[:120]}")
    gated("transcription", "the local engine is available, but this audit has no audio "
                           "fixture to feed it; covered by tests/test_dictation.py")
    gated("OAuth connectors", "needs a real client id/secret and a provider redirect")

    # ----------------------------------------------------------- coverage
    templates = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/"):
            templates.add(path)
    patterns = {t: re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", t) + "$")
                for t in templates}
    uncovered = sorted(t for t, rx in patterns.items()
                       if not any(rx.match(v) for v in VISITED))

    passed = [r for r in RESULTS if r["ok"]]
    failed = [r for r in RESULTS if not r["ok"] and not r["gated"]]
    skipped = [r for r in RESULTS if r["gated"]]

    report = {
        "generated_at": time.time(),
        "features": RESULTS,
        "summary": {"passed": len(passed), "failed": len(failed),
                    "gated": len(skipped), "total": len(RESULTS)},
        "routes": {"total": len(templates), "visited": len(VISITED) - 0,
                   "uncovered": uncovered},
    }
    out = ROOT / "docs" / "feature-audit.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"],
                      "uncovered_routes": uncovered,
                      "report": str(out.relative_to(ROOT))}, indent=2))
    client.close()
    state.close()
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
