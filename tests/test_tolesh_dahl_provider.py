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


def test_chat_chain_tries_the_other_dahl_models_before_other_vendors():
    registry = Registry.from_defaults()
    chain = [m.id for m in registry.candidates("chat")]
    assert chain[0] == "dahl:deepseek-ai/DeepSeek-V4-Flash-0731"
    # A queued model should fall to its neighbour inside the same provider.
    assert chain[1] == "dahl:MiniMaxAI/MiniMax-M2.7"
    assert chain[2] == "dahl:zai-org/GLM-5.3-Flash"
    assert chain[-1] == "offline:tolesh-offline"


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
