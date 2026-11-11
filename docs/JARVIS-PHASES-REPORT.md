# Jarvis-inspired capabilities — implementation report

Six phases, implemented in `toluenetech-spec/Tolesh-AI` between `5b6cee3` and
`9585ae6`. Every phase was tested on its own, then against the full suite, then
against `scripts/audit_features.py`, and committed only once all three were
green. Nothing in this document is a plan: it describes code that runs.

| Phase | Commit | Feature |
|---|---|---|
| 1 | `5b6cee3` | Cron schedules for background jobs |
| 2 | `4ec5404` | Failure recovery: attempts, backoff, pause at the limit |
| 3 | `a227c33` | Audio-level wake-word detection |
| 4 | `0401c58` | Telegram channel |
| 5 | `74b37e9` | Volume / brightness / lock, permission-gated |
| 6 | `9585ae6` | Microphone level visualisation |

Pushed to `origin/main` (`664dc34..9585ae6`).

---

## 1. Cron scheduling

`src/tolesh/jobs/cron.py` (new, ~370 lines) implements five-field expression
matching and nothing else: no command splitting, no execution, no `shell=True`.
The calendar arithmetic is adapted from Jarvis's
`modules/crontab/expression.py` (MIT, attributed in the module docstring).

Supported: `*`, `n`, `a-b`, `*/s`, `a-b/s`, `a/s`; month and weekday names;
`@yearly`/`@annually`/`@monthly`/`@weekly`/`@daily`/`@midnight`/`@hourly`;
`L` (last day of month), `5L` (last Friday), `15W` (nearest weekday),
`5#3` (third Friday), and `%n` (Jarvis's epoch-periodicity extension, kept
because it was in the source being ported and is documented as non-standard).

Two semantics that are easy to get wrong, and are tested against a brute-force
reference over 10,000 random minutes across 40 random expressions:

* day-of-month and day-of-week are **OR**ed when both are restricted, as in
  classic cron (`_day_ok`);
* `next_after` starts at the next whole minute and never returns its input.

In the scheduler (`src/tolesh/jobs/scheduler.py`), `schedule="cron"` is a third
branch beside `once` and `interval`, not a rewrite of either:

* `next_run_at` for a cron job comes from the calendar, not from the finish
  time — a job that took four minutes still fires on the next matching minute;
* `last_fired_minute` (a new column, not in-memory state) is enforced inside
  the atomic claim SQL, so a restart cannot re-fire a minute that already ran;
* an invalid expression is refused at create/patch time, never stored.

`GET /api/cron/preview?expression=…` returns `{valid, error, next, next_local}`
so the Jobs form can show the next three runs while the user types.

## 2. Failure recovery

Same file, extending the existing scheduler rather than adding a task system.

* `attempts` counts **consecutive** failures and resets to 0 on success.
* `max_attempts` (default 3, range 1–20) and `backoff_sec` (default 60, range
  1–86400) are per job.
* Delay is `min(base × 2^(n−1), 3600)`.
* At the limit the job is **paused**, not left failing forever, and a
  notification is written *even when `notify` is false* — a job that stopped
  working is not something to keep quiet about.
* One-off jobs are never retried: there is nothing left to run, and a retry
  would spend model budget to fail again. This is a deliberate deviation from
  "retry everything", recorded in the code.
* `resume()` clears the count and re-anchors a cron job to the calendar.

The Jobs UI shows `attempt n/m`, labels retries, and marks a gave-up job in the
danger colour.

## 3. Audio-level wake word

`src/tolesh/listening/hotword.py` (new) defines a `HotwordDetector` interface
with two optional backends — openWakeWord (Apache-2.0, no account) and
Porcupine (needs `TOLESH_PORCUPINE_KEY`) — plus `create_detector`/`probe`.
The base class buffers arbitrary frame sizes into the backend's chunk size
(1280 samples / 80 ms for openWakeWord, 512 for Porcupine), so the existing
audio pipeline is unchanged.

Integration is on the **audio-frame worker thread**, never the audio callback.
Gating is off by default; when off, every utterance is transcribed and the wake
word is matched in the text exactly as before. Every failure path is
non-fatal: a detector that raises, a model that will not load, a missing
package — each disables the gate and falls back to the transcript detector,
so a gate can never silently swallow a command. The ~300 ms pre-roll is
preserved and widened to 300 ms when gating.

Settings: `hotword_enabled`, `hotword_backend`, `hotword_model`,
`hotword_threshold` (0.05–0.95), `hotword_window_sec` (0.5–10), persisted
through the existing config file, with `PATCH /api/voice/hotword` and
`POST /api/voice/hotword/test` (loads the backend and scores one silent chunk).

Verified against the real openWakeWord 0.6.0: six models, 80 ms chunks,
silence and noise both score 0.002. The test suite uses a stub, so CI never
downloads a model.

## 4. Telegram

`src/tolesh/channels/telegram.py` (new, ~660 lines). Long polling, not
webhooks: a laptop behind NAT cannot accept an inbound connection.

