"""Telegram as a second way in.

Polling, not webhooks: Tolesh runs on a laptop behind NAT, so a webhook would
need a public URL and a TLS certificate that the user does not have. One long
poll every few seconds is enough for a personal assistant and needs nothing
exposed.

Everything here goes through a ``Transport`` interface. The real one is a thin
httpx wrapper; the tests use a fake, so the suite never touches the network and
never needs a bot token.

Security posture, because this is the one channel reachable from the internet:

  * the bot token lives in the encrypted connector vault, never in the config
    file and never in a log line;
  * a numeric chat id must be on the allowlist *before* anything is parsed,
    stored or answered — an unknown chat is ignored, not told the bot exists;
  * the allowlist is compared as integers, so "123" and 123 are the same chat
    and a stringly-typed id cannot slip past;
  * each chat gets its own conversation, so two people talking to the same bot
    never see each other's context.
"""

from __future__ import annotations

import json
import time
import threading
from typing import Any, Callable

from .._sqlite import connect

__all__ = ["TelegramError", "Transport", "HttpTransport", "FakeTransport",
           "TelegramLinks", "TelegramSettings", "TelegramChannel",
           "TelegramService", "looks_like_token"]

API_BASE = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4000        # Telegram's own limit is 4096; leave a margin.
# A voice note is a few hundred kilobytes. The cap exists because the size of
# the download is decided by the remote end: without it, a compromised or
# misbehaving bot API could make Tolesh hold an arbitrarily large body in RAM
# before the transcriber's own 25 MB check ever runs.
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
# One connection id, so the vault holds exactly one bot token and replacing it
# replaces rather than accumulates.
BOT_CONNECTION_ID = "telegram:bot"


def looks_like_token(token: str) -> bool:
    """Telegram bot tokens are ``<bot id>:<secret>``; anything else is a typo."""
    token = (token or "").strip()
    if ":" not in token:
        return False
    head, _, tail = token.partition(":")
    return head.isdigit() and 20 <= len(tail) <= 200 and " " not in token


