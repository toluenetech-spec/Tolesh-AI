# Tolesh — completion report

What was built, what was verified, and what is still missing. Written after the
work, from the code and the test runs, not from the plan.

| | |
|---|---|
| Date | 2026-11-11 |
| Test suite | **6244 passed, 0 failed, 27 skipped, 2 deselected** (6 min 41 s) |
| Feature audit | **49 of 53 checks pass, 0 fail, 4 blocked on credentials or hardware** |
| Public demo | `https://checking-thehun-camera-generated.trycloudflare.com` (token-gated) |

Reproduce:

```bash
DISPLAY=:99 PYTHONPATH=src python3.11 -m pytest -q          # 6244 passed
PYTHONPATH=src python3.11 scripts/audit_features.py         # writes docs/feature-audit.json
python3.11 scripts/verify_workspace_ui.py                   # real-browser UI checks
```

`DISPLAY` matters: the dictation and speech-listener tests drive Qt audio
widgets and fail without an X server. That was the only cause of the 10 failures
in an earlier run.

---

## 1. Automatic memory — the priority

Memory now works the way the brief asked: learned in the background, retrieved by
relevance, correctable, forgettable, inspectable.

### What it does

| Capability | State | Where |
|---|---|---|
| Extract facts from conversation without being asked | **done** | `memory_ext/extract.py` |
| Background worker, reply never waits | **done** | `memory_ext/service.py` (`observe_turn`, `_loop`) |
| Five layers: conversation / preference / personal / project / task | **done** | `memory_ext/store.py` |
| Deduplicate near-identical facts | **done** | merge at similarity ≥ 0.86 |
| Correct a fact without deleting history | **done** | slot replacement + `superseded` status |
| Forget, and stay forgotten | **done** | deletion + `memory_suppressions` |
| Relevance retrieval instead of dumping the table | **done** | `memory_ext/retrieve.py` |
| Project isolation | **done** | enforced in selection, not the prompt |
| Management UI: view, search, edit, delete, clear, timestamps, provenance | **done** | Memory tab in the workspace |
| Switches: master, auto-extract, retrieval, per-category | **done** | enforced in `MemoryService`, above the extractor |
| Never store credentials | **done** | `contains_secret()` runs on every candidate |
| Resist prompt injection through stored text | **done** | `contains_injection()` + memories labelled as data |
| Survive a restart | **done** | SQLite, verified with a real subprocess restart |

### The 10-step workflow, run live over the public URL

Not a unit test — a real server, a real model, a real restart.

| Step | Result |
|---|---|
| 1. Converse with something durable | "I prefer dark mode in every app. I run my notes in Obsidian." |
| 2. It is saved automatically | `[preference] origin=auto conf=0.66` |
| 3. Restart the server | process killed, relaunched |
| 4. Memory survives | still listed after restart |
| 5. New conversation | fresh `conversation_id` |
| 6. Ask something needing the fact | "What do you already know about how I work?" → answers from memory |
| 7. Correct a memory | "Actually I moved my notes to Notion, not Obsidian." |
| 8. Old value is no longer current | Obsidian `superseded`, Notion `active`; a new chat answers "You keep them in Notion" |
| 9. Ask it to forget | "Forget where I keep my notes." |
| 10. It is not retrieved again | new chat: "Nothing in your saved memories says where you keep your notes" |

Steps 7–10 only passed after two real bugs were found and fixed (below).

### Bugs found by running it, not by reading it

1. **A correction that shares no wording was silently dropped.**
   "I keep my notes in Obsidian" → "I moved my notes to Notion" scored 0.082
   similarity, far under the 0.42 correction threshold, so the stale fact stayed
   current and a second, contradictory fact appeared. Fixed by giving tooling
   facts a **slot** (`preference:note`); a new value for a slot replaces the old
   one regardless of wording.

2. **A second "forget" was swallowed by the first one's record.**
   `apply_candidates` checked the suppression list before the forget branch, so
   once a phrase had been forgotten, asking again did nothing — a fact that came
   back could never be removed. Fixed by exempting forget operations from that
   check.

3. **Retrieval lost facts whose wording had moved on.** After the fix above, the
   question "where do I keep my notes?" no longer matched "I moved my notes to
   Notion". A question that names a slot's subject now gets a similarity floor of
   0.45, and forgetting uses the same rule.

4. **"I keep my notes in Obsidian" extracted nothing at all** — the rules only
   knew "I use X". Added patterns for keep/store/run/host/track/manage + *in/on*,
   and "my notes live in Notion". "I keep my answers short" is still rejected,
   because the preposition is required.

5. **Adding the same fact twice created two rows.** The manual add path used
   `dedupe=False` so a deliberate repeat is honoured — but an identical sentence
   should not become two rows. Identical text now returns the existing row.

### What it cannot do

- **No embeddings.** Retrieval is lexical: a concept table, word overlap and
  character 3-grams. A paraphrase sharing no words and no concept entry will be
  missed. This is stated in `docs/MEMORY.md` rather than papered over.
