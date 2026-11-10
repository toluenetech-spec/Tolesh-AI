# Tolesh ↔ ChatGPT Web: capability inventory and build log

This file is the persistent checklist the brief asked for. It records what each
reference does, what Tolesh had before this work, what was actually built and
tested, and what is deliberately not built. If the work is resumed after a
context limit or a restart, this file is the entry point.

Legend: **✅** present and tested · **◐** partial, with the limit named ·
**✗** absent · **⛔** deliberately not built.

## How the inventory was built

| Source | What was actually read |
|---|---|
| `chatgpt.com` | The public, logged-out surface only. The signed-in sidebar, Projects, Memory and Connectors sit behind authentication, so they are described from their documented behaviour and from the open-source reimplementations — not from private UI. No attempt was made to bypass authentication. |
| LibreChat (`LibreChat-AI/LibreChat`) | Cloned and read: `packages/api/src/mcp/*` (transports, connection pooling, OAuth, per-tool approval, audit hooks), `packages/data-provider/src/mcp.ts` (the configuration shape the MCP store is modelled on), the agent controller, and the code-interpreter/file pipelines. |
| Open WebUI (`open-webui/open-webui`) | Cloned and read: `utils/tool_permissions.py`, `utils/tool_approval.py`, the router and model inventory (knowledge, tools, pipelines, tasks), and the RAG pipeline. |
| LobeChat (`lobehub/lobe-chat`) | Cloned and read: `packages/agent-runtime/src/loop` (the agent state machine), the tool-calling runtime, and the MCP marketplace/plugin layer. |
| Tolesh | `src/tolesh/**`, `web/**`, `tests/**` — the real source, read before any change. |

All three references are read for **architecture**, not copied. No code was
taken from them; each carries its own licence and Tolesh's own licence is
unchanged.

## Capability matrix

| Capability | ChatGPT | LibreChat | Open WebUI | LobeChat | Tolesh before | Tolesh now |
|---|---|---|---|---|---|---|
| Streaming chat | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Provider registry + fallback chain | ✗ (single vendor) | ✅ | ✅ | ✅ | ✅ | ✅ (user-editable order) |
| Conversation history list | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ |
| Search across conversations | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (titles + bodies, snippets) |
| Pin / archive / delete / rename | ✅ | ✅ | ✅ | ✅ | rename + delete | ✅ |
| Export a conversation | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (Markdown, JSON) |
| Projects / workspaces | ✅ | ◐ (agents) | ◐ (knowledge) | ✅ | ✗ | ✅ (folders + per-project instructions) |
| File upload + text extraction | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (pdf, docx, html, text/code, images) |
| Retrieval over uploaded files | ✅ | ✅ | ✅ (vector) | ✅ | ✗ | ◐ (lexical chunk ranking, no embeddings) |
| Web page reading | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (SSRF-guarded, untrusted-wrapped) |
| Web search | ✅ | ✅ | ✅ | ✅ | ✗ | ◐ (needs a key; Wikipedia works keyless) |
| Sandboxed code execution | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (subprocess, rlimits, network namespace) |
| **MCP servers** | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ stdio + streamable HTTP, tools/resources/prompts, per-tool permissions, approvals |
| **External connectors / OAuth** | ✅ | ✅ | ◐ | ✅ | ✗ | ✅ 6 providers, PKCE, encrypted vault, approval-gated writes |
| **Multi-step agents** | ✅ | ✅ | ◐ | ✅ | ✗ | ✅ bounded loop, 14 tools, approvals, cancel, history |
| **Image understanding** | ✅ | ✅ | ✅ | ✅ | ◐ (task hint) | ✅ (vision model, data-URL content part) |
| **Image generation** | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (OpenAI-compatible endpoint; 503 with a reason when unset) |
| **Voice in / out** | ✅ | ✅ | ✅ | ✅ | ✅ (browser + local TTS) | ✅ + server-side STT/TTS endpoints |
| **Data analysis → charts/reports** | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ profile, 5 chart types, HTML report, downloadable artifacts |
| **Background tasks** | ✅ | ◐ | ◐ | ◐ | ◐ (task centre) | ✅ once/interval, pause, cancel, progress, notifications |
| **Assistants (custom GPT-style)** | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ instructions + model + greeting + icon |
| **Prompt templates** | ✅ | ◐ | ✅ | ✅ | ✗ | ✅ |
| **Conversation branching** | ✅ | ◐ | ◐ | ◐ | ✗ | ✅ branch from any message |
| **Message edit / regenerate** | ✅ | ✅ | ✅ | ✅ | ◐ (regenerate only) | ✅ |
| **Sharing a conversation** | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ opt-in, token, TTL, revocable, `noindex` |
| **Settings surface** | ✅ | ✅ | ✅ | ✅ | ◐ (config file) | ✅ typed, validated, in-app |
| **Data export / deletion** | ✅ | ◐ | ◐ | ◐ | ✗ | ✅ zip export, exact-phrase deletion |
| **Audit log** | ◐ | ◐ | ◐ | ◐ | ✗ | ✅ gate + MCP + agents + jobs, one view |
| Multi-user / RBAC | ✅ | ✅ | ✅ | ✅ | ✗ | ⛔ by policy |

