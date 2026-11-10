"""Provider and model registry for Tolesh.

Declaring a provider here does not require its API key to exist. Keys are
resolved lazily from the process environment at registry-build time; a provider
without a key is simply reported as ``configured: False`` and is skipped by the
router unless it is the offline engine. This keeps the application runnable
(and testable) on a fresh install with zero credentials.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(
    os.environ.get(
        "TOLESH_PROVIDERS_PATH",
        str(Path.home() / ".config" / "tolesh" / "providers.json"),
    )
)

# Task -> ordered model-id preferences. The router uses this to pick a model
# suited to the job ("different models for different tasks"). Anything not
# listed falls back to the registry default model.
TASK_MODEL_ORDER: dict[str, tuple[str, ...]] = {
    "chat": ("gonka:GLM-4.6", "openai:gpt-4o", "jev:jev-chat", "dahl:dahl-core"),
    "reasoning": ("deepseek:deepseek-reasoner", "gonka:GLM-4.6", "dahl:dahl-core"),
    "code": ("deepseek:deepseek-chat", "qwen2.5:7b", "openai:gpt-4o"),
    "fast": ("offline:tolesh-offline", "dahl:dahl-fast", "groq:llama-3.3-70b-versatile", "openai:gpt-4o-mini"),
    "voice_intent": ("offline:tolesh-offline", "dahl:dahl-fast", "openai:gpt-4o-mini"),
    "classification": ("offline:tolesh-offline", "dahl:dahl-fast", "openai:gpt-4o-mini"),
    "vision": ("openai:gpt-4o", "openrouter:openrouter/auto"),
}


@dataclass(frozen=True)
class ModelSpec:
    id: str                      # "provider:wire-model"
    provider: str
    model: str                   # exact model name sent over the wire
    label: str
    tasks: tuple[str, ...] = ("chat",)
    context: int = 32768
    streaming: bool = True
    tools: bool = False
    vision: bool = False
    cost_tier: str = "standard"  # free | cheap | standard | premium
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "label": self.label,
            "tasks": list(self.tasks),
            "context": self.context,
            "streaming": self.streaming,
            "tools": self.tools,
            "vision": self.vision,
            "cost_tier": self.cost_tier,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str
    kind: str = "openai_compatible"   # "openai_compatible" | "offline"
    base_url: str = ""
    api_key_env: str = ""
    api_key: str = ""                 # runtime only; NEVER persisted
    models: tuple[ModelSpec, ...] = ()
    timeout: float = 60.0
    max_retries: int = 2
    enabled: bool = True
    notes: str = ""

    @property
    def configured(self) -> bool:
        if not self.enabled:
            return False
        if self.kind == "offline":
            return True
        if not self.base_url:
            return False
        # Local/keyless endpoints (Ollama) need no key; hosted ones do.
        if not self.api_key_env:
            return True
        return bool(self.api_key)

    def as_dict(self, *, redact: bool = True) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "base_url": self.base_url,
            "api_key": ("***" if self.api_key else "") if redact else self.api_key,
            "api_key_env": self.api_key_env,
            "configured": self.configured,
            "enabled": self.enabled,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "notes": self.notes,
            "models": [m.as_dict() for m in self.models],
        }


def _m(provider: str, model: str, label: str, **kw: Any) -> ModelSpec:
    return ModelSpec(id=f"{provider}:{model}", provider=provider, model=model, label=label, **kw)


def _catalog() -> dict[str, ProviderSpec]:
    """Built-in declaration of planned and compatible providers."""

    def keyed(name: str, label: str, base: str, models: tuple[ModelSpec, ...],
              *, timeout: float = 60.0, notes: str = "") -> ProviderSpec:
        env = f"TOLESH_{name.upper()}_API_KEY"
        return ProviderSpec(
            name=name, label=label, kind="openai_compatible",
            base_url=os.environ.get(f"TOLESH_{name.upper()}_BASE_URL", base),
            api_key_env=env, api_key=os.environ.get(env, ""),
            models=models, timeout=timeout, notes=notes,
        )

    offline = ProviderSpec(
        name="offline", label="Tolesh Offline Engine", kind="offline",
        base_url="", api_key_env="", api_key="", timeout=5.0,
        notes="Deterministic local engine. Honest about not being a cloud LLM. Always available; used for offline mode, tests and last-resort fallback.",
        models=(
            _m("offline", "tolesh-offline", "Tolesh Offline (local)",
               tasks=("chat", "fast", "voice_intent", "classification"),
               cost_tier="free", streaming=True, context=8192),
        ),
    )

    ollama = ProviderSpec(
        name="ollama", label="Ollama (local)", kind="openai_compatible",
        base_url=os.environ.get("TOLESH_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
        api_key_env="", api_key="", timeout=120.0, max_retries=0,
        notes="Keyless local runtime inherited from the upstream foundation.",
        models=(
            _m("ollama", "llama3.1:8b", "Llama 3.1 8B", tasks=("chat", "code"), cost_tier="free"),
            _m("ollama", "qwen2.5:7b", "Qwen 2.5 7B", tasks=("chat", "code", "reasoning"), cost_tier="free"),
            _m("ollama", "gemma2:9b", "Gemma 2 9B", tasks=("chat",), cost_tier="free"),
        ),
    )

    jev = keyed(
        "jev", "Experiential Labs / JEV",
        os.environ.get("TOLESH_JEV_BASE_URL", "https://api.jev.chat/v1"),
        (
            _m("jev", "jev-chat", "JEV Chat", tasks=("chat",), cost_tier="standard"),
            _m("jev", "jev-reason", "JEV Reason", tasks=("chat", "reasoning"), cost_tier="premium"),
        ),
        notes="Primary planned provider. Configure TOLESH_JEV_BASE_URL + TOLESH_JEV_API_KEY.",
    )

    gonka = keyed(
        "gonka", "Gonka",
        os.environ.get("TOLESH_GONKA_BASE_URL", "https://api.gonka.ai/v1"),
        (
            _m("gonka", "GLM-4.6", "GLM-4.6", tasks=("chat", "reasoning"), cost_tier="standard", tools=True),
            _m("gonka", "DeepSeek-V3", "DeepSeek V3", tasks=("chat", "code", "reasoning"), cost_tier="standard", tools=True),
            _m("gonka", "MiniMax-M2", "MiniMax M2", tasks=("chat", "code"), cost_tier="standard"),
        ),
        notes="Gonka-hosted GLM / DeepSeek / MiniMax models.",
    )

    dahl = keyed(
        "dahl", "Dahl",
        os.environ.get("TOLESH_DAHL_BASE_URL", "https://api.dahl.ai/v1"),
        (
            _m("dahl", "dahl-core", "Dahl Core", tasks=("chat", "reasoning"), cost_tier="standard"),
            _m("dahl", "dahl-fast", "Dahl Fast", tasks=("fast", "voice_intent", "classification"), cost_tier="cheap"),
        ),
        notes="Dahl and comparable model providers.",
    )

    openai = keyed(
        "openai", "OpenAI", "https://api.openai.com/v1",
        (
            _m("openai", "gpt-4o", "GPT-4o", tasks=("chat", "vision", "code"), vision=True, tools=True, cost_tier="premium"),
            _m("openai", "gpt-4o-mini", "GPT-4o mini", tasks=("chat", "fast", "classification"), cost_tier="cheap"),
        ),
    )

    groq = keyed(
        "groq", "Groq", "https://api.groq.com/openai/v1",
        (_m("groq", "llama-3.3-70b-versatile", "Llama 3.3 70B", tasks=("chat", "fast"), cost_tier="cheap"),),
    )

    deepseek = keyed(
        "deepseek", "DeepSeek", "https://api.deepseek.com/v1",
        (
            _m("deepseek", "deepseek-chat", "DeepSeek Chat", tasks=("chat", "code"), cost_tier="cheap", tools=True),
            _m("deepseek", "deepseek-reasoner", "DeepSeek Reasoner", tasks=("reasoning", "code"), cost_tier="standard"),
        ),
    )

    openrouter = keyed(
        "openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
        (_m("openrouter", "openrouter/auto", "OpenRouter Auto", tasks=("chat", "vision"), cost_tier="standard"),),
        notes="Aggregator; useful as a broad fallback.",
    )

    return {p.name: p for p in (offline, ollama, jev, gonka, dahl, openai, groq, deepseek, openrouter)}


class Registry:
    """Ordered, queryable catalogue of providers and models."""

    def __init__(self, providers: dict[str, ProviderSpec] | None = None):
        self.providers: dict[str, ProviderSpec] = providers or {}
        self._models: dict[str, ModelSpec] = {}
        for p in self.providers.values():
            for m in p.models:
                self._models[m.id] = m
        self.default_model = os.environ.get("TOLESH_DEFAULT_MODEL", "")

    @classmethod
    def from_defaults(cls) -> "Registry":
        providers = _catalog()
        # Per-user overrides (outside the repo) can add endpoints/keys or
        # disable providers without touching source code.
        default_model = ""
        try:
            if CONFIG_PATH.exists():
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                for name, patch in (raw.get("providers") or {}).items():
                    cur = providers.get(name)
                    if cur is None:
                        continue
                    allowed = {k: patch[k] for k in ("base_url", "enabled", "timeout", "max_retries", "notes")
                               if k in patch}
                    if "api_key" in patch:
                        allowed["api_key"] = str(patch["api_key"])
                    providers[name] = replace(cur, **allowed)
                default_model = str(raw.get("default_model") or "").strip()
        except Exception:
            # A malformed user config must never prevent Tolesh from starting.
            pass
        reg = cls(providers)
        if default_model and reg.model(default_model) is not None:
            reg.default_model = default_model
        return reg

    # -------------------------------------------------- user configuration
    def update_provider(self, name: str, patch: dict[str, Any], *,
                        persist: bool = False) -> ProviderSpec:
        """Apply a partial update to one provider, in memory and on disk.

        The router holds this Registry instance, so mutating ``providers`` in
        place means a key saved from the settings UI is usable immediately —
        no restart, no second registry.
        """
        cur = self.providers.get(name)
        if cur is None:
            raise KeyError(name)
        allowed = {k: patch[k] for k in ("base_url", "enabled", "timeout",
                                         "max_retries", "notes")
                   if k in patch and patch[k] is not None}
        if "api_key" in patch and patch["api_key"] is not None:
            allowed["api_key"] = str(patch["api_key"])
        if "base_url" in allowed:
            base = str(allowed["base_url"]).strip()
            if base and not base.startswith(("http://", "https://")):
                raise ValueError("base_url must start with http:// or https://")
            allowed["base_url"] = base
        new = replace(cur, **allowed)
        self.providers[name] = new
        for m in new.models:
            self._models[m.id] = m
        if persist:
            self.persist_provider(name, allowed)
        return new

    def persist_provider(self, name: str, patch: dict[str, Any]) -> None:
        """Merge *patch* into the per-user providers file (mode 0600)."""
        raw = self._read_user_config()
        providers = raw.setdefault("providers", {})
        entry = providers.setdefault(name, {})
        entry.update({k: v for k, v in patch.items() if v is not None})
        self._write_user_config(raw)

    def set_default_model(self, model_id: str, *, persist: bool = False) -> None:
        """Set (or clear, with "") the model the router tries first."""
        model_id = (model_id or "").strip()
        if model_id and self.model(model_id) is None:
            raise KeyError(f"Unknown model: {model_id}")
        self.default_model = model_id
        if persist:
            raw = self._read_user_config()
            raw["default_model"] = model_id
            self._write_user_config(raw)

    @staticmethod
    def _read_user_config() -> dict[str, Any]:
        try:
            if CONFIG_PATH.exists():
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    @staticmethod
    def _write_user_config(raw: dict[str, Any]) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        try:
            # API keys live here; keep the file private to the user.
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass

    def provider(self, name: str) -> ProviderSpec | None:
        return self.providers.get(name)

    def model(self, model_id: str) -> ModelSpec | None:
        return self._models.get(model_id)

    def models(self, *, provider: str | None = None, task: str | None = None,
               enabled_only: bool = True) -> list[ModelSpec]:
        out = []
        for m in self._models.values():
            if not m.enabled and enabled_only:
                continue
            p = self.providers.get(m.provider)
            if p is None or (not p.enabled and enabled_only):
                continue
            if provider and m.provider != provider:
                continue
            if task and task not in m.tasks and "chat" not in m.tasks:
                continue
            out.append(m)
        return out

    def candidates(self, task: str = "chat", preferred: str | None = None) -> list[ModelSpec]:
        """Ordered fallback chain for a task. Configured providers come first;
        the offline engine is always appended as a last resort so Tolesh never
        silently fails when every cloud provider is unavailable."""
        order: list[str] = []
        if preferred:
            order.append(preferred)
        if self.default_model:
            order.append(self.default_model)
        order.extend(TASK_MODEL_ORDER.get(task, ()))
        order.extend(TASK_MODEL_ORDER.get("chat", ()))
        order.append("offline:tolesh-offline")

        seen: set[str] = set()
        ready: list[ModelSpec] = []
        unconfigured: list[ModelSpec] = []
        for mid in order:
            if mid in seen:
                continue
            seen.add(mid)
            m = self.model(mid)
            if m is None or not m.enabled:
                continue
            p = self.provider(m.provider)
            if p is None or not p.enabled:
                continue
            (ready if p.configured else unconfigured).append(m)
        # Include any other configured models not already in the chain.
        for m in self.models():
            if m.id in seen:
                continue
            p = self.provider(m.provider)
            if p and p.configured:
                ready.append(m)
                seen.add(m.id)
        # The offline engine is guaranteed to be the terminal fallback.
        # An explicit user selection is honoured even when its provider has no
        # key yet, so the failure is visible in the stream instead of the model
        # being silently ignored. It is then followed by the normal chain.
        if preferred:
            pm = self.model(preferred)
            if pm and pm.enabled and all(m.id != preferred for m in ready):
                ready.insert(0, pm)

        offline = self.model("offline:tolesh-offline")
        if offline and offline.id in seen:
            ready = [m for m in ready if m.id != offline.id]
            ready.append(offline)
        return ready

    def redacted(self) -> dict[str, Any]:
        return {
            "default_model": self.default_model,
            "providers": [p.as_dict(redact=True) for p in self.providers.values()],
        }
