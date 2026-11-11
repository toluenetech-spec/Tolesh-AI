# Production-readiness and security audit

Scope: the Tolesh application as it stands after the six Jarvis-inspired phases,
with particular attention to what those phases added — cron schedules, retry and
backoff, the audio wake-word gate, the Telegram channel, permission-gated system
controls, and the microphone visualisation.

Method: read the code, then try to break it. Every finding below was reproduced
before it was fixed, and every fix has a regression test that fails against the
old code. Where something could not be tested here — a real Telegram bot, a real
microphone, macOS and Windows — it is listed as outstanding rather than assumed.

---

## 1. Vulnerabilities found and fixed

### 1.1 The API bearer token was compared with `!=`

`src/tolesh/server/app.py`, the `token_guard` middleware compared the supplied
header with `supplied != f"Bearer {api_token}"`. Python stops comparing at the
first differing byte, so the time a rejection takes leaks how much of the token
was guessed correctly — enough to recover a token one character at a time given
enough samples.

**Fixed** with `hmac.compare_digest`. Regression test asserts behaviour (wrong
token, prefix of the token, missing token all 401; correct token 200) and the
source no longer contains the non-constant-time form.

### 1.2 An approval could be spent on a different action

`ComputerTools.invoke(..., approved=...)` accepted a **dict** and passed it
straight through:

```python
else:
    decision = approved          # caller-supplied, unchecked
return self.execute(tool, args, risk=decision.get("risk", ...))
```

Nothing in the HTTP layer passed `approved`, so it was not reachable from the
network — but the public signature of the tool layer let any caller hand itself
`{"status": "approved"}` and run `write_text_file`, and it let an approval
issued for one argument set be spent on another (`set_volume 10` approved,
`set_volume 100` executed). A future caller would have inherited the hole.

**Fixed**: `approved` is now an *identifier*. The gate's own stored row is the
only source of truth — the id is looked up, the status must be `approved`, and
the tool and argument set must match exactly (compared as canonical JSON, so
`30` and `"30"` are not the same approval). Regression tests cover a forged
dict, an unknown id, a widened argument, a different tool, an unapproved
pending action, an expired approval, and a replayed approval.

### 1.3 A Telegram download had no size limit

`HttpTransport.download` returned `response.content` whole. The size of a voice
note is decided by the remote end, so a compromised or misbehaving bot API could
make the process hold an arbitrarily large body in memory — before the
transcriber's own 25 MB check ever ran, because that check happens after the
bytes are already in RAM.

**Fixed**: a 20 MB cap, checked against `Content-Length` before the body is kept
and against the actual length afterwards.

### 1.4 Two allowlisted Telegram chats shared one memory store

Conversations were already isolated per chat, but memory was not:
`_run_turn` retrieves through `memory_service.context_for(query, project_id)`,
and `eligible()` only hides memories filed under a *different* project. Two
consequences:

* memories written during a Telegram turn were global (`project_id=None`), so
  they were retrievable from every chat and from the desktop UI;
* a global memory — "Ada's bank PIN is 4321" — was visible to any allowlisted
  chat.

For a single owner with one chat this is a feature. For the configuration the
brief asks about — more than one authorised person — it is a leak.

**Fixed** with per-chat projects and a memory scope:

* each chat gets a project of its own (`Telegram <chat id>`), created on first
  message; its conversation and its memories live inside it;
* `ChatRequest.memory_project_only` makes retrieval *strict*: only memories
  filed under that project are candidates, so a global memory is not "global"
  on a channel a second person can reach;
* `telegram_settings.memory_scope` is `"chat"` by default and can be set to
  `"shared"` by an owner who wants one memory across their own devices.

The `telegram_chats` table gained a nullable `project_id` column, added with
`ALTER TABLE`, so a chat that predates this keeps its conversation.

### 1.5 The Telegram poller could busy-loop

