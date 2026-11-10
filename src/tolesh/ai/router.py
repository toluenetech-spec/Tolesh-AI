"""Provider-independent model router.

Responsibilities (all required by the Tolesh brief):
  * multiple providers and models, selected per task
  * explicit model selection and switching
  * provider-specific configuration (see registry.py)
  * automatic fallback when a provider fails
  * streaming responses
  * usage / token tracking
  * error handling and retries with exponential backoff
  * different models for different tasks
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterator

from .providers import (
    AIProvider, OfflineProvider, OpenAICompatibleProvider, ProviderError,
    ProviderResult, Usage, estimate_tokens,
)
from .providers.base import Message
from .registry import ModelSpec, Registry
from .usage import UsageLedger


@dataclass
class RouterEvent:
    """One item in the SSE stream consumed by the desktop / web / Android UI."""
    type: str          # token | status | usage | reset | done | error
    data: Any = None
    model_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data, "model_id": self.model_id}


class RouterError(Exception):
    def __init__(self, message: str, attempts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.attempts = attempts or []

    def as_dict(self) -> dict[str, Any]:
        return {"message": str(self), "attempts": self.attempts}


class ModelRouter:
    def __init__(self, registry: Registry | None = None,
                 ledger: UsageLedger | None = None, *, offline_delay: float = 0.012):
        self.registry = registry or Registry.from_defaults()
        self.ledger = ledger or UsageLedger()
        self._offline_delay = offline_delay
        self._providers: dict[str, AIProvider] = {}

    # ------------------------------------------------------------------ setup
    def _provider_for(self, spec: ModelSpec) -> AIProvider:
        p = self.registry.provider(spec.provider)
        if p is None:
            raise RouterError(f"unknown provider '{spec.provider}'")
        if p.kind == "offline":
            return self._providers.setdefault(
                "offline", OfflineProvider(delay=self._offline_delay))
        key = p.name
        if key not in self._providers:
            self._providers[key] = OpenAICompatibleProvider(p)
        return self._providers[key]

    def chain(self, task: str = "chat", preferred: str | None = None) -> list[ModelSpec]:
        return self.registry.candidates(task, preferred)

    def select(self, task: str = "chat", preferred: str | None = None) -> ModelSpec | None:
        chain = self.chain(task, preferred)
        return chain[0] if chain else None

    # -------------------------------------------------------------- streaming
    def stream(self, messages: list[Message], *, task: str = "chat",
               model: str | None = None, temperature: float = 0.7,
               max_tokens: int = 1024) -> Iterator[RouterEvent]:
        chain = self.chain(task, model)
        if not chain:
            yield RouterEvent("error", {"message": "No model candidates available."})
            return

        attempts: list[dict[str, Any]] = []
        started = time.time()
        emitted_any = False

        for index, spec in enumerate(chain):
            provider = self._provider_for(spec)
            pconf = self.registry.provider(spec.provider)
            max_attempts = 1 + max(0, pconf.max_retries if pconf else 0)
            yield RouterEvent("status", {
                "kind": "select", "model": spec.id,
                "provider": spec.provider, "position": index + 1,
                "total": len(chain),
                "configured": pconf.configured if pconf else False,
            }, spec.id)

            for attempt in range(1, max_attempts + 1):
                usage_holder: list[Usage | None] = [None]
                partial: list[str] = []
                t0 = time.time()

                def on_usage(u: Usage, holder=usage_holder) -> None:
                    holder[0] = u

                try:
                    for delta in provider.chat_stream(
                        messages, spec.id, temperature=temperature,
                        max_tokens=max_tokens, on_usage=on_usage,
                    ):
                        partial.append(delta)
                        emitted_any = True
                        yield RouterEvent("token", delta, spec.id)
                except ProviderError as exc:
                    attempts.append(exc.as_dict())
                    self.ledger.record(
                        provider=spec.provider, model=spec.id, task=task,
                        prompt_tokens=estimate_tokens(
                            "\n".join(m.get("content", "") for m in messages)),
                        completion_tokens=estimate_tokens("".join(partial)),
                        latency_ms=int((time.time() - t0) * 1000), status="error",
                    )
                    yield RouterEvent("status", {
                        "kind": "provider_error", "model": spec.id,
                        "message": str(exc), "retryable": exc.retryable,
                        "attempt": attempt,
                    }, spec.id)
                    if exc.retryable and attempt < max_attempts:
                        backoff = min(0.25 * (2 ** (attempt - 1)), 2.0)
                        yield RouterEvent("status", {
                            "kind": "retry", "model": spec.id,
                            "delay_ms": int(backoff * 1000), "attempt": attempt + 1,
                        }, spec.id)
                        time.sleep(backoff)
                        continue
                    # Discard partial output honestly before switching models.
                    if partial:
                        yield RouterEvent("reset", {"reason": "provider_failed"}, spec.id)
                    break  # next model in the fallback chain
                except Exception as exc:  # defensive: never crash the SSE stream
                    attempts.append({"model": spec.id, "message": f"unexpected: {exc}"})
                    yield RouterEvent("status", {
                        "kind": "provider_error", "model": spec.id,
                        "message": f"unexpected: {exc}", "retryable": False,
                    }, spec.id)
                    if partial:
                        yield RouterEvent("reset", {"reason": "provider_failed"}, spec.id)
                    break
                else:
                    usage = usage_holder[0] or Usage(
                        prompt_tokens=estimate_tokens(
                            "\n".join(m.get("content", "") for m in messages)),
                        completion_tokens=estimate_tokens("".join(partial)),
                    )
                    latency = int((time.time() - t0) * 1000)
                    self.ledger.record(
                        provider=spec.provider, model=spec.id, task=task,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        latency_ms=latency, status="ok",
                    )
                    yield RouterEvent("usage", {
                        **usage.as_dict(), "latency_ms": latency,
                        "model": spec.id, "provider": spec.provider,
                        "estimated": usage_holder[0] is None,
                    }, spec.id)
                    yield RouterEvent("done", {
                        "model": spec.id, "provider": spec.provider,
                        "fallbacks_used": index,
                        "elapsed_ms": int((time.time() - started) * 1000),
                    }, spec.id)
                    return

            if index < len(chain) - 1:
                yield RouterEvent("status", {
                    "kind": "fallback", "from": spec.id,
                    "to": chain[index + 1].id,
                }, chain[index + 1].id)

        yield RouterEvent("error", {
            "message": "Every provider in the fallback chain failed.",
            "attempts": attempts,
        })

    # ------------------------------------------------------------- single shot
    def complete(self, messages: list[Message], *, task: str = "chat",
                 model: str | None = None, temperature: float = 0.7,
                 max_tokens: int = 1024) -> ProviderResult:
        chain = self.chain(task, model)
        attempts: list[dict[str, Any]] = []
        for spec in chain:
            provider = self._provider_for(spec)
            pconf = self.registry.provider(spec.provider)
            max_attempts = 1 + max(0, pconf.max_retries if pconf else 0)
            for attempt in range(1, max_attempts + 1):
                t0 = time.time()
                try:
                    result = provider.chat(messages, spec.id,
                                           temperature=temperature,
                                           max_tokens=max_tokens)
                except ProviderError as exc:
                    attempts.append(exc.as_dict())
                    if exc.retryable and attempt < max_attempts:
                        time.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
                        continue
                    break
                except Exception as exc:
                    attempts.append({"model": spec.id, "message": f"unexpected: {exc}"})
                    break
                else:
                    self.ledger.record(
                        provider=spec.provider, model=spec.id, task=task,
                        prompt_tokens=result.usage.prompt_tokens,
                        completion_tokens=result.usage.completion_tokens,
                        latency_ms=int((time.time() - t0) * 1000), status="ok",
                    )
                    return result
        raise RouterError("Every provider in the fallback chain failed.", attempts)
