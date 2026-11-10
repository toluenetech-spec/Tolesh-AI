"""Tests for the Dahl provider wiring and the model-discovery path.

Everything here is offline: the endpoint is replaced by a stub that returns a
recorded shape. The point is to pin the contract the live gateway was observed
to have — three model ids, ``429 model_concurrency`` on a busy model, and a
``/models`` listing that must not be confused with a failure.
"""

from __future__ import annotations

import inspect
import json

import pytest

from tolesh.ai import Registry, discover_models
from tolesh.ai.providers.base import ProviderError
from tolesh.ai.providers.openai_compatible import ThinkFilter, strip_think
from tolesh.ai.registry import ProviderSpec
from tolesh.server.app import _LazyApp


# --------------------------------------------------------------- the catalogue
def test_dahl_catalog_points_at_the_real_gateway():
    registry = Registry.from_defaults()
    dahl = registry.provider("dahl")
    assert dahl is not None
    assert dahl.base_url == "https://inference.dahl.global/v1"
    assert [m.model for m in dahl.models] == [
        "deepseek-ai/DeepSeek-V4-Flash-0731",
        "MiniMaxAI/MiniMax-M2.7",
        "zai-org/GLM-5.3-Flash",
    ]
    # The old placeholder ids never existed upstream.
    assert registry.model("dahl:dahl-core") is None
    assert registry.model("dahl:dahl-fast") is None


def test_chat_chain_keeps_the_dahl_models_in_a_sensible_order():
    """The chain itself is owned by the routing table (see
    test_tolesh_provider_integrations for the requested order); what matters
    here is that Dahl's own models do not drift apart from each other."""
    registry = Registry.from_defaults()
    chain = [m.id for m in registry.candidates("chat")]
    assert chain[-1] == "offline:tolesh-offline"
    dahl = [m for m in chain if m.startswith("dahl:")]
    assert dahl.index("dahl:zai-org/GLM-5.3-Flash") < dahl.index("dahl:MiniMaxAI/MiniMax-M2.7")
    # Nothing was dropped from the catalogue by the reorder.
    assert "dahl:deepseek-ai/DeepSeek-V4-Flash-0731" in chain


# ------------------------------------------------------------ model discovery
class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    def __init__(self, response=None, raises: Exception | None = None, **_kw):
        self._response = response
        self._raises = raises

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):  # noqa: ARG002 - signature parity
        if self._raises is not None:
            raise self._raises
        return self._response


def _spec(**kw) -> ProviderSpec:
    base = dict(name="dahl", label="Dahl", kind="openai_compatible",
                base_url="https://inference.dahl.global/v1", api_key="k")
    base.update(kw)
    return ProviderSpec(**base)


def test_discovery_reports_the_ids_the_endpoint_lists(monkeypatch):
    payload = {"data": [{"id": "deepseek-ai/DeepSeek-V4-Flash-0731"},
                        {"id": "MiniMaxAI/MiniMax-M2.7"},
                        {"id": "zai-org/GLM-5.3-Flash"}]}
    monkeypatch.setattr("tolesh.ai.discovery.httpx.Client",
                        lambda **kw: _FakeClient(_FakeResponse(200, payload)))
    out = discover_models(_spec())
    assert out["ok"] is True and out["status"] == "ok"
    assert out["models"] == ["deepseek-ai/DeepSeek-V4-Flash-0731",
                             "MiniMaxAI/MiniMax-M2.7", "zai-org/GLM-5.3-Flash"]
    assert out["count"] == 3


def test_discovery_reports_an_http_error_instead_of_an_empty_success(monkeypatch):
    body = {"error": {"code": "model_concurrency", "message": "at concurrency capacity"}}
    monkeypatch.setattr("tolesh.ai.discovery.httpx.Client",
                        lambda **kw: _FakeClient(_FakeResponse(429, body)))
    out = discover_models(_spec())
    assert out["ok"] is False
    assert out["status"] == "http_429"
    assert "concurrency capacity" in out["detail"]
    assert out["models"] == []


def test_discovery_reports_transport_failure(monkeypatch):
    monkeypatch.setattr("tolesh.ai.discovery.httpx.Client",
                        lambda **kw: _FakeClient(raises=OSError("connection refused")))
    out = discover_models(_spec())
    assert out["ok"] is False and out["status"] == "error"
    assert "connection refused" in out["detail"]


def test_discovery_refuses_an_endpoint_that_lists_nothing(monkeypatch):
    monkeypatch.setattr("tolesh.ai.discovery.httpx.Client",
                        lambda **kw: _FakeClient(_FakeResponse(200, {"data": []})))
    out = discover_models(_spec())
    assert out["ok"] is False and out["status"] == "no_models"


