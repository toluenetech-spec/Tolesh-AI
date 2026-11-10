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
    # The conversation chain the user asked for: DeepSeek V4.1 Flash on Token
    # Harbor, then DeepSeek's own API, then GLM-5.3 Flash, then MiMo V2.6 Flash,
    # then MiniMax. A ``:free`` id is the same model under the gateway's free
    # admission, so it sits directly behind its paid twin instead of being a
    # separate vendor hop. Providers with no key are dropped from the chain by
    # ``candidates()``, so a declared-but-unkeyed step costs nothing.
    "chat": ("tokenharbor:deepseek-v4.1-flash",
             "tokenharbor:deepseek-v4.1-flash:free",
             "deepseek:deepseek-chat",
             "dahl:zai-org/GLM-5.3-Flash",
             "mimo:mimo-v2.6-flash", "tokenharbor:mimo-v2.6-flash:free",
             "minimax:MiniMax-M3.1-Flash", "dahl:MiniMaxAI/MiniMax-M2.7",
             "openai:gpt-4o", "jev:jev-chat"),
    "reasoning": ("tokenharbor:deepseek-v4.1-flash",
                  "tokenharbor:deepseek-v4.1-flash:free",
                  "deepseek:deepseek-reasoner",
                  "dahl:zai-org/GLM-5.3-Flash",
                  "mimo:mimo-v2.6-pro",
                  "minimax:MiniMax-M3", "dahl:MiniMaxAI/MiniMax-M2.7",
                  "gonka:GLM-4.6"),
    "code": ("tokenharbor:deepseek-v4.1-flash",
             "tokenharbor:deepseek-v4.1-flash:free",
             "deepseek:deepseek-chat",
             "dahl:zai-org/GLM-5.3-Flash",
             "mimo:mimo-v2.6-flash", "tokenharbor:mimo-v2.6-flash:free",
             "minimax:MiniMax-M3.1-Flash", "dahl:MiniMaxAI/MiniMax-M2.7",
             "openai:gpt-4o"),
    "fast": ("dahl:MiniMaxAI/MiniMax-M2.7", "dahl:zai-org/GLM-5.3-Flash",
             "offline:tolesh-offline", "groq:llama-3.3-70b-versatile",
             "openai:gpt-4o-mini"),
    "voice_intent": ("offline:tolesh-offline", "dahl:MiniMaxAI/MiniMax-M2.7",
                     "openai:gpt-4o-mini"),
    "classification": ("offline:tolesh-offline", "dahl:MiniMaxAI/MiniMax-M2.7",
                       "openai:gpt-4o-mini"),
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


def _label_for(wire_id: str) -> str:
    """Readable label from a wire model id ("vendor/Model-Name" -> "Model-Name")."""
    return wire_id.rsplit("/", 1)[-1] or wire_id


