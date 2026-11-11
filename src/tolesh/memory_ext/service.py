"""The memory service: settings, background extraction, retrieval, audit.

This is what the rest of Tolesh talks to. It owns four decisions that must not
be scattered around the codebase:

1. **When to extract.** Never inline in a chat request. A reply must not wait on
   a second model call, so extraction is queued and a worker thread drains it.
   ``process_now()`` exists for tests and for the "Learn from this conversation"
   button, which is an explicit request to wait.
2. **What may be stored.** The user's switches (master, per-category,
   auto-extract) are enforced here, above the extractor, so no future extractor
   can bypass them by forgetting to check.
3. **What goes into a prompt.** A relevance-ranked, budgeted block — not the
   whole store. Retrieval can be switched off entirely, and it is off for the
   voice daemon's terse intents by construction (they never ask for context).
4. **What forgetting means.** Deleting a row *and* recording a suppression, so
   the same fact is not re-extracted from the next conversation. Without the
   second half, "forget that" is a promise Tolesh breaks on the next turn.

The worker is a daemon thread with a queue and a short debounce. It is started
lazily on the first observed turn, so constructing a service (or an AppState in
a test) does not spawn threads that nothing will use.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Iterable

from . import retrieve as mretrieve
from . import text as mtext
from .extract import (Candidate, ModelExtractor, RuleExtractor, filter_candidates)
from .store import CATEGORIES, LayeredMemory

# Categories automatic extraction may write. ``conversation`` is deliberately
# excluded: it is the temporary layer, and the whole point of the layering is
# that a passing remark does not become a permanent record.
AUTO_CATEGORIES = ("preference", "personal", "project", "task")

SETTING_KEYS = (
    "memory_enabled", "memory_auto_extract", "memory_retrieval",
    "memory_categories", "memory_extraction_mode", "memory_extraction_model",
    "memory_retrieval_limit",
)

SETTING_DESCRIPTIONS = {
    "memory_enabled": "Keep any long-term memory at all. Off means nothing is stored or recalled.",
    "memory_auto_extract": "Learn from conversations in the background without being asked.",
    "memory_retrieval": "Put relevant memories into the prompt when answering.",
    "memory_categories": "Which kinds of memory may be stored automatically.",
    "memory_extraction_mode": "'rules' is deterministic and free; 'model' asks a model to read the conversation.",
    "memory_extraction_model": "Model used when extraction mode is 'model'. Empty means the router picks.",
    "memory_retrieval_limit": "How many memories one answer may see (1-12).",
}


class MemoryService:
    def __init__(self, store: LayeredMemory, *, settings: Any = None,
                 router: Any = None, conversations: Any = None,
                 debounce_sec: float = 1.5):
        self.store = store
        self._settings = settings
        self._router = router
        self._conversations = conversations
        self._debounce = debounce_sec
        self._rules = RuleExtractor()
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_run: dict[str, Any] = {"ts": None, "status": "idle"}
        self._counters = {"created": 0, "merged": 0, "corrected": 0,
                          "forgotten": 0, "rejected": 0, "runs": 0}

    # ------------------------------------------------------------ settings
    def _setting(self, key: str, default: Any = None) -> Any:
        if self._settings is None:
            return default
        try:
            return self._settings.get(key)
        except Exception:
            return default

    def settings_view(self) -> dict[str, Any]:
        values = {key: self._setting(key) for key in SETTING_KEYS}
        values["memory_categories"] = list(values.get("memory_categories") or AUTO_CATEGORIES)
        return {"values": values, "descriptions": SETTING_DESCRIPTIONS,
                "auto_categories": list(AUTO_CATEGORIES),
                "all_categories": list(CATEGORIES)}

    def patch_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        if self._settings is None:
            raise ValueError("memory settings are not available")
        unknown = set(changes) - set(SETTING_KEYS)
        if unknown:
            raise ValueError(f"unknown memory setting: {sorted(unknown)[0]}")
        cleaned: dict[str, Any] = {}
        for key, value in changes.items():
            if key == "memory_categories":
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise ValueError("memory_categories must be a list of category names")
                bad = [v for v in value if v not in CATEGORIES]
                if bad:
                    raise ValueError(f"unknown category: {bad[0]}")
                cleaned[key] = [v for v in value if v in AUTO_CATEGORIES]
            elif key == "memory_extraction_mode":
                if value not in ("rules", "model"):
                    raise ValueError("memory_extraction_mode must be 'rules' or 'model'")
                cleaned[key] = value
            elif key == "memory_extraction_model":
                if not isinstance(value, str):
                    raise ValueError("memory_extraction_model must be text")
                cleaned[key] = value.strip()[:200]
            elif key == "memory_retrieval_limit":
                if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 12:
                    raise ValueError("memory_retrieval_limit must be between 1 and 12")
                cleaned[key] = value
            else:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
                cleaned[key] = value
        self._settings.patch(cleaned)
        return self.settings_view()

    @property
    def enabled(self) -> bool:
        return bool(self._setting("memory_enabled", True))

    @property
    def auto_extract(self) -> bool:
        return self.enabled and bool(self._setting("memory_auto_extract", True))

    @property
    def retrieval_enabled(self) -> bool:
        return self.enabled and bool(self._setting("memory_retrieval", True))

    def allowed_categories(self) -> list[str]:
        configured = self._setting("memory_categories")
        if not configured:
            return list(AUTO_CATEGORIES)
        return [c for c in configured if c in AUTO_CATEGORIES]

    # -------------------------------------------------------------- worker
    def observe_turn(self, *, conversation_id: str, project_id: str | None,
                     turns: Iterable[dict[str, Any]]) -> bool:
        """Queue a conversation for background learning. Never raises, never blocks.

        Called from the request path, so a failure here must not reach the user:
        a memory problem is not a chat problem.
        """
        if not self.auto_extract:
            return False
        snapshot = [{"role": t.get("role"), "content": str(t.get("content") or "")[:4000]}
                    for t in turns][-8:]
        if not any(t["role"] == "user" and t["content"].strip() for t in snapshot):
            return False
        self._ensure_thread()
        self._queue.put({"conversation_id": conversation_id,
                         "project_id": project_id, "turns": snapshot,
                         "queued_at": time.time()})
        return True

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="tolesh-memory",
                                            daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            # Debounce: a burst of turns (a fast exchange) is one extraction,
            # not one per message.
            deadline = time.time() + self._debounce
            merged_jobs = [job]
            while time.time() < deadline:
                try:
                    extra = self._queue.get(timeout=max(0.01, deadline - time.time()))
                except queue.Empty:
                    break
                if extra is None:
                    return
                if extra["conversation_id"] == job["conversation_id"]:
                    merged_jobs[0] = extra
                else:
                    merged_jobs.append(extra)
            for item in merged_jobs:
                try:
                    self.apply_candidates(
                        self.extract_turns(item["turns"]),
                        conversation_id=item["conversation_id"],
                        project_id=item["project_id"],
                    )
                except Exception as exc:  # a bad turn must not kill the worker
                    self._last_run = {"ts": time.time(), "status": "error",
                                      "error": str(exc)[:300]}

    def drain(self, timeout: float = 10.0) -> bool:
        """Wait until the queue is empty and no job is being processed."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.empty():
                return True
            time.sleep(0.05)
        return self._queue.empty()

    # ---------------------------------------------------------- extraction
    def _extractor(self):
        mode = self._setting("memory_extraction_mode", "rules")
        if mode == "model" and self._router is not None:
            extractor = ModelExtractor(self._router,
                                       self._setting("memory_extraction_model") or None)
            if extractor.available():
                return extractor
        return self._rules

    def extract_turns(self, turns: Iterable[dict[str, Any]], *,
                      project_id: str | None = None) -> list[Candidate]:
        extractor = self._extractor()
        candidates = extractor.extract(turns, project_id=project_id)
        if extractor is not self._rules and not candidates:
            # The model found nothing or failed to parse: the rules still get a
            # chance, because a silent empty result is indistinguishable from a
            # broken response.
            candidates = self._rules.extract(turns, project_id=project_id)
        return filter_candidates(candidates,
                                 allowed_categories=self.allowed_categories())

    def extract_conversation(self, conversation_id: str, *,
                             project_id: str | None = None) -> dict[str, Any]:
        """Extract from a stored conversation, synchronously."""
        if self._conversations is None:
            raise ValueError("no conversation store is attached")
        messages = self._conversations.messages(conversation_id)
        if not messages:
            return {"status": "empty", "created": [], "merged": [], "corrected": [],
                    "forgotten": [], "rejected": []}
        project = project_id
        if project is None:
            meta = self._conversations.get(conversation_id) or {}
            project = meta.get("project_id")
        return self.apply_candidates(self.extract_turns(messages, project_id=project),
                                     conversation_id=conversation_id,
                                     project_id=project)

    def apply_candidates(self, candidates: list[Candidate], *,
                         conversation_id: str | None = None,
                         project_id: str | None = None) -> dict[str, Any]:
        """Write the survivors to the store, merging and superseding as needed."""
        result: dict[str, Any] = {"created": [], "merged": [], "corrected": [],
                                  "forgotten": [], "rejected": []}
        for candidate in candidates:
            # A forget must run even when its own phrase is already suppressed:
            # otherwise a fact that crept back in could never be removed a
            # second time, because the first "forget" would block the retry.
            if (candidate.operation != "forget"
                    and self.store.is_suppressed(candidate.content)):
                result["rejected"].append({"content": candidate.content,
                                           "reason": "forgotten by the user"})
                continue
            if candidate.operation == "forget":
                outcome = self.store.forget_matching(candidate.content)
                if outcome["deleted"]:
                    result["forgotten"].extend(outcome["deleted"])
                continue
            try:
                item = self._write(candidate, conversation_id=conversation_id,
                                   project_id=project_id)
            except ValueError as exc:
                result["rejected"].append({"content": candidate.content,
                                           "reason": str(exc)})
                continue
            bucket = {"add": "created", "merge": "merged", "correct": "corrected"}[item["how"]]
            result[bucket].append({"id": item["memory"]["id"],
                                   "content": item["memory"]["content"],
                                   "category": item["memory"]["category"]})
            if item.get("replaced"):
                result["corrected"].append({"id": item["replaced"],
                                            "superseded_by": item["memory"]["id"]})
        for key in ("created", "merged", "corrected", "forgotten", "rejected"):
            self._counters[key] += len(result[key])
        self._counters["runs"] += 1
        self._last_run = {"ts": time.time(), "status": "ok",
                          "created": len(result["created"]),
                          "merged": len(result["merged"]),
                          "corrected": len(result["corrected"]),
                          "forgotten": len(result["forgotten"]),
                          "rejected": len(result["rejected"])}
        return result

    def _write(self, candidate: Candidate, *, conversation_id: str | None,
               project_id: str | None) -> dict[str, Any]:
        evidence = dict(candidate.evidence or {})
        evidence.setdefault("cue", candidate.cue)
        evidence.setdefault("extractor", "rules" if candidate.cue != "model" else "model")

        # A named slot ("my name is …") is the same fact by definition, so a new
        # value replaces the old one even when the wording differs a lot.
        if candidate.key:
            for row in self.store.list(limit=10000, category=candidate.category):
                if row.get("key") == candidate.key:
                    if mtext.similarity(row["content"], candidate.content) > 0.95:
                        merged = self.store.add(
                            candidate.category, candidate.content,
                            source="auto", confidence=candidate.confidence,
                            importance=candidate.importance, project_id=project_id,
                            conversation_id=conversation_id, origin="auto",
                            key=candidate.key, evidence=evidence)
                        return {"how": "merge", "memory": merged}
                    fresh = self.store.add(
                        candidate.category, candidate.content, source="auto",
                        confidence=candidate.confidence,
                        importance=candidate.importance, project_id=project_id,
                        conversation_id=conversation_id, origin="correction",
                        key=candidate.key, evidence=evidence, dedupe=False)
                    self.store.supersede(row["id"], fresh["id"])
                    return {"how": "correct", "memory": fresh, "replaced": row["id"]}

        if candidate.operation == "correct":
            target = self.store.find_correction_target(
                candidate.content, category=candidate.category, project_id=project_id)
            if target is not None:
                fresh = self.store.add(
                    candidate.category, candidate.content, source="auto",
                    confidence=candidate.confidence, importance=candidate.importance,
                    project_id=project_id, conversation_id=conversation_id,
                    origin="correction", key=candidate.key, evidence=evidence,
                    dedupe=False)
                self.store.supersede(target["id"], fresh["id"])
                return {"how": "correct", "memory": fresh, "replaced": target["id"]}

        before = self.store.stats()["total"]
        stored = self.store.add(
            candidate.category, candidate.content, source="auto",
            confidence=candidate.confidence, importance=candidate.importance,
            project_id=project_id, conversation_id=conversation_id, origin="auto",
            key=candidate.key, evidence=evidence)
        how = "merge" if self.store.stats()["total"] == before else "add"
        return {"how": how, "memory": stored}

    # ----------------------------------------------------------- retrieval
    def retrieve(self, query: str, *, project_id: str | None = None,
                 limit: int | None = None) -> list[dict[str, Any]]:
        if not self.retrieval_enabled or not query.strip():
            return []
        cap = limit or int(self._setting("memory_retrieval_limit", 6) or 6)
        items = self.store.active(project_id=None)
        picked = mretrieve.select(items, query, now=time.time(),
                                  project_id=project_id, limit=cap)
        if picked:
            self.store.mark_used([item["id"] for item in picked])
        return picked

    def context_for(self, query: str, *, project_id: str | None = None) -> str:
        """The memory block for this prompt, or "" when there is nothing to say."""
        picked = self.retrieve(query, project_id=project_id)
        return mretrieve.format_block(picked)

    def memory_note(self, query: str, *, project_id: str | None = None) -> str:
        """What to tell the model when retrieval found nothing.

        "Nothing is stored" and "nothing stored is relevant" are different
        facts, and telling a model the first when the second is true is how it
        ends up claiming amnesia about something the user can see in the panel.
        """
        if not self.retrieval_enabled:
            return "Memory recall is switched off for this turn. Do not claim to remember anything."
        total = self.store.stats()["total"]
        if total == 0:
            return ""
        return (f"The user has {total} saved memories, but none of them bear on this "
                "message. Do not invent one; if they ask what you remember, say that "
                "nothing relevant is stored for this question.")

    # --------------------------------------------------------------- views
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "auto_extract": self.auto_extract,
            "retrieval": self.retrieval_enabled,
            "mode": self._setting("memory_extraction_mode", "rules"),
            "pending": self._queue.qsize(),
            "worker": bool(self._thread and self._thread.is_alive()),
            "last_run": self._last_run,
            "counters": dict(self._counters),
        }

    def close(self) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread and thread.is_alive():
            self._queue.put(None)
            thread.join(timeout=2.0)