# --------------------------------------------------------- catalogue patching
def test_patching_models_replaces_the_catalogue_and_drops_stale_ids(tmp_path,
                                                                    monkeypatch):
    cfg = tmp_path / "providers.json"
    monkeypatch.setattr("tolesh.ai.registry.CONFIG_PATH", cfg)
    registry = Registry.from_defaults()

    registry.update_provider("dahl", {"models": ["vendor/Only-Model"]}, persist=True)
    assert [m.id for m in registry.provider("dahl").models] == ["dahl:vendor/Only-Model"]
    assert registry.model("dahl:MiniMaxAI/MiniMax-M2.7") is None

    saved = json.loads(cfg.read_text("utf-8"))
    assert saved["providers"]["dahl"]["models"] == ["vendor/Only-Model"]
    assert oct(cfg.stat().st_mode & 0o777) == "0o600"

    # A fresh registry must come back with exactly that catalogue.
    again = Registry.from_defaults()
    assert [m.id for m in again.provider("dahl").models] == ["dahl:vendor/Only-Model"]


def test_unknown_model_in_patch_is_ignored_rather_than_invented(tmp_path, monkeypatch):
    monkeypatch.setattr("tolesh.ai.registry.CONFIG_PATH", tmp_path / "providers.json")
    registry = Registry.from_defaults()
    before = [m.id for m in registry.provider("dahl").models]
    registry.update_provider("dahl", {"models": []})
    assert [m.id for m in registry.provider("dahl").models] == before


# ------------------------------------------------------------- error surface
def test_probe_attributes_failure_to_the_model_that_failed():
    """``probe`` must not walk the fallback chain like ``complete`` does."""

    class _Boom:
        def chat(self, *a, **kw):  # noqa: ARG002
            raise ProviderError("HTTP 429: at concurrency capacity", provider="dahl",
                                model="deepseek-ai/DeepSeek-V4-Flash-0731",
                                retryable=True, status=429, retry_after=2.0)

        def chat_stream(self, *a, **kw):  # noqa: ARG002
            raise AssertionError("not used")

    registry = Registry.from_defaults()
    from tolesh.ai.router import ModelRouter

    router = ModelRouter(registry)
    router._providers["dahl"] = _Boom()          # the adapter seam, not the network
    with pytest.raises(ProviderError) as exc:
        router.probe([{"role": "user", "content": "hi"}],
                     model="dahl:deepseek-ai/DeepSeek-V4-Flash-0731")
    assert exc.value.status == 429
    assert exc.value.retry_after == 2.0
    assert exc.value.as_dict()["retry_after"] == 2.0


# ----------------------------------------------------------- reasoning blocks
@pytest.mark.parametrize("raw,expected", [
    ("<think>noise</think>Hello", "Hello"),
    ("H<think>hidden</think>i", "Hi"),
    ("plain answer", "plain answer"),
    ("<think>never closed", ""),
    ("trailing <", "trailing <"),
])
def test_think_filter_strips_reasoning_per_character(raw, expected):
    f = ThinkFilter()
    out = "".join(f.feed(ch) for ch in raw) + f.flush()
    assert out == expected


def test_strip_think_is_the_whole_message_variant():
    assert strip_think("<think>why</think>Because.") == "Because."
    assert strip_think("no reasoning here") == "no reasoning here"


# ---------------------------------------------------------------- ASGI wiring
def test_lazy_app_is_asgi3_not_asgi2():
    """uvicorn picks ASGI2 when ``__call__`` is a plain function, then every
    request fails with a missing-arguments TypeError. Launching uvicorn found
    this; this test keeps it fixed."""
    assert inspect.iscoroutinefunction(_LazyApp().__call__)


# --------------------------------------------------- capacity-aware fallback
class _GatewayStub:
    """A provider whose busy models answer 429 model_concurrency, like Dahl."""

    name = "dahl"

    def __init__(self, busy: tuple[str, ...]):
        self.busy = busy
        self.calls: list[str] = []

    def _maybe_fail(self, model_id: str) -> None:
        self.calls.append(model_id)
        if any(b in model_id for b in self.busy):
            raise ProviderError(
                "HTTP 429: This model is at concurrency capacity.",
                provider="dahl", model=model_id, retryable=True,
                status=429, retry_after=30.0, code="model_concurrency",
            )

    def chat_stream(self, messages, model_id, **kw):
        self._maybe_fail(model_id)
        yield "hello"
        yield " there"

    def chat(self, messages, model_id, **kw):
        from tolesh.ai.providers import ProviderResult, Usage
        self._maybe_fail(model_id)
        return ProviderResult(text="hello there", model_id=model_id,
                              usage=Usage(prompt_tokens=1, completion_tokens=2))