Security posture, which is the point of the module:

* the bot token is stored through `ConnectorStore`, so it is Fernet-encrypted
  under the existing 0600 key; it never enters the config file, a log line, or
  an API response (every response reports only `has_token`);
* a numeric chat id must be on the allowlist **before** a message is parsed,
  stored or answered; ids are coerced to `int` so `"123"` cannot slip past
  `123`, and a non-numeric id is dropped rather than becoming `0`;
* an unknown chat gets silence, not an error — a stranger should not learn the
  bot answers anyone;
* one conversation per chat, so two people talking to the same bot never see
  each other's context.

Replies go through the same path as a typed message: `chat_sync`'s body was
extracted into `_run_turn`, which both the HTTP route and the channel call, so
memory, web context and model routing are shared rather than duplicated.

Voice notes: download → faster-whisper (`.ogg` suffix now passed through, since
Telegram sends Ogg Opus) → chat pipeline → Piper/HTTP TTS → `sendVoice` when
the synthesiser returns Ogg. A failed synthesis falls back to text; losing the
voice note is a downgrade, not a failure.

Lifecycle: one daemon thread, its own offset, exponential backoff 1→60 s on
network errors, clean shutdown that joins the thread and closes the transport.
Off by default; `start_if_configured` treats "switched off" and "no token" as
quiet states rather than errors.

API: `GET/PATCH /api/telegram`, `POST /api/telegram/{start,stop,test}`,
`DELETE /api/telegram/token`. Settings card: toggle, token field, allowlist
chips, live status, test button.

## 5. System controls

`src/tolesh/system/controls.py` (new). Three controls and nothing else:
`set_volume`, `set_brightness`, `lock_screen`. All three are `ELEVATED`, so the
existing `PermissionGate` returns a pending action with an expiring token and
nothing runs until that exact token comes back.

`shutdown`, `restart`, `run_command`, `delete_file`, `send_message` and
`network_request` keep their `CONSEQUENTIAL` classification and stay in
`_NOT_IMPLEMENTED`; a test asserts they are refused even after approval.

Two properties are enforced by construction:

* **no shell** — every command is an argv list built from a fixed executable
  and an integer validated to 0–100 before formatting;
* **no false success** — `ok` only when the utility exited zero; a missing
  utility returns `unavailable` naming it and the command that installs it;
  an unsupported platform returns `unsupported`.

| | Linux | macOS | Windows |
|---|---|---|---|
| volume | `pactl` → `amixer` | `osascript` | `pycaw` (optional package) |
| brightness | `brightnessctl` | `brightness` (Homebrew) | WMI via PowerShell |
| lock | `loginctl` → `xdg-screensaver` → `gnome-screensaver-command` | `pmset displaysleepnow` | `rundll32 user32.dll,LockWorkStation` |

Linux and macOS volume are read back after setting and reported `verified`
only when the device landed within two points of the request. On this machine
the detection reports `pactl` and `loginctl` present, `brightnessctl` missing
with the install hint — which is the intended behaviour, not a failure.

Exposed through the existing registry: `/api/tools` gains `available` and
`detail` per control, `GET /api/tools/system` reports what works here plus the
refusal list, the agent catalogue lists the three with `needs_approval=True`,
and the context panel disables an unavailable control instead of offering a
button that will fail.

## 6. Microphone visualisation

`web/js/meter.js` (new). `getUserMedia` → `MediaStreamSource` → `AnalyserNode`;
the level is the RMS of 1024 time-domain samples, written to the orb as
`--mic-level` and turned into scale, opacity and glow by CSS. The GSAP pulse
that used to animate the ring is gone — it ran whether or not anything was
being heard.

Four states: `idle`, `listening`, `speaking`, `paused`. While Tolesh speaks the
microphone stays open (that is what makes barge-in work) but the level is
hidden, because what it hears is Tolesh. A hidden tab pauses the display.
Every exit path — `stop()`, denied permission, a device reporting `ended`,
`pagehide` — stops the tracks and closes the `AudioContext`.

Nothing is drawn without a microphone: a denied permission or a browser without
Web Audio produces a sentence saying which.

---

## Database changes

All additive; no table is recreated and no column is dropped.

| Store | Change |
|---|---|
| `jobs` | `+ cron TEXT`, `+ last_fired_minute INTEGER`, `+ attempts INTEGER`, `+ max_attempts INTEGER`, `+ backoff_sec REAL` |
| `telegram_chats` (new) | `chat_id INTEGER PRIMARY KEY, conversation_id, title, created_at, updated_at` |
| `telegram_settings` (new) | `key TEXT PRIMARY KEY, value TEXT, updated_at` |

`telegram_settings` exists rather than widening `SettingsStore`: that store
holds a fixed set of typed keys and refuses anything else, and adding a channel
should not change the settings contract.

