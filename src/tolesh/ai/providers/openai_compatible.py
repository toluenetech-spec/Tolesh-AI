"""Real OpenAI-compatible HTTP adapter.

Works with any endpoint that implements ``POST /chat/completions`` with SSE
streaming (OpenAI, Groq, DeepSeek, OpenRouter, Ollama's /v1, Gonka, JEV, Dahl,
vLLM, LM Studio, etc.). Adding a vendor is a registry entry, not new code.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterator

import httpx

from ..registry import ProviderSpec
from .base import (
    AIProvider, Message, ProviderError, ProviderResult, Usage, estimate_tokens,
)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenAICompatibleProvider(AIProvider):
    def __init__(self, spec: ProviderSpec, client: httpx.Client | None = None):
        self.spec = spec
        self._injected_client = client

    @property
    def name(self) -> str:
        return self.spec.name

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.spec.api_key:
            h["Authorization"] = f"Bearer {self.spec.api_key}"
        return h

    def _url(self) -> str:
        base = self.spec.base_url.rstrip("/")
        return base + "/chat/completions"

    def _body(self, messages: list[Message], model: str, *, stream: bool,
              temperature: float, max_tokens: int) -> dict[str, Any]:
        return {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

    def _raise_for_status(self, response: httpx.Response, model: str) -> None:
        if response.status_code < 400:
            return
        try:
            detail = response.json().get("error", {}).get("message", response.text[:300])
        except Exception:
            detail = response.text[:300]
        raise ProviderError(
            f"HTTP {response.status_code}: {detail}",
            provider=self.spec.name, model=model,
            retryable=response.status_code in _RETRYABLE_STATUS,
            status=response.status_code,
        )

    def _client(self, timeout: float):
        if self._injected_client is not None:
            return self._injected_client
        return httpx.Client(timeout=timeout)

    def chat(self, messages: list[Message], model_id: str, *,
             temperature: float = 0.7, max_tokens: int = 1024,
             timeout: float | None = None) -> ProviderResult:
        model = model_id.split(":", 1)[-1]
        tmo = timeout or self.spec.timeout
        body = self._body(messages, model, stream=False,
                          temperature=temperature, max_tokens=max_tokens)
        try:
            client = self._client(tmo)
            owns = self._injected_client is None
            try:
                r = client.post(self._url(), json=body, headers=self._headers())
                self._raise_for_status(r, model)
                payload = r.json()
            finally:
                if owns:
                    client.close()
        except ProviderError:
            raise
        except Exception as exc:  # network / parse
            raise ProviderError(f"transport error: {exc}", provider=self.spec.name,
                                model=model, retryable=True, cause=exc) from exc

        choice = (payload.get("choices") or [{}])[0]
        text = choice.get("message", {}).get("content") or ""
        usage_raw = payload.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or estimate_tokens(
                "\n".join(m.get("content", "") for m in messages))),
            completion_tokens=int(usage_raw.get("completion_tokens") or estimate_tokens(text)),
        )
        return ProviderResult(text=text, model_id=model_id, usage=usage,
                              finish_reason=choice.get("finish_reason") or "stop",
                              raw=payload)

    def chat_stream(self, messages: list[Message], model_id: str, *,
                    temperature: float = 0.7, max_tokens: int = 1024,
                    timeout: float | None = None,
                    on_usage: Callable[[Usage], None] | None = None) -> Iterator[str]:
        model = model_id.split(":", 1)[-1]
        tmo = timeout or self.spec.timeout
        body = self._body(messages, model, stream=True,
                          temperature=temperature, max_tokens=max_tokens)
        usage_raw: dict[str, Any] = {}
        full_text: list[str] = []
        client = None
        owns = False
        try:
            client = self._client(tmo)
            owns = self._injected_client is None
            with client.stream("POST", self._url(), json=body,
                               headers=self._headers()) as response:
                self._raise_for_status(response, model)
                for line in response.iter_lines():
                    if not line:
                        continue
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="replace")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        usage_raw = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        full_text.append(piece)
                        yield piece
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"stream error: {exc}", provider=self.spec.name,
                                model=model, retryable=True, cause=exc) from exc
        finally:
            if owns and client is not None:
                client.close()

        text = "".join(full_text)
        if on_usage is not None:
            on_usage(Usage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or estimate_tokens(
                    "\n".join(m.get("content", "") for m in messages))),
                completion_tokens=int(usage_raw.get("completion_tokens") or estimate_tokens(text)),
            ))

    def health(self) -> bool:
        if not self.spec.configured:
            return False
        try:
            with httpx.Client(timeout=3.0) as c:
                r = c.get(self.spec.base_url.rstrip("/") + "/models",
                          headers=self._headers())
                return r.status_code < 500
        except Exception:
            return False