## What shipped in this round

### Phase 1 — chat interface and conversation management ✅

* `ConversationManager` gained `pinned`, `archived`, `project_id`, `get()`,
  `stats()`, `export()` and a `query` filter on `list()`.
* The schema change is **additive and applied in place**: an existing
  `tolesh.db` keeps every row (verified against the live database and by a test
  that builds an old-schema file first).
* API: `GET /api/conversations?q=&include_archived=&project_id=&unfiled=`,
  `GET /api/conversations/{id}`, `PATCH /api/conversations/{id}/flags`,
  `GET /api/conversations/{id}/export`.

### Phase 3 — projects ✅

* `ProjectStore`: name, description, instructions, conversation count.
* Deleting a project **keeps its conversations** and unfiles them — deleting a
  folder must not delete the work inside it.
* Project instructions are injected into the system prompt above retrieved
  material and below the safety rules, so a project cannot grant itself
  permissions the persona withholds.

### Phase 4 — files and retrieval ✅ / ◐

* `AttachmentStore`: bytes stored next to the database (mode 0600), text
  extracted, split into ~1100-character chunks with overlap, ranked lexically.
* Uploads are raw-body (`POST /api/attachments?filename=…`).
* Only matching chunks enter the prompt; a 300-page PDF does not fill the
  context window.
* ◐ Lexical ranking, not embeddings. Paraphrases that share no words with the
  document will not be found.

### Phase 5 — web ✅ / ◐

* `netsearch.fetch`: http/https only, DNS-checked against private/loopback/
  link-local/reserved addresses, 2 MB cap, 12 s timeout, text-only.
* Retrieved content is wrapped in `<web_page>` / `<web_search>`; the persona
  gained an explicit "untrusted content" section; the closing marker is stripped
  from the page body so a page cannot end the block early.
* ◐ Search needs a key: Tavily, Brave or your own SearXNG. Keyless DuckDuckGo,
  Mojeek and Brave all refused this host (202/403/429), so the only keyless
  backend shipped is Wikipedia.

### Phase 6 — MCP ✅

* `mcp_bridge/store.py`: the registry, modelled on LibreChat's configuration
  shape — name, transport, command/args/env or url/headers, `enabled`,
  `timeout_sec` (1–300), `default_policy`, per-tool `tool_permissions`. File is
  `~/.config/tolesh/mcp_servers.json`, mode 0600, written atomically, secrets
  masked on read and preserved when a masked preview is posted back.
* `mcp_bridge/client.py`: one background asyncio loop owns every session
  (`run_coroutine_threadsafe`), so a server that keeps state between calls keeps
  it. Per-server `asyncio.Lock` serialises calls; different servers run in
  parallel. One automatic reconnect on a dropped session.
