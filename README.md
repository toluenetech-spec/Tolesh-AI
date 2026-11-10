# Tolesh

> A private, local-first AI assistant with a provider-independent model router,
> transparent personal memory and permission-gated computer control.
> Male-presenting, warm, intelligent and culturally Nigerian — understands
> Nigerian English, Nigerian Pidgin and Yoruba, and is always honest that his
> emotions are simulated.

**Tolesh is not a greenfield rewrite.** It is a transformation of existing
JARVIS source code, as required. The [`isair/jarvis`](https://github.com/isair/jarvis)
tree was selected as the strongest foundation, copied verbatim into this
repository, rebranded across 479 files, then extended with new AI routing,
memory, permission, persona, server and interface modules. All three candidate
repositories were cloned and inspected first; see [ARCHITECTURE.md](ARCHITECTURE.md).

---

<p align="center">
  <img src="docs/img/face.png" alt="Tolesh's floating brass-toned face on a warm near-black ground, with no frame or background" width="420">
</p>

<p align="center"><sub>A voice, a face, and a place in the conversation.</sub></p>

The face above is Tolesh's own brand asset. It is derived from the inherited
upstream illustration (`docs/img/upstream-face.png`, kept for attribution) and
re-graded into the Lagos Nocturne palette — warm near-black ink, burnished
brass and a trace of adire indigo — described in [DESIGN.md](DESIGN.md).

## Status at a glance

| Area | State | Notes |
| --- | --- | --- |
| Source inspection | Done | 3 repos cloned to `_sources/`, compared, licence-checked |
| Foundation | Done | `isair/jarvis` → `/workspace/tolesh` (independent git working copy) |
| Rebrand | Done | 479 files; JARVIS naming, UI strings, website, installer scripts |
| New Python modules | Done | `ai/`, `conversation/`, `memory_ext/`, `permissions/`, `persona/`, `server/`, `tools_ext/` |
| Web interface | Done | `web/` — real design system, components, GSAP motion, voice surface |
| Browser verification | Done | Chromium/Playwright: desktop, mobile, streaming chat, dialogs, zero JS errors |
| Skill installation | Done | 28 skills installed to the agent; see [SKILLS.md](SKILLS.md) |
| Documentation | Done | 8 mandatory documents + this README |
| Backend test suite | See [TESTING.md](TESTING.md) | Exact results recorded there, not glossed over |
| Desktop Qt shell | **Integrated** | `desktop_app` owns the FastAPI engine in-process; AI window loads `web/` in QtWebEngine |
| Desktop launch (Linux) | **Verified** | Xvfb + xcb: engine online, UI rendered, data survived exit; screenshot in `docs/img/` |
| Web / Android clients | Architecture only | Shared `/api` contract is ready; no separate clients built yet |

---

## Quick start

Requirements: Python 3.11+, Node 22+ (only for the skills CLI), Chromium for
browser checks. On Linux, Qt also needs `libxcb-cursor0` and
`libxcb-icccm4`/`libxcb-keysyms1` — the launcher checks and tells you.

### Desktop application (the product)

```bash
cd /workspace/tolesh
./scripts/run_linux.sh            # Windows: scripts\run_desktop_app.bat
```

That is the whole procedure. The script creates `.venv`, installs
`requirements.txt`, checks the Qt runtime, and launches `python -m desktop_app`.
**The AI engine starts inside the application** — one process, one window, one
database. There is no uvicorn command to run and no second service to manage.

`./scripts/run_linux.sh --check` verifies the environment without starting
anything; `--daemon` runs the inherited headless voice daemon instead.

### Headless / browser client (for development and the future web build)

```bash
# Same API, started by hand. Not required for the desktop app.
PYTHONPATH=src TOLESH_WEB_DIR=$PWD/web \
  python3.11 -m uvicorn tolesh.server.app:app --host 127.0.0.1 --port 8787
# then open http://127.0.0.1:8787/
```

Both paths share one database: `$TOLESH_DATA_DIR/tolesh.db`
(default `~/.local/share/tolesh/tolesh.db`, `%LOCALAPPDATA%`-equivalent via
`XDG_DATA_HOME`). Conversations, memory, permissions and usage all live there,
so they survive restarts and are identical whether the app or the standalone
server wrote them.

The app runs with **zero API keys**. In that state the router uses the
deterministic `offline:tolesh-offline` engine, which is a rule-based,
culturally-aware conversation surface — it is explicitly **not a language
model** and never pretends to be one. Add a key to enable a real provider.

### Connecting Dahl (the provider this build is wired against)

Dahl's gateway is OpenAI-compatible, so it is a registry entry, not new code:

```bash
export TOLESH_DAHL_API_KEY=dahl_...
export TOLESH_DAHL_BASE_URL=https://inference.dahl.global/v1   # already the default
```

Or use the app: **Settings → Dahl → paste the key → Save**, then
**Load model list from endpoint**. That button performs one real
`GET /v1/models` with the stored key and lists exactly what the gateway serves;
ticked ids become the picker's catalogue and are written to
`~/.config/tolesh/providers.json` (mode `0600`, outside the repository).
The key is never echoed back — every endpoint returns `"api_key": "***"`.

The gateway's free tier is queued behind paid traffic, so a busy model answers
`429 {"error":{"code":"model_concurrency", ...}}` and names the models that are
free at that moment. Tolesh handles this honestly rather than hiding it:

* the failing model is named in the stream (`provider_error` with `status: 429`
  and the endpoint's own `Retry-After`),
* the router then tries the other Dahl models before any other vendor,
* if the answer came from a different model, the bubble says so
  (`MiniMax M2.7 · fallback`),
* a connection test reports per-model results instead of one pass/fail.

Optional hardening: `TOLESH_API_TOKEN` gates every `/api` route except
`/api/health`; `TOLESH_ALLOWED_ORIGINS` extends the explicit CORS allow-list.
Provider keys are read from the environment at registry-build time and are
**never** returned by any endpoint (`Registry.redacted()`).

---

## What is actually implemented

### Provider-independent model router
`src/tolesh/ai/` — a `Registry` of providers/models, a `ModelRouter` that
selects per task (`chat`, `reasoning`, `code`, `fast`, `voice_intent`,
`classification`), explicit model switching, ordered fallback chains, SSE
streaming, exponential-backoff retries, and a SQLite `UsageLedger` for token
accounting. `OpenAICompatibleProvider` speaks the standard
`POST /chat/completions` contract, so adding Gonka, JEV, Dahl, DeepSeek, Groq,
OpenRouter, Ollama, vLLM or LM Studio is a registry entry, not new code.

### Conversation + layered memory
`conversation/manager.py` persists conversations and supplies a bounded context
window. `memory_ext/store.py` is a separate, transparent, exportable SQLite
store with five categories (`conversation`, `preference`, `personal`,
`project`, `task`). Every item is listed in the UI, individually deletable, and
`forget_everything` requires typing an explicit confirmation phrase.

### Permission-gated computer control
`permissions/policy.py` classifies actions as `safe`, `elevated` or
`consequential`; `permissions/gate.py` issues expiring approval tokens and
writes an audit log; `tools_ext/computer.py` executes only approved actions.
Read-only actions are audited and run immediately. **`delete_file`,
`run_command`, `shutdown`, `restart`, `send_message` and `network_request` are
deliberately not implemented even after approval** — see the
`_NOT_IMPLEMENTED` set in the source. This is an honest safety boundary, not a
missing TODO.

### Shared HTTP/SSE backend
`src/tolesh/server/app.py` exposes `/api/health`, `/api/config`,
`/api/providers`, `/api/providers/{name}/models` (live model discovery),
`/api/models`, `/api/chat` (SSE), `/api/chat/sync`, conversations, memory, usage,
permissions, tools, and serves the static `web/` client. The same JSON/SSE
contract is intended for a future Cloudflare Pages client and a future Android
client.

### Voice
- **Browser** (`web/js/voice.js`): real Web Speech API STT/TTS, continuous
  conversation, barge-in (speech cancels current TTS), `en-NG` language.
  Reports plainly when the API is unavailable instead of faking a waveform.
- **Python foundation** (inherited `src/tolesh/listening/`, `src/tolesh/output/`):
  wake-word, VAD, echo detection and Piper/Chatterbox TTS code exists from the
  upstream source. It was **not re-wired into the new server** in this pass —
  see [TESTING.md](TESTING.md) "Outstanding work".

### Interface
`web/` is a real, reusable design system — see [DESIGN.md](DESIGN.md),
[COMPONENT-LIBRARY.md](COMPONENT-LIBRARY.md),
[ANIMATION-GUIDELINES.md](ANIMATION-GUIDELINES.md) and
[ACCESSIBILITY.md](ACCESSIBILITY.md).

<p align="center">
  <img src="docs/img/tolesh-interface.png" alt="Tolesh desktop conversation interface with the Lagos Nocturne palette, model selector, composer and transparent context panel" width="900">
</p>

<p align="center"><sub>The real interface, captured in Chromium during the regression pass.</sub></p>

---

## Documentation index

| Document | Purpose |
| --- | --- |
| [DESIGN.md](DESIGN.md) | "Lagos Nocturne" design system: tokens, palette, type, layout, principles |
| [SKILLS.md](SKILLS.md) | Every inspected/installed skill: repo, commit, README review, usage, limits |
| [SKILL-MATRIX.md](SKILL-MATRIX.md) | Skill → task → Tolesh application mapping |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Foundation selection, module map, data flow, platform roadmap |
| [COMPONENT-LIBRARY.md](COMPONENT-LIBRARY.md) | Reusable CSS classes and JS component contracts |
| [ANIMATION-GUIDELINES.md](ANIMATION-GUIDELINES.md) | Motion principles, GSAP usage, reduced-motion, performance |
| [ACCESSIBILITY.md](ACCESSIBILITY.md) | Vercel Web Interface Guidelines audit, findings and fixes |
| [TESTING.md](TESTING.md) | Test strategy and the exact, honest results |
| [IMPLEMENTATION-REPORT.md](IMPLEMENTATION-REPORT.md) | Honest final report: repos, skills, deliverables, tests, gaps |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Upstream configuration reference (retained, rebranded) |
| [docs/upstream/](docs/upstream/) | Archived upstream README/AGENTS/EVALS for attribution |

---

## Attribution and licences

> **Commercial distribution blocker.** Tolesh is a derivative work of
> `isair/jarvis`, whose licence is **non-commercial**: it forbids selling or
> commercialising the software and requires derivative works to use the same
> terms. Commercial use requires a **separate written licence** from the
> copyright holder, Baris Sencan (`baris@writeme.com`). Tolesh is **not** claimed
> to be commercially distributable. See
> [LICENSING-ASSESSMENT.md](LICENSING-ASSESSMENT.md) and
> [ATTRIBUTION.md](ATTRIBUTION.md).

The governing licence is reproduced verbatim in [`LICENSE`](LICENSE). All
upstream licences are preserved in [`LICENSES/`](LICENSES/), upstream
documentation in [`docs/upstream/`](docs/upstream/), and the full notice
inventory in [ATTRIBUTION.md](ATTRIBUTION.md).

| Source | Licence | Role in Tolesh |
| --- | --- | --- |
| [`isair/jarvis`](https://github.com/isair/jarvis) | **Custom "Jarvis AI Assistant License" — non-commercial**; derivatives must use the same terms; commercial use requires a separate licence | **Foundation**: copied verbatim, rebranded, extended |
| [`FatihMakes/Mark-LV`](https://github.com/FatihMakes/Mark-LV) | **CC BY-NC 4.0 — non-commercial** | Inspected only. Its confirmation-gate concept informed `permissions/policy.py`; **no code copied** |
| [`jev-chat/jev-chat-jarvis`](https://github.com/jev-chat/jev-chat-jarvis) | MIT (+ NOTICE) | Inspected only. NOT selected as foundation |

Neither Mark-LV nor the selected foundation permits commercialisation; the
difference is that Mark-LV's code was never copied. Any commercial release
requires upstream clearance and a counsel review of this table, the
`LICENSES/` directory, PyQt6's dual licensing and the GSAP Standard License.

---

## Known limitations (stated plainly)

- The `offline` provider is rule-based, not a language model. Real answers need
  a configured provider.
- Provider entries for JEV / Gonka (GLM, DeepSeek, MiniMax) / Dahl are declared
  in the registry but their live endpoints were **not** verified with real keys
  in this environment.
- The desktop Qt shell (`src/desktop_app/`) is inherited and rebranded; the
  native wake-word/VAD/TTS pipeline was not reconnected to the new server.
- Background assistance (startup-on-boot, notifications, resource governor) is
  a foundation only; there is no secret file/mic/camera access.
- Web and Android clients do not exist yet; only the shared API contract is ready.
- See [TESTING.md](TESTING.md) for the exact test results and remaining gaps.