Measured, not assumed: against a transport that answers an empty poll instantly,
the loop ran **199 polls in 2 seconds** (~100/s of pure CPU). A real long poll
blocks for its timeout so this does not happen in normal operation — but a proxy
that strips the timeout, or an API that returns early, turns the loop into a
space heater.

**Fixed**: an empty poll now waits 500 ms. Re-measured: **4 polls in 2 seconds**.

### 1.6 Two pollers could exist during a restart

`TelegramService.stop()` released its lock before joining the channel thread. A
`start()` arriving in that window built a second channel while the first was
still draining a long poll, and both would fetch and answer the same updates.

**Fixed**: the join happens while the lock is held. A test drives `start`/`stop`
against a slow transport and asserts the maximum number of concurrent polls
never exceeds one.

---

## 2. Reliability issues found and fixed

### 2.1 A job interrupted by a crash was lost forever

A job is claimed by flipping `status` to `running` in one `UPDATE`, which is
what stops two ticks from running it twice. The price was never paid: a process
killed mid-run left the row `running` forever. Nothing claims a `running` row,
so the job never fired again, the UI showed work that was not happening, and the
job was silently discarded — exactly what "do not silently discard failed jobs"
forbids.

**Fixed** with `JobScheduler.recover_interrupted()`, called at construction. A
new process owns no running job, so every `running` row is stale by definition:

* the job returns to `scheduled` with the interruption recorded, and its
  `next_run_at` is left in the past so the missed run happens now;
* its `job_runs` row is closed as `interrupted`;
* it counts as an attempt, so a job that takes the process down with it is
  paused after `max_attempts` instead of restarting forever.

### 2.2 A DST fall-back could schedule the next run in the past

`next_after_ts` converted an epoch to naive local time, computed the next
matching wall-clock minute, and converted back. On the autumn fall-back the same
wall-clock minute happens twice, and a naive datetime built from components
resolves to the *first* occurrence. A job that had just fired at the second
01:30 was therefore given a next run of the first 01:30 — an instant already in
the past — and would fire again immediately, in a loop, until the offset
changed.

**Fixed**: the result is walked forward until it is genuinely after the input,
with a bounded guard. Tests run under `TZ=Europe/London` and assert the next run
is strictly in the future across both the fall-back and the spring-forward.

### 2.3 Permanent failures were retried

A revoked key or a deleted model produces the same error in sixty seconds. The
retry logic treated every failure as transient and spent all `max_attempts`
arriving at the same answer.

**Fixed**: `is_permanent()` matches a deliberately narrow set of errors (401,
403, 404, unauthorised, forbidden, invalid API key, authentication failed,
permission denied, model not found, unknown model, does not exist, insufficient
quota/credit, invalid model). A permanent failure pauses immediately and the
notification says "will not be retried" rather than "failed attempts". The
patterns are narrow on purpose: a false positive pauses a job that would have
recovered, which is worse than one wasted retry.

### 2.4 Shutdown waited for a whole long poll

`TelegramChannel.stop()` joined with `poll_timeout + 5` seconds — up to 15 s of
apparent freeze when quitting while the bot was running. An HTTP request in
flight cannot be cancelled from another thread.

**Fixed**: a short join, then the transport is closed to break the socket, then
a short second join. The thread is a daemon and checks its stop flag before
handling any update, so nothing is answered after `stop()` returns. Measured:
`stop()` against a transport that blocks for 3 s returns in under 2.5 s.

---

## 3. Checked and found sound