def _as_chat_id(value: Any) -> int | None:
    """Compare ids as integers, so ``"123"`` and ``123`` cannot disagree.

    A JSON payload from the network is not trusted to be well-typed: coercing
    here means the allowlist check and the conversation lookup always see the
    same number, and an id that is not a number at all is dropped rather than
    turned into ``0``.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TelegramError(RuntimeError):
    """The Telegram API refused the call, or could not be reached."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class Transport:
    """What the channel needs from Telegram. Implemented by httpx and by tests."""

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        raise NotImplementedError

    def send_audio(self, chat_id: int, audio: bytes, filename: str,
                   *, mime: str, voice: bool) -> dict[str, Any]:
        raise NotImplementedError

    def get_file(self, file_id: str) -> str:
        raise NotImplementedError

    def download(self, file_path: str) -> bytes:
        raise NotImplementedError

    def me(self) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class HttpTransport(Transport):
    """The real one. ``token`` is never included in an exception message."""

    def __init__(self, token: str, *, client: Any = None, base: str = API_BASE):
        if not token or ":" not in token:
            raise TelegramError("A bot token looks like 123456:ABC-DEF…")
        self._token = token
        self._base = f"{base.rstrip('/')}/bot{token}"
        self._file_base = f"{base.rstrip('/')}/file/bot{token}"
        self._owned = client is None
        if client is None:
            import httpx

            client = httpx.Client(timeout=45)
        self._client = client

    def _call(self, method: str, payload: dict[str, Any] | None = None,
              *, timeout: float | None = None) -> Any:
        try:
            response = self._client.post(f"{self._base}/{method}", json=payload or {},
                                         timeout=timeout)
        except Exception as exc:
            raise TelegramError(f"Telegram is unreachable: {type(exc).__name__}") from exc
        try:
            body = response.json()
        except Exception as exc:
            raise TelegramError(f"Telegram returned a non-JSON reply ({response.status_code})") from exc
        if not body.get("ok"):
            description = str(body.get("description") or "the request was refused")
            # 401 means the token is wrong; say so without echoing the token.
            if response.status_code == 401:
                description = "The bot token was rejected by Telegram."
            raise TelegramError(description, status=response.status_code)
        return body.get("result")

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout,
                                   "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, timeout=timeout + 15) or []

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        return self._call("sendMessage", {"chat_id": chat_id, "text": text,
                                          "disable_web_page_preview": True})

    def send_audio(self, chat_id: int, audio: bytes, filename: str,
                   *, mime: str, voice: bool) -> dict[str, Any]:
        field = "voice" if voice else "document"
        method = "sendVoice" if voice else "sendDocument"
        files = {field: (filename, audio, mime)}
        try:
            response = self._client.post(f"{self._base}/{method}",
                                         data={"chat_id": str(chat_id)},
                                         files=files, timeout=120)
        except Exception as exc:
            raise TelegramError(f"Telegram is unreachable: {type(exc).__name__}") from exc
        body = response.json()
        if not body.get("ok"):
            raise TelegramError(str(body.get("description") or "upload refused"),
                                status=response.status_code)
        return body.get("result") or {}

    def get_file(self, file_id: str) -> str:
        result = self._call("getFile", {"file_id": file_id})
        path = (result or {}).get("file_path")
        if not path:
            raise TelegramError("Telegram did not return a file path.")
        return path

    def download(self, file_path: str) -> bytes:
        try:
            response = self._client.get(f"{self._file_base}/{file_path}", timeout=120)
        except Exception as exc:
            raise TelegramError(f"Download failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise TelegramError(f"Download failed ({response.status_code}).")
        declared = response.headers.get("content-length")
        if declared and str(declared).isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
            raise TelegramError("That voice note is larger than 20 MB.")
        body = response.content
        if len(body) > MAX_DOWNLOAD_BYTES:
            raise TelegramError("That voice note is larger than 20 MB.")
        return body

    def me(self) -> dict[str, Any]:
        return self._call("getMe") or {}

    def close(self) -> None:
        if self._owned:
            try:
                self._client.close()
            except Exception:
                pass


class FakeTransport(Transport):
    """A scripted Telegram, for tests and for the offline self-check."""

    def __init__(self, *, updates: list[list[dict]] | None = None,
                 files: dict[str, bytes] | None = None):
        self.queues = list(updates or [])
        self.files = files or {}
        self.sent: list[dict[str, Any]] = []
        self.audio: list[dict[str, Any]] = []
        self.offsets: list[int | None] = []
        self.failures = 0
        self.closed = False

    def get_updates(self, offset, timeout):
        self.offsets.append(offset)
        if self.failures > 0:
            self.failures -= 1
            raise TelegramError("simulated network failure")
        if self.queues:
            return self.queues.pop(0)
        time.sleep(0.01)
        return []

    def send_message(self, chat_id, text):
        self.sent.append({"chat_id": chat_id, "text": text})
        return {"message_id": len(self.sent)}

    def send_audio(self, chat_id, audio, filename, *, mime, voice):
        self.audio.append({"chat_id": chat_id, "bytes": audio, "filename": filename,
                           "mime": mime, "voice": voice})
        return {"message_id": len(self.audio)}

    def get_file(self, file_id):
        return f"voice/{file_id}.oga"

    def download(self, file_path):
        return self.files.get(file_path, b"OggS-fake-audio")

    def me(self):
        return {"id": 1, "username": "tolesh_test_bot", "first_name": "Tolesh"}

    def close(self):
        self.closed = True


class TelegramLinks:
    """Which conversation belongs to which chat."""

    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect(db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS telegram_chats (
                chat_id INTEGER PRIMARY KEY, conversation_id TEXT,
                title TEXT, created_at REAL, updated_at REAL)""")
        # Additive: a chat that predates project scoping simply has no project
        # and keeps its old conversation, which is why this is a column rather
        # than a rebuild of the table.
        self._add_column("project_id", "TEXT")
        self._conn.commit()

    def _add_column(self, name: str, kind: str) -> None:
        # ``name`` and ``kind`` are literals written above, never network data:
        # SQLite does not accept parameters for identifiers, so this is the only
        # way to add a column, and the values never leave this module.
        columns = {row[1] for row in
                   self._conn.execute("PRAGMA table_info(telegram_chats)")}
        if name not in columns:
            self._conn.execute(f"ALTER TABLE telegram_chats ADD COLUMN {name} {kind}")

    def conversation_for(self, chat_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT conversation_id FROM telegram_chats WHERE chat_id=?",
            (int(chat_id),)).fetchone()
        return row[0] if row else None

    def project_for(self, chat_id: int) -> str | None:
        """The project this chat's memories and files belong to, if any."""
        row = self._conn.execute(
            "SELECT project_id FROM telegram_chats WHERE chat_id=?",
            (int(chat_id),)).fetchone()
        return (row[0] or None) if row else None

    def link(self, chat_id: int, conversation_id: str, title: str = "",
             project_id: str = "") -> None:
        now = time.time()
        self._conn.execute(
            """INSERT INTO telegram_chats (chat_id, conversation_id, title,
                                           project_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET conversation_id=excluded.conversation_id,
                 title=excluded.title, project_id=excluded.project_id,
                 updated_at=excluded.updated_at""",
            (int(chat_id), conversation_id, title[:120], project_id or None, now, now))
        self._conn.commit()

    def reset(self, chat_id: int) -> None:
        self._conn.execute("DELETE FROM telegram_chats WHERE chat_id=?", (int(chat_id),))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class TelegramSettings:
    """Whether the channel runs, and who is allowed to talk to it.

    Kept out of ``SettingsStore`` on purpose: that store holds a fixed set of
    typed keys and refuses anything else, and widening it for one channel would
    make every future channel a change to the settings contract. A two-row
    table beside the chat links keeps the channel self-contained.
    """

    KEYS = ("enabled", "allowlist", "memory_scope")
    SCOPES = ("chat", "shared")

    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS telegram_settings (key TEXT PRIMARY KEY, "
            "value TEXT, updated_at REAL)")
        self._conn.commit()

    def get(self) -> dict[str, Any]:
        rows = dict(self._conn.execute(
            "SELECT key, value FROM telegram_settings").fetchall())
        enabled = False
        allowlist: list[int] = []
        try:
            enabled = json.loads(rows.get("enabled") or "false") is True
        except Exception:
            enabled = False
        try:
            raw = json.loads(rows.get("allowlist") or "[]")
            if isinstance(raw, list):
                allowlist = [int(v) for v in raw if str(v).lstrip("-").isdigit()]
        except Exception:
            allowlist = []
        # Default is the isolated one. Sharing one memory across every
        # allowlisted chat is a convenience the owner can switch on, not
        # something a second person should receive by default.
        scope = "chat"
        try:
            stored = json.loads(rows.get("memory_scope") or '"chat"')
            if stored in self.SCOPES:
                scope = stored
        except Exception:
            scope = "chat"
        return {"enabled": enabled, "allowlist": allowlist, "memory_scope": scope}

    def _put(self, key: str, value: Any) -> None:
        self._conn.execute(
            """INSERT INTO telegram_settings (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                 updated_at=excluded.updated_at""",
            (key, json.dumps(value), time.time()))
        self._conn.commit()

    def set_enabled(self, enabled: bool) -> None:
        self._put("enabled", bool(enabled))

    def set_memory_scope(self, scope: str) -> None:
        if scope not in self.SCOPES:
            raise ValueError("Memory scope is 'chat' or 'shared'.")
        self._put("memory_scope", scope)

    def set_allowlist(self, chat_ids: list[int]) -> None:
        self._put("allowlist", [int(c) for c in chat_ids])

    def add_chat(self, chat_id: int) -> list[int]:
        current = self.get()["allowlist"]
        if int(chat_id) not in current:
            current.append(int(chat_id))
            self.set_allowlist(current)
        return sorted(current)

    def remove_chat(self, chat_id: int) -> list[int]:
        current = [c for c in self.get()["allowlist"] if c != int(chat_id)]
        self.set_allowlist(current)
        return current

    def close(self) -> None:
        self._conn.close()


class TelegramChannel:
    """Polls Telegram on its own thread and answers through the chat pipeline.

    ``reply(conversation_id, text)`` is injected by the server so this module
    never has to know how a chat turn is built.
    """

    def __init__(self, transport: Transport, *, links: TelegramLinks,
                 reply: Callable[[str, str], str],
                 allowlist: Callable[[], set[int]],
                 transcribe: Callable[[bytes], str] | None = None,
                 synthesize: Callable[[str], tuple[bytes, str]] | None = None,
                 poll_timeout: float = 10.0,
                 new_conversation: Callable[[str], str] | None = None,
                 new_project: Callable[[str], str] | None = None):
        self.transport = transport
        self.links = links
        self._reply = reply
        self._allowlist = allowlist
        self._transcribe = transcribe
        self._synthesize = synthesize
        self._new_conversation = new_conversation
        self._new_project = new_project
        self._poll_timeout = max(1.0, float(poll_timeout))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset: int | None = None
        self._backoff = 1.0

        # Status the UI reads. Plain ints so a snapshot cannot race a dict.
        self.polls = 0
        self.messages = 0
        self.replies = 0
        self.rejected = 0
        self.last_rejected_chat: int | None = None
        self.last_error = ""
        self.last_activity = 0.0
        self.connected_as = ""

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tolesh-telegram",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            # A long poll is an HTTP request already in flight, so it cannot be
            # cancelled from here; waiting for it would block shutdown for the
            # whole poll timeout. The join is short, the transport is closed to
            # break the socket, and the thread is a daemon that checks _stop
            # before it handles any update — so nothing is answered after this
            # returns, even if the request takes another second to unwind.
            thread.join(timeout=1.5)
            try:
                self.transport.close()
            except Exception:
                pass
            thread.join(timeout=0.5)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "bot": self.connected_as,
                "polls": self.polls, "messages": self.messages,
                "replies": self.replies, "rejected": self.rejected,
                "last_rejected_chat": self.last_rejected_chat,
                "last_error": self.last_error,
                "last_activity": self.last_activity or None}

    # --------------------------------------------------------------------- loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                updates = self.transport.get_updates(self._offset,
                                                     int(self._poll_timeout))
                self._backoff = 1.0
                self.last_error = ""
            except TelegramError as exc:
                self.last_error = str(exc)
                self._sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 60.0)
                continue
            except Exception as exc:  # a transport bug must not kill the thread
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 60.0)
                continue
            self.polls += 1
            for update in updates or []:
                if self._stop.is_set():
                    break
                try:
                    self.handle_update(update)
                except Exception as exc:  # one bad message must not stop polling
                    self.last_error = f"{type(exc).__name__}: {exc}"
            if not updates:
                # A real long poll blocks for its timeout, so this never fires.
                # But an API that answers an empty poll immediately — a proxy
                # that strips the timeout, a token Telegram dislikes, a fake in
                # a test — would otherwise spin this loop as fast as the socket
                # allows: measured at ~100 polls a second on one core.
                self._sleep(0.5)

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(min(seconds, 60.0))

    # -------------------------------------------------------------- dispatch
    def handle_update(self, update: dict[str, Any]) -> None:
        """Route one update. Public so it can be driven without a live socket."""
        update_id = update.get("update_id")
        if isinstance(update_id, int) and not isinstance(update_id, bool):
            self._offset = update_id + 1
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = _as_chat_id(chat.get("id"))
        if chat_id is None:
            return
        if chat_id not in self._allowlist():
            # Silence, not an error reply: a stranger should not learn that
            # this bot answers anyone.
            self.rejected += 1
            self.last_rejected_chat = chat_id
            return
        self.messages += 1
        self.last_activity = time.time()
        text = (message.get("text") or "").strip()
        if text.startswith("/"):
            self._command(chat_id, text)
            return
        voice = message.get("voice") or message.get("audio")
        if voice and not text:
            self._voice(chat_id, voice)
            return
        if not text:
            self.transport.send_message(chat_id, "I can read text and voice notes.")
            return
        self._answer(chat_id, text)

    def _command(self, chat_id: int, text: str) -> None:
        command = text.split()[0].split("@")[0].lower()
        if command in ("/start", "/help"):
            self.transport.send_message(
                chat_id, "Send me a message and I will answer. /new starts a "
                         "fresh conversation, /id shows this chat's id.")
            return
        if command == "/id":
            self.transport.send_message(chat_id, f"This chat's id is {chat_id}.")
            return
        if command == "/new":
            self.links.reset(chat_id)
            self.transport.send_message(chat_id, "Started a new conversation.")
            return
        self.transport.send_message(chat_id, f"Unknown command {command}.")

    def _conversation(self, chat_id: int) -> str:
        existing = self.links.conversation_for(chat_id)
        if existing:
            return existing
        title = f"Telegram {chat_id}"
        # One project per chat is what keeps two allowlisted people apart: the
        # memory store is scoped by project, so a fact learned in chat A is not
        # even a candidate in chat B.
        project_id = ""
        if self._new_project is not None:
            try:
                project_id = self._new_project(title) or ""
            except Exception as exc:
                self.last_error = f"project: {exc}"
                project_id = ""
        conversation_id = (self._new_conversation(title, project_id)
                           if self._new_conversation else "")
        if not conversation_id:
            raise TelegramError("No conversation could be created.")
        self.links.link(chat_id, conversation_id, title, project_id)
        return conversation_id

    def _ask(self, chat_id: int, text: str) -> str | None:
        """One turn through the chat pipeline, or ``None`` if it failed.

        A model or tool error is answered, not swallowed: the person on the
        other end is waiting, and silence reads as a broken bot.
        """
        try:
            conversation_id = self._conversation(chat_id)
            answer = (self._reply(conversation_id, text) or "").strip()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.transport.send_message(
                chat_id, f"Something went wrong while answering: {str(exc)[:200]}")
            return None
        return answer or "I did not get an answer from the model."

    def _answer(self, chat_id: int, text: str) -> None:
        answer = self._ask(chat_id, text)
        if answer is None:
            return
        self.transport.send_message(chat_id, answer[:MAX_MESSAGE_CHARS])
        self.replies += 1

    def _voice(self, chat_id: int, voice: dict[str, Any]) -> None:
        if self._transcribe is None or self._synthesize is None:
            self.transport.send_message(
                chat_id, "Voice notes need local speech-to-text and text-to-speech.")
            return
        file_id = voice.get("file_id")
        if not file_id:
            return
        try:
            path = self.transport.get_file(file_id)
            audio = self.transport.download(path)
        except TelegramError as exc:
            self.last_error = str(exc)
            self.transport.send_message(chat_id, "I could not fetch that voice note.")
            return
        try:
            heard = (self._transcribe(audio) or "").strip()
        except Exception as exc:
            self.last_error = f"transcription failed: {exc}"
            self.transport.send_message(chat_id, f"I could not transcribe that: {str(exc)[:200]}")
            return
        if not heard:
            self.transport.send_message(chat_id, "I could not make out any words.")
            return
        answer = self._ask(chat_id, heard)
        if answer is None:
            return
        self.replies += 1
        try:
            audio_out, mime = self._synthesize(answer)
        except Exception as exc:
            # Text is a complete answer; losing the voice note is a downgrade,
            # not a failure.
            self.last_error = f"synthesis failed: {exc}"
            self.transport.send_message(chat_id, answer[:MAX_MESSAGE_CHARS])
            return
        voice_note = mime in ("audio/ogg", "audio/opus")
        self.transport.send_audio(
            chat_id, audio_out,
            "reply.ogg" if voice_note else "reply.wav",
            mime=mime, voice=voice_note)


