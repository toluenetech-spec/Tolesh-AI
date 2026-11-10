"""Tolesh AI engine.

A provider-independent model router. Nothing in this package is hardcoded to a
single AI vendor: providers are declared in :mod:`registry`, adapters live in
:mod:`providers`, and :class:`router.ModelRouter` handles selection, fallback,
streaming, retries and usage accounting.

Secrets are NEVER stored in this repository. Provider keys are resolved at
runtime from ``TOLESH_<PROVIDER>_API_KEY`` environment variables or from a
per-user ``~/.config/tolesh/providers.json`` outside the source tree.
"""

from .registry import Registry, ProviderSpec, ModelSpec, TASK_MODEL_ORDER
from .router import ModelRouter, RouterError, RouterEvent
from .usage import UsageLedger

__all__ = [
    "Registry",
    "ProviderSpec",
    "ModelSpec",
    "TASK_MODEL_ORDER",
    "ModelRouter",
    "RouterError",
    "RouterEvent",
    "UsageLedger",
]
