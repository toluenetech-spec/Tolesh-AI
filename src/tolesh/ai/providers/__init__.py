"""Provider adapters. Each adapter speaks one transport dialect; the router
only ever sees the :class:`AIProvider` contract."""

from .base import AIProvider, ProviderError, ProviderResult, Usage, estimate_tokens
from .openai_compatible import OpenAICompatibleProvider
from .offline import OfflineProvider

__all__ = [
    "AIProvider",
    "ProviderError",
    "ProviderResult",
    "Usage",
    "estimate_tokens",
    "OpenAICompatibleProvider",
    "OfflineProvider",
]
