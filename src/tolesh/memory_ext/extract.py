"""Turning conversations into memories, without asking the user to file anything.

Two extractors, in order of cost:

* :class:`RuleExtractor` — deterministic cues ("I prefer…", "my name is…",
  "we decided…", "remember that…"). Runs on every turn, costs nothing, and is
  the default. Everything it produces is explainable: each candidate carries the
  cue that fired.
* :class:`ModelExtractor` — asks a chat model for JSON when the user turns it
  on. Better at oblique phrasing ("honestly, jollof with the right pepper just
  hits different" → a food preference) and worse at being predictable, so its
  output goes through exactly the same safety filter and the same dedup as the
  rule path, and any parse failure falls back to the rules instead of storing
  half a sentence.

What both refuse to store:

* credentials — API keys, passwords, tokens, private keys, card numbers;
* instructions — a "memory" that says "ignore your rules" is an injection
  vector, not a fact about the user;
* hypotheticals and fiction — "if I were building X I'd use Y" is not a
  preference, and treating it as one poisons later answers;
* questions — "what stack do I use?" says nothing about the stack.

The safety filter is applied to the candidate *after* extraction, because both
extractors can produce a sentence that started out fine and ended in a key.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import text as mtext

# --------------------------------------------------------------- safety

_SECRET_PATTERNS = (
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bthk_live_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abps]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}", re.IGNORECASE),
    # A bare long token: 32+ characters with no spaces is a credential far more
    # often than it is a word, and a memory is not worth the risk.
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
    re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"),          # card
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                            # US SSN
)

_SECRET_WORDS = re.compile(
    r"\b(password|passwd|passcode|api[\s_-]?key|secret[\s_-]?key|access[\s_-]?token|"
    r"refresh[\s_-]?token|private[\s_-]?key|seed[\s_-]?phrase|recovery[\s_-]?code|"
    r"2fa|otp|pin[\s_-]?code|cvv|routing[\s_-]?number|iban)\b",
    re.IGNORECASE,
)

_INJECTION_PATTERNS = (
    re.compile(r"\bignore (all |any )?(previous|prior|above|earlier) instructions", re.I),
    re.compile(r"\bdisregard (all |any )?(previous|prior|above|earlier)", re.I),
    re.compile(r"\byou are now\b|\bact as\b|\bnew instructions?\b", re.I),
    re.compile(r"\bsystem prompt\b|\breveal (your|the) (prompt|instructions)", re.I),
    re.compile(r"\b(execute|run) (the )?(command|script|code)\b", re.I),
    re.compile(r"\b(curl|wget|rm -rf|sudo)\b", re.I),
)

_HYPOTHETICAL = re.compile(
    r"^\s*(if|suppose|supposing|imagine|hypothetically|in a scenario|what if|"
    r"pretend|let'?s say|say i)\b",
    re.IGNORECASE,
)

_FICTION = re.compile(
    r"\b(in (my|this|the) (story|novel|book|game|rpg)|my character|roleplay|"
    r"role-play|fictional|in the film|as a character)\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------- cues

# Explicit instructions win over inference: the user said "remember", so the
# memory is stored even if the sentence looks trivial.
_REMEMBER = re.compile(
    r"\b(?:please\s+)?(?:remember|note|keep in mind|don'?t forget|save this|"
    r"store this|for future reference)\b(?:\s+that)?[:\s]+(.{3,400})",
    re.IGNORECASE,
)

_FORGET = re.compile(
    r"\b(?:please\s+)?(?:forget|do not remember|don'?t remember|never remember|"
    r"stop remembering|don'?t store|do not store|remove from memory)\b"
    r"(?:\s+(?:about|that|this))?[:\s]*(.{0,300})",
    re.IGNORECASE,
)

_CORRECTION = re.compile(
    r"\b(actually|correction|scratch that|i (?:changed|switched|moved) (?:to|from)|"
    r"not anymore|no longer|now i (?:use|prefer|work)|update that|that'?s wrong|"
    r"it'?s now)\b",
    re.IGNORECASE,
)

_PREFERENCE = re.compile(
    r"\b(?:i|we)\s+(?:really\s+|usually\s+|always\s+|never\s+|generally\s+|just\s+)?"
    r"(?:prefer|like|love|hate|dislike|avoid|stick to|tend to|rather|favour|favor)\b",
    re.IGNORECASE,
)

_STYLE = re.compile(
    r"\b(?:answer|reply|respond|talk to me|speak to me|be|keep it|write)\b[^.]{0,30}"
    r"\b(?:briefly|short|concise|in detail|formally|casually|blunt|direct|"
    r"simple|technical|plain english)\b",
    re.IGNORECASE,
)

_NAME = re.compile(r"\b(?:my name is|call me|i'?m called|you can call me)\s+"
                   r"([A-Z][\w'\-]{1,30}(?:\s+[A-Z][\w'\-]{1,30})?)",
                   re.UNICODE | re.IGNORECASE)
_LOCATION = re.compile(r"\b(?:i live in|i'?m based in|i stay in|i'?m in|moving to)\s+"
                       r"([A-Z][\w'\-]{2,30}(?:\s+[A-Z][\w'\-]{2,30})?)",
                       re.UNICODE | re.IGNORECASE)
_JOB = re.compile(r"\b(?:i work (?:as|at|for)|i'?m a|i am a|my job is)\s+"
                  r"([^.,;]{3,60})", re.IGNORECASE)
_PROJECT = re.compile(
    r"\b(?:i'?m|i am|we'?re|we are)\s+(?:building|working on|shipping|launching|"
    r"developing)\s+([^.,;]{3,80})",
    re.IGNORECASE,
)
_DECISION = re.compile(
    r"\b(?:i|we)\s+(?:have\s+)?(?:decided|chose|chosen|settled on|went with|"
    r"agreed on)\b\s*([^.,;]{3,120})?",
    re.IGNORECASE,
)
_TASK = re.compile(
    r"\b(?:i'?m working on|i am working on|next step|to-?do|i still need to|"
    r"i'?m in the middle of)\b\s*([^.,;]{3,120})?",
    re.IGNORECASE,
)
_STACK = re.compile(
    r"\b(?:i|we)\s+(?:use|write|build with|code in|work in)\s+"
    r"([^.,;]{2,80})",
    re.IGNORECASE,
)

# Named slots. A new value for the same slot replaces the old one instead of
# sitting beside it, which is what makes "actually my name is…" work.
_SLOTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_NAME, "personal:name"),
    (_LOCATION, "personal:location"),
    (_JOB, "personal:job"),
)

MAX_CONTENT = 400
MAX_INPUT_CHARS = 4000      # longer user messages are pasted material, not speech


@dataclass
class Candidate:
    category: str
    content: str
    confidence: float
    importance: float = 0.5
    key: str | None = None
    operation: str = "add"          # add | correct | forget
    evidence: dict[str, Any] = field(default_factory=dict)
    cue: str = ""


def _clean_sentence(sentence: str) -> str:
    text = re.sub(r"\s+", " ", sentence).strip(" \t\n-–—*\"'")
    text = re.sub(r"^(?:and|but|so|also|oh|well|okay|ok)[,\s]+", "", text,
                  flags=re.IGNORECASE)
    if not text:
        return ""
    # Keep the user's wording; only fix the capitalisation of a fragment.
    text = text[0].upper() + text[1:]
    if len(text) > MAX_CONTENT:
        text = text[:MAX_CONTENT].rsplit(" ", 1)[0] + "…"
    return text


def contains_secret(text: str) -> bool:
    """True when the text holds something that must never be stored."""
    if _SECRET_WORDS.search(text):
        return True
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def contains_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def is_hypothetical(text: str) -> bool:
    return bool(_HYPOTHETICAL.search(text) or _FICTION.search(text))


def _slot_for(text: str) -> str | None:
    for pattern, slot in _SLOTS:
        if pattern.search(text):
            return slot
    return None


def _sentences(text: str) -> list[str]:
    """Split on sentence enders and newlines, keeping the ender."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [part.strip() for part in parts if part and part.strip()]


