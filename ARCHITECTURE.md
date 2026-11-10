# Tolesh Architecture

## 1. Foundation selection (not a greenfield build)

Three repositories were cloned to `/workspace/_sources/` and inspected before
any code was written:

| Repository | Files | Size | Stack | Licence | Verdict |
| --- | --- | --- | --- | --- | --- |
| `isair/jarvis` | 669 | 23 MB | Python 3.11, PyQt6 desktop, SQLite memory graph, Whisper/faster-whisper, Ollama/OpenAI LLM, MCP tools, pytest suite (302 test files) | **Custom "Jarvis AI Assistant License" — non-commercial**, derivatives inherit the same terms, commercial use needs a separate licence | **Selected** |
| `jev-chat/jev-chat-jarvis` | 263 | 50 MB | Chat-centric, JEV provider | MIT (+ NOTICE) | Inspected, not selected |
| `FatihMakes/Mark-LV` | 78 | 2.9 MB | PyQt6, provider abstraction, layered memory, human-confirmation gate | **CC BY-NC 4.0 — no commercial use** | Inspected, concept only |

`isair/jarvis` won because it is the only candidate with a complete, tested
desktop voice pipeline, persistent conversation memory, a tool/MCP registry and
a real packaging story. **Important correction from the Priority-1 licensing
audit:** the selected foundation's licence is also **non-commercial** — it
prohibits commercialisation and explicitly requires derivative works to carry
the same terms. An earlier version of this document incorrectly described it as
"permissive, attribution". Mark-LV's CC BY-NC 4.0 licence is likewise
non-commercial, but its code was never copied; only the *concept* of a `CONFIRM`
token for destructive settings changes was carried over and re-implemented from
scratch in `permissions/policy.py` and `permissions/gate.py`. jev-chat-jarvis
(MIT) was inspected for provider ideas but its chat-centric scope was narrower
and Tolesh is not derived from it, so its permissive terms do not govern this
codebase. **Commercial distribution is therefore blocked until a separate
commercial licence is obtained from Baris Sencan; see
[LICENSING-ASSESSMENT.md](LICENSING-ASSESSMENT.md).**

### Transformation, not a rewrite

The selected tree was copied **verbatim** into `/workspace/tolesh` as an
independent git working copy on branch `master`, then:

- 479 files rebranded: package `jarvis` → `tolesh`, product name, UI strings,
  website, installer scripts, release config, spec files.
- Upstream README / AGENTS / EVALS archived unchanged under `docs/upstream/`.
- All three licences preserved under `LICENSES/`.
- Working features were **not removed**; the Qt desktop shell, listening,
  dictation, memory graph, tools and evals remain present and importable.

`git status` after transformation shows the original files as modified and the
new modules as untracked additions — the originals were never destructively
edited in the upstream clone (`_sources/` is untouched).

## 2. Module map

```
src/
├── desktop_app/          # INHERITED PyQt6 desktop shell (rebranded)
│   ├── app.py, chat_window.py, settings_window.py, setup_wizard.py …
│   └── desktop_assets/   # icons, fonts
├── tolesh/
│   ├── main.py, daemon.py, config.py, activity.py, tasks.py   # INHERITED core
│   ├── llm/             # INHERITED LLM backends (Ollama, OpenAI-compatible, tiers)
│   ├── listening/       # INHERITED wake word, VAD, echo detection, Whisper
│   ├── dictation/       # INHERITED dictation engine + history
│   ├── output/          # INHERITED TTS (Piper/Chatterbox) + tune player
│   ├── memory/          # INHERITED conversation memory graph (SQLite)
│   ├── reply/           # INHERITED reply engine, planner, enrichment
│   ├── tools/           # INHERITED tool registry, builtins, MCP client
│   ├── utils/           # INHERITED audio/vector/redaction helpers
│   │
│   ├── ai/              # NEW: provider-independent router
│   │   ├── registry.py          # ModelSpec / ProviderSpec / Registry
│   │   ├── router.py            # ModelRouter, RouterEvent, fallback + retry
│   │   ├── usage.py             # UsageLedger (SQLite)
│   │   └── providers/           # base.py, offline.py, openai_compatible.py
│   ├── conversation/    # NEW: ConversationManager (SQLite)
│   ├── memory_ext/      # NEW: LayeredMemory (5 categories, transparent)
│   ├── permissions/     # NEW: policy.py (risk) + gate.py (tokens + audit)
│   ├── persona/         # NEW: Tolesh system persona
│   ├── tools_ext/       # NEW: ComputerTools (permission-gated)
│   └── server/          # NEW: FastAPI app + SSE + schemas
└── web/                 # NEW: static client (design system, components, voice)
```