Verified against the **real pre-Phase-1 schema** (taken from `664dc34`): a
legacy job reads back with `cron=None`, `attempts=0`, `max_attempts=3`, and a
restart keeps conversations, messages, settings and cron jobs intact.

## Dependencies

**No mandatory new dependency.** Everything added is either stdlib, already
present (`httpx`), or browser-native.

| Package | Phase | Status | Needed for |
|---|---|---|---|
| `openwakeword` | 3 | optional, installed here (0.6.0 + tflite-runtime) | audio wake-word gating |
| `pvporcupine` | 3 | optional, **not** installed | alternative detector; needs `TOLESH_PORCUPINE_KEY` |
| `pycaw` (+ `comtypes`) | 5 | optional, Windows only | volume on Windows |
| `brightness` (Homebrew) | 5 | optional, macOS only | brightness on macOS |
| `brightnessctl` | 5 | optional, Linux | brightness on Linux |

Playwright was already in `requirements.txt` for `scripts/`; a test now uses it.

## Tests

| Run | Result |
|---|---|
| `tests/test_jobs_cron.py` | 76 passed |
| `tests/test_jobs_retry.py` | 14 passed |
| `tests/test_hotword.py` | 25 passed |
| `tests/test_telegram.py` | 56 passed |
| `tests/test_system_controls.py` | 50 passed |
| `tests/test_mic_meter.py` | 10 passed |
| **New tests** | **231** |
| **Full suite** (`DISPLAY=:99 PYTHONPATH=src python3.11 -m pytest -q`) | **6475 passed, 27 skipped, 2 deselected, 0 failed** |
| `scripts/audit_features.py` | **55 passed, 0 failed, 4 gated (59 checks)** |

The audit gained six checks: cron validates and schedules; a failing job pauses
and notifies; wake-word settings persist and validate; Telegram is off by
default, stores the token encrypted and enforces the allowlist; the three
system controls are gated while shutdown stays refused; and the orb level is
measured from the microphone.

Notable test techniques, because they are what makes the claims credible:

* the cron matcher is compared against an independent brute-force reference
  over 10,000 random minutes;
* the wake-word tests use a stub detector, so no model is downloaded;
* the Telegram tests use `FakeTransport`, so no socket is opened and no token
  is needed — plus one end-to-end test that injects the fake into the running
  app and asserts a real conversation row is created;
* every system-control platform branch is exercised on one machine by
  injecting the runner, the executable lookup and the platform name;
* the microphone meter is measured in a real headless Chromium fed a generated
  WAV: a loud tone reads > 0.6 and a tone 25 dB quieter reads < 60% of that,
  which a timer-driven animation cannot produce.

## Known limitations

1. **`%n` is not standard cron.** It is Jarvis's epoch-periodicity extension,
   kept for fidelity and labelled as such in the source.
2. **Cron search horizon is five years.** An expression that can never fire
   (e.g. `0 0 30 2 *`) raises `CronError` after the walk instead of looping;
   this is tested, and such an expression is refused at save time.
3. **Backoff never delays past the job's own next run.** A 5-second interval
   job with a 60-second backoff retries after 5 seconds, not 60. The scheduler
   prioritises not skipping a scheduled run; for long-interval and cron jobs
   the exponential delay applies in full.
4. **One-off jobs are not retried.** Deliberate; see §2.
5. **macOS `lock_screen` is best-effort.** `pmset displaysleepnow` sleeps the
   display, and macOS locks it only when "require password immediately" is set.
   The response says exactly that rather than claiming a lock.
6. **Windows volume needs `pycaw`.** Windows has no built-in volume CLI, and
   shelling out to an inline C# block would be exactly the arbitrary-code path
   this phase forbids. Without `pycaw` the control reports `unavailable` with
   `pip install pycaw`.
7. **Windows brightness needs an internal display.** The WMI interface does not
   cover external monitors; the failure is reported with the WMI error.
8. **Telegram is polling, not webhooks.** Updates arrive within the poll
   interval (long-poll, so usually immediately). A bot in many chats would want
   webhooks; a personal assistant does not.
9. **The wake-word gate is per-process.** It lives on the daemon's listener
   thread; a headless server with no microphone reports the settings as
   available but `gating: false`, and says so.
10. **`/api/telegram/{start,stop,test}` are not exercised by the audit script**
    (they would open a real connection). They are covered by 56 tests against a
    fake transport.

## What was deliberately not built

* Jarvis's keyword-table intent router (245 lines, 214 functions across 56
  executor modules) — replaced by the existing model router, untouched.
* Jarvis's `multiprocessing.Process` tree and its shell executor.
* Any arbitrary command execution: `run_command`, `shutdown`, `restart` remain
  refused after approval, as before.

## Preservation check

Untouched and still passing: model routing and the provider registry, memory
(store, service, extraction, retrieval), conversations and projects, MCP,
agents and their tool catalogue, the permission gate's existing decisions,
notifications, and the database architecture. The only schema changes are the
three additive ones listed above.