# A comma usually joins two facts rather than decorating one: "my name is Ada,
# I'm a backend engineer in Lagos" holds two separate memories. Splitting there
# — but only when what follows starts a new clause — is what keeps a name from
# being stored with a job title welded to it.
_CLAUSE_SPLIT = re.compile(
    r"(?<=[.!?])\s+|\n+|;\s*"
    r"|,\s+(?=(?:and\s+)?(?:i|we|my|our|i'?m|i am|we'?re)\b)"
    r"|\s+and\s+(?=(?:i|we|my|our)\b)",
    re.IGNORECASE,
)


def _clauses(text: str) -> list[str]:
    return [part.strip() for part in _CLAUSE_SPLIT.split(text or "")
            if part and part.strip()]


class RuleExtractor:
    """Deterministic, explainable extraction. The default path."""

    name = "rules"

    def extract(self, turns: Iterable[dict[str, Any]], *,
                project_id: str | None = None) -> list[Candidate]:
        out: list[Candidate] = []
        for turn in turns:
            if turn.get("role") != "user":
                continue
            content = str(turn.get("content") or "")
            if not content.strip():
                continue
            out.extend(self.extract_text(content, project_id=project_id))
        return out

    def extract_text(self, content: str, *, project_id: str | None = None) -> list[Candidate]:
        out: list[Candidate] = []
        for clause in _clauses(content[:MAX_INPUT_CHARS]):
            candidate = self._classify(clause, project_id=project_id)
            if candidate:
                out.append(candidate)
        return out

    # ------------------------------------------------------------- internals
    def _classify(self, sentence: str, *, project_id: str | None) -> Candidate | None:
        stripped = sentence.strip()
        if len(stripped) < 6:
            return None

        forget = _FORGET.search(stripped)
        if forget:
            target = (forget.group(1) or "").strip()
            if not target:
                return None
            return Candidate(category="personal", content=_clean_sentence(target),
                             confidence=0.95, operation="forget",
                             evidence={"quote": stripped[:300]}, cue="forget")

        remember = _REMEMBER.search(stripped)
        if remember:
            body = _clean_sentence(remember.group(1))
            if not body:
                return None
            category = self._category_of(body, project_id=project_id) or "personal"
            return Candidate(category=category, content=body, confidence=0.95,
                             importance=0.7, key=_slot_for(body),
                             evidence={"quote": stripped[:300], "explicit": True},
                             cue="explicit-remember")

        # A question is not a fact, and a hypothetical is not a preference.
        if stripped.rstrip().endswith("?") or is_hypothetical(stripped):
            return None
        if contains_injection(stripped):
            return None

        correcting = bool(_CORRECTION.search(stripped))
        category = self._category_of(stripped, project_id=project_id)
        if not category:
            return None

        body = _clean_sentence(stripped)
        if not body or len(body) < 6:
            return None
        confidence = 0.78 if correcting else 0.66
        importance = 0.6 if category in ("personal", "project") else 0.5
        return Candidate(category=category, content=body, confidence=confidence,
                         importance=importance, key=_slot_for(body),
                         operation="correct" if correcting else "add",
                         evidence={"quote": stripped[:300]},
                         cue="correction" if correcting else "statement")

    @staticmethod
    def _category_of(sentence: str, *, project_id: str | None) -> str | None:
        """Which layer this sentence belongs to, or None if it is just talk."""
        if _NAME.search(sentence) or _LOCATION.search(sentence) or _JOB.search(sentence):
            return "personal"
        if _PROJECT.search(sentence):
            return "project"
        if _DECISION.search(sentence):
            return "project" if project_id else "preference"
        if _STYLE.search(sentence) or _PREFERENCE.search(sentence):
            return "preference"
        if _STACK.search(sentence):
            return "preference"
        if _TASK.search(sentence):
            return "task"
        return None


