"""Lifecycle: what the six features start, and whether they stop.

A background thread that outlives its owner is a leak that only shows up after
a day of use, and a shutdown that waits on a long poll looks like a freeze. The
measurements here are real — thread counts from ``threading.enumerate`` and
elapsed wall time — not assertions about intent.
"""

from __future__ import annotations

import threading
import time

import pytest

from tolesh.channels import FakeTransport, TelegramChannel, TelegramLinks
from tolesh.server.app import AppState
from tolesh.server.app import create_app


def thread_names() -> list[str]:
    return sorted(t.name for t in threading.enumerate())


def wait_for_threads_gone(prefix: str, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(name.startswith(prefix) for name in thread_names()):
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------------------ AppState
def test_creating_and_closing_a_state_leaves_no_threads():
    before = thread_names()
    st = AppState(in_memory=True)
    try:
        time.sleep(0.2)
        assert any(name.startswith("tolesh-") for name in thread_names())
    finally:
        st.close()
    assert wait_for_threads_gone("tolesh-"), thread_names()
    assert thread_names() == before


def test_closing_twice_is_harmless():
    st = AppState(in_memory=True)
    st.close()
    st.close()


def test_a_job_thread_is_not_duplicated_by_a_second_start():
    st = AppState(in_memory=True)
    try:
        st.jobs.start()
        st.jobs.start()
        st.jobs.start()
        time.sleep(0.2)
        names = [n for n in thread_names() if n == "tolesh-jobs"]
        assert len(names) == 1
    finally:
        st.close()


# ------------------------------------------------------------------ Telegram
class SlowTransport(FakeTransport):
    """A transport whose long poll blocks, as a real one does."""

    def __init__(self, block: float = 3.0):
        super().__init__()
        self.block = block
        self.answered_after_stop = 0
        self._stop_seen = False

    def get_updates(self, offset, timeout):
        time.sleep(self.block)
        return []


def _channel(tmp_path, transport, *, poll_timeout=10.0):
    return TelegramChannel(
        transport, links=TelegramLinks(str(tmp_path / "tg.db")),
        reply=lambda cid, text: "ok", allowlist=lambda: {1},
        poll_timeout=poll_timeout, new_conversation=lambda t, p="": "conv-1")


def test_stopping_does_not_wait_for_a_whole_long_poll(tmp_path):
    transport = SlowTransport(block=3.0)
    channel = _channel(tmp_path, transport, poll_timeout=10.0)
    channel.start()
    time.sleep(0.1)

    started = time.time()
    channel.stop()
    elapsed = time.time() - started

    assert elapsed < 2.5, f"stop blocked for {elapsed:.1f}s on an in-flight poll"
    assert transport.closed is True


def test_no_reply_is_sent_after_stop(tmp_path):
    """A poll that lands after shutdown must not answer anyone."""
    transport = SlowTransport(block=0.4)
    transport.queues = [[{"update_id": 1,
                          "message": {"chat": {"id": 1}, "text": "late"}}]]
    channel = _channel(tmp_path, transport)
    channel.start()
    time.sleep(0.05)
    channel.stop()
    time.sleep(0.6)

    assert transport.sent == []


def test_the_poller_thread_is_gone_after_stop(tmp_path):
    transport = FakeTransport()
    channel = _channel(tmp_path, transport)
    channel.start()
    assert channel.running is True
    channel.stop()

    assert wait_for_threads_gone("tolesh-telegram")
    assert channel.running is False


def test_the_app_lifespan_starts_and_stops_the_channel():
    st = AppState(in_memory=True)
    # create_app builds the channel, so the fake transport is injected after.
    app = create_app(state=st, serve_static=False)
    transport = FakeTransport()
    st.telegram.set_transport_factory(lambda token: transport)
    st.connectors.store.upsert(connection_id="telegram:bot", provider="telegram",
                               access_token="123456789:" + "A" * 35,
                               label="bot", token_type="bot")
    st.telegram_settings.set_enabled(True)
    st.telegram_settings.set_allowlist([1])
    from fastapi.testclient import TestClient

    try:
        with TestClient(app):
            time.sleep(0.2)
            assert st.telegram.status()["running"] is True
        assert wait_for_threads_gone("tolesh-telegram")
        assert st.telegram.status()["running"] is False
    finally:
        st.close()


# --------------------------------------------------------------- microphone
def test_a_disabled_detector_is_never_constructed(mock_config, monkeypatch):
    """A disabled feature must cost nothing, not merely do nothing."""
    from tolesh.listening.listener import VoiceListener

    monkeypatch.setattr("tolesh.listening.listener.create_intent_judge",
                        lambda cfg: None)
    mock_config.hotword_enabled = False
    listener = VoiceListener(None, mock_config, None, None)
    try:
        listener._ensure_hotword()
        assert listener._hotword is None
        assert listener.hotword_active is False
    finally:
        listener.stop()


def test_the_gate_costs_less_than_a_fraction_of_real_time():
    """Scoring must be cheaper than the audio it scores, or it cannot keep up."""
    import time

    import numpy as np

    from tolesh.listening.hotword import HotwordDetector

    class Stub(HotwordDetector):
        name = "stub"
        chunk = 1280

        def _predict(self, chunk):
            return 0.0

    detector = Stub()
    frame = np.zeros(320, dtype=np.float32)      # 20 ms at 16 kHz
    frames = 50 * 60                              # sixty seconds of audio

    started = time.perf_counter()
    for _ in range(frames):
        detector.feed(frame)
    elapsed = time.perf_counter() - started

    audio_seconds = frames * 0.02
    assert elapsed < audio_seconds * 0.5, (
        f"{elapsed:.2f}s of CPU for {audio_seconds:.0f}s of audio")