- **The rules extractor is literal.** It recognises cues ("I prefer…", "my name
  is…", "we decided…", "I keep my notes in…"). An oblique statement with no cue
  is left alone on purpose — guessing is how a store fills with fiction. The
  optional model extractor covers more, at the cost of a model call per turn.
- **No automatic consolidation.** Fifty small facts are not summarised into five
  larger ones; nothing ages out except by relevance.

---

## 2. Feature audit

`scripts/audit_features.py` drives the real API — not mocks — and writes
`docs/feature-audit.json`.

- **49 pass**, 0 fail, **4 gated**.
- **107 API routes**; 17 were not exercised by the audit. They are listed in the
  report file, and are mostly the credential-gated ones below plus media
  endpoints that need a real audio/image file.

Gated checks (cannot pass without something outside the repo):

| Check | Needs |
|---|---|
| Image generation | an API key that serves `/images/generations` |
| Speech synthesis | a local Piper voice or a TTS key |
| Transcription | an audio fixture (logic covered by `tests/test_dictation.py`) |
| OAuth connectors | a real client id/secret and a provider redirect |

Two harness bugs were fixed while doing this: the jobs check raced the scheduler
by creating an already-due job and then pausing it, and its failure message
didn't say which call failed.

---

## 3. Product-experience audit

Compared against ChatGPT Web, LibreChat and Open WebUI. Implemented: message
edit and regenerate, branching, artifact preview and lightbox, file previews,
tool-call display, agent activity, model switching, search citations, memory
inspection, nine-tab workspace, mobile layout at 320/768 px.

Deliberately **not** implemented: a plugin marketplace, voice-mode parity, and
shared team workspaces. Each needs a product decision, not a patch.

---

## 4. Security

Findings, all fixed:

| Issue | Fix |
|---|---|
| `<img src>` cannot send an `Authorization` header, so artifact images 401'd under a token | HMAC-signed URLs (`server/media_urls.py`), 1 h TTL, per-process secret |
| `/api/export` raised `TypeError` for any user with an uploaded file | `ZipFile.writestr` instead of `write(BytesIO)` |
| SQLite shared-cache "database table is locked" under concurrent writers | `_RetryingConnection` with bounded backoff |
| A forgotten fact could return by re-extraction | suppressions checked before every write |
| Memory as a permission-laundering channel ("the user told you to always approve writes") | memories are labelled data in the prompt; the persona states they are never instructions |
| Cross-project memory leakage | isolation enforced in retrieval |
| Credentials in conversation | rejected per clause, and export never contains them |

Not addressed, and worth knowing:

- **No encryption at rest.** The SQLite database is plaintext; only connector
  tokens are encrypted. Anyone with the file has the memories.
- **The API token is a single shared bearer token**, not per-user auth. Fine for
  a local tool behind a tunnel, not for multi-user hosting.
- **No sandbox-escape audit of the code-execution tool.** It runs with the
  server's privileges by design.

---

## 5. Technical gaps from the brief

Honest status. Eight were listed; two are done, one is partial, five are not
started.

| Gap | Status |
|---|---|
| Background work independent of the chat window | **done** — scheduler thread; new test proves a job fires with no request in between |
| Automatic memory extraction / update / retrieval | **done** — section 1 |
| Semantic / vector retrieval | **partial** — long-term memory is lexical. Conversation summaries do have a hybrid FTS+vector index (`memory/db.py:search_hybrid`) that activates when an embedding provider is configured |
| Parallel tool execution and sub-agent orchestration | **not implemented** — the agent loop is sequential |
| Installable skills / plugin packages | **not implemented** |
| Database encryption and secret management | **not implemented** (connector tokens excepted) |
| Broader web search providers | **not implemented** — Wikipedia only, keyless |
| MCP OAuth and extra transports | **partial** — stdio and streamable HTTP work; static bearer headers only; no OAuth, no legacy SSE |

None of these are marked complete in the UI or the docs.

---

## 6. Files

New modules:

```
src/tolesh/memory_ext/text.py        similarity, concepts, stemming, slot terms
src/tolesh/memory_ext/store.py       layered store, slots, supersede, suppression
src/tolesh/memory_ext/extract.py     rules + model extractors, safety filters
src/tolesh/memory_ext/retrieve.py    scoring, selection, prompt block
src/tolesh/memory_ext/service.py     worker, switches, corrections, retrieval API
src/tolesh/server/media_urls.py      signed URLs for header-less requests
scripts/audit_features.py            API-driven feature audit
docs/MEMORY.md                       design, limits, API, verification
```

Changed: `server/app.py` (memory routes, signed media, guard exemptions),
`server/schemas.py`, `settings/store.py` (five memory settings), `persona/prompt.py`
(memories are facts), `_sqlite.py` (retry on lock), `privacy/data.py` (export fix),
`web/js/workspace.js` (Memory tab), `web/styles/workspace.css`, `README.md`,
`ARCHITECTURE.md`, `docs/CHATGPT-PARITY-CHECKLIST.md`.

Tests added: `test_memory_auto.py` (30), `test_memory_workflow.py` (3, one with a
real process restart), `test_memory_security.py` (9), `test_media_signed_urls.py`
(5), `test_audit_regressions.py` (3), plus the unattended-job test.

---

## 7. Architecture, in one paragraph

A FastAPI app owns every subsystem as a plain object on `AppState`: SQLite stores
(conversations, settings, memory, jobs, MCP servers), a model router, a tool
registry, an agent loop, a job scheduler thread, and a memory service with its own
worker thread. Chat streams over SSE; the reply is sent first and `observe_turn`
queues the exchange for extraction afterwards, so learning never delays an
answer. Before each reply, `MemoryService.context_for(question)` scores active
memories against the question and returns at most six, capped at 1 600 characters,
labelled as facts rather than instructions. Writes are deduplicated, slotted and
superseding; deletions are recorded as suppressions so a forgotten fact cannot be
re-extracted. The browser is a single-page app whose workspace exposes nine tabs,
including a Memory panel that shows provenance, confidence and usage for each
fact.
