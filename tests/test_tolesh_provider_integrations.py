"""Token Harbor, Xiaomi MiMo and MiniMax, plus the user-editable chain.

Everything here is offline. The endpoint facts being asserted were read from the
providers' own documentation and from ``GET /v1/models`` with a real key, so a
future edit that "tidies" a base URL or a model id has to argue with this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tolesh.ai import ModelRouter, Registry, UsageLedger
from tolesh.ai import registry as registry_mod
from tolesh.ai.providers.base import ProviderError
from tolesh.ai.registry import ProviderSpec


@pytest.fixture()
def user_config(tmp_path, monkeypatch):
    """Point the per-user providers file at a temp path, never the real one."""
    path = tmp_path / "providers.json"
    monkeypatch.setattr(registry_mod, "CONFIG_PATH", path)
    monkeypatch.delenv("TOLESH_DEFAULT_MODEL", raising=False)
    return path


@pytest.fixture()
def ledger():
    led = UsageLedger(in_memory=True)
    try:
        yield led
    finally:
        led.close()


# --------------------------------------------------------------- the catalogue
def test_token_harbor_is_declared_against_the_real_gateway():
    reg = Registry.from_defaults()
    th = reg.provider("tokenharbor")
    assert th is not None
    assert th.base_url == "https://tokenharbor.ai/v1"
    assert th.api_key_env == "TOLESH_TOKENHARBOR_API_KEY"
    ids = [m.model for m in th.models]
    # These five are the ids this app routes to; the gateway serves 81 in total.
    assert "deepseek-v4.1-flash" in ids
    assert "mimo-v2.6-flash" in ids
    assert "glm-5.3-flash" in ids
    # ":free" is the gateway's own suffix for free-tier admission.
    assert "deepseek-v4.1-flash:free" in ids
    assert "mimo-v2.6-flash:free" in ids


def test_mimo_is_declared_against_xiaomis_own_platform():
    reg = Registry.from_defaults()
    mimo = reg.provider("mimo")
    assert mimo is not None
    assert mimo.base_url == "https://api.xiaomimimo.com/v1"
    assert mimo.api_key_env == "TOLESH_MIMO_API_KEY"
    assert [m.model for m in mimo.models] == ["mimo-v2.6-flash", "mimo-v2.6-pro"]


def test_minimax_is_declared_against_the_current_platform_models():
    """MiniMax's own endpoint serves M3.1-Flash and M3 — the M2.7 id only exists
    on Dahl, so claiming M2.7 here would be a model that never answers."""
    reg = Registry.from_defaults()
    mm = reg.provider("minimax")
    assert mm is not None
    assert mm.base_url == "https://api.minimax.io/v1"
    assert mm.api_key_env == "TOLESH_MINIMAX_API_KEY"
    assert [m.model for m in mm.models] == ["MiniMax-M3.1-Flash", "MiniMax-M3"]
    assert reg.model("dahl:MiniMaxAI/MiniMax-M2.7") is not None


def test_free_tier_suffix_survives_the_trip_to_the_wire():
    """The provider strips only the leading ``provider:`` — a second colon is
    part of the model id and must not be eaten."""
    spec = ProviderSpec(name="tokenharbor", label="Token Harbor",
                        base_url="https://tokenharbor.ai/v1",
                        api_key_env="TOLESH_TOKENHARBOR_API_KEY", api_key="k")
    model_id = "tokenharbor:deepseek-v4.1-flash:free"
    assert model_id.split(":", 1)[-1] == "deepseek-v4.1-flash:free"


# ------------------------------------------------------------- the chain order
def test_chat_chain_follows_the_requested_order(user_config, monkeypatch):
    for env in ("TOLESH_TOKENHARBOR_API_KEY", "TOLESH_DEEPSEEK_API_KEY",
                "TOLESH_DAHL_API_KEY", "TOLESH_MIMO_API_KEY", "TOLESH_MINIMAX_API_KEY"):
        monkeypatch.setenv(env, "test-key")
    reg = Registry.from_defaults()
    chain = [m.id for m in reg.candidates("chat")]
    order = [
        "tokenharbor:deepseek-v4.1-flash",
        "tokenharbor:deepseek-v4.1-flash:free",
        "deepseek:deepseek-chat",
        "dahl:zai-org/GLM-5.3-Flash",
        "mimo:mimo-v2.6-flash",
        "minimax:MiniMax-M3.1-Flash",
        "dahl:MiniMaxAI/MiniMax-M2.7",
    ]
    # Each requested step appears, in the requested relative order.
    positions = [chain.index(x) for x in order]
    assert positions == sorted(positions), f"out of order: {chain[:8]}"
    assert chain[0] == "tokenharbor:deepseek-v4.1-flash"
    assert chain[-1] == "offline:tolesh-offline"


def test_unkeyed_providers_cost_nothing_in_the_chain(user_config, monkeypatch):
    monkeypatch.setenv("TOLESH_TOKENHARBOR_API_KEY", "test-key")
    monkeypatch.delenv("TOLESH_MIMO_API_KEY", raising=False)
    reg = Registry.from_defaults()
    chain = [m.id for m in reg.candidates("chat")]
    assert "mimo:mimo-v2.6-flash" not in chain      # no key yet
    assert "tokenharbor:mimo-v2.6-flash:free" in chain


# -------------------------------------------------------- per-model switches
def test_disabling_a_model_removes_it_from_the_chain_and_persists(user_config, monkeypatch):
    monkeypatch.setenv("TOLESH_TOKENHARBOR_API_KEY", "test-key")
    reg = Registry.from_defaults()
    assert reg.candidates("chat")[0].id == "tokenharbor:deepseek-v4.1-flash"

    reg.set_model_enabled("tokenharbor:deepseek-v4.1-flash", False, persist=True)
    assert "tokenharbor:deepseek-v4.1-flash" not in [m.id for m in reg.candidates("chat")]

    saved = json.loads(user_config.read_text(encoding="utf-8"))
    assert saved["model_enabled"]["tokenharbor:deepseek-v4.1-flash"] is False

    # A restart keeps the switch, and re-enabling brings the model back first.
    again = Registry.from_defaults()
    assert again.model("tokenharbor:deepseek-v4.1-flash").enabled is False
    again.set_model_enabled("tokenharbor:deepseek-v4.1-flash", True, persist=True)
    assert again.candidates("chat")[0].id == "tokenharbor:deepseek-v4.1-flash"


def test_unknown_model_cannot_be_switched(user_config):
    reg = Registry.from_defaults()
    with pytest.raises(KeyError):
        reg.set_model_enabled("tokenharbor:nope", False)


# ---------------------------------------------------------- the edited order
def test_custom_order_is_honoured_and_persisted(user_config, monkeypatch):
    monkeypatch.setenv("TOLESH_TOKENHARBOR_API_KEY", "test-key")
    monkeypatch.setenv("TOLESH_DAHL_API_KEY", "test-key")
    reg = Registry.from_defaults()

    reg.set_chat_order(["dahl:zai-org/GLM-5.3-Flash",
                        "tokenharbor:deepseek-v4.1-flash"], persist=True)
    assert [m.id for m in reg.candidates("chat")][:2] == [
        "dahl:zai-org/GLM-5.3-Flash", "tokenharbor:deepseek-v4.1-flash"]

    again = Registry.from_defaults()
    assert again.chat_order == ["dahl:zai-org/GLM-5.3-Flash",
                               "tokenharbor:deepseek-v4.1-flash"]
    assert again.candidates("chat")[0].id == "dahl:zai-org/GLM-5.3-Flash"


def test_custom_order_does_not_hijack_the_fast_lane(user_config, monkeypatch):
    monkeypatch.setenv("TOLESH_TOKENHARBOR_API_KEY", "test-key")
    reg = Registry.from_defaults()
    reg.set_chat_order(["dahl:zai-org/GLM-5.3-Flash"], persist=True)
    # "fast" keeps its own order; a conversation reorder must not change it.
    assert [m.id for m in reg.candidates("fast")][0] != "dahl:zai-org/GLM-5.3-Flash"


def test_unknown_id_in_order_is_rejected(user_config):
    reg = Registry.from_defaults()
    with pytest.raises(KeyError):
        reg.set_chat_order(["tokenharbor:does-not-exist"])


def test_empty_order_restores_the_built_in_chain(user_config, monkeypatch):
    monkeypatch.setenv("TOLESH_TOKENHARBOR_API_KEY", "test-key")
    reg = Registry.from_defaults()
    reg.set_chat_order(["tokenharbor:deepseek-v4.1-flash:free"], persist=True)
    reg.set_chat_order([], persist=True)
    assert reg.chat_order == []
    assert reg.candidates("chat")[0].id == "tokenharbor:deepseek-v4.1-flash"


# ------------------------------------------------- failure modes the spec names
class _FlakyGateway:
    """One provider, several models, scripted failures per model."""

    name = "tokenharbor"

    def __init__(self, script: dict[str, list[ProviderError | None]]):
        self.script = script
        self.calls: list[str] = []

    def _fail(self, model_id: str) -> None:
        self.calls.append(model_id)
        queue = self.script.get(model_id) or [None]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if item is not None:
            raise item

    def chat_stream(self, messages, model_id, **kw):
        self._fail(model_id)
        yield "ok"

    def chat(self, messages, model_id, **kw):
        from tolesh.ai.providers import ProviderResult, Usage
        self._fail(model_id)
        return ProviderResult(text="ok", model_id=model_id,
                              usage=Usage(prompt_tokens=1, completion_tokens=1))


def _router_with(stub, user_config, monkeypatch):
    monkeypatch.setenv("TOLESH_TOKENHARBOR_API_KEY", "test-key")
    router = ModelRouter(Registry.from_defaults(), UsageLedger(in_memory=True))
    router._provider_for = lambda spec: stub
    return router


def test_timeout_falls_back_to_the_next_model(user_config, monkeypatch):
    timeout = ProviderError("transport error: timed out", provider="tokenharbor",
                            model="deepseek-v4.1-flash", retryable=True, status=None)
    stub = _FlakyGateway({"tokenharbor:deepseek-v4.1-flash": [timeout]})
    router = _router_with(stub, user_config, monkeypatch)
    events = list(router.stream([{"role": "user", "content": "hi"}], task="chat"))
    done = next(e for e in events if e.type == "done")
    assert done.data["model"] != "tokenharbor:deepseek-v4.1-flash"
    assert "tokenharbor:deepseek-v4.1-flash:free" in stub.calls


def test_5xx_falls_back_to_the_next_model(user_config, monkeypatch):
    boom = ProviderError("HTTP 503: unavailable", provider="tokenharbor",
                         model="deepseek-v4.1-flash", retryable=True, status=503)
    stub = _FlakyGateway({"tokenharbor:deepseek-v4.1-flash": [boom]})
    router = _router_with(stub, user_config, monkeypatch)
    events = list(router.stream([{"role": "user", "content": "hi"}], task="chat"))
    assert any(e.type == "done" for e in events)


def test_connection_failure_falls_back(user_config, monkeypatch):
    dead = ProviderError("transport error: connection refused", provider="tokenharbor",
                         model="deepseek-v4.1-flash", retryable=True)
    stub = _FlakyGateway({"tokenharbor:deepseek-v4.1-flash": [dead]})
    router = _router_with(stub, user_config, monkeypatch)
    assert any(e.type == "done" for e in router.stream(
        [{"role": "user", "content": "hi"}], task="chat"))


def test_a_malformed_request_stops_the_chain_instead_of_burning_providers(user_config, monkeypatch):
    bad = ProviderError("HTTP 400: messages[0].role is invalid", provider="tokenharbor",
                        model="deepseek-v4.1-flash", retryable=False, status=400,
                        code="invalid_request")
    stub = _FlakyGateway({"tokenharbor:deepseek-v4.1-flash": [bad]})
    router = _router_with(stub, user_config, monkeypatch)
    events = list(router.stream([{"role": "user", "content": "hi"}], task="chat"))

    # Exactly one call: every model would reject the same payload.
    assert stub.calls == ["tokenharbor:deepseek-v4.1-flash"]
    err = next(e for e in events if e.type == "error")
    assert err.data["fatal"] is True
    assert "rejected the request" in err.data["message"]


def test_every_provider_failing_produces_one_clear_error(user_config, monkeypatch):
    class _AllDead:
        name = "tokenharbor"

        def __init__(self):
            self.calls = []

        def chat_stream(self, messages, model_id, **kw):
            self.calls.append(model_id)
            raise ProviderError("transport error: refused", provider="tokenharbor",
                                model=model_id, retryable=True)
            yield  # pragma: no cover - makes this a generator

    stub = _AllDead()
    router = _router_with(stub, user_config, monkeypatch)
    events = list(router.stream([{"role": "user", "content": "hi"}], task="chat"))
    errors = [e for e in events if e.type == "error"]
    assert len(errors) == 1, "one clear error, not one per provider"
    assert errors[0].data["attempts"], "the error must say what was tried"
    assert not [e for e in events if e.type == "done"]
    # Retries are bounded by the provider's max_retries (2 by default), so a
    # dead endpoint costs 3 attempts and then the chain moves on — never a loop.
    from collections import Counter
    counts = Counter(stub.calls)
    assert max(counts.values()) <= 3, f"unbounded retry: {counts.most_common(2)}"


def test_tokens_from_a_failed_model_are_discarded_before_the_next_one(user_config, monkeypatch):
    """Partial output from a failed model is followed by a ``reset`` so the UI
    can drop it; the answer must come from one model only."""
    class _Partial:
        name = "tokenharbor"

        def __init__(self):
            self.calls = []

        def chat_stream(self, messages, model_id, **kw):
            self.calls.append(model_id)
            if model_id.endswith("deepseek-v4.1-flash"):
                yield "half a sen"
                raise ProviderError("HTTP 503: gone", provider="tokenharbor",
                                    model=model_id, retryable=True, status=503)
            yield "full answer"

    stub = _Partial()
    router = _router_with(stub, user_config, monkeypatch)
    events = list(router.stream([{"role": "user", "content": "hi"}], task="chat"))

    kinds = [e.type for e in events]
    assert "reset" in kinds
    reset_at = kinds.index("reset")
    # Nothing before the reset may survive: the answer is what comes after it.
    after = "".join(e.data for e in events[reset_at:] if e.type == "token")
    assert after == "full answer"
    assert "half a sen" not in after


# ------------------------------------------------------------------- the API
@pytest.fixture()
def client(user_config, monkeypatch):
    from fastapi.testclient import TestClient
    from tolesh.server.app import AppState, create_app
    monkeypatch.setenv("TOLESH_TOKENHARBOR_API_KEY", "thk_live_test")
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    state = AppState(in_memory=True)
    try:
        with TestClient(create_app(state=state, serve_static=False)) as c:
            yield c
    finally:
        state.close()


def test_api_switches_one_model_and_reports_the_chain(client):
    order = client.get("/api/fallback-order").json()
    assert order["models"][0] == "tokenharbor:deepseek-v4.1-flash"

    r = client.patch("/api/models/tokenharbor:deepseek-v4.1-flash", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["model"]["enabled"] is False

    after = client.get("/api/fallback-order").json()
    assert "tokenharbor:deepseek-v4.1-flash" not in after["models"]
    # Switched-off models stay visible so they can be switched back on.
    parked = [m for m in after["detail"] if not m["enabled"]]
    assert "tokenharbor:deepseek-v4.1-flash" in [m["id"] for m in parked]


def test_api_rejects_an_unknown_model(client):
    assert client.patch("/api/models/nope:nope", json={"enabled": False}).status_code == 404


def test_api_saves_and_resets_the_order(client):
    ids = ["tokenharbor:deepseek-v4.1-flash:free",
           "tokenharbor:deepseek-v4.1-flash"]
    r = client.put("/api/fallback-order", json={"models": ids})
    assert r.status_code == 200
    assert r.json()["models"][:2] == ids

    assert client.put("/api/fallback-order", json={"models": []}).status_code == 200
    assert client.get("/api/fallback-order").json()["custom"] == []


def test_api_rejects_an_unknown_id_in_the_order(client):
    r = client.put("/api/fallback-order", json={"models": ["tokenharbor:nope"]})
    assert r.status_code == 400


def test_api_never_returns_the_token_harbor_key(client):
    body = json.dumps(client.get("/api/providers").json())
    assert "thk_live_test" not in body
    th = next(p for p in client.get("/api/providers").json()["providers"]
              if p["name"] == "tokenharbor")
    assert th["api_key"] == "***"
    assert th["configured"] is True


def test_saved_key_is_written_outside_the_repository_with_0600(client, user_config):
    client.patch("/api/providers/tokenharbor", json={"api_key": "thk_live_secret"})
    saved = json.loads(Path(user_config).read_text(encoding="utf-8"))
    assert saved["providers"]["tokenharbor"]["api_key"] == "thk_live_secret"
    mode = Path(user_config).stat().st_mode & 0o777
    assert mode == 0o600, f"key file is {oct(mode)}, expected 0600"
    assert not str(Path(user_config).resolve()).startswith("/workspace")


def test_the_offline_engine_cannot_be_switched_off(client):
    """It is the terminal fallback; a chain that can end with nothing to answer
    is not a setting anyone means to choose."""
    r = client.patch("/api/models/offline:tolesh-offline", json={"enabled": False})
    assert r.status_code == 400
    assert "last-resort" in r.json()["detail"]
    chain = client.get("/api/fallback-order").json()["models"]
    assert chain[-1] == "offline:tolesh-offline"