| Area | What was checked | Result |
| --- | --- | --- |
| SQL injection | every `execute()` in `src/`; the only f-strings interpolate `?` placeholders and `int()`-cast limits | no injection path |
| Path traversal | artifact, media and static routes; `is_path_allowed` on every tool path | contained |
| Shell injection | `system/controls.py` builds argv lists from fixed executables and a validated 0–100 integer | no `shell=True` anywhere in `src/` |
| Telegram token handling | stored via `ConnectorStore` (Fernet, 0600 key); every API response reports only `has_token`; `HttpTransport` scrubs 401 messages; grep for token in log lines and responses | no leak |
| Allowlist ordering | the chat-id check runs before any parsing, storage or reply; ids compared as integers; `True` is rejected as a chat id | enforced |
| Unauthorised tool use | a rejected chat never reaches `_run_turn`; and `_run_turn` has no tool loop at all | not reachable |
| Confirmation tokens | expired, reused, forged, missing and wrong-token cases; binding to the exact tool and arguments | enforced |
| Dangerous tools | `shutdown`, `restart`, `run_command`, `delete_file`, `send_message`, `network_request` remain CONSEQUENTIAL and refused even after approval | unchanged |
| Concurrent claims | 16 threads calling `_tick_once` on one due job; two schedulers on one database | one execution |
| Duplicate calendar fire | 8 ticks inside the same minute | one execution |
| Long jobs | a job that outlives its interval | no self-overlap; next run from the finish |
| Missed runs | a cron job three hours overdue | one catch-up run, not a backlog replay |
| Thread lifecycle | `AppState` create/close, `jobs.start()` × 3, app lifespan with Telegram enabled | no leaked or duplicated threads |
| Migration | real pre-Phase-1 schema, four consecutive opens, `PRAGMA integrity_check` | no data loss, no corruption |

---

## 4. Measurements

Produced by `scripts/measure_runtime.py` on this machine (Linux, Python 3.11.1,
CPU-only). Synthetic audio, so this measures cost, not accuracy.

| What | Result |
| --- | --- |
| openWakeWord, 60 s of audio | 1.99 s wall / 1.99 s CPU → **real-time factor 0.033 (3.3 % of one core)**, 80 ms chunks |
| openWakeWord, resident memory after load | **+225 MB** (onnxruntime plus the model set) |
| Scheduler idle, 3 s, no jobs | 0.7 ms CPU (**0.02 %** of one core) |
| Telegram polling a fake transport, 2 s | 4 polls, 1.8 ms CPU (was 199 polls / 4.9 ms before the empty-poll fix) |
| Full `AppState`, 2 s idle | 0.5 ms CPU (0.02 %), one `tolesh-jobs` thread, +20 MB RSS |
| Orb, 600 particles at 68 px | one `drawImage` per particle, two depth passes, no allocation per frame |

The 225 MB is the honest cost of the wake-word gate and is why it is off by
default: enabling it is a deliberate trade of memory for not sending every
frame to Whisper.

---

## 5. Tests added

