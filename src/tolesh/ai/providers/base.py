"""The provider contract. Deliberately tiny so new vendors are cheap to add."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

Message = dict[str, str]


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class ProviderResult:
    text: str
    model_id: str
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = "stop"
    raw: dict[str, Any] | None = None


class ProviderError(Exception):
    """Normalised failure from any provider.

    ``retryable`` drives the router's backoff; auth/config errors are not
    retried because retrying them only burns quota and time.
    """

    def __init__(self, message: str, *, provider: str, model: str,
                 retryable: bool = False, status: int | None = None,
                 cause: BaseException | None = None,
                 retry_after: float | None = None,
                 code: str | None = None):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.retryable = retryable
        self.status = status
        self.cause = cause
        # Seconds the endpoint asked us to wait, when it bothered to say so.
        self.retry_after = retry_after
        # Machine-readable code from the endpoint's error body, e.g.
        # "model_concurrency". Lets the router tell "this model is full, use
        # another one" apart from "this request is wrong, do not retry".
        self.code = code

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "message": str(self),
            "retryable": self.retryable,
            "status": self.status,
            "retry_after": self.retry_after,
            "code": self.code,
        }


class AIProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def chat(self, messages: list[Message], model_id: str, *,
             temperature: float = 0.7, max_tokens: int = 1024,
             timeout: float | None = None) -> ProviderResult:
        """Single-shot completion."""

    @abstractmethod
    def chat_stream(self, messages: list[Message], model_id: str, *,
                    temperature: float = 0.7, max_tokens: int = 1024,
                    timeout: float | None = None,
                    on_usage: Callable[[Usage], None] | None = None) -> Iterator[str]:
        """Yield text deltas. Call ``on_usage`` once at the end if available."""

    def health(self) -> bool:
        """Cheap liveness signal. Never raises."""
        return True


def estimate_tokens(text: str) -> int:
    """Deterministic fallback when a provider does not report usage.

    ~4 characters per token is a reasonable cross-lingual approximation for
    accounting only; it is explicitly labelled as an estimate in the ledger.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