The split is deliberate: inherited modules are left intact; new capability is
added in parallel namespaces. Nothing in `ai/` hard-wires a single vendor.

## 3. Request / data flow

There is **one application**, not three. `python -m desktop_app` (what
`scripts/run_linux.sh` runs) owns the whole lifecycle:

```
desktop_app.app:main()
  ├── ToleshSystemTray()                       tray icon, log/face windows
  ├── start_ai_backend()                       tolesh.server.embedded
  │     └── uvicorn.Server  ── daemon thread, 127.0.0.1:<ephemeral>
  │           └── create_app(AppState(db_path=<shared tolesh.db>),
  │                          api_token=<per-run secret>)
  ├── start_daemon()                           inherited voice daemon
  ├── show_ai_window()                         desktop_app.ai_window
  │     └── QWebEngineView  →  http://127.0.0.1:<port>/?token=…
  └── app.exec()
        └── aboutToQuit → cleanup_on_exit() → stop_ai_backend()
```

The window is a browser view of the local API, so the same UI also works in a
real browser and, later, on Android — without a second backend. The engine is
stopped with the process: `EmbeddedServer.stop()` sets `should_exit`, joins the
thread and closes every SQLite handle.

Inside a request:

```
Browser / QtWebEngine / (future web, Android)
        │  HTTP + SSE  (/api/*)
        ▼
src/tolesh/server/app.py  ── token_guard (injected token or TOLESH_API_TOKEN)
        │
        ├── ConversationManager.window(cid, limit=20)   → recent turns
        ├── LayeredMemory.build_context()               → transparent facts
        ├── build_system_prompt(persona, task, model)   → system message
        ▼
ModelRouter.stream(messages, task, model)
        │  select() → chain() → per-provider attempt
        │  retry w/ exponential backoff on retryable errors
        │  fallback to next candidate, then offline engine
        ▼
OpenAICompatibleProvider.chat_stream()  │  OfflineProvider.chat_stream()
        │  SSE frames: meta / status / token / reset / usage / done / error
        ▼
UsageLedger.record()   +   ConversationManager.add_message()
```

`RouterEvent` is the single SSE contract consumed by every client. The frontend
`web/js/api.js` decodes `data:` frames and dispatches them in `web/js/app.js`.

### One database, three writers

`src/tolesh/_sqlite.py` resolves `$TOLESH_DATA_DIR/tolesh.db` for the desktop
app, the embedded server and the inherited daemon. All four stores
(`UsageLedger`, `LayeredMemory`, `ConversationManager`, `PermissionGate`) open
it through `connect()`, which sets `journal_mode=WAL` and `busy_timeout=5000`
so one writer and many readers can coexist **across processes** instead of
raising `database is locked`. Schema creation is `CREATE TABLE IF NOT EXISTS`,
so an older file is upgraded in place; table names are namespaced per store and
do not collide with the inherited `memory/` tables.

## 4. Provider independence

`Registry` builds an ordered catalogue of `ProviderSpec` and `ModelSpec`
entries. Keys are resolved lazily from the process environment; a provider
without a key is reported as `configured: False` and skipped — so a fresh
install runs with **zero credentials**. `TASK_MODEL_ORDER` maps each task to a
preference list, satisfying "different models for different tasks":

| Task | Preferred chain (first available wins) |
| --- | --- |
| `chat` | `gonka:GLM-4.6` → `openai:gpt-4o` → `jev:jev-chat` → `dahl:dahl-core` |
| `reasoning` | `deepseek:deepseek-reasoner` → `gonka:GLM-4.6` → `dahl:dahl-core` |
| `code` | `deepseek:deepseek-chat` → `qwen2.5:7b` → `openai:gpt-4o` |
| `fast` / `voice_intent` | `offline:tolesh-offline` → `dahl:dahl-fast` → `groq:…` |
| `classification` | `offline:tolesh-offline` → `dahl:dahl-fast` → `gpt-4o-mini` |

A user-selected model overrides the chain. `_RETRYABLE_STATUS` covers 408, 409,
425, 429, 5xx; auth/config errors fail fast. `OpenAICompatibleProvider` targets
any `POST /chat/completions` endpoint with SSE streaming, so adding a vendor is
a registry entry, not new code.