80 new tests, all of which fail against the pre-audit code where applicable.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/test_security_audit.py` | 28 | constant-time token, gate binding, download cap, chat isolation, restart races |
| `tests/test_jobs_reliability.py` | 22 | concurrent ticks, crash recovery, crash-loop pause, missed runs, long jobs, month/year/leap boundaries, DST, permanent errors, retry persistence |
| `tests/test_lifecycle.py` | 9 | thread counts, duplicate workers, shutdown latency, no reply after stop, disabled detector costs nothing, gate cheaper than real time |
| `tests/test_migrations.py` | 13 | legacy schema, idempotent migration, data survival, concurrent updates, integrity check |
| `tests/test_orb.py` | 8 | particles not a disc, level→brightness, four states, reduced motion, `destroy()`, accessibility |

Final results:

* full suite — **6555 passed, 27 skipped, 2 deselected, 0 failed** (7 m 17 s)
* feature audit — **56 passed, 0 failed, 4 gated** (`docs/feature-audit.json`)

---

## 6. The orb

The supplied mockup — a sphere of neon cyan particles with a bright irregular
rim on a dark ground — is now the voice orb, drawn in `web/js/orb.js` rather
than shipped as a still. A still cannot react, and the previous implementation
spun a PNG on a CSS timer while the ring beside it claimed to show a level.

* ~600 particles on a lat/long lattice, so the dot rows read like the mockup;
* additive compositing, so overlapping dots add light instead of averaging to
  grey — the first attempt looked like a dusty ball for exactly that reason;
* a pre-rendered radial sprite stamped per particle (round, soft, cheap);
* radius, jitter and brightness driven by the measured RMS; `speaking` and
  `paused` are drawn differently because they are different facts;
* `prefers-reduced-motion` stops the rotation and redraws only on change;
* `destroy()` ends the frame loop, verified by comparing pixels before and
  after rather than by trusting a flag.

Browser tests read the canvas pixels: the field is not a filled disc, green and
blue dominate red (it is cyan), a loud level emits measurably more light than
silence, and the four states are distinguishable.

---

## 7. Hardware and external integration — what is **not** verified

Nothing below was tested, and nothing below is claimed to work.

### Microphone and wake word

* **Not tested**: a physical microphone, a human saying the wake word, false
  accept and false reject rates, behaviour in a noisy room, a USB microphone
  unplugged mid-session, macOS/Windows audio stacks.
* **Tested**: the detector scores 60 s of synthetic audio at 3.3 % of a core;
  the gate is off by default and constructs nothing when disabled; a failing
  backend falls back to transcript matching; the listener releases the device on
  every exit path; the browser meter's level rises with a louder tone and the
  device is released on stop.

Manual checklist before relying on the wake word:

1. Enable it in Settings, run **Test detector**, confirm the backend and model
   name are reported.
2. Say the wake word ten times at normal volume; note how many are heard.
3. Repeat with a podcast playing at conversation volume; note false accepts.
4. Unplug the microphone while listening; confirm the orb stops and a message
   explains why, with no stack trace and no stuck "listening" state.
5. Restart Tolesh with the feature enabled; confirm it comes back enabled and
   does not double-load the model.

### Telegram

* **Not tested**: a real bot token, real `getUpdates` long polling, a real voice
  note round trip (Ogg Opus → faster-whisper → reply → `sendVoice`), Telegram
  rate limits, a real network drop and reconnect.
* **Tested** with a fake transport: allowlist rejection before parsing, per-chat
  conversation and project creation, text reply, voice download → transcribe →
  answer → synthesised reply, transport errors and backoff, clean shutdown, no
  reply after stop, and the API's token/allowlist/scope handling.

Manual checklist with a real bot:

1. Store a token from @BotFather, press **Test**; confirm the bot's username is
   reported and the token is not echoed.
2. Send `/id`, add that id to the allowlist, send a message; confirm a reply.
3. From a second account, message the bot; confirm silence and that the
   rejected counter increments.
4. Send a voice note; confirm a voice reply (or a text reply plus a stated
   reason if TTS is unavailable).
5. Disable the network for 30 s; confirm polling backs off and recovers without
   duplicate replies.

---

## 8. Remaining risks and blockers

1. **`TOLESH_API_TOKEN` is optional.** With no token set, the API is open to
   anything that can reach the port. The server binds `127.0.0.1` by default,
   which is the mitigation — but binding it to `0.0.0.0` without a token would
   expose every route, including the Telegram allowlist.
2. **No rate limiting.** A local single-user app has little to protect, but
   `/api/chat` and `/api/tools/invoke` have no throttle. Out of scope for this
   audit; a reverse proxy is the right place for it.
3. **The wake-word gate costs 225 MB.** Not a bug, but it must be stated: a
   machine with 2 GB free will notice.
4. **Backoff never delays past the job's own next run.** Deliberate — a 5 s
   interval job retries in 5 s — but it means a job with a very short interval
   retries often.
5. **`%n` is not standard cron.** It is Jarvis's epoch-periodicity extension,
   kept for fidelity and labelled as such in the source.
6. **macOS `lock_screen` sleeps the display.** It only truly locks if "require
   password immediately" is set; the response says so rather than claiming a
   lock.
7. **Windows volume needs `pycaw`.** Without it the control reports
   `unavailable` instead of shelling out to an inline C# block.
8. **The cron scheduler is wall-clock based.** It follows the machine's local
   time; a user who changes timezone while a job is scheduled will see the job
   move. There is no per-job timezone field.