@pytest.fixture()
def dahl_router(monkeypatch, tmp_path):
    """A router whose chain starts with the three Dahl models.

    Pinned explicitly so these tests keep exercising Dahl's capacity behaviour
    no matter what the default routing table prefers — the order itself is
    asserted in test_tolesh_provider_integrations. The per-user providers file
    is redirected to tmp_path so a developer's real keys cannot change what
    these assertions see.
    """
    from tolesh.ai import ModelRouter, UsageLedger
    from tolesh.ai import registry as registry_mod
    monkeypatch.setattr(registry_mod, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setenv("TOLESH_DAHL_API_KEY", "test-key")
    monkeypatch.delenv("TOLESH_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("TOLESH_TOKENHARBOR_API_KEY", raising=False)
    monkeypatch.delenv("TOLESH_DEEPSEEK_API_KEY", raising=False)
    ledger = UsageLedger(in_memory=True)
    registry = Registry.from_defaults()
    registry.set_chat_order(["dahl:deepseek-ai/DeepSeek-V4-Flash-0731",
                             "dahl:zai-org/GLM-5.3-Flash",
                             "dahl:MiniMaxAI/MiniMax-M2.7"])
    router = ModelRouter(registry, ledger)
    try:
        yield router
    finally:
        ledger.close()


def test_full_model_is_skipped_immediately_instead_of_retried(dahl_router):
    """A queued model says "switch model" — sleeping through retries would just
    make the user wait before the fallback they already asked for."""
    import time

    stub = _GatewayStub(busy=("DeepSeek", "GLM"))
    dahl_router._provider_for = lambda spec: stub

    started = time.time()
    events = list(dahl_router.stream([{"role": "user", "content": "hi"}], task="chat"))
    elapsed = time.time() - started

    # Exactly one call per model: no retry of a model that is already full.
    assert stub.calls == [
        "dahl:deepseek-ai/DeepSeek-V4-Flash-0731",
        "dahl:zai-org/GLM-5.3-Flash",
        "dahl:MiniMaxAI/MiniMax-M2.7",
    ]
    assert not [e for e in events if e.type == "status" and e.data.get("kind") == "retry"]
    assert elapsed < 2.0, f"fallback slept {elapsed:.1f}s on a full model"

    done = next(e for e in events if e.type == "done")
    assert done.data["model"] == "dahl:MiniMaxAI/MiniMax-M2.7"
    assert done.data["fallbacks_used"] == 2


def test_gateway_error_code_reaches_the_stream(dahl_router):
    stub = _GatewayStub(busy=("DeepSeek", "GLM"))
    dahl_router._provider_for = lambda spec: stub
    events = list(dahl_router.stream([{"role": "user", "content": "hi"}], task="chat"))
    errors = [e.data for e in events
              if e.type == "status" and e.data.get("kind") == "provider_error"]
    assert [e["code"] for e in errors] == ["model_concurrency", "model_concurrency"]
    assert [e["status"] for e in errors] == [429, 429]


def test_complete_path_also_switches_models_without_retrying(dahl_router):
    stub = _GatewayStub(busy=("DeepSeek", "GLM"))
    dahl_router._provider_for = lambda spec: stub
    result = dahl_router.complete([{"role": "user", "content": "hi"}], task="chat")
    assert result.text == "hello there"
    assert result.model_id == "dahl:MiniMaxAI/MiniMax-M2.7"
    assert stub.calls == [
        "dahl:deepseek-ai/DeepSeek-V4-Flash-0731",
        "dahl:zai-org/GLM-5.3-Flash",
        "dahl:MiniMaxAI/MiniMax-M2.7",
    ]


def test_error_body_code_is_parsed_from_the_gateway_response():
    """The 429 body is ``{"error": {"code": "model_concurrency", ...}}``; the
    router needs that code, not a substring match on prose."""
    from tolesh.ai.providers.openai_compatible import OpenAICompatibleProvider
    from tolesh.ai.registry import ProviderSpec

    spec = ProviderSpec(name="dahl", label="Dahl",
                        base_url="https://example.invalid/v1",
                        api_key_env="TOLESH_DAHL_API_KEY", api_key="k")
    prov = OpenAICompatibleProvider(spec)
    body = json.dumps({"error": {"code": "model_concurrency", "message": "busy"}})

    class _R:
        status_code = 429
        headers = {}

        def read(self):
            return body.encode()

    with pytest.raises(ProviderError) as exc:
        prov._raise_for_status(_R(), "zai-org/GLM-5.3-Flash")
    assert exc.value.code == "model_concurrency"
    assert exc.value.as_dict()["code"] == "model_concurrency"


def test_health_states_the_real_fallback_order(monkeypatch, tmp_path):
    """The UI line under the composer names the chain, so it has to come from
    the router rather than from catalogue order (which is by declaration)."""
    from fastapi.testclient import TestClient
    from tolesh.ai import registry as registry_mod
    from tolesh.server.app import AppState, create_app

    monkeypatch.setattr(registry_mod, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setenv("TOLESH_DAHL_API_KEY", "test-key")
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    state = AppState(in_memory=True)
    try:
        with TestClient(create_app(state=state, serve_static=False)) as c:
            order = c.get("/api/health").json()["fallback_order"]
    finally:
        state.close()
    # Only Dahl has a key here, so the chain is the requested order minus the
    # steps whose provider is not configured yet — and it still ends offline.
    assert order[0] == "dahl:zai-org/GLM-5.3-Flash"
    assert "dahl:MiniMaxAI/MiniMax-M2.7" in order
    assert order[-1] == "offline:tolesh-offline"
    assert not any(m.startswith("tokenharbor:") for m in order)