* Transports: **stdio** and **streamable HTTP** (protocol `2025-06-18`,
  `mcp==1.13.1`). Discovery covers tools, resources and prompts; execution
  covers `tools/call`, `resources/read` and `prompts/get`.
* Permissions: `allow` runs, `ask` parks at the **shared** permission gate and
  resumes with `approval_id`, `deny` refuses before anything reaches the server.
* Errors are mapped to sentences a person can act on (command not found, process
  died, handshake timeout, HTTP status + body excerpt, tool raised).
* ⛔ Legacy HTTP+SSE transport, OAuth/DCR for MCP, and the `sampling`/`roots`/
  `elicitation` client features. Reasons in [MCP.md](MCP.md).

### Phase 7 — external connections ✅

* Six providers: GitHub, Google Drive, Gmail, Google Calendar, Notion, Slack —
  each with its auth style, scopes, endpoints, a `test_path` and a curated
  action list.
* Two ways in: a pasted personal access token, or OAuth 2.0 authorization code
  with **PKCE (S256)**. `state` rows are single-use with a 600 s TTL; the
  callback route is the only `/api` path exempt from the bearer guard, because a
  provider redirect cannot carry a header — the state value *is* the credential.
* Tokens are encrypted with Fernet; the key lives in a separate 0600 file. The
  API never returns a token, and the export redacts connections entirely.
* Refresh on expiry and on a 401/403; 429 is reported as a rate limit rather
  than a failure.
* SSRF guards: `base_url` and request paths are checked, so a connection cannot
  be pointed at `127.0.0.1` or a link-local address.
* Write actions (`create_issue`, `post_message`, …) raise `ConnectorError(kind=
  "approval")` unless `approved=True`; the route parks them at the gate first.
* ⛔ Webhooks and push subscriptions; Google service accounts; per-scope
  toggles in the UI (the requested scopes are fixed per provider).

### Phase 8 — multi-step agents ✅

* `agents/loop.py`: the model answers with either one fenced `action` block or a
  `final` block. One tool call per step, observation fed back wrapped in
  `<tool_result tool="…">` and labelled as data, loop bounded by
  `agent_max_steps` (settings, 1–40).
* `agents/tools.py`: `run_python`, `web_fetch`, `web_search`, `search_files`,
  `analyse_dataset`, `make_chart`, `make_report`, `save_text_artifact`,
  `list_artifacts`, `describe_image`, `generate_image`, plus every MCP tool and
  every connector action — 14 tools on a clean install, more as servers are
  added.
* Approvals: a tool with `risk="write"` parks the run. Approving resumes it, and
  the grant covers **only that exact call** (canonical JSON comparison), so a
  resumed run cannot quietly do something else.
* Cancellation is checked between steps; the run is marked `cancelled` and the
  partial transcript is kept.
* Every run is persisted (`agent_runs`) with its messages, steps and status;
  the UI streams `run/step/thought/tool_call/tool_result/approval_required/
  final/error/cancelled` over SSE.
* ◐ One call per step, single agent. No parallel tool calls and no sub-agents —
  both are runtime designs of their own, and a half-built one would be worse
  than none.

### Phase 9 — data analysis, charts, reports, artifacts ✅

* `artifacts/analysis.py`: CSV/TSV/JSON/XLSX profiling (types, missing values,
  min/mean/median/max, most common values, first rows), five chart types
  (bar/line/scatter/hist/pie) rendered with matplotlib `Agg`, and a standalone
  HTML report with every value escaped.
* `artifacts/store.py`: per-artifact directory 0700, file 0600, sanitised names,
  25 MB cap, `GET /api/artifacts/{id}/download`.
* The sandbox accepts seeded input files (`files=`) and collects named outputs
  (`collect=`), which is what makes "read this CSV, write me a cleaned copy"
  work end to end.
* ◐ No notebook-style REPL: code runs as one script per call, not as a live
  kernel with persistent variables.

### Phase 10 — images and voice ✅

* `media/images.py`: `describe_image` sends the image as a data-URL content part
  to a vision-capable model; `generate_image` calls an OpenAI-compatible
  `/images/generations` endpoint and accepts base64 or URL responses.