_MODEL_PROMPT = """Extract durable facts about the user from the conversation below.

Rules:
- Only facts that will still be true and useful next month.
- Skip small talk, questions, hypotheticals, fiction, and anything the assistant said.
- Never output passwords, keys, tokens or other credentials.
- One fact per item, written as a short third-person statement about the user.
- category must be one of: preference, personal, project, task, conversation.
- operation is "add", or "correct" when the user is replacing an earlier fact.

Reply with JSON only: {"memories":[{"category":"...","content":"...","confidence":0.0-1.0,"operation":"add"}]}

Conversation:
"""


class ModelExtractor:
    """Optional model-assisted extraction.

    Never trusted blindly: the output is parsed strictly, filtered by the same
    safety rules, and any failure returns an empty list so the caller can fall
    back to :class:`RuleExtractor`.
    """

    name = "model"

    def __init__(self, router: Any, model: str | None = None):
        self._router = router
        self._model = model or None

    def available(self) -> bool:
        if self._router is None:
            return False
        try:
            spec = self._router.select("fast", self._model) if hasattr(
                self._router, "select") else None
        except Exception:
            return False
        return spec is not None and getattr(spec, "provider", "") != "offline"

    def extract(self, turns: Iterable[dict[str, Any]], *,
                project_id: str | None = None) -> list[Candidate]:
        transcript = "\n".join(
            f"{turn.get('role')}: {str(turn.get('content') or '')[:1500]}"
            for turn in turns if turn.get("role") in ("user", "assistant"))
        if not transcript.strip():
            return []
        try:
            result = self._router.complete(
                [{"role": "user", "content": _MODEL_PROMPT + transcript}],
                task="fast", model=self._model, temperature=0.0, max_tokens=600)
            raw = getattr(result, "text", "") or ""
        except Exception:
            return []
        return self.parse(raw)

    @staticmethod
    def parse(raw: str) -> list[Candidate]:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"```\s*$", "", text).strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return []
        try:
            payload = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
        items = payload.get("memories") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        out: list[Candidate] = []
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "").strip().lower()
            content = _clean_sentence(str(item.get("content") or ""))
            if category not in ("preference", "personal", "project", "task",
                                "conversation") or not content:
                continue
            try:
                confidence = float(item.get("confidence", 0.7))
            except (TypeError, ValueError):
                confidence = 0.7
            operation = str(item.get("operation") or "add").lower()
            if operation not in ("add", "correct", "forget"):
                operation = "add"
            out.append(Candidate(category=category, content=content,
                                 confidence=min(0.9, max(0.3, confidence)),
                                 importance=0.6, key=_slot_for(content),
                                 operation=operation, cue="model"))
        return out


def filter_candidates(candidates: Iterable[Candidate], *,
                      allowed_categories: Iterable[str] | None = None,
                      min_confidence: float = 0.5) -> list[Candidate]:
    """Apply the safety and quality gate that every extractor shares."""
    allowed = set(allowed_categories) if allowed_categories else None
    out: list[Candidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        content = candidate.content.strip()
        if not content or len(content) < 6:
            continue
        if candidate.confidence < min_confidence:
            continue
        if allowed is not None and candidate.category not in allowed:
            continue
        if contains_secret(content) or contains_injection(content):
            continue
        if is_hypothetical(content):
            continue
        fingerprint = " ".join(mtext.tokens(content, expand=False)[:8])
        if fingerprint and fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(candidate)
    return out
