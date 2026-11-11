# Tolesh × Jarvis — source-level integration assessment

**Reference repository:** [thevickypedia/Jarvis](https://github.com/thevickypedia/Jarvis) @ `7613d5b` (MIT, last commit 2026-05-30)

**Target:** Tolesh @ `ac37f4b` (`/workspace/tolesh`)

**Status:** inspection and planning only. No Tolesh source file was modified.

Claims are cited as `file:line` from source I read. Where something is an inference I did not execute — dependency behaviour, licence terms, performance — it is marked **[assumed]**. Nothing here is called working merely because a module exists.

---

## 1. What was actually inspected

| | Jarvis | Tolesh |
|---|---|---|
| Python files | 133 | 181 |
| Python LOC | 20,937 | 62,373 |
| Test files / test functions | 7 files, **12 test functions** | 310 files, ~6,240 tests |
| Process model | CLI process tree: main + 2–4 `multiprocessing.Process` children, PID map on disk | single FastAPI/uvicorn process + background threads, state in SQLite |
| State | YAML files in `fileio/` + thin SQLite wrapper | SQLite (WAL) for everything |
| UI | none (terminal + optional PySide6 widget) | Qt desktop shell + browser workspace (9 tabs) |
| Python support | `>=3.10,<3.12` | 3.11 |

Read in full: `jarvis/main.py`, `modules/audio/{listener,speaker,wave,tts_stt}.py`, `modules/microphone/{plotter,widget,recognizer}.py`, `modules/camera/camera.py`, `modules/facenet/face.py`, `modules/crontab/expression.py`, `modules/database/database.py`, `modules/telegram/{bot,audio_handler,file_handler}.py`, `modules/transformer/gpt.py`, `executors/conditions.py`, `executors/{crontab,background_task,automation,controls,processor,process_map,others,guard,restrictions,word_match}.py`, `api/{server,entrypoint}.py`, `api/background_task/{task,agent}.py`, `api/squire/scheduler.py`, `api/routers/{surveillance,speech_synthesis,routes}.py`, `_preexec/keywords_handler.py`, `lib/*requirements*.txt`, `pyproject.toml`, `LICENSE`.

Read on the Tolesh side: `listening/{listener,wake_detection}.py`, `utils/audio_capture.py`, `jobs/scheduler.py`, `tools_ext/computer.py`, `permissions/policy.py`, `tools/{registry.py,builtin/*}`, `dictation/dictation_engine.py`, `media/speech.py`, `connectors/{providers,manager}.py`, `netsearch/search.py`, `config.py`, `requirements.txt`.

**The single most important structural fact:** Jarvis is a voice-first, single-user, single-machine desktop assistant whose intent routing is a **keyword table** (`modules/conditions/keywords.py`, 245 lines) mapping trigger words to 214 hard-coded functions across 56 executor modules. A model exists but only as a **last-resort fallback**: if no keyword matches, `executors/conditions.py:120-124` calls a local Ollama instance (`modules/transformer/gpt.py:30`) — or, before that, replays a previously stored answer whose prompt clears a `difflib` similarity threshold (`gpt.py:48-116`), with history persisted in `gpt_data.yaml`. That fast-path/slow-path split is interesting; the keyword table itself is not importable, because it would become a second router competing with Tolesh's model router, which the brief forbids. Jarvis is a reference for *device-facing* primitives and for scheduling policy, never for orchestration.

---

## 2. Feature-by-feature comparison

### 2.1 Voice intelligence

| Capability | Jarvis | Tolesh today | Class |
|---|---|---|---|
| Wake word | **Audio-level.** `pvporcupine.create()` on 512-frame PCM in a tight loop, `main.py:96` + `main.py:140-151`; multi-keyword with per-keyword sensitivity `main.py:37-40` | **Transcript-level.** Whisper transcribes every utterance, then `is_wake_word_detected()` matches the name with Unicode boundaries + `difflib` fuzzy ratio 0.78 (`wake_detection.py:31-57`), plus an LLM "intent judge" (`intent_judge.py`) | **Partially implemented** — the *audio* gate is missing |
| Always-available listening | Mic open permanently in the Activator loop | `VoiceListener` thread, continuous capture | Already implemented |
| Continuous listening + privacy controls | No mute, no pause; only killing the process | `capture_paused(reason)` with per-reason counters, `listener.py:544-565` | **Tolesh better** |
| Voice activity detection | Energy threshold only (`energy_threshold`, `pause_threshold`, `phrase_threshold` from `recognizer.yaml`, `listener.py:23-28`) | `webrtcvad` aggressiveness 2, `listener.py:494`, per-frame `is_speech` `listener.py:1168` | **Tolesh better** |
| Microphone management | `pyaudio` device index, enumeration in `peripherals.py:44-60` | `resolve_input_device` + `open_input_stream` with input-format fallback, `utils/audio_capture.py:9-98` | Equivalent |
| Speech-to-text | **Cloud.** `recognizer.recognize_google(...)` — Google Web Speech, `modules/audio/listener.py:36`; needs network, sends audio off-device | **Local.** faster-whisper / mlx-whisper, model load `listener.py:391-397` | **Tolesh better** |
| Text-to-speech | HTTP TTS service (`speaker.py:25-82`) with `pyttsx3` fallback (`speaker.py:119-120`); does text pre-processing (12h→24h clock, IP spelled out, degree signs) `speaker.py:44-56` | piper / chatterbox local, `media/speech.py:127` | Equivalent — Jarvis's **text normalisation** is worth stealing |
| Natural interruption (barge-in) | **None.** `runAndWait()` blocks; the audio stream is closed before listening (`main.py:120`) so the wake word is dead while speaking | `tts.interrupt()` on stop command, `listener.py:772,953`; interruptible playback `output/tts.py:412-433` | **Tolesh better** |
| Voice latency | cloud STT round-trip + blocking TTS | local STT/TTS | **Tolesh better** |
| Audio visualisation | Real-time FFT with matplotlib + `sounddevice` (`microphone/plotter.py:89-137`) and a PySide6 wave widget (`microphone/widget.py:15-97`) | **None.** `web/js/voice.js:5` states explicitly it is "not a simulated waveform" | **Missing** |
| Hands-free conversation | Implicit: after a reply it loops back to wake word | Hot window with expiry (`state_manager.py`), follow-up playback hooks (`listener.py:608-633`) | Equivalent |

### 2.2 Desktop intelligence

| Capability | Jarvis | Tolesh today | Class |
|---|---|---|---|
| Application launch | macOS/Windows only; `os.system(f"start {keyword}")` `others.py:92`; `open /Applications/<name>` after `ls /Applications` `others.py:105-115`; **Linux explicitly unsupported** `others.py:72-74` | `open_application` allowlisted to 7 app classes, risk `ELEVATED`, `permissions/policy.py:26,37-50` | **Tolesh safer**, Jarvis broader |
| OS controls | Volume (`executors/volume.py:13-25`), brightness (`pybrightness`, `controls.py:94`), screen lock (`pywslocker`, `controls.py:96`), shutdown/restart (`controls.py:52,231`) | **Explicitly refused**: `shutdown`, `restart`, `run_command` in `tools_ext/computer.py:26-29` | **Missing** (deliberate) |
| Keyboard / mouse automation | None | `pynput` hotkey listener + simulated paste, `dictation/dictation_engine.py:69,188-215` | Equivalent |
| Window management | None | None | Not present in either |
| File / folder ops | YAML CRUD `executors/files.py:21-273` | `local_files` tool + sandbox path check `permissions/policy.py:66+` | Equivalent |
| Clipboard | Not implemented | 3 OS backends (`_clipboard_windows/_macos/_linux`) + `Cmd/Ctrl+V` via CGEvent or pynput, `dictation_engine.py:164-215` | **Tolesh better** |
| Screenshot / screen awareness | Camera stills only | `screencapture` / region capture + **Tesseract OCR**, `tools/builtin/screenshot.py:12-45` | Tolesh for OCR; Jarvis for live feed |
| Camera | Device enumeration via `v4l2-ctl` / `wmic` / `system_profiler` (`camera/camera.py:14-16`), stills via `cv2.VideoCapture` (`facenet/face.py:234+`), **MJPEG live stream** (`api/routers/surveillance.py:145-206`) | **None** | **Missing** |
| Face recognition | `face_recognition`/dlib encodings + Haar cascade (`facenet/face.py:108-233`); training dir `train/<name>/*.jpg` (`executors/face.py:12-60`) | None | **Missing** — not recommended |
| Hardware / peripherals | pyaudio, pywifi, wakeonlan, LIFX lights, TV, thermostat | None | Out of scope |

### 2.3 Background intelligence

| Capability | Jarvis | Tolesh today | Class |
|---|---|---|---|
| Execution independent of any UI | Yes, and finer-grained: subsystems are separate `multiprocessing.Process` children with a PID map on disk (`executors/process_map.py:31-48`), so the **API server can be restarted without killing background work** (`restart_checker`, `main.py:57-73`) | Yes with respect to the browser/chat window; **no** with respect to the server process — `JobScheduler` is a thread inside the FastAPI process (`server/app.py:141`), so a full restart stops execution. Jobs are **rows**, claimed with `UPDATE ... WHERE status='scheduled'` (`jobs/scheduler.py:261-279`), so they resume correctly after restart | Split: Jarvis survives an API-only restart, Tolesh survives a full restart *with correct resumption* and never double-runs |
| Scheduling vocabulary | **5-field cron with `L`, `W`, `#`, `%`, `@hourly`-style macros, named months/days, and a custom epoch** (`modules/crontab/expression.py:15-260`); plus fixed-interval tasks (`BackgroundTask.seconds`, `models/classes.py:157`) and time-of-day automation (`executors/automation.py:78-160`) | **`once` and `interval` only.** `if schedule not in ("once","interval"): raise JobError` — `jobs/scheduler.py:115-117` | **Missing** — the clearest gap |
| Event-driven automation | Time-of-day + day-of-week filters with a persisted `status` flag to prevent double-fire (`automation.py:78-160`); weather-alert monitor | None | Missing (low value for Tolesh) |
| Task monitoring / history | Log files only | `job_runs` table with per-run status, progress JSON, result, error, timestamps; `runs()` `scheduler.py:207` | **Tolesh better** |
| Process management | `psutil` + PID map + `stop_process` | Threads only | Different model |
| Failure recovery | `@retry(attempts=3, interval=2)` decorator (`modules/retry/retry.py`), used in `listener_controls.py:53`, `meetings/events.py:28`, `ics_meetings.py:31`; plus a `FAIL_SAFE` counter that notifies after 10 failures and stops rescheduling (`api/background_task/task.py:25-78`) | Caught, marked `failed`, no retry, no backoff, no escalation (`scheduler.py:305-320`) | **Missing** — second clearest gap |
| Notifications | Email / Telegram | `notifications` table + unread counters + UI (`scheduler.py:218-240`) | Equivalent |
| Hot reload of schedules | Re-reads and re-validates the YAML every 3 s tick and diffs it (`task.py:169-182`) | Reads rows every tick | Equivalent |

### 2.4 Integrations

| Capability | Jarvis | Tolesh today | Class |
|---|---|---|---|
| Telegram | Full bot: long-poll or webhook, **whitelist by `chat_id` *and* `username`** (`telegram/bot.py:373-397`), voice notes downloaded → converted (ffmpeg/pydub/ftransc) → transcribed → **reply synthesised as audio** (`bot.py:487-537`, `audio_handler.py`), file up/download with an allowlist (`file_handler.py:12-60`) | **None** | **Missing** |
| Smart devices | LIFX lights, TV, thermostat, Google Home, Wake-on-LAN | None | Not suitable |
| API architecture | FastAPI + a declarative route registry (`api/routers/routes.py:8-36`), bearer auth, strict CORS allowlist (`api/entrypoint.py:12-45`) | FastAPI + token guard + signed media URLs + 60+ routes | **Tolesh better** |
| Auth / permissions | Single bearer token from env; **email OTP as second factor** for the surveillance endpoint (`routers/surveillance.py:24-50`, single-use token) | Bearer token, plus a real `PermissionGate` with `SAFE/ELEVATED/CONSEQUENTIAL` and confirm-tokens (`permissions/policy.py:21-34`, `gate.py`) | **Tolesh better** |

---

## 3. The four classifications, condensed

**1 — Already implemented in Tolesh (do not rebuild):** continuous listening, VAD, local STT, local TTS, barge-in, mic device selection, hot window, clipboard paste, screenshot + OCR, sandboxed file ops, job history and notifications, bearer auth, risk-tiered permission gate, MCP tool bridge, multi-provider model routing, automatic memory.

**2 — Partially implemented (extend, don't replace):**
- Wake word: transcript-level works; the *audio* gate is absent, so Whisper runs on every sound.
- App launch: exists but allowlisted to 7 names and no `run_command`.
- Scheduling: `once`/`interval` only — no calendar-shaped schedules.
- Failure handling: terminal state recorded, but no retry, no backoff, no escalation.
- Background independence: real, but bound to the server process's lifetime.

**3 — Missing and worth implementing:** cron scheduling · audio-level hotword · retry/backoff with escalation · Telegram channel · gated OS controls (volume/brightness/lock) · camera snapshot + live view · mic level visualisation.

**4 — Not suitable for Tolesh:** face recognition and dlib (heavy, biometric data retention, no product need) · smart-home drivers (LIFX/TV/thermostat/Google Home) · `pywifi` network juggling · Robinhood/stock modules · email-OTP MFA infrastructure (Tolesh's threat model is local-first) · the PySide6 mic widget and matplotlib plotter (a browser WebAudio analyser is strictly better here) · Google Web Speech STT (Tolesh is local-first by design) · the keyword-table intent router (would displace the LLM).

---

## 4. Deep dive on the five candidates worth building

### 4.1 Cron scheduling — highest value, lowest risk

**How Jarvis does it.** `modules/crontab/expression.py` is a self-contained 335-line MIT parser. `CronExpression(line, epoch, epoch_utc_offset)` normalises macros (`@daily` → `0 0 * * *`, `:56-64`), maps `jan..dec`/`sun..sat` to numbers (`:87-91`), folds `7`→`0` and `?`→`*` (`:84-85`), then `compute_numtab()` expands every field into a set (`:120-145`). `check_trigger(date_tuple)` (`:147-260`) evaluates one minute and returns bool, handling `%n` (every n units from epoch), `#` (nth weekday of month), `L` (last day / last weekday), and `W` (nearest weekday) inline. A trailing 6th field is the *command*, which is what `executors/crontab.py:23-48` shells out.

**What Tolesh needs from it.** Only the *matcher*. Tolesh's jobs already store a `goal` and dispatch through `self.run_job` (`server/app.py:141`) — there is no shell string, so `executors/crontab.py` (and its `subprocess.call(..., shell=True)`) is **not** to be ported. That removes the entire class of injection risk the original has.

**Reuse:** `expression.py` verbatim, minus `from jarvis.modules.exceptions import InvalidArgument` → Tolesh's own `JobError`. `parse_atom()` is pure stdlib (`calendar`, `datetime`). **Zero new dependencies.**

**Conflict with Tolesh's design:** Tolesh computes `next_run_at` as `now + interval_sec` from the *finish* time (`scheduler.py:336-338`) to avoid drift stacking. Cron needs the opposite: the next matching minute strictly after *now*, computed forward from a minute boundary. Both must coexist — `_finish()` branches on `schedule` already, so it is a third branch, not a rewrite. A `cron` column needs an additive `ALTER TABLE` migration, which Tolesh already does elsewhere.

**Cost of getting it wrong:** a cron that fires twice in the same minute. Guard: persist `last_cron_minute` and refuse to claim the same minute twice.

**Complexity: low — ~1 day** including migration, validation, UI picker and tests.

### 4.2 Audio-level hotword gate

**How Jarvis does it.** `Activator.__init__` builds Porcupine with per-keyword sensitivity (`main.py:30-54,90-98`), opens a raw `pyaudio` stream at the detector's exact `sample_rate`/`frame_length` (`:100-114`), then blocks on `detector.process(struct.unpack_from("h"*frame_length, stream.read(frame_length)))` (`:140-151`). A non-negative result means "keyword heard" → close the stream, run one listen turn, speak, reopen (`:116-134`). Detection runs on ~1% of one core because the model is a tiny CNN over 32 ms frames.

**How Tolesh does it now.** Every captured utterance goes through Whisper, then `is_wake_word_detected()` on the transcript (`listener.py:746-754`), with an LLM/embedding judge to decide if it was addressed to Tolesh. This is accurate but expensive: **the microphone is transcribed continuously**, which is both a battery/CPU cost and the single largest privacy surface in the product.

**The exact seam.** `listener.py:1522-1552`: `_audio_frames()` already slices the stream into fixed `self._frame_samples` frames at native rate, and `_on_audio()` pushes raw chunks to `self._audio_q`. A gate belongs between them — run the detector on each frame, and only start enqueuing for Whisper once it fires, keeping a 300 ms pre-roll ring buffer so the first phoneme of the command is not clipped. That is a contained change to one class, not a re-architecture.

**Dependency decision — this is where the brief's "check for a lighter alternative" matters.**

| Option | Licence | New deps | Key needed | Verdict |
|---|---|---|---|---|
| `pvporcupine` (what Jarvis uses) | Apache-2.0 wrapper on PyPI; **the keyword models are Picovoice's, and v3+ requires an access key and their service terms** | `requests` only | **Yes** | Lightest runtime, but adds an account + third-party terms to a local-first product |
| `openwakeword` 0.6.0 | Apache-2.0 | `onnxruntime`, `scipy`, `scikit-learn`, `tflite-runtime` (Linux) ≈ 60–90 MB | No | **Recommended default** — genuinely free, pip-installable, cross-platform, custom "Tolesh" model trainable |
| `vosk` limited-vocabulary | Apache-2.0 | `vosk` + ~40 MB model | No | Viable, but duplicates STT that Tolesh already has |

**Recommendation:** a `HotwordDetector` protocol in a new `listening/hotword.py` with `openwakeword` as the default backend and Porcupine as an optional one, behind a setting that defaults to `off`. Keep the existing transcript-level check as the fallback whenever no detector is installed — that is what makes the feature safe to ship incrementally. **Never** make the gate mandatory: a false negative silently breaks voice for the user.

**Risk:** `tflite-runtime` has no wheels for some Python/arch combos; the ONNX path must be the primary and `tflite` optional. Must be verified on the target Zorin OS box before promising it.

**Complexity: medium — 3–5 days** (interface, two backends, pre-roll, settings, threshold tuning, tests with a stub detector).

### 4.3 Retry, backoff and escalation for jobs

**How Jarvis does it.** Two independent mechanisms. `@retry(attempts=3, interval=2, exclude_exc=...)` as a decorator (`modules/retry/retry.py:12-60`), applied to the DB writers. And, for the always-on loop, a `FAIL_SAFE` dict counting failures per task name with three escalation levels — warn below 3, error at 3, **notify the user and stop rescheduling past 10** (`api/background_task/task.py:25-78`).

**What Tolesh does.** `_execute` catches everything, writes `status="failed"` and `error`, and `_finish` reschedules an interval job unconditionally (`scheduler.py:305-341`). So a permanently broken job retries forever, silently, every interval — and the user only finds out by opening the Jobs tab.

**Reuse:** the escalation *policy*, not the code. Concretely: `attempts` column, exponential backoff capped (e.g. `min(60 * 2**attempts, 3600)`), and after N consecutive failures flip the job to `paused` with a `notifications` row. Tolesh already has the notifications table, so the escalation target exists.

**Complexity: low — half a day.**

### 4.4 Telegram channel

**How Jarvis does it.** Long-polling (`bot.poll_for_messages`, `bot.py:297`) or webhook (`telegram/webhook.py`). Every inbound update is authenticated by **both** `chat.id in bot_chat_ids` **and** `username_is_valid(username)` (`bot.py:373-397`); failures get an explicit 401 message. Voice notes are downloaded, format-converted per OS, transcribed, and the reply is synthesised back to audio and sent as a voice message (`bot.py:487-537`). Documents are restricted to an allowlist of logs/YAML/uploads (`file_handler.py:41-60`).

**Why it is valuable for Tolesh specifically.** Tolesh already has the hard parts — local STT, local TTS, an agent loop, memory, jobs. Telegram is the cheapest credible path to "the companion in your pocket" without writing an Android client, and the brief lists Android as a target.

**What must change vs Jarvis.** Do **not** copy `_make_request` + manual `requests` calls (`bot.py:148-235`); use `python-telegram-bot` or `httpx` against the Bot API directly — either is fine, but the polling loop must live on its own thread with a stop event, not inside the job scheduler. Auth must be a configured allowlist of numeric chat IDs only (usernames are mutable and were the weaker half of Jarvis's check). Inbound text must go through the **same** chat pipeline as the web UI so memory extraction and conversation history apply — that is the whole point.

**Security:** the bot token is a bearer credential for the user's whole assistant; it belongs in the encrypted connector store Tolesh already has (`connectors/store.py`), not in plaintext settings.

**Complexity: medium — 3–4 days** (transport, allowlist, text+voice round trip, wiring to the chat service, tests with a fake transport).

### 4.5 Gated desktop OS controls

**How Jarvis does it.** `volume.py:13-25` shells to OS mixers, `pybrightness` for brightness, `pywslocker` for lock, `os.system("shutdown /r /t 1")` / `sudo -S reboot` for power (`controls.py:24-62,209-241`) — the last one piping a root password from an env var into `sudo -S`.

**What Tolesh has.** A complete, unused skeleton: `TOOL_POLICY` already classifies `shutdown`/`restart` as `CONSEQUENTIAL` and `ComputerTools.execute` returns an explicit refusal (`tools_ext/computer.py:26-29,52-56`).

**What to actually build.** Only the *low-risk, high-frequency* subset — `set_volume`, `set_brightness`, `lock_screen` — as `ELEVATED` tools that still require a confirm token. **Do not** implement `run_command`, `delete_file`, `send_message`, or power control: the refusal in `computer.py` is a deliberate, documented safety decision and importing Jarvis's `shell=True` implementations would undo it. Per-OS adapters: `pactl`/`amixer` (Linux), `osascript` (macOS), `nircmd`/COM (Windows); brightness via `brightnessctl`/`xrandr` (Linux), `brightness` (macOS).

**Complexity: low–medium — 2 days**, mostly adapter testing.

**Runner-up, mentioned for completeness:** camera snapshot + live view (`surveillance.py:145-206` MJPEG with a single-use OTP). Real capability, but it is a biometric-adjacent always-on sensor; if built, it must be off by default, require an explicit per-session grant, and never auto-upload. Not in the top five.

---

## 5. Architecture compatibility

**Compatible, adopt:**

- Both are FastAPI + uvicorn with a bearer-token guard, so a new route family slots in without touching the proxy chain.
- Both target Python 3.10/3.11 — no runtime conflict.
- Jarvis's cron module is dependency-free, so `numpy<2`, `pydantic 2.11–2.13`, `fastapi>=0.115` pins in Tolesh's `requirements.txt` are untouched.
- Jarvis's `retry` and `FAIL_SAFE` are ~80 lines of policy with no imports beyond stdlib.

**Incompatible, do not adopt:**

- **Process-per-subsystem.** Jarvis forks `jarvis_api` and `background_task` as separate `Process` objects and persists a PID map to disk (`process_map.py:31-48`). Tolesh runs one process with SQLite WAL and a thread scheduler; adding a process tree would break the in-process `AppState` that every route closes over, and would require inter-process locking around the same SQLite file.
- **YAML as primary state.** Jarvis keeps alarms, reminders, automation, tasks, crontab, smart devices and restrictions in `fileio/*.yaml`, re-reading them every 3 s (`task.py:169-182`). Tolesh's SQLite tables give atomic claims and history; migrating any of that to YAML would be a regression.
- **Keyword-table intent routing** (`conditions/keywords.py`, 245 lines of `{function: [trigger words]}`). This is Jarvis's substitute for an LLM, and importing it would create a second, competing router. The Ollama *response cache* behind it (`transformer/gpt.py:48-116`) is also unsuitable: it replays a stored answer when a new prompt is textually similar, which is only safe for stateless Q&A — in Tolesh every reply depends on conversation state and retrieved memory, so a replay would be a stale-answer bug.
- **Cloud STT.** `recognize_google` contradicts Tolesh's local-first posture and its offline story.
- **PySide6 / matplotlib visualisers.** Tolesh already ships a Qt shell; a second Qt widget tree and a matplotlib process for a mic level is strictly worse than a WebAudio `AnalyserNode` in the existing workspace.

**Platform notes:** Jarvis's app launch is macOS/Windows only and says so (`others.py:72-74`). Tolesh must keep per-OS adapters for anything device-facing; there is no single portable implementation in Jarvis to inherit. Android is out of scope for every item here — Jarvis has no Android support either.

---

## 6. Dependency, licence and security assessment

### Licences

| Item | Licence | Obligation if adapted |
|---|---|---|
| Jarvis repository | **MIT** [verified, `LICENSE`] | Retain copyright + permission notice. Tolesh already has `ATTRIBUTION.md` and a `LICENSES/` directory — the notice goes there, and the ported file keeps a header naming the origin and commit. |
| `expression.py` (to be ported) | MIT (same repo) | as above |
| `pvporcupine` | PyPI classifier says Apache-2.0 (read from PyPI metadata); the **keyword models are Picovoice's and v3+ requires an access key** — their service terms apply **[assumed]**, I did not read them | Not redistributable on those terms → optional backend only |
| `openwakeword` | Apache-2.0 [verified] | Notice only |
| `face_recognition` / `dlib` | MIT / Boost | not adopted |
| `opencv-python-headless` | Apache-2.0 [verified] | only if camera is built |

### Security defects found **in Jarvis** (reasons not to copy its executors)

| Finding | Evidence | Consequence |
|---|---|---|
| Command injection from transcribed speech | `os.system(f"start {keyword}")` — `others.py:92`; `os.system(f"start {filename}")` — `others.py:400` | A mis-transcribed phrase becomes a shell command on Windows |
| Shell interpolation of device paths | `subprocess.check_output(f"v4l2-ctl --device={cam.strip()} --all", shell=True)` — `camera/camera.py:95,108` | Path from device enumeration reaches a shell |
| TLS verification disabled | `requests.post(..., verify=False)` — `modules/audio/speaker.py:64` | TTS response is unauthenticated |
| Root password piped into sudo | `os.system(f"echo {models.env.root_password} \| sudo -S reboot")` — `controls.py:54-55` (same at `:233-234` for shutdown) | Password on a command line, visible in the process table |
| Cron file is arbitrary code execution | `subprocess.call(command, shell=True)` with the cron line's 6th field — `executors/crontab.py:34-46` | Any writer to `crontab.yaml` owns the machine |
| `assert` used for control flow | `process_map.assert_process_names()` — `process_map.py:24-28` | Silently vanishes under `python -O` |
| No encryption at rest | `modules/database/database.py:1-60` — plain `sqlite3.connect` | Tokens and chat IDs sit in plaintext |

**Tolesh already beats Jarvis on every one of these** except encryption at rest, which neither has. Nothing in this plan should be allowed to weaken `permissions/policy.py` or the `PermissionGate`.

### New dependencies proposed

| Addition | Weight | Justification |
|---|---|---|
| none (cron) | 0 | pure stdlib port |
| `openwakeword` + `onnxruntime` (+`scipy`,`scikit-learn`) | 60–90 MB | optional extra, only if the user enables hotword |
| `python-telegram-bot` **or** plain `httpx` | 0 (httpx already present) | prefer `httpx` — no new dep |
| none (OS controls) | 0 | `pactl`/`osascript`/`nircmd` are subprocess calls |

Net new mandatory dependencies across all five: **zero**. Everything heavy is an optional extra.

---

## 7. Prioritised roadmap

Ranked by user value ÷ (feasibility × maintenance cost), with the brief's eight named areas mapped in.

| # | Capability | Value | Feasibility | Risk | Effort | Brief area |
|---|---|---|---|---|---|---|
| **P0** | **Cron scheduling** for jobs | High — the single most-requested shape of "run this every weekday at 8" | High — dependency-free port, additive migration | Low | 1 d | 8 |
| **P0** | **Job retry / backoff / escalation** | High — today a broken job fails silently forever | High — 40 lines + 1 column | Low | 0.5 d | 3 |
| **P1** | **Audio-level hotword gate** | Very high — cuts always-on transcription cost and privacy surface | Medium — new dep, tuning | Medium | 3–5 d | 1, 2 |
| **P1** | **Telegram channel** | Very high — remote/mobile access with voice in and out | Medium | Medium (new credential surface) | 3–4 d | 4 |
| **P2** | **Gated OS controls** (volume/brightness/lock) | Medium — frequent, low-risk desktop actions | High — policy skeleton exists | Low | 2 d | 4 |
| **P2** | **Mic level visualisation** | Medium — makes the listener state legible | High — `AnalyserNode`, no backend work | Low | 0.5 d | 1 |
| **P3** | Camera snapshot + live view | Medium, niche | Medium | High (privacy) | 2–3 d | 6 |
| **P3** | Event-driven triggers (time-of-day + weekday) | Low — cron covers most of it | High | Low | 0.5 d | 8 |
| — | Face recognition | Low | Low (dlib build pain) | High (biometrics) | — | — |
| — | Smart-home / lights / TV / thermostat | None for this product | — | — | — | — |

Suggested order: **P0 together (1.5 days) → P1 hotword → P1 Telegram → P2 controls + visualisation.** P0 ships first because it is small, self-contained, and immediately visible in the Jobs tab.

---

## 8. Exact Tolesh files each change touches

### P0-a Cron
| File | Change |
|---|---|
| `src/tolesh/jobs/cron.py` | **new** — `CronExpression` ported from `expression.py` @ `7613d5b`, MIT header, `InvalidArgument`→`JobError`, plus `next_after(ts)` helper |
| `src/tolesh/jobs/scheduler.py` | `_create_tables` `:53-85` — add `cron TEXT`, `last_cron_minute TEXT`; `create()` `:106-133` — accept `schedule="cron"` + validate via `CronExpression`; `_finish()` `:322-341` — third branch computing `next_run_at = cron.next_after(now)`; `_claim()` `:261-279` — minute-level double-fire guard |
| `src/tolesh/server/schemas.py` | `JobCreate` / job patch — add `cron: str \| None`, widen the `schedule` literal |
| `src/tolesh/server/app.py` | job routes — surface `cron`, map `JobError` to 400 |
| `web/js/workspace.js` | Jobs tab — schedule picker gains "cron" + expression field with live preview |
| `tests/test_tolesh_jobs.py` | new cases (see §9) |
| `ATTRIBUTION.md`, `LICENSES/` | MIT notice for the ported file |

### P0-b Retry
| File | Change |
|---|---|
| `src/tolesh/jobs/scheduler.py` | `_create_tables` — `attempts INTEGER DEFAULT 0`, `max_attempts`; `_execute` `:280-320` — increment on failure; `_finish` `:322-341` — backoff `min(60*2**attempts, 3600)`, pause + notify past `max_attempts` |
| `src/tolesh/server/schemas.py` | `max_attempts` on create/patch |
| `web/js/workspace.js` | job row shows attempt count and next retry time |

### P1 Hotword
| File | Change |
|---|---|
| `src/tolesh/listening/hotword.py` | **new** — `HotwordDetector` protocol, `OpenWakeWordDetector`, `PorcupineDetector`, `NullDetector` |
| `src/tolesh/listening/listener.py` | `_on_audio` `:1535-1550` — feed frames to the detector before `_audio_q.put_nowait`; `_audio_frames` `:1522-1533` — 300 ms pre-roll ring buffer; `_process_transcript` `:746-754` — keep transcript check as fallback |
| `src/tolesh/config.py` | new keys near `:180-208` and defaults near `:583-613`: `hotword_backend`, `hotword_threshold`, `hotword_model_path` |
| `src/tolesh/settings/store.py` | mirror the same keys so the workspace Settings tab can write them |
| `requirements.txt` | optional extras block; `installer/` notes |
| `web/js/workspace.js` | Settings → Voice: backend select, threshold slider, "test detector" button |
| `tests/test_hotword.py` | **new** — stub detector, no model download |

### P1 Telegram
| File | Change |
|---|---|
| `src/tolesh/channels/__init__.py`, `src/tolesh/channels/telegram.py` | **new** — polling thread, chat-ID allowlist, text→chat pipeline, voice→STT→chat→TTS→voice |
| `src/tolesh/server/app.py` | start/stop the poller in the lifespan next to `self.jobs`; `GET/POST /api/channels/telegram` |
| `src/tolesh/connectors/store.py` (reuse) | store the bot token encrypted, not in plain settings |
| `src/tolesh/settings/store.py` | enabled flag, allowed chat IDs |
| `web/js/workspace.js` | Settings → Channels card |

### P2 OS controls
| File | Change |
|---|---|
| `src/tolesh/permissions/policy.py` | `:21-34` — add `set_volume`, `set_brightness`, `lock_screen` as `ELEVATED` |
| `src/tolesh/tools_ext/computer.py` | `:52-56` — implement the three; **keep** `run_command`/`shutdown`/`restart` in `_NOT_IMPLEMENTED` |
| `src/tolesh/tools_ext/system_adapters.py` | **new** — Linux/macOS/Windows backends with capability probing |
| `src/tolesh/tools/registry.py` | `:30-31` — expose as tools |

### P2 Visualisation
| File | Change |
|---|---|
| `web/js/voice.js` | `AnalyserNode` + canvas tied to listener state; delete the "not a simulated waveform" caveat at `:5` |
| `web/styles/` | level bar / ring styles |

---

## 9. Implementation and testing strategy

**Order of work.** Each step lands independently and is releasable on its own: cron → retry → hotword → telegram → controls → visualisation.

**Cron — the tests that actually catch bugs.** Do not test "it parses". Test:
1. **Vectors**: `*/15 * * * *`, `0 9 * * 1-5`, `0 0 1 * *`, `30 2 * * 0#2`, `0 0 L * *`, `0 0 15W * *`, `0 0 * * 7` (→ Sunday), `@daily`, `0 0 * jan-mar *`. Assert `check_trigger` at the matching minute and at ±1 minute.
2. **Idempotence**: for a random minute, `check_trigger` is true for exactly the fields' intersection — a property test over 10,000 random minutes against a naive brute-force expander written in the test.
3. **Double-fire guard**: two `_tick_once()` calls inside the same minute produce one run.
4. **Restart**: create a cron job, close the scheduler, reopen on the same DB, assert `next_run_at` is still in the future and the job is not lost.
5. **Bad input**: `"* * * *"` (4 fields), `"60 * * * *"`, `"* * * * 9"` all raise `JobError` with a 400 from the route.

**Hotword.** Unit-test the gate with a **stub detector** that fires on a magic frame — this proves the plumbing (pre-roll, state transition, no Whisper call before the trigger) without downloading a model, which keeps CI offline. Accuracy is a manual, documented procedure: 50 utterances × 3 distances, report false-accept and false-reject, and ship with the threshold that minimises false-reject (a missed wake is worse than an extra Whisper call).

**Telegram.** Fake transport returning canned `getUpdates` payloads; assert unauthorised chat IDs get exactly one refusal and never reach the chat pipeline; assert a voice note produces a transcript, a reply, and an audio response.

**OS controls.** Assert every tool still returns `pending-confirmation` without an approval token, and that `run_command`/`shutdown` remain refused — this is a regression test protecting the existing safety decision.

**Whole-suite gate.** Tolesh runs `DISPLAY=:99 PYTHONPATH=src python3.11 -m pytest -q` (currently 6,244 passed / 0 failed). Every step above must keep that green, and `scripts/audit_features.py` gains one check per new route.

**Migration discipline.** Additive `ALTER TABLE` only; no data loss; every new column nullable with a default so an old DB opens cleanly.

---

## 10. Final recommendation — the five most valuable Jarvis capabilities

1. **Cron expression scheduling** (`modules/crontab/expression.py`). The clearest capability gap in Tolesh — jobs can only say "once" or "every N seconds". The parser is 335 lines of MIT, dependency-free stdlib code, and the port is a net *security improvement* because Tolesh runs goals, not shell strings. **1 day.**
2. **Audio-level wake-word detection** (`main.py:90-184`). Turns "always transcribing" into "always listening, occasionally transcribing". Biggest single win for CPU, battery and privacy. Adopt the *pattern* with `openwakeword` as the default backend, not Porcupine's key. **3–5 days.**
3. **Failure escalation for background work** (`api/background_task/task.py:25-78` + `modules/retry`). Cheap, and it closes a real silent-failure hole in Tolesh's job scheduler today. **0.5 day.**
4. **Telegram channel with voice in and out** (`modules/telegram/bot.py:373-537`). The cheapest credible route to a companion that travels with the user, reusing Tolesh's existing local STT/TTS and chat pipeline. Harden the auth beyond Jarvis's chat-ID-plus-username check. **3–4 days.**
5. **Gated desktop OS controls** — volume, brightness, screen lock (`executors/volume.py`, `controls.py:93-101`). Fills the one obviously missing "assistant can touch the machine" capability, on top of the permission skeleton Tolesh already has, without importing any of Jarvis's `shell=True` code. **2 days.**

Together: **~7.5 engineering days for P0+P1**, zero new mandatory dependencies, no change to the model router, memory, MCP, agents or task model, and every item additive.

**What I would not take, and why:** the process-per-subsystem architecture (conflicts with Tolesh's single-process SQLite design), YAML-as-database (regression), the keyword-table intent router (would compete with the LLM), cloud STT (contradicts local-first), face recognition (biometric cost, no product need), and smart-home drivers (no target hardware).

Tolesh does not become Jarvis here. It gains a scheduler that understands calendars, a microphone that only wakes when spoken to, background work that reports its own failures, a channel that leaves the desk, and hands that can reach the volume slider — all inside the architecture it already has.
