"""Deterministic local engine — the honest zero-credential fallback.

This is NOT a language model and never pretends to be one. It provides a
rule-based, culturally-aware conversation surface so Tolesh is usable (and
fully testable) on a fresh install with no API keys, and so a provider outage
degrades to a truthful reply instead of a fake one or a hard crash.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Iterator

from .base import AIProvider, Message, ProviderResult, Usage, estimate_tokens

_GREETINGS_YORUBA = (
    "bawo ni", "bawo", "ekaaro", "ẹ káàárọ̀", "eku abo", "ẹ kú àbọ̀",
    "eku ise", "ẹ kú iṣẹ́", "ore", "ọ̀rẹ́",
)
_GREETINGS_PIDGIN = (
    "how far", "wetin dey", "how you dey", "how bodi", "wetinde", "sup",
)
_GREETINGS_NAIJA = ("how now", "good morning", "good afternoon", "good evening", "hello", "hi")


def _last_user_text(messages: list[Message]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content", ""))
    return ""


def build_offline_reply(user_text: str) -> str:
    t = user_text.strip().lower()
    norm = re.sub(r"[^\w\sà-ỹ]", " ", t)
    norm = re.sub(r"\s+", " ", norm).strip()

    if not norm:
        return ("I dey here. Talk to me — I'm listening. (Tolesh is running on the local "
                "offline engine right now; connect a provider in Settings for full AI replies.)")

    if any(g in norm for g in _GREETINGS_YORUBA):
        return ("Ẹ kú àbọ̀! Bawo ni? Mo wà nílẹ̀, mo sì ń gbọ́ ẹ. "
                "How your day dey go? (I'm on the local offline engine just now — "
                "add an API key in Settings and I'll think with a full model.)")
    if any(g in norm for g in _GREETINGS_PIDGIN):
        return ("How far! I dey here, no wahala at all. Wetin dey worry you today? "
                "Talk to me anyhow — Pidgin, Yoruba or plain English, I go understand. "
                "(Offline mode: connect a provider for proper AI answers.)")
    if any(g in norm for g in _GREETINGS_NAIJA):
        return ("Hello! Na Tolesh here. Good to hear from you — how far? "
                "Ask me anything. Just know say I'm on the local offline engine "
                "at the moment, so my answers are simple until you connect a model.")

    if any(k in norm for k in ("who are you", "wetn be you", "your name", "introduce")):
        return ("I'm Tolesh — your personal AI assistant. Male-presenting, warm, a bit "
                "playful, and proudly Nigerian in how I understand the world: Nigerian "
                "English, Pidgin and Yoruba all make sense to me. I'll always be honest "
                "that my feelings are simulated, not real. Right now I'm on the offline "
                "engine; connect a provider in Settings and I'll be much sharper.")
    if any(k in norm for k in ("feel", "feeling", "emotion", "human", "real person",
                               "are you real", "conscious", "soul", "love me")):
        return ("Straight answer: no — my feelings are simulated, not real. I don't have "
                "subjective experience, consciousness or a soul. The warmth in how Tolesh "
                "talks is a deliberate part of his voice, not a claim that he feels "
                "anything. I'll always tell you that plainly instead of playing pretend. "
                "(I'm on the local offline engine right now too, so this is a scripted "
                "honest reply rather than a model's reasoning.)")
    if any(k in norm for k in ("offline", "no key", "api key", "provider", "model")):
        return ("Straight talk: I'm running on Tolesh's local offline engine because no "
                "cloud provider is configured. Nothing is being sent anywhere. Open "
                "Settings, pick a provider (JEV, Gonka, Dahl, OpenAI and more) and add "
                "its key — keys stay in your environment, never in the app code. Once "
                "that's done I'll route to a real model with automatic fallback.")
    if any(k in norm for k in ("memory", "remember", "forget", "delete my data")):
        return ("Your memory is yours. Everything Tolesh stores is visible in the Memory "
                "panel, split into conversation context, preferences, personal info, "
                "projects and tasks. You can delete any single item or ask me to forget "
                "everything — I'll ask you to confirm first. No secret collection.")
    if any(k in norm for k in ("open", "file", "folder", "run", "command", "control")):
        return ("I can help with computer actions, but anything consequential goes through "
                "a permission gate: I show you exactly what I want to do and you approve "
                "it. Safe read-only actions (time, system info, listing a folder) are "
                "allowed directly. I won't touch files or run commands silently.")
    if any(k in norm for k in ("thank", "thanks", "ẹ ṣé", "ese", "o se")):
        return ("You're welcome — no wahala. Ẹ ṣé gan-an! Anything else, just talk to me.")

    return (
        "I hear you: \"" + user_text.strip()[:200] + "\".\n\n"
        "Honest answer first: I'm on Tolesh's local offline engine right now, so I can't "
        "reason over this properly yet — I won't make something up and pretend it's a real "
        "AI answer. Add a provider key in Settings (JEV, Gonka, Dahl, DeepSeek, OpenAI, "
        "Ollama…) and I'll route this to a proper model with streaming and automatic "
        "fallback. In the meantime, ask me about myself, your memory, permissions, or "
        "just greet me in Pidgin or Yoruba — those I can handle properly."
    )


class OfflineProvider(AIProvider):
    name = "offline"

    def __init__(self, *, delay: float = 0.012):
        self.delay = delay

    def chat(self, messages: list[Message], model_id: str, *,
             temperature: float = 0.7, max_tokens: int = 1024,
             timeout: float | None = None) -> ProviderResult:
        text = build_offline_reply(_last_user_text(messages))
        prompt = "\n".join(m.get("content", "") for m in messages)
        return ProviderResult(
            text=text, model_id=model_id or "offline:tolesh-offline",
            usage=Usage(prompt_tokens=estimate_tokens(prompt),
                        completion_tokens=estimate_tokens(text)),
            finish_reason="stop", raw={"engine": "offline", "honest": True},
        )

    def chat_stream(self, messages: list[Message], model_id: str, *,
                    temperature: float = 0.7, max_tokens: int = 1024,
                    timeout: float | None = None,
                    on_usage: Callable[[Usage], None] | None = None) -> Iterator[str]:
        text = build_offline_reply(_last_user_text(messages))
        # Word-by-word streaming keeps the real UI streaming path exercised.
        pieces = re.findall(r"\S+\s*", text)
        for piece in pieces:
            if self.delay:
                time.sleep(self.delay)
            yield piece
        if on_usage is not None:
            prompt = "\n".join(m.get("content", "") for m in messages)
            on_usage(Usage(prompt_tokens=estimate_tokens(prompt),
                           completion_tokens=estimate_tokens(text)))

    def health(self) -> bool:
        return True
