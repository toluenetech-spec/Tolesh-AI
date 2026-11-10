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


def _retry_after(response: httpx.Response) -> float | None:
    """Parse ``Retry-After`` (seconds form) without trusting it blindly.

    Gateways send this when they queue a request; ignoring it makes the client
    hammer a model that has already said it is busy. The date form is skipped
    rather than mis-parsed.
    """
    raw = (response.headers.get("retry-after") or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if seconds < 0 or seconds > 3600:
        return None
    return seconds


_OPEN_TAGS = ("<think>",)
_CLOSE_TAGS = ("</think>",)


class ThinkFilter:
    """Drop reasoning blocks that some models inline in ``content``.

    MiniMax and the DeepSeek reasoners on this gateway emit their chain of
    thought inside the message itself. Rendering that in the chat bubble is
    noise, and storing it in the conversation corrupts later turns, so the
    adapter removes it at the boundary. Chunk boundaries are handled: a partial
    tag is held until it can be classified.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    @staticmethod
    def _first(text: str, tags: tuple[str, ...]) -> tuple[int, str]:
        best, best_tag = -1, ""
        for tag in tags:
            i = text.find(tag)
            if i >= 0 and (best < 0 or i < best):
                best, best_tag = i, tag
        return best, best_tag

    def feed(self, piece: str) -> str:
        self._buf += piece
        out: list[str] = []
        while self._buf:
            if self._inside:
                idx, tag = self._first(self._buf, _CLOSE_TAGS)
                if idx < 0:
                    keep = max(len(t) for t in _CLOSE_TAGS) - 1
                    if len(self._buf) > keep:
                        self._buf = self._buf[-keep:]
                    return "".join(out)
                self._buf = self._buf[idx + len(tag):]
                self._inside = False
                continue
            idx, tag = self._first(self._buf, _OPEN_TAGS)
            if idx < 0:
                keep = max(len(t) for t in _OPEN_TAGS) - 1
                if len(self._buf) > keep:
                    out.append(self._buf[: len(self._buf) - keep])
                    self._buf = self._buf[-keep:]
                return "".join(out)
            out.append(self._buf[:idx])
            self._buf = self._buf[idx + len(tag):]
            self._inside = True
        return "".join(out)

    def flush(self) -> str:
        """Release anything still buffered.

        An unterminated reasoning block is dropped (it was never an answer), and
        so is a dangling partial tag at end of stream.
        """
        if self._inside:
            self._buf = ""
            return ""
        held, self._buf = self._buf, ""
        cut = held.rfind("<")
        # A lone "<" at the very end is far more likely literal text than the
        # start of a tag, so only a longer suffix is treated as a partial tag.
        if cut >= 0 and len(held) - cut >= 2 and any(
                tag.startswith(held[cut:]) for tag in _OPEN_TAGS):
            return held[:cut]
        return held


def strip_think(text: str) -> str:
    """Whole-message variant of :class:`ThinkFilter`."""
    f = ThinkFilter()
    return (f.feed(text) + f.flush()).strip()


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
        # A streaming response has no body until it is read, and touching
        # ``.text`` first raises "Attempted to access streaming response
        # content, without having called read()" — which would hide the real
        # 429 model_concurrency message behind a transport-sounding error.
        try:
            raw = response.read()
        except Exception:
            raw = b""
        body = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        detail = body[:300]
        code: str | None = None
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict) and err.get("message"):
                    detail = str(err["message"])[:300]
                    if err.get("code"):
                        code = str(err["code"])
                elif payload.get("message"):
                    detail = str(payload["message"])[:300]
                    if payload.get("code"):
                        code = str(payload["code"])
        except Exception:
            pass
        raise ProviderError(
            f"HTTP {response.status_code}: {detail}",
            provider=self.spec.name, model=model,
            retryable=response.status_code in _RETRYABLE_STATUS,
            status=response.status_code,
            retry_after=_retry_after(response),
            code=code,
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
        text = strip_think(choice.get("message", {}).get("content") or "")
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
        think = ThinkFilter()
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
                        visible = think.feed(piece)
                        if visible:
                            yield visible
                tail = think.flush()
                if tail:
                    yield tail
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
