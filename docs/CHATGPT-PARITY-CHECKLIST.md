# Tolesh ↔ ChatGPT Web: capability inventory and build log

This file is the persistent checklist the brief asked for. It records what each
reference does, what Tolesh had before this work, what was actually built and
tested, and what is deliberately not built. If the work is resumed after a
context limit or a restart, this file is the entry point.

## How the inventory was built

| Source | What was actually read |
|---|---|
| `chatgpt.com` | The public, logged-out surface only. The signed-in sidebar, Projects, Memory and Connectors sit behind authentication, so they are described from their documented behaviour and from the open-source reimplementations — not from private UI. No attempt was made to bypass authentication. |
| LibreChat | `README.md` at `LibreChat-AI/LibreChat` (features, Agents, Skills, MCP, code interpreter, pinned chats, attachments, context usage). |
| Open WebUI | `README.md` at `open-webui/open-webui` (local-first, RAG/KB, tools, terminals, computer, knowledge sync). |
| LobeChat | `README.md` at `lobehub/lobe-chat` (agents, MCP marketplace, skills, multi-agent). |
| Tolesh | `src/tolesh/**`, `web/**`, `tests/**` — the real source, read before any change. |

All three references are read for **architecture**, not copied. No code was
taken from them; each carries its own licence and Tolesh's own licence is
unchanged.

## Capability matrix

Legend: **✅** present and tested · **◐** partial · **✗** absent · **⛔** deliberately not built.

| Capability | ChatGPT | LibreChat | Open WebUI | LobeChat | Tolesh before | Tolesh now |
|---|---|---|---|---|---|---|
| Streaming chat | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Provider registry + fallback chain | ✗ (single vendor) | ✅ | ✅ | ✅ | ✅ | ✅ (user-editable order) |
| Conversation history list | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ |
| Search across conversations | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (titles + message bodies, snippets) |
| Pin / archive / delete / rename | ✅ | ✅ | ✅ | ✅ | rename + delete only | ✅ |
| Export a conversation | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (Markdown, JSON) |
| Projects / workspaces | ✅ | ◐ (agents) | ◐ (knowledge) | ✅ | ✗ | ✅ (folders + per-project instructions) |
| File upload + text extraction | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (pdf, docx, html, text/code) |
| Retrieval over uploaded files | ✅ | ✅ | ✅ (vector) | ✅ | ✗ | ◐ (lexical chunk ranking, no embeddings) |
| Web page reading | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (SSRF-guarded, untrusted-wrapped) |
| Web search | ✅ | ✅ | ✅ | ✅ | ✗ | ◐ (needs a key; Wikipedia works keyless) |
| Sandboxed code execution | ✅ | ✅ | ✅ | ✅ | ✗ | ✅ (subprocess, rlimits, network namespace) |
| MCP servers | ✅ | ✅ | ✅ | ✅ | ✗ | ⛔ this round |
| External connectors / OAuth | ✅ | ✅ | ◐ | ✅ | ✗ | ⛔ this round |
| Multi-step agents / subagents | ✅ | ✅ | ◐ | ✅ | ✗ | ⛔ this round |
| Image understanding / generation | ✅ | ✅ | ✅ | ✅ | ◐ (vision task hint) | ⛔ this round |
| Voice in / out | ✅ | ✅ | ✅ | ✅ | ✅ (browser + local TTS) | ✅ (unchanged) |
| Memory | ✅ | ◐ | ◐ | ◐ | ✅ (transparent, deletable) | ✅ (unchanged) |
| Background tasks | ✅ | ◐ | ◐ | ◐ | ◐ (task centre) | ⛔ this round |
| Sharing a conversation | ✅ | ✅ | ✅ | ✅ | ✗ | ⛔ by policy (see below) |
| Multi-user / RBAC | ✅ | ✅ | ✅ | ✅ | ✗ | ⛔ by policy |

## What shipped in this round

### Phase 1 — chat interface and conversation management ✅

* `ConversationManager` gained `pinned`, `archived`, `project_id`, `get()`,
  `stats()`, `export()` and a `query` filter on `list()`.
* The schema change is **additive and applied in place**: an existing
  `tolesh.db` keeps every row (verified against the live 52-conversation
  database and by a test that builds an old-schema file first).
* API: `GET /api/conversations?q=&include_archived=&project_id=&unfiled=`,
  `GET /api/conversations/{id}`, `PATCH /api/conversations/{id}/flags`,
  `GET /api/conversations/{id}/export`.
* UI: a Conversations section in the context panel with search, project chips,
  per-row pin / archive / export / delete, plus the same actions in the topbar.

### Phase 3 — projects ✅