* `media/speech.py`: `transcribe` uses faster-whisper when installed;
  `synthesize` prefers a local Piper voice and falls back to `/audio/speech`.
* `GET /api/media/capabilities` reports what is actually available **with the
  reason when it is not** ("No local Piper voice and no TTS API key are
  configured"), and the endpoints return 503 with that sentence rather than
  failing silently.
* ◐ Real-time voice is still the browser's own STT/TTS loop; there is no
  streaming duplex audio session with the model.
* The persona is unchanged: same voice, same honesty about simulated emotion.

### Phase 11 — background tasks ✅

* `jobs/scheduler.py`: jobs are rows; the queue is a claim done as an atomic
  `UPDATE … WHERE status='scheduled'`, so two workers cannot take the same job.
  A worker pool executes them; `progress` is a separate column from `result`, so
  a progress update cannot overwrite the answer.
* Once or interval (`interval_sec`, `max_runs`), pause / resume / cancel / run
  now, run history per job, notifications table with read state.
* Interval jobs reschedule from their **finish** time, so a job that takes 10
  minutes every hour does not drift into back-to-back runs.
* State survives a restart (verified by reopening the store).
* ◐ The scheduler runs inside the app process: tasks run while Tolesh is open.
  Notifications are in-app only — no email, no OS notification.

### Phase 12 — settings and privacy ✅

* `settings/store.py`: typed defaults with validation; `telemetry` is forced
  `false` and the switch is disabled in the UI, because there is no telemetry to
  turn on and a dead switch is a lie.
* `privacy/audit.py` merges the permission gate, MCP calls, agent runs and job
  runs into one newest-first view with a summary.
* `privacy/data.py`: `export_all()` builds a zip with conversations and
  messages, memory, projects, settings, jobs, agent runs, artifacts and their
  files — and **no** API keys or connection tokens. `delete_all()` requires the
  exact phrase `DELETE ALL DATA`; provider keys are kept unless `include_keys`.
* ◐ The SQLite database itself is not encrypted at rest; only connection tokens
  are (Fernet). Stated plainly rather than implied otherwise.

### Extra features ✅

* **Assistants**: name, instructions, model, greeting, icon. Instructions are
  appended to the system prompt under the persona; the model applies only when
  the request carries none, so the picker is never overridden silently.
* **Prompt templates**: name, category, body; inserting one appends to the
  composer.
* **Message editing**: editing a user message truncates everything after it and
  regenerates; **branching** copies the conversation up to a message into a new
  one.
* **Sharing**: off by default (`share_links_enabled`), token URL, TTL, revoke,
  read-only HTML, `noindex`. The share route is exempt from the bearer guard
  because the token is the credential.

## Deliberately not built, and why

| Item | Reason |
|---|---|
| Multi-user / RBAC | Single-user product by design; the desktop shell and the SQLite file are per-user. |
| Vector embeddings | Adds a model dependency and an index-build step for a gain that is not yet measured. `AttachmentStore.retrieve` is already the shape a vector store returns. |
| Legacy MCP HTTP+SSE transport | Superseded by streamable HTTP; supporting both doubles the client surface for no user-visible gain. |
| MCP OAuth / dynamic client registration | The specification is still moving. A static bearer token works today and is documented. |
| Sub-agents and parallel tool calls | A different runtime, not a switch. The loop is honest about being sequential. |
| Cron expressions | `once` + `interval` covers the real use; a cron parser is a dependency and a source of silent mistakes. |

## Known limitations, stated plainly

| Area | Limit |
|---|---|
| Retrieval | Lexical, not semantic. |
| Web search | Keyless fallback is Wikipedia only. |
| Charts | Static PNG; no interactive chart artefact. |
| Image generation | Needs an OpenAI-compatible endpoint; unconfigured returns 503 with the reason. |
| Speech | Server-side STT needs `faster-whisper`; TTS needs Piper or an API key. No duplex streaming. |
| Jobs | Run only while the app is open; in-app notifications only. |
| MCP | No sampling/roots/elicitation; one call at a time per server. |
| Connectors | Curated action lists, not the whole provider API; no webhooks. |
| Sharing | Read-only page, no per-viewer identity, no comments. |
| Encryption | Connection tokens only; the database is not encrypted at rest. |

## Credentials still required

| Feature | Variable | State |
|---|---|---|
| Web search (recommended) | `TOLESH_TAVILY_API_KEY` | not set — Wikipedia fallback in use |
| Web search (alternative) | `TOLESH_BRAVE_API_KEY` | not set |
| Web search (self-hosted) | `TOLESH_SEARXNG_URL` (JSON output enabled) | not set |
| Image generation | `TOLESH_IMAGE_API_BASE` + `TOLESH_IMAGE_API_KEY` | not set |
| Speech to text | `pip install faster-whisper` (or `TOLESH_WHISPER_MODEL`) | not installed |
| Speech output | local Piper voice, or `TOLESH_TTS_API_BASE` + key | neither present |
| Connectors | per-provider OAuth app or a personal access token | none connected |
| Token Harbor paid tier | dashboard top-up | key present, `402 balance_zero` on paid ids |

Every one of these is reported by `GET /api/media/capabilities` or by the
connector catalogue rather than assumed present.

## Verification performed

| Check | Result |
|---|---|
| Full test suite (`DISPLAY=:99`) | **6192 passed, 27 skipped, 2 deselected, 0 failed** in 563 s |
| New tests this round | 131 across 10 files (MCP 25, connectors 23, agents 20, artifacts 15, media 18, jobs 15, privacy/extras 15) |
| Old-schema database migration | existing conversations preserved; every new column is additive |
| MCP end-to-end | real stdio fixture (tools/resources/prompts, tool-level error), real streamable-HTTP fixture with an `Authorization` header |
| Connectors end-to-end | fake GitHub: OAuth + PKCE, encrypted at rest, refresh on 401, SSRF refusal, write parked for approval |
| Agent end-to-end | real sandbox run returning `42`; observation wrapped; step budget enforced; cancel; approval → resume executes the exact call |
| Artifacts | real PNG (`\x89PNG`), real statistics, escaped report, download |
| Media | fake provider for image and audio; explicit 503 with a reason when unconfigured |
| Jobs | deterministic ticks, persistence across restart, notifications, progress separate from result |
| Privacy | zip export contents, exact-phrase deletion, sharing off by default, edit/branch/regenerate |
| Browser (Chromium, `scripts/verify_workspace_ui.py`) | **23/23 checks, 0 console errors**: 8 tabs, live MCP connect + tool list, live agent run, CSV upload → profile → chart, full-size chart viewer, task scheduling, assistant creation and selection, settings persistence, connector catalogue, no horizontal overflow at 320/375/768/1024/1440/1920 px, Escape and focus return |

Reproduce the browser run:

```bash
PYTHONPATH=src TOLESH_WEB_DIR=web \
  python3.11 -m uvicorn tolesh.server.app:app --host 127.0.0.1 --port 8787
python3.11 scripts/verify_workspace_ui.py --url http://127.0.0.1:8787
```

It writes `docs/workspace-verification.json` and 16 screenshots to
`docs/img/workspace/`.

## Resuming this work

1. `git log --oneline -12` — the last commit on this line of work is the one
   that added the workspace drawer.
2. Read the matrix above; every ✅ has a test, every ◐ has its limit written
   next to it.
3. Where things live:
   * `src/tolesh/mcp_bridge/`, `connectors/`, `agents/`, `artifacts/`, `media/`,
     `jobs/`, `settings/`, `privacy/`, `workspace/`
   * routes: `src/tolesh/server/app.py` (core) and
     `src/tolesh/server/routes_ext.py` (everything above)
   * UI: `web/js/workspace.js` + `web/styles/workspace.css`
4. `DISPLAY=:99 PYTHONPATH=src python3.11 -m pytest -q` before and after; the
   dictation tests need an X display (`Xvfb :99`).