class TelegramService:
    """Owns the channel: builds it from the store, restarts it on demand."""

    def __init__(self, *, links: TelegramLinks, reply: Callable[[str, str], str],
                 allowlist: Callable[[], set[int]],
                 token: Callable[[], str],
                 transport_factory: Callable[[str], Transport] = HttpTransport,
                 transcribe: Callable[[bytes], str] | None = None,
                 synthesize: Callable[[str], tuple[bytes, str]] | None = None,
                 new_conversation: Callable[[str, str], str] | None = None,
                 new_project: Callable[[str], str] | None = None):
        self._links = links
        self._reply = reply
        self._allowlist = allowlist
        self._token = token
        self._factory = transport_factory
        self._transcribe = transcribe
        self._synthesize = synthesize
        self._new_conversation = new_conversation
        self._new_project = new_project
        self._channel: TelegramChannel | None = None
        self._lock = threading.RLock()
        self.last_error = ""

    @property
    def channel(self) -> TelegramChannel | None:
        return self._channel

    def set_transport_factory(self, factory: Callable[[str], Transport]) -> None:
        """Swap the transport. Tests use it; the UI never does."""
        self._factory = factory

    def transport_for_test(self, token: str) -> Transport:
        """A throwaway transport for a ``getMe`` check — never the live one."""
        return self._factory(token)

    def start(self) -> dict[str, Any]:
        """(Re)build and start the channel. Returns the status."""
        with self._lock:
            self.stop()
            token = (self._token() or "").strip()
            if not token:
                self.last_error = "No bot token is stored."
                return self.status()
            try:
                transport = self._factory(token)
            except Exception as exc:
                self.last_error = str(exc)
                return self.status()
            channel = TelegramChannel(
                transport, links=self._links, reply=self._reply,
                allowlist=self._allowlist, transcribe=self._transcribe,
                synthesize=self._synthesize,
                new_conversation=self._new_conversation,
                new_project=self._new_project)
            try:
                channel.connected_as = str((transport.me() or {}).get("username") or "")
            except Exception as exc:
                # A bad token is a normal, reportable state — not a crash.
                self.last_error = str(exc)
                try:
                    transport.close()
                except Exception:
                    pass
                return self.status()
            self.last_error = ""
            self._channel = channel
            channel.start()
            return self.status()

    def start_if_configured(self, *, enabled: bool) -> dict[str, Any]:
        """Start only when the user turned it on *and* a token exists.

        Called at startup, so the two conditions that are normal rather than
        exceptional — switched off, no token — must produce a quiet status
        instead of an error banner.
        """
        if not enabled:
            return self.status()
        if not (self._token() or "").strip():
            self.last_error = "Telegram is switched on but no bot token is stored."
            return self.status()
        return self.start()

    def stop(self) -> None:
        # The join happens while the lock is held, so a concurrent ``start()``
        # cannot build a second channel while the first one's thread is still
        # draining its last long poll. Two live pollers would both fetch and
        # both answer, and the same message would get two replies.
        with self._lock:
            channel, self._channel = self._channel, None
            if channel is not None:
                channel.stop()

    def restart(self) -> dict[str, Any]:
        return self.start()

    def status(self) -> dict[str, Any]:
        channel = self._channel
        if channel is None:
            return {"running": False, "bot": "", "polls": 0, "messages": 0,
                    "replies": 0, "rejected": 0, "last_rejected_chat": None,
                    "last_error": self.last_error, "last_activity": None}
        status = channel.status()
        status["last_error"] = status["last_error"] or self.last_error
        return status