* `ProjectStore`: name, description, instructions, conversation count.
* Deleting a project **keeps its conversations** and unfiles them — deleting a
  folder must not delete the work inside it.
* Project instructions are injected into the system prompt above retrieved
  material and below the safety rules, so a project cannot grant itself
  permissions the persona withholds.

### Phase 4 — files and retrieval ✅ / ◐

* `AttachmentStore`: bytes stored next to the database (mode 0600), text
  extracted, split into ~1100-character chunks with overlap, ranked lexically
  against the question.
* Uploads are raw-body (`POST /api/attachments?filename=…`) so no multipart
  parser is needed and the browser sends the `File` object unchanged.
* Only matching chunks enter the prompt; a 300-page PDF does not fill the
  context window.
* ◐ Lexical ranking, not embeddings. Honest limitation: paraphrases that share
  no words with the document will not be found.

### Phase 5 — web ✅ / ◐

* `netsearch.fetch`: http/https only, DNS-checked against private/loopback/
  link-local/reserved addresses, 2 MB cap, 12 s timeout, article-body
  preference, text-only.
* Retrieved content is wrapped in `<web_page>` / `<web_search>` and the persona
  gained an explicit "untrusted content" section; the closing marker is stripped
  from the page body so a page cannot end the block early.
* ◐ Search needs a key: Tavily, Brave or your own SearXNG. Keyless DuckDuckGo,
  Mojeek and Brave all refused this host (202/403/429), so the only keyless
  backend shipped is Wikipedia — real results, encyclopedic queries only. When
  nothing answers, the API returns 503 with the reason instead of an empty list.

### Phase 9 — code execution ✅

* `codeexec.run_python`: fresh temp cwd, stripped environment (no provider
  keys), `RLIMIT_CPU`/`RLIMIT_AS`/`RLIMIT_FSIZE`/`RLIMIT_NOFILE`, wall-clock
  timeout, process group killed, output clipped at 20 000 characters, and
  `unshare -n` for network isolation when the kernel allows it (tested, not
  assumed).
* This is containment, not a security boundary: the child runs as the same user.
  Stated plainly in the module docstring.

## Deliberately not built, and why

| Item | Reason |
|---|---|
| Sharing / public links | Tolesh's promise is that the user's data stays theirs. A public link feature needs an access model, revocation and an audit trail before it is honest to ship. |
| Multi-user / RBAC | Single-user product by design; the desktop shell and the SQLite file are per-user. |
| MCP, connectors, agents, images | Each needs its own phase; doing them badly would be worse than not doing them. Tracked above as ⛔. |
| Vector embeddings | Adds a model dependency and an index-build step for a gain that is not yet measured. The retrieval interface is already the shape a vector store returns, so the swap is contained in `AttachmentStore.retrieve`. |

## Credentials still required

| Feature | Variable | State |
|---|---|---|
| Web search (recommended) | `TOLESH_TAVILY_API_KEY` | not set — Wikipedia fallback in use |
| Web search (alternative) | `TOLESH_BRAVE_API_KEY` | not set |
| Web search (self-hosted) | `TOLESH_SEARXNG_URL` (JSON output enabled) | not set |
| Token Harbor paid tier | dashboard top-up | key present, `402 balance_zero` on paid ids |
| DeepSeek direct / MiMo / MiniMax | `TOLESH_DEEPSEEK_API_KEY`, `TOLESH_MIMO_API_KEY`, `TOLESH_MINIMAX_API_KEY` | not set |

## Verification performed

| Check | Result |
|---|---|
| Full test suite | **6060 passed, 27 skipped, 2 deselected, 0 failed** |
| New tests | 30 (`tests/test_tolesh_workspace.py`) |
| Old-schema database migration | 52 conversations preserved on the live database |
| Attachment retrieval end-to-end | file with "launch code ZANZIBAR-7" → model answered `ZANZIBAR-7` |
| Web search end-to-end | "Who wrote Things Fall Apart?" with `web_search` → correct answer via Wikipedia backend |
| Browser (Chromium, 1440×1000) | history list, search, pin, project filing, project filter, attach, web toggle — 0 console errors, 0 failed requests |
| Sandbox | prints `42`; infinite loop stopped at the timeout; provider key not visible to the child |

## Resuming this work

1. `git log --oneline -5` — the last commit on this line of work is the one that
   added this file.
2. Read this table, pick the next ⛔ row.
3. The stores live in `src/tolesh/workspace/`, `src/tolesh/netsearch/` and
   `src/tolesh/codeexec/`; routes are all in `src/tolesh/server/app.py`; the UI
   additions are in `web/js/{app,components}.js` and `web/styles/app.css`.
4. `DISPLAY=:99 PYTHONPATH=src python3 -m pytest -q` before and after.