def _models_from_ids(provider: str, ids: Any) -> tuple[ModelSpec, ...]:
    """Build ModelSpecs from the plain id list a discovery call returned.

    Used when the user pulls the live model list of an arbitrary
    OpenAI-compatible endpoint from the settings UI. Only the ids are trusted;
    capabilities are left at conservative defaults instead of being invented.
    """
    if not isinstance(ids, (list, tuple)):
        return ()
    out: list[ModelSpec] = []
    seen: set[str] = set()
    for raw in ids:
        wire = str(raw or "").strip()
        if not wire or wire in seen or len(wire) > 200:
            continue
        seen.add(wire)
        out.append(_m(provider, wire, _label_for(wire), cost_tier="cheap"))
    return tuple(out)


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

    # Dahl is the provider this build is actually wired against. The model ids
    # below were read from ``GET https://inference.dahl.global/v1/models`` with
    # a real key — they are not guesses. The gateway is OpenAI-compatible, and
    # the free tier is queued behind paid traffic: a busy model answers
    # ``429 {"error":{"code":"model_concurrency", ...}}`` and names the models
    # that are free right now, which is why several are declared here.
    dahl = keyed(
        "dahl", "Dahl",
        os.environ.get("TOLESH_DAHL_BASE_URL", "https://inference.dahl.global/v1"),
        (
            _m("dahl", "deepseek-ai/DeepSeek-V4-Flash-0731", "DeepSeek V4 Flash",
               tasks=("chat", "code", "reasoning"), cost_tier="cheap", context=131072),
            _m("dahl", "MiniMaxAI/MiniMax-M2.7", "MiniMax M2.7",
               tasks=("chat", "code", "fast"), cost_tier="cheap"),
            _m("dahl", "zai-org/GLM-5.3-Flash", "GLM-5.3 Flash",
               tasks=("chat", "code", "reasoning", "fast", "classification"),
               cost_tier="cheap"),
        ),
        notes=("Dahl inference gateway (OpenAI-compatible). Free tier queues behind "
               "paid accounts, so a busy model returns 429 model_concurrency — "
               "switch model or retry. Configure TOLESH_DAHL_API_KEY."),
    )

    # Token Harbor: an OpenAI-compatible gateway that serves DeepSeek V4.1
    # Flash and MiMo V2.6 Flash alongside Claude/GPT/Gemini. Verified against
    # ``GET https://tokenharbor.ai/v1/models`` with a real key — 81 ids. Only
    # the ones this app routes to are declared; ``:free`` is a real suffix on
    # the gateway (same model, free-tier admission), not a label we invented.
    tokenharbor = keyed(
        "tokenharbor", "Token Harbor", "https://tokenharbor.ai/v1",
        (
            _m("tokenharbor", "deepseek-v4.1-flash", "DeepSeek V4.1 Flash",
               tasks=("chat", "code", "reasoning"), cost_tier="cheap",
               context=1048576, tools=True),
            _m("tokenharbor", "deepseek-v4.1-flash:free", "DeepSeek V4.1 Flash (free tier)",
               tasks=("chat", "code"), cost_tier="free", context=1048576),
            _m("tokenharbor", "mimo-v2.6-flash", "MiMo V2.6 Flash",
               tasks=("chat", "code"), cost_tier="cheap", context=1048576, tools=True),
            _m("tokenharbor", "mimo-v2.6-flash:free", "MiMo V2.6 Flash (free tier)",
               tasks=("chat", "code"), cost_tier="free", context=1048576),
            _m("tokenharbor", "glm-5.3-flash", "GLM-5.3 Flash",
               tasks=("chat", "code", "reasoning", "fast"), cost_tier="cheap",
               context=1000000, tools=True),
        ),
        notes=("Token Harbor gateway (OpenAI-compatible). Keys look like "
               "thk_live_…; configure TOLESH_TOKENHARBOR_API_KEY. A key with no "
               "plan answers 402 balance_zero on paid ids while the ':free' ids "
               "still work, which is why both are declared."),
    )

    # Xiaomi MiMo open platform. Both the OpenAI-compatible and Anthropic-shaped
    # endpoints exist; this uses the OpenAI one, like every other provider here.
    mimo = keyed(
        "mimo", "Xiaomi MiMo", "https://api.xiaomimimo.com/v1",
        (
            _m("mimo", "mimo-v2.6-flash", "MiMo V2.6 Flash",
               tasks=("chat", "code"), cost_tier="cheap", context=1048576, tools=True),
            _m("mimo", "mimo-v2.6-pro", "MiMo V2.6 Pro",
               tasks=("chat", "code", "reasoning"), cost_tier="standard",
               context=1048576, tools=True),
        ),
        notes=("Xiaomi MiMo open platform (OpenAI-compatible at /v1). Keys look "
               "like sk-…; configure TOLESH_MIMO_API_KEY. Thinking models return "
               "reasoning_content next to content."),
    )

    # MiniMax's own platform. It no longer serves the M2.7 id that Dahl does —
    # the current OpenAI-compatible models are M3.1-Flash and M3.
    minimax = keyed(
        "minimax", "MiniMax", "https://api.minimax.io/v1",
        (
            _m("minimax", "MiniMax-M3.1-Flash", "MiniMax M3.1 Flash",
               tasks=("chat", "code", "fast"), cost_tier="cheap",
               context=1000000, tools=True),
            _m("minimax", "MiniMax-M3", "MiniMax M3",
               tasks=("chat", "code", "reasoning"), cost_tier="standard",
               context=1000000, tools=True),
        ),
        notes=("MiniMax platform (OpenAI-compatible at /v1). Configure "
               "TOLESH_MINIMAX_API_KEY. MiniMax M2.7 is not on this endpoint — "
               "it is served by Dahl, which is why both are declared."),
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

    return {p.name: p for p in (offline, ollama, jev, gonka, dahl, tokenharbor,
                                mimo, minimax, openai, groq, deepseek, openrouter)}


class Registry:
    """Ordered, queryable catalogue of providers and models."""

    # Tasks whose chain follows the user's edited order. The short-latency
    # lanes (fast, voice_intent, classification) keep their own order because
    # a reorder meant for conversations must not silently change them.
    ORDERED_TASKS = ("chat", "reasoning", "code")

    def __init__(self, providers: dict[str, ProviderSpec] | None = None):
        self.providers: dict[str, ProviderSpec] = providers or {}
        self._models: dict[str, ModelSpec] = {}
        for p in self.providers.values():
            for m in p.models:
                self._models[m.id] = m
        self.default_model = os.environ.get("TOLESH_DEFAULT_MODEL", "")
        # User-edited fallback order for the conversational tasks. Empty means
        # "use the built-in order" — the code's opinion, not the user's.
        self.chat_order: list[str] = []

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
                    if patch.get("models"):
                        # Live model ids the user pulled from the endpoint.
                        discovered = _models_from_ids(name, patch["models"])
                        if discovered:
                            allowed["models"] = discovered
                    providers[name] = replace(cur, **allowed)
                default_model = str(raw.get("default_model") or "").strip()
        except Exception:
            # A malformed user config must never prevent Tolesh from starting.
            pass
        reg = cls(providers)
        if default_model and reg.model(default_model) is not None:
            reg.default_model = default_model
        # Per-model switches and the edited chain live in the same file. Read
        # them through the registry so a restart keeps what the user chose.
        try:
            if CONFIG_PATH.exists():
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                overrides = {str(mid): bool(flag)
                             for mid, flag in (raw.get("model_enabled") or {}).items()}
                if overrides:
                    for pname, p in list(reg.providers.items()):
                        if any(m.id in overrides for m in p.models):
                            reg.providers[pname] = replace(p, models=tuple(
                                replace(m, enabled=overrides[m.id]) if m.id in overrides else m
                                for m in p.models))
                    reg._models = {m.id: m for p in reg.providers.values() for m in p.models}
                order = [str(x) for x in (raw.get("chat_order") or []) if isinstance(x, str)]
                reg.chat_order = [x for x in order if reg._models.get(x) is not None]
        except Exception:
            pass
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
        models_replaced = False
        if patch.get("models"):
            discovered = _models_from_ids(name, patch["models"])
            if discovered:
                allowed["models"] = discovered
                models_replaced = True
        if "base_url" in allowed:
            base = str(allowed["base_url"]).strip()
            if base and not base.startswith(("http://", "https://")):
                raise ValueError("base_url must start with http:// or https://")
            allowed["base_url"] = base
        new = replace(cur, **allowed)
        self.providers[name] = new
        if models_replaced:
            # A refreshed model list replaces the old one; leaving stale ids in
            # the index would keep offering models the endpoint no longer serves.
            live = {m.id for m in new.models}
            for mid in [mid for mid, spec in self._models.items()
                        if spec.provider == name and mid not in live]:
                self._models.pop(mid, None)
            if self.default_model and self.model(self.default_model) is None:
                self.default_model = ""
        for m in new.models:
            self._models[m.id] = m
        if persist:
            to_persist: dict[str, Any] = dict(allowed)
            if "models" in to_persist:
                to_persist["models"] = [m.model for m in to_persist["models"]]
            self.persist_provider(name, to_persist)
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

    def set_model_enabled(self, model_id: str, enabled: bool, *,
                          persist: bool = False) -> ModelSpec:
        """Switch one model on or off without touching its provider.

        The spec is replaced in the provider tuple as well as in the index, so
        every reader (chain builder, /api/models, the picker) sees the same
        answer — there is no second source of truth to drift.
        """
        spec = self._models.get(model_id)
        if spec is None:
            raise KeyError(f"Unknown model: {model_id}")
        p = self.providers.get(spec.provider)
        if p is not None:
            self.providers[spec.provider] = replace(p, models=tuple(
                replace(m, enabled=enabled) if m.id == model_id else m for m in p.models))
        self._models[model_id] = replace(spec, enabled=enabled)
        if persist:
            raw = self._read_user_config()
            table = raw.setdefault("model_enabled", {})
            table[model_id] = bool(enabled)
            self._write_user_config(raw)
        return self._models[model_id]

    def set_chat_order(self, model_ids: list[str], *, persist: bool = False) -> list[str]:
        """Persist the user's fallback order for the conversational tasks.

        Unknown ids are rejected rather than silently dropped: a typo in the
        order would otherwise look like it saved and then do nothing.
        """
        cleaned: list[str] = []
        for mid in model_ids:
            mid = str(mid).strip()
            if not mid:
                continue
            if self._models.get(mid) is None:
                raise KeyError(f"Unknown model: {mid}")
            if mid not in cleaned:
                cleaned.append(mid)
        self.chat_order = cleaned
        if persist:
            raw = self._read_user_config()
            raw["chat_order"] = cleaned
            self._write_user_config(raw)
        return cleaned

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
        # A user-edited order wins over the built-in one for conversations, and
        # is then followed by the built-in order so newly added models still get
        # a turn instead of being stranded outside the chain.
        if self.chat_order and task in self.ORDERED_TASKS:
            order.extend(self.chat_order)
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
