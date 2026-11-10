"""Unit tests for the Tolesh modules added on top of the JARVIS foundation.

The inherited upstream suite (5,500+ tests) exercises the original Python voice
pipeline, but it does not cover the new provider-independent router, layered
memory, permission gate, persona or shared API server. These tests close that
gap. Everything runs offline: no network, no native audio, no provider keys and
no writes outside ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tolesh.ai import (ModelRouter, Registry, RouterEvent, TASK_MODEL_ORDER,
                       UsageLedger)
from tolesh.ai.providers import estimate_tokens
from tolesh.ai.providers.offline import build_offline_reply
from tolesh.conversation import ConversationManager
from tolesh.memory_ext import CATEGORIES, LayeredMemory
from tolesh.permissions import (ALLOWED_APPS, PermissionGate, RiskLevel,
                                app_command, classify, is_path_allowed)
from tolesh.persona import TOLESH_PERSONA, build_system_prompt
from tolesh.tools_ext import ComputerTools
from tolesh.tools_ext.computer import _NOT_IMPLEMENTED

pytestmark = pytest.mark.unit


@pytest.fixture
def offline_registry() -> Registry:
    """A registry containing only the keyless offline engine.

    Guarantees the router never attempts a real HTTP request in tests while
    still exercising selection, streaming, retries, fallback and accounting.
    """
    offline = Registry.from_defaults().provider("offline")
    assert offline is not None, "the offline engine must always be declared"
    return Registry({"offline": offline})


# --------------------------------------------------------------------- registry

def test_routing_table_and_catalog_are_provider_independent(monkeypatch):
    monkeypatch.delenv("TOLESH_DEFAULT_MODEL", raising=False)
    registry = Registry.from_defaults()
    assert {"chat", "reasoning", "code", "fast", "voice_intent"} <= set(TASK_MODEL_ORDER)
    for task, order in TASK_MODEL_ORDER.items():
        assert order, f"{task} has no candidates"
        assert all(":" in model_id for model_id in order)

    providers = {m.provider for m in registry.models()}
    assert {"offline", "ollama", "jev", "gonka", "dahl", "openai", "groq",
            "deepseek"} <= providers
    assert len(registry.models()) >= 12
    assert registry.model("offline:tolesh-offline") is not None


def test_registry_never_exposes_provider_secrets(monkeypatch):
    secret = "sk-super-secret-do-not-leak"
    monkeypatch.setenv("TOLESH_JEV_API_KEY", secret)
    registry = Registry.from_defaults()

    jev = registry.provider("jev")
    assert jev is not None and jev.configured is True

    redacted = registry.redacted()
    assert secret not in json.dumps(redacted, ensure_ascii=False)
    jev_public = next(p for p in redacted["providers"] if p["name"] == "jev")
    assert jev_public["api_key"] == "***"
    assert jev_public["api_key_env"] == "TOLESH_JEV_API_KEY"


def test_router_fallback_chain_always_ends_with_offline(offline_registry):
    ledger = UsageLedger(in_memory=True)
    router = ModelRouter(offline_registry, ledger)
    try:
        for task in ("chat", "reasoning", "code", "fast", "voice_intent"):
            chain = router.chain(task)
            assert chain, f"no candidates for {task}"
            assert chain[-1].id == "offline:tolesh-offline"
        assert router.select("chat") is not None
    finally:
        ledger.close()


def test_preferred_model_is_honoured_first(monkeypatch):
    monkeypatch.delenv("TOLESH_DEFAULT_MODEL", raising=False)
    router = ModelRouter(Registry.from_defaults(), UsageLedger(in_memory=True))
    try:
        assert router.chain("chat", "openai:gpt-4o")[0].id == "openai:gpt-4o"
    finally:
        router.ledger.close()


# ----------------------------------------------------------------------- router

def _user(text: str = "How far?") -> list[dict[str, str]]:
    return [{"role": "user", "content": text}]


def test_router_streams_offline_with_usage_and_done(offline_registry):
    ledger = UsageLedger(in_memory=True)
    router = ModelRouter(offline_registry, ledger, offline_delay=0)
    try:
        events = list(router.stream(_user(), task="fast"))
        types = [ev.type for ev in events]

        assert all(isinstance(ev, RouterEvent) for ev in events)
        assert "status" in types and "token" in types
        assert "usage" in types and "done" in types
        assert "error" not in types

        done = next(ev for ev in events if ev.type == "done")
        assert done.data["model"] == "offline:tolesh-offline"
        assert done.data["fallbacks_used"] == 0
        assert done.as_dict() == {"type": "done", "data": done.data,
                                  "model_id": "offline:tolesh-offline"}

        usage = next(ev for ev in events if ev.type == "usage")
        assert usage.data["provider"] == "offline"
        assert usage.data["prompt_tokens"] > 0
        assert ledger.summary()["requests"] == 1
    finally:
        ledger.close()


def test_router_complete_returns_text_and_usage(offline_registry):
    ledger = UsageLedger(in_memory=True)
    router = ModelRouter(offline_registry, ledger, offline_delay=0)
    try:
        result = router.complete(_user("Wetin dey?"), task="fast")
        assert result.text.strip()
        assert result.model_id == "offline:tolesh-offline"
        assert result.usage.total_tokens > 0
        assert ledger.summary()["requests"] == 1
        assert ledger.summary()["by_provider"][0]["provider"] == "offline"
    finally:
        ledger.close()


def test_offline_engine_is_honest_and_culturally_aware():
    assert "offline" in build_offline_reply("which model are you?").lower()
    assert "simulated" in build_offline_reply("do you have real feelings?").lower()
    assert any(word in build_offline_reply("Bawo ni?").lower()
               for word in ("bawo", "yoruba", "Ẹ káàárọ̀".lower(), "how"))
    assert "no wahala" in build_offline_reply("How far?").lower() \
        or "how" in build_offline_reply("How far?").lower()
    assert estimate_tokens("one two three") > 0


def test_usage_ledger_summary_and_reset():
    ledger = UsageLedger(in_memory=True)
    try:
        ledger.record(provider="offline", model="offline:tolesh-offline",
                      task="chat", prompt_tokens=10, completion_tokens=20,
                      latency_ms=5)
        ledger.record(provider="jev", model="jev:jev-chat", task="chat",
                      prompt_tokens=4, completion_tokens=6, latency_ms=9,
                      status="error")
        summary = ledger.summary()
        assert summary["requests"] == 2
        assert summary["total_tokens"] == 40
        assert summary["errors"] == 1
        assert summary["by_provider"][0]["provider"] == "offline"
        ledger.reset()
        assert ledger.summary()["requests"] == 0
    finally:
        ledger.close()


# ------------------------------------------------------------- conversation

def test_conversation_window_is_bounded_and_ordered():
    manager = ConversationManager()
    try:
        conversation = manager.create("Evening")
        cid = conversation["id"]
        for i in range(12):
            manager.add_message(cid, "user", f"message {i}")
        window = manager.window(cid, limit=5)
        assert [m["content"] for m in window] == [f"message {i}" for i in range(7, 12)]
        assert all(set(m) == {"role", "content"} for m in window)
        assert manager.list()[0]["message_count"] == 12
    finally:
        manager.close()


def test_conversation_rename_and_delete():
    manager = ConversationManager()
    try:
        cid = manager.create()["id"]
        assert manager.rename(cid, "Renamed") is True
        assert manager.list()[0]["title"] == "Renamed"
        assert manager.rename("does-not-exist", "x") is False
        manager.add_message(cid, "user", "hello")
        assert manager.delete(cid) is True
        assert manager.messages(cid) == []
        assert manager.delete(cid) is False
    finally:
        manager.close()


# -------------------------------------------------------------------- memory

def test_layered_memory_categories_crud_export():
    memory = LayeredMemory()
    try:
        assert CATEGORIES == ("conversation", "preference", "personal",
                             "project", "task")
        for category in CATEGORIES:
            memory.add(category, f"a {category} fact")
        assert memory.stats()["total"] == 5
        assert memory.stats()["by_category"]["preference"] == 1

        preferred = memory.list(category="preference")
        assert len(preferred) == 1
        assert preferred[0]["category_label"] == "Preference"
        assert memory.search("project fact")[0]["category"] == "project"

        exported = memory.export()
        assert exported["categories"] == list(CATEGORIES)
        assert len(exported["items"]) == 5

        assert memory.delete(preferred[0]["id"]) is True
        assert memory.get(preferred[0]["id"]) is None
        assert memory.delete(preferred[0]["id"]) is False
        with pytest.raises(ValueError):
            memory.add("not-a-category", "x")
        with pytest.raises(ValueError):
            memory.add("personal", "   ")
    finally:
        memory.close()


def test_layered_memory_forget_requires_exact_phrase():
    memory = LayeredMemory()
    try:
        memory.add("personal", "My name is Ayo")
        assert memory.forget_everything("please delete")["status"] == "error"
        assert memory.stats()["total"] == 1
        result = memory.forget_everything("FORGET EVERYTHING")
        assert result == {"status": "ok", "deleted": 1}
        assert memory.stats()["total"] == 0
    finally:
        memory.close()


def test_layered_memory_build_context_is_transparent():
    memory = LayeredMemory()
    try:
        assert memory.build_context() == ""
        memory.add("preference", "Prefers short answers")
        memory.add("project", "Tolesh desktop app")
        context = memory.build_context()
        assert "[Preference]" in context and "[Project]" in context
        assert "Prefers short answers" in context
        assert "Only use the above" in build_system_prompt(context)
    finally:
        memory.close()


# ------------------------------------------------------------------- policy

def test_policy_classification_defaults_to_consequential():
    assert classify("get_time") is RiskLevel.SAFE
    assert classify("read_text_file") is RiskLevel.SAFE
    assert classify("write_text_file") is RiskLevel.ELEVATED
    assert classify("delete_file") is RiskLevel.CONSEQUENTIAL
    assert classify("run_command") is RiskLevel.CONSEQUENTIAL
    assert classify("never-seen-before-tool") is RiskLevel.CONSEQUENTIAL


def test_policy_open_application_allowlist():
    for name in ALLOWED_APPS:
        assert classify("open_application", {"name": name}) is RiskLevel.ELEVATED
        assert app_command(name)
    assert classify("open_application", {"name": "unknown-app"}) is RiskLevel.CONSEQUENTIAL
    assert app_command("unknown-app") == ()


def test_policy_path_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("TOLESH_SANDBOX_ROOT", str(tmp_path))
    allowed = tmp_path / "notes.txt"
    allowed.write_text("hello", encoding="utf-8")
    assert is_path_allowed(str(allowed)) is True
    assert is_path_allowed("/etc/passwd") is False
    assert is_path_allowed(str(tmp_path / ".ssh" / "id_rsa")) is False
    assert is_path_allowed(str(tmp_path / ".." / ".." / "etc" / "passwd")) is False


# --------------------------------------------------------------------- gate

def test_permission_gate_safe_action_is_auto_approved_and_audited():
    gate = PermissionGate()
    try:
        decision = gate.request("get_time", {}, reason="test")
        assert decision["status"] == "auto-approved"
        assert decision["token"] is None
        assert any(e["tool"] == "get_time" and e["status"] == "auto-approved"
                   for e in gate.audit_log())
    finally:
        gate.close()


def test_permission_gate_requires_matching_token():
    gate = PermissionGate()
    try:
        pending = gate.request("write_text_file", {"path": "x"}, reason="test")
        assert pending["status"] == "pending"
        assert pending["id"] and pending["token"]
        assert gate.pending()[0]["id"] == pending["id"]

        assert gate.approve(pending["id"], "wrong-token")["status"] == "error"
        assert gate.approve("unknown-id", pending["token"])["status"] == "error"

        decision = gate.approve(pending["id"], pending["token"])
        assert decision["status"] == "approved"
        assert decision["tool"] == "write_text_file"
        assert gate.pending() == []
        assert gate.approve(pending["id"], pending["token"])["status"] == "error"
        assert any(e["status"] == "approved" for e in gate.audit_log())
    finally:
        gate.close()


def test_permission_gate_expiry_and_deny():
    gate = PermissionGate(ttl_sec=-1)
    try:
        pending = gate.request("open_application", {"name": "browser"})
        assert gate.pending() == []
        assert gate.approve(pending["id"], pending["token"])["status"] == "error"
    finally:
        gate.close()

    gate2 = PermissionGate()
    try:
        pending = gate2.request("open_application", {"name": "browser"})
        assert gate2.deny(pending["id"], pending["token"])["status"] == "denied"
        assert any(e["status"] == "denied" for e in gate2.audit_log())
    finally:
        gate2.close()


# -------------------------------------------------------------------- tools

def test_computer_tools_refuse_unimplemented_consequential_actions():
    tools = ComputerTools()
    try:
        listed = {t["name"]: t for t in tools.list_tools()}
        assert set(_NOT_IMPLEMENTED) <= set(listed)
        for name in _NOT_IMPLEMENTED:
            assert listed[name]["implemented"] is False
            assert listed[name]["risk"] == "consequential"
            result = tools.execute(name, {})
            assert result["status"] == "refused"
    finally:
        tools.gate.close()


def test_computer_tools_safe_read_and_pending_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("TOLESH_SANDBOX_ROOT", str(tmp_path))
    tools = ComputerTools()
    try:
        assert tools.invoke("get_time")["status"] == "ok"

        target = tmp_path / "note.txt"
        target.write_text("Tolesh", encoding="utf-8")
        read = tools.invoke("read_text_file", {"path": str(target)})
        assert read["status"] == "ok"
        assert read["result"]["text"] == "Tolesh"

        outside = tools.invoke("read_text_file", {"path": "/etc/passwd"})
        assert outside["status"] == "refused"

        elevated = tools.invoke("write_text_file", {"path": str(tmp_path / "new.txt")})
        assert elevated["status"] == "pending-confirmation"
        assert elevated["token"]
        assert tools.gate.pending()[0]["id"] == elevated["id"]
    finally:
        tools.gate.close()


# ------------------------------------------------------------------- persona

def test_persona_prompt_is_transparent_and_culturally_nigerian():
    prompt = build_system_prompt("My name is Ayo", task="code",
                                 model_label="Tolesh Offline (local)")
    assert "TOLESH" in TOLESH_PERSONA.upper()
    assert "male-presenting" in prompt
    assert "Nigerian Pidgin" in prompt and "Yoruba" in prompt
    assert "simulated" in prompt
    assert "My name is Ayo" in prompt
    assert "Never imply you know more than this." in prompt
    assert "Give correct, runnable code" in prompt
    assert "Tolesh Offline (local)" in prompt
    assert "offline engine" in prompt.lower()