## 5. Memory model

Two separate SQLite stores, intentionally:

- `conversation/manager.py` — **current context**: conversations, messages, a
  bounded 20-message window, rename/delete.
- `memory_ext/store.py` — **long-term facts**: categories `conversation`,
  `preference`, `personal`, `project`, `task`; search, export, per-item delete,
  and `forget_everything` gated by an explicit typed phrase.
- `memory/` (inherited) — the upstream vector/graph memory remains available
  for the desktop pipeline.

`build_context()` injects only what is stored, and the UI shows every item so
the user can see and delete anything. Nothing is written silently.

## 6. Permission model

```
ComputerTools.invoke(tool, args)
   → policy.classify()  →  safe | elevated | consequential
   → safe: execute immediately + audit
   → elevated / consequential: PermissionGate.request() → PendingAction
       → UI shows tool + args + risk, user approves with token
       → /api/permissions/{id}/approve  →  gate.approve() → execute
       → every decision written to the audit log
```

`is_path_allowed()` refuses traversal outside the home directory or an
explicitly configured sandbox root. ALLOWED_APPS is a closed set of aliases
mapped to platform commands. Requests expire. `_NOT_IMPLEMENTED` in
`tools_ext/computer.py` lists actions that are **never** executed even after
approval (`delete_file`, `run_command`, `shutdown`, `restart`,
`send_message`, `network_request`).

## 7. Shared backend / platform roadmap

`src/tolesh/server/app.py` (`create_app()`) is the single contract for all
clients. Static `web/` is mounted at `/` when `serve_static=True`; the same
`/api/*` routes can be pointed at a Cloudflare Worker, a Neon-backed service,
or an Android client later.

Priority order, as specified:

1. **Desktop, Linux (Ubuntu/Zorin) and Windows 10/11** — the inherited PyQt6
   shell plus the new `web/` client served locally. This is the target now.
2. **Web** — Cloudflare Pages consuming the same `/api` contract.
3. **Android** — later.

Only one platform is being built at a time. The web client is served by the
local backend today, which is why it counts as part of the desktop delivery
rather than a separate web product.

### Planned ecosystem (not forced in)

The brief mentions Cloudflare Pages/Workers, Neon, Clerk, Resend and Cloudflare
R2. Inspection of all three repositories showed **none** of these services are
used upstream. Forcing a new stack would violate the "do not build a new
assistant from scratch / do not force a new stack" rule, so none were
introduced. What is in place instead:

- A provider-independent router that does not care where the endpoint lives.
- Explicit CORS allow-listing and an optional bearer token for a future remote
  deployment.
- Provider keys read from the environment only, never stored in SQLite, never
  returned by `Registry.redacted()`, never committed.
- SQLite stores that can be swapped for Neon Postgres behind the same manager
  interfaces.

## 8. Security posture

| Concern | Control |
| --- | --- |
| Provider secrets | Environment, or the per-user `providers.json` written at mode `0600`; `redacted()` responses; no key in any API payload |
| API access | Per-run bearer token injected by `EmbeddedServer` (or `TOLESH_API_TOKEN`) on all `/api` except health |
| Local-only binding | The embedded server binds `127.0.0.1` on an ephemeral port; nothing is exposed to the network |
| CORS | Explicit origin list, `allow_credentials=False`, restricted methods/headers |
| Consequential actions | Expiring approval token + human decision + audit log |
| File access | Home/sandbox-root boundary check, closed app alias set, traversal rejection |
| Frontend injection | All dynamic strings escaped (`esc()`); message bodies use `textContent` |
| Audio in tests | `conftest.py` blocks physical audio operations for the whole test session |

## 9. Outstanding architectural work

- Reconnect the inherited native wake-word / VAD / Piper TTS pipeline to the
  embedded server so desktop voice uses the Python engine rather than only the
  browser Web Speech API. The engine now runs in-process, so this is wiring,
  not new architecture.
- Background assistance foundation: configurable startup, notifications and a
  resource governor (explicitly no secret file/mic/camera access).
- Verify JEV / Gonka / Dahl live endpoints with real keys.
- Implement `delete_file` / `run_command` behind a stronger sandbox, or keep
  them permanently disabled and document the decision.
- Windows 10/11 launch verification and system-tray interaction on a real
  desktop session (Linux launch is verified under Xvfb + xcb).
