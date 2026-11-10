# Tolesh Testing

> **Honesty contract.** Every number in this document came from a real command
> run in this workspace. A test that failed is listed as failed. Work that was
> not performed is listed under [Outstanding work](#10-outstanding-work). No
> result here is inferred, estimated or back-filled.

## 1. Principles

- **Tests are code, not prose.** The upstream JARVIS suite was inherited in
  full (5,900+ tests) rather than replaced; new Tolesh modules got their own
  unit and integration tests.
- **A failing test is information, not an embarrassment.** Several runs below
  got progressively greener because real defects were found and fixed — see
  [§7](#7-defects-found-and-fixed-by-tests).
- **Environment gaps are reported, not hidden.** Native audio, X display and
  third-party API drift are documented explicitly rather than skipped silently.
- **A testing library is not an agent skill.** Microsoft Playwright is used as
  a Python library for real-browser verification; it was never claimed as an
  installed agent skill. See [SKILLS.md](SKILLS.md).

## 2. Test layers

| Layer | Location | Runner | What it actually proves |
| --- | --- | --- | --- |
| Inherited unit/integration | `tests/test_*.py` (upstream) | `pytest` | Voice, dictation, config, memory, updater, desktop logic still work after the rebrand |
| Tolesh core unit | [`tests/test_tolesh_core.py`](tests/test_tolesh_core.py) | `pytest` | Registry/redaction, routing/fallback, offline provider + persona honesty, usage ledger, conversation window, layered memory, policy, permission gate, computer tools |
| HTTP/SSE contract | [`tests/test_tolesh_server.py`](tests/test_tolesh_server.py) | `pytest` + `fastapi.testclient.TestClient` | `/api/*` JSON + SSE contract, redaction, token guard, conversations, memory, permissions, tools, static serving |
| Desktop↔engine integration | [`tests/test_tolesh_integration.py`](tests/test_tolesh_integration.py) | `pytest` + real uvicorn on a loopback port | In-process backend lifecycle, persistent database, restart persistence, provider config on disk, tray start/stop, AI window |
| Browser E2E | [`scripts/verify_web_ui.py`](scripts/verify_web_ui.py) | Playwright + Chromium | Desktop/mobile/reduced-motion rendering, streaming chat, keyboard and focus behaviour, zero JS errors |
| Desktop launch | `/tmp/tolesh_desktop_launch_check.py` (harness) | `xvfb-run` + xcb + QtWebEngine | Real tray + real window + real engine, screenshot, shutdown, restart persistence |
| Desktop Qt | `tests/test_wizard_layout.py`, `tests/test_updater.py`, … | `pytest` under `xvfb-run` | Wizard layout and updater installers on a headless X server |
| Accessibility | browser E2E subset + [ACCESSIBILITY.md](ACCESSIBILITY.md) | Playwright | Skip link, focus trap, listbox keyboard nav, reduced motion, live regions |
| Performance | `tests/performance/` | `pytest -m performance` | Deselected by default (`pytest.ini` sets `-m "not performance"`) |

## 3. Environment

The workspace runs Python 3.11.1 (pyenv). The desktop stack needs a display
and native audio libraries that are absent from a bare container:

```bash
# Native
apt-get install -y libportaudio2 xvfb x11-utils
# Qt's xcb platform plugin (without these the app aborts before drawing a window)
apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 \
                   libxkbcommon-x11-0 libxcb-randr0 libxcb-shape0 \
                   libxcb-xinerama0 libxcb-xfixes0
# Python (use the project interpreter; sudo pip3 hits PEP 668 / Python 3.12)
python3.11 -m pip install -r requirements.txt
python3.11 -m pip install faiss-cpu faster-whisper pynput
# Optional but needed for the embedded desktop UI (otherwise the app falls
# back to opening the interface in the system browser)
python3.11 -m pip install "PyQt6-WebEngine>=6.6"
```

Two non-obvious prerequisites:

- **Xvfb is required for the full suite.** `pynput` connects to the X server
  via an `AF_UNIX` socket at import time and `PyQt6` needs a display. Run the
  suite through `xvfb-run -a`.
- **`tests/__init__.py` exists.** The upstream tree omitted it, so
  `tests.performance.timing_recorder` failed to import. Adding it changed
  pytest's import mode, so [`conftest.py`](conftest.py) also puts `tests/` on
  `sys.path` for sibling absolute imports such as `test_hot_window_input`.

`pytest.ini` applies `-m "not performance"`, which accounts for the
`2 deselected` in every full-suite result below.

## 4. Commands

```bash
cd /workspace/tolesh

# Full inherited + Tolesh suite (headless display)
xvfb-run -a python3.11 -m pytest -q

# New Tolesh modules only
xvfb-run -a python3.11 -m pytest -q \
  tests/test_tolesh_core.py tests/test_tolesh_server.py

# Browser E2E (start the server first, see README quick start)
PYTHONPATH=src python3.11 scripts/verify_web_ui.py \
  --url http://127.0.0.1:8787

# Regenerate the config example consumed by docs/CONFIGURATION.md
PYTHONPATH=src python3.11 scripts/generate_config_examples.py
```

The browser script writes [`docs/verification-report.json`](docs/verification-report.json)
and real screenshots into `docs/img/`.

## 5. Tolesh-specific test modules

### `tests/test_tolesh_core.py` — 22 tests

| Area | Cases |
| --- | --- |
| Registry / redaction | default config, `TASK_MODEL_ORDER`, `Registry.redacted()` never leaks API keys |
| Router | candidate chain, fallback, retry, `RouterEvent.as_dict()` SSE shape |
| Offline provider | deterministic replies, Nigerian English / Pidgin / Yoruba greetings, streaming, **honesty about simulated emotions** |
| Usage ledger | record, summary, reset |
| Conversation | CRUD, `window()` returns the **newest** N turns in chronological order |
| Layered memory | add/get/list/search/delete/export/forget/context/stats across 5 categories |
| Policy | risk classification, app allowlist, path sandbox boundary |
| Permission gate | approve, deny, expiry, audit log |
| Computer tools | refusal of `_NOT_IMPLEMENTED` actions even after approval |
| Persona | system prompt contains cultural voice + simulated-emotion honesty |

### `tests/test_tolesh_server.py` — 11 tests

Health, config redaction, providers/models, `/api/chat/sync`, `/api/chat` SSE
frames, conversation CRUD, memory endpoints + forget, permission
deny→approve flow, tools endpoints, `TOLESH_API_TOKEN` guard, static web
serving. Uses an in-memory state and the offline-only router, so it needs no
API keys and no network.

### `tests/test_tolesh_integration.py` — 16 tests

The seam the project was missing: **one application, one engine, one
database**. Nothing here is mocked — each test starts a real uvicorn server on
a real loopback port and talks to it over HTTP.

| Test | What it actually proves |
| --- | --- |
| `test_appstate_defaults_to_file_backed_database` | `AppState()` resolves `tolesh.db` on disk, not `:memory:` |
| `test_data_directory_honours_override` | `TOLESH_DATA_DIR` drives every store |
| `test_embedded_server_serves_real_frontend` | `/api/health` answers and `/` returns the real `web/index.html` |
| `test_embedded_server_binds_an_ephemeral_loopback_port` | A TCP connection to the port succeeds |
| `test_token_guard_rejects_unauthenticated_api_calls` | 401 without the bearer token, 200 with it; `/api/health` stays open |
| `test_frontend_url_carries_the_token` | The URL handed to the desktop window authenticates on first load |
| `test_second_server_reuses_the_first_instead_of_double_binding` | No second bind; a borrower cannot kill the owner |
| `test_memory_and_conversations_survive_restart` | Stop, start a new server, both are still there — with messages |
| `test_provider_settings_persist_and_stay_private` | Key on disk at mode `0600`, absent from every response, reloaded by a new process |
| `test_default_model_selection_validates_and_persists` | Unknown model → 400; `""` restores auto routing; choice persisted |
| `test_provider_test_endpoint_is_honest_without_credentials` | Reports `unconfigured`, never fakes success |
| `test_consequential_tools_require_approval_and_stay_refused` | Gate intercepts → approval → executor still refuses `run_command`; audited |
| `test_tray_starts_and_stops_the_embedded_backend` | The exact methods `main()` calls; port released after stop |
| `test_tray_reports_startup_failure_instead_of_raising` | Failure is logged and surfaced to the user, not swallowed |
| `test_ai_window_builds_without_webengine` | Fallback panel with the real URL when QtWebEngine is absent |
| `test_ai_window_retry_uses_the_reconnect_callback` | "Reconnect" re-points the window at a live engine |

### `scripts/verify_web_ui.py`

Drives real Chromium against the running server. Collects `console.error` and
`pageerror`, then asserts: skip link, main focus target, listbox opened by
keyboard / closed by Escape, dialog opened / focus trapped / closed by Escape,
a real streamed reply, `?view=` URL state surviving reload, mobile drawer
collapsed by default, and reduced-motion behaviour. Any collected error fails
the run.

## 6. Browser verification result

Latest run: `VERIFY_EXIT=0`, `passed: true`, `errors: []`.

| Check | Result |
| --- | --- |
| Title | `Tolesh — your personal AI assistant` |
| Skip link / main focus target | `true` / `true` |
| Listbox keyboard + Escape | `true` / `true` |
| Dialog open / focus inside / Escape | `true` / `true` / `true` |
| Real streamed reply | `"How far! I dey here, no wahala "` |
| `?view=` state + restore after reload | `true` / `true` |
| Mobile drawer collapsed | `true` |
| Reduced motion | `--dur: 0ms`, transition `1e-06s`, `scroll-behavior: auto` |
| Console / page errors | `[]` |

Screenshots: `docs/img/tolesh-verify-desktop.png`,
`docs/img/tolesh-verify-mobile.png`,
`docs/img/tolesh-verify-reduced-motion.png`.

## 6b. Desktop launch verification (real X server, real widgets)

The browser check above proves the *web* surface. It does not prove the
desktop application. This run does: `ToleshSystemTray` is constructed for
real, `start_ai_backend()` boots uvicorn in-process, `show_ai_window()` loads
`web/` into QtWebEngine, and `cleanup_on_exit()` shuts it all down.

Command (Xvfb provides the X server; the machine has no physical display):

```bash
QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu" \
TOLESH_DATA_DIR=/tmp/tolesh-launch-data \
TOLESH_CONFIG_PATH=/tmp/tolesh-launch-data/config.json \
TOLESH_PROVIDERS_PATH=/tmp/tolesh-launch-data/providers.json \
PYTHONPATH=src xvfb-run -a -s "-screen 0 1440x900x24" \
  python3.11 /tmp/tolesh_desktop_launch_check.py /tmp/tolesh-launch
```

| Step | Observed |
| --- | --- |
| Qt platform | `xcb` (real X11 plugin, not `offscreen`) |
| Backend | started in-process on `127.0.0.1:43571` |
| `/api/health` | `status: ok`, `product: Tolesh`, providers `["offline","ollama"]` |
| `/api/chat/sync` | reply from `offline:tolesh-offline`, conversation id returned |
| AI window | `has_web_view: true`, URL `http://127.0.0.1:43571/` |
| QtWebEngine load | `loadFinished(true)` |
| Screenshot | 565 KB, `docs/img/tolesh-desktop-integrated.png` |
| Cleanup | `server.is_running == False` after `cleanup_on_exit()` |
| Persistence after exit | memory marker present, 1 conversation, read back by a fresh server |

Full machine-readable record: `docs/desktop-launch-report.json`
(`"ok": true`).

The screenshot shows the real product: engine **ONLINE**, version `0.1.0`,
2 providers ready, default model `auto`, the memory item written through the
API before the window opened, and the composer/voice controls.

**Not** verified by this run: system-tray interaction (Xvfb has no tray host,
so `isSystemTrayAvailable()` is `false`), a physical microphone/speaker, and a
real provider key. Those are recorded as limitations, not as passes.

## 7. Defects found and fixed by tests

These are real fixes, not test edits to force green.

1. **Conversation context window** — `ConversationManager.window()` returned
   the *oldest* N turns because it reused `messages(limit=N)`. Fixed to select
   newest N by `rowid DESC` then restore chronological order.
2. **Persona honesty in the offline fallback** — `build_offline_reply()` had no
   answer for "do you have real feelings?". Added an explicit branch stating
   emotions are simulated, not real.
3. **`no_network` blocked local IPC** — the fixture replaced
   `socket.socket.connect` unconditionally. `pynput` opens an `AF_UNIX` X11
   socket (`/tmp/.X11-unix/X99`) during import, so if `dictation_engine` was
   first imported while the fixture was active it cached
   `pynput_keyboard = None` for the whole session. This surfaced 18 tests later
   as `RuntimeError: pynput is not installed`. Fixed in
   [`tests/conftest.py`](tests/conftest.py): only `AF_INET`/`AF_INET6` are
   blocked; `AF_UNIX` is forwarded to the original `connect`. Real network
   access is still rejected. Result: `82 passed` for
   `test_dictation.py` + `test_dictation_test_isolation.py`.
4. **Linux updater was broken and unsafe on Python 3.11.1** —
   `install_update_linux()` called `tarfile.extractall(filter="data")`, a
   keyword only available in Python 3.11.4+/3.12. On 3.11.1 it raised
   `TypeError`, was swallowed as a generic failure, and disabled updates.
   Fixed in [`src/desktop_app/updater.py`](src/desktop_app/updater.py) with
   `_extract_update_archive()`: use the native `data` filter where supported,
   otherwise reproduce equivalent containment checks member by member
   (absolute paths, `..` traversal, escaping symlinks/hardlinks, special
   files, and a re-check after earlier symlinks are created). **The 10 malicious
   archive cases still fail closed** — `70 passed` across
   `test_linux_update_archive_integrity.py` + `test_updater.py`.
5. **Hub transport stub drift** — `huggingface_hub` 1.4.0 removed
   `file_download._request_wrapper`; `http_get` now uses
   `http_stream_backoff` + `iter_bytes`. The test's stub point was stale while
   the product behaviour (byte progress on non-terminal stderr, configured by
   `TQDM_POSITION=-1` in `desktop_app/app.py`) was intact. The test now selects
   the stub for the installed Hub version **and keeps every original
   assertion**. Downgrading the library was rejected because
   `transformers==5.1.0` requires `huggingface-hub>=1.3.0,<2.0`. Result:
   `22 passed` in `test_log_progress.py`.
6. **Incomplete wake-word rebrand** — default `wake_aliases` still contained
   JARVIS-era mishearings (`joris`, `jarvus`, `jar is`, …). Replaced with
   Tolesh variants in `src/tolesh/config.py`, synchronised 39 occurrences in
   `tests/` and `evals/`, regenerated `examples/config.json`. Wake/alias/intent
   subset: `75 passed`.
7. **Collection and fixture infrastructure** — added `tests/__init__.py`,
   fixed `conftest.py` sibling imports, restored `examples/config.json`, and
   restored the required README screenshots (`docs/img/face.png`,
   `docs/img/tolesh-interface.png`) that a README rewrite had removed.
8. **Letter-spaced brand strings survived the rebrand** —
   `QLabel('J A R V I S  /  CHAT')` in `chat_window.py` and
   `QLabel("J A R V I S   /   SETUP")` in `setup_wizard.py` used spaced text
   that a plain `JARVIS -> Tolesh` replacement could not match. Both now read
   `T O L E S H`. A full-repository scan confirmed the remaining `jarvis`
   occurrences are attribution URLs, licence/upstream references and test mock
   data that must be preserved. Wizard + desktop tests: `201 passed`.
9. **A borrowed backend handle could kill the owner's server** — when
   `EmbeddedServer.start()` finds a live instance it copies the server, thread
   and state references. `stop()` on that second handle then set
   `should_exit = True` on the *shared* uvicorn server, taking the application's
   engine offline behind its owner's back. Fixed with an explicit `_borrowed`
   flag: a borrower detaches without touching the server it does not own.
   Regression: `test_second_server_reuses_the_first_instead_of_double_binding`.
10. **`stop_ai_backend()` raised during teardown** — it read `self.ai_server`
    unconditionally, so any exit path on a partially-constructed tray raised
    `AttributeError` *while the process was shutting down*. Now uses
    `getattr(..., None)` and returns quietly. Found by
    `test_desktop_app.py::TestOllamaRuntimeOwnership`.
11. **`no_network` counted the X11 TCP fallback as a network request** —
    `pynput` first tries the `AF_UNIX` display socket, then falls back to
    loopback port `6000 + display` when no X server is listening. The fixture
    already allowed `AF_UNIX`; it now also allows exactly the loopback targets
    derived from `DISPLAY`. A genuine outbound connection is still rejected —
    the guard was narrowed, not removed.
12. **Wizard page overflowed a 780 px display** — see [§9](#9-formerly-known-failure-now-fixed).
    Fixed by bounding the connection-status label to three lines (full text
    kept in a tooltip) and tightening this page's row spacing from 14 px to
    12 px. The assertion was **not** loosened.

## 8. Test history (chronological, verbatim results)

| Stage | Result |
| --- | --- |
| First collection | `99 errors` (55 `mcp`, 24 `PyQt6`, 16 `rapidfuzz`, 3 `tests.performance`) |
| After deps + `tests/__init__.py` | `4 errors` (`test_hot_window_input` sibling import) |
| After `conftest.py` fix | `131 failed, 5509 passed, 36 skipped, 2 deselected, 263 errors in 298.91s` |
| After native deps (`libportaudio2`, `faiss-cpu`, `faster-whisper`, `pynput`) | `18 failed, 5918 passed, 36 skipped, 2 deselected, 3 warnings in 298.86s` |
| After fixes 1–8 above | `1 failed, 5944 passed, 27 skipped, 2 deselected, 3 warnings in 300.68s` |
| After unified desktop integration + fixes 9–12 | `5961 passed, 27 skipped, 2 deselected, 5 warnings in 280.86s` |

**Final full-suite result (current):** `xvfb-run -a python3.11 -m pytest tests -q`
returned **`5961 passed, 27 skipped, 2 deselected, 5 warnings in 280.86s`** —
**0 failures, 0 errors**. The single failure carried over from the previous
audit (`test_wizard_layout.py::test_controls_fit_on_short_display[960-780-OpenAICompatiblePage]`)
is now fixed in product code, not by loosening the assertion. No test was
deleted, skipped or weakened to reach this result.

The inherited suite is large and slow (~5 minutes). Every run above was
executed under `xvfb-run -a` once the X requirement was understood; earlier
runs without it produced `PortAudio`/`ImportError` noise that is attributed to
the environment, not to product code. `pynput` and Qt both need a live X
display: without one, `pynput` caches `pynput_keyboard = None` and nine
dictation tests fail with `RuntimeError: pynput is not installed`.

## 9. Formerly known failure, now fixed

`tests/test_wizard_layout.py::test_controls_fit_on_short_display[960-780-OpenAICompatiblePage]`
used to fail with:

```
assert scroll.verticalScrollBar().maximum() == 0
E  assert 13 == 0
```

Cause, measured rather than guessed: the connection-status label on
`OpenAICompatiblePage` is a wrapped `QLabel`, so a long provider error grew it
from 17 px to 68 px and pushed the page content 13 px past the 715 px
viewport — the wizard then grew a vertical scrollbar and the navigation row
moved out of reach. Isolated probe:

```
long=False embed=False link=False -> viewport=715 widget=715 overflow=0  status_h=17
long=True  embed=False link=False -> viewport=715 widget=728 overflow=13 status_h=68
```

Fix in [`src/desktop_app/setup_wizard.py`](src/desktop_app/setup_wizard.py):
`_BoundedStatusLabel` caps the label at three text lines and keeps the full
message in a tooltip, and the page's row spacing drops from 14 px to 12 px.
Measured after the fix: `overflow=0`, `status_h=57`. All 41 wizard layout
cases pass across `700×600`, `700×800` and `960×780` for all 12 pages.

## 10. Outstanding work

Explicitly **not** verified in this pass:

- **Live provider endpoints** — JEV / Experiential Labs, Gonka (GLM, DeepSeek,
  MiniMax), Dahl and comparable providers were implemented through the
  OpenAI-compatible adapter but never called with real API keys. Only the
  offline provider and a local Ollama config were exercised.
- **Native voice pipeline re-wiring** — upstream wake-word, VAD, echo
  detection, Piper and Chatterbox TTS code is inherited but was not reconnected
  to the new `/api` server. The browser Web Speech surface
  ([`web/js/voice.js`](web/js/voice.js)) is separate and was verified.
- **Qt desktop shell end-to-end GUI** — now verified on a real X server
  ([§6b](#6b-desktop-launch-verification-real-x-server-real-widgets)):
  launch, in-process engine, embedded UI, shutdown, restart persistence.
  Still **not** verified: system-tray menu interaction (Xvfb exposes no tray
  host, so `isSystemTrayAvailable()` is `false`), Windows 10/11, and a real
  Zorin/Ubuntu desktop session with a compositor.
- **Screen readers** — no NVDA / VoiceOver / Orca pass.
- **Automated a11y audit** — no axe-core run and no WCAG contrast measurement;
  [ACCESSIBILITY.md](ACCESSIBILITY.md) documents the manual guideline audit.
- **Performance benchmarks** — `pytest -m performance` is deselected by
  default and was not run as a qualification gate.
- **Mobile / Android clients** — the shared `/api` contract is ready; no
  separate web or Android client was built.
- **Background assistance** — startup/notification/resource management is a
  documented foundation only; no always-on daemon was implemented.
- **`_NOT_IMPLEMENTED` computer actions** — `delete_file`, `run_command`,
  `shutdown`, `restart`, `send_message`, `network_request` remain deliberately
  unimplemented even after approval.
- **CI** — no CI config file was written; `ci-cd-and-automation` is documented
  as installed-but-not-landed in [SKILL-MATRIX.md](SKILL-MATRIX.md).

## 11. Adding tests

- New backend modules go under `src/tolesh/<namespace>/` with matching tests in
  `tests/test_tolesh_*.py`; keep them offline (no keys, no network) so the
  suite stays reproducible.
- Use the `no_network` fixture for anything that must not reach the internet;
  remember it now permits `AF_UNIX` local IPC by design.
- Browser assertions belong in `scripts/verify_web_ui.py` so
  `docs/verification-report.json` stays the single record of UI verification.
- Config changes must be regenerated with
  `scripts/generate_config_examples.py`; `tests/test_config_example.py` asserts
  the example equals `export_example_config()`.
- Never commit provider secrets. `Registry.redacted()` exists specifically so
  `/api/config` cannot leak keys; `tests/test_tolesh_server.py` asserts this.

## 12. Related documents

[DESIGN.md](DESIGN.md) · [ARCHITECTURE.md](ARCHITECTURE.md) ·
[COMPONENT-LIBRARY.md](COMPONENT-LIBRARY.md) ·
[ANIMATION-GUIDELINES.md](ANIMATION-GUIDELINES.md) ·
[ACCESSIBILITY.md](ACCESSIBILITY.md) · [SKILLS.md](SKILLS.md) ·
[SKILL-MATRIX.md](SKILL-MATRIX.md) · [README.md](README.md)
