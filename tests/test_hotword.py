"""Audio-level wake-word gating.

The listener's transcript-level detector can only decide after Whisper has
transcribed the utterance. These tests pin down the audio gate that replaces
that decision when a backend is available — and, just as importantly, that
nothing changes when one is not.

Every test here uses a stub detector, so CI never downloads a model.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from tolesh.listening import hotword as hotword_module
from tolesh.listening.hotword import (HotwordDetector, HotwordError,
                                      create_detector, probe)
from tolesh.listening.listener import VoiceListener

pytestmark = pytest.mark.unit

FRAME = 320          # 20 ms at 16 kHz, what the microphone actually delivers


class StubDetector(HotwordDetector):
    """Scores from a script; 0.0 once the script runs out."""

    name = "stub"
    sample_rate = 16000
    chunk = FRAME

    def __init__(self, scores=None, threshold=0.5):
        super().__init__(threshold)
        self.scores = list(scores or [])
        self.calls = 0
        self.closed = False

    def _predict(self, chunk):
        self.calls += 1
        return self.scores.pop(0) if self.scores else 0.0

    def close(self):
        self.closed = True


def silence(seconds=1.0, amplitude=0.0):
    return np.full(int(16000 * seconds), amplitude, dtype=np.float32)


@pytest.fixture
def listener(mock_config, monkeypatch):
    monkeypatch.setattr("tolesh.listening.listener.create_intent_judge", lambda cfg: None)
    mock_config.hotword_enabled = True
    mock_config.hotword_threshold = 0.5
    mock_config.hotword_window_sec = 3.0
    mock_config.vad_pre_roll_ms = 240
    mock_config.voice_min_energy = 0.02
    mock_config.whisper_min_audio_duration = 0.15
    mock_config.hot_window_enabled = True
    mock_config.hot_window_seconds = 3.0
    instance = VoiceListener(None, mock_config, None, None)
    yield instance
    instance.stop()


def speak(listener, seconds=1.0, amplitude=0.3):
    """Fill the listener with one loud utterance, as the VAD would."""
    frames = [np.full((FRAME,), amplitude, dtype=np.float32)
              for _ in range(int(seconds * 16000 / FRAME))]
    listener._utterance_frames = frames
    listener.is_speech_active = True
    listener.echo_detector._utterance_start_time = time.time() - seconds


# ------------------------------------------------------------- the detector
def test_frames_are_buffered_up_to_the_backend_chunk():
    detector = StubDetector()
    detector.chunk = 1280
    for _ in range(3):
        assert detector.feed(silence(0.02)) == 0.0     # 960 samples: not enough
    assert detector.calls == 0
    detector.feed(silence(0.02))                        # 1280: one prediction
    assert detector.calls == 1


def test_feed_returns_the_highest_score_in_the_block():
    detector = StubDetector(scores=[0.2, 0.9, 0.4])
    detector.chunk = 320
    scores = [detector.feed(silence(0.02)) for _ in range(3)]
    assert scores == [0.2, 0.9, 0.4]


def test_an_unknown_backend_is_refused_with_a_reason(mock_config):
    mock_config.hotword_backend = "porcupine"
    mock_config.hotword_model = ""
    with pytest.raises(HotwordError) as excinfo:
        create_detector(mock_config)
    # Deterministic: no key is set in the test environment.
    assert "TOLESH_PORCUPINE_KEY" in str(excinfo.value) or "pvporcupine" in str(excinfo.value)


def test_probe_reports_a_failure_instead_of_raising(mock_config):
    mock_config.hotword_backend = "porcupine"
    result = probe(mock_config)
    assert result["ok"] is False
    assert result["backend"] is None
    assert result["error"]


def test_probe_reports_a_loaded_detector(monkeypatch, mock_config):
    monkeypatch.setattr(hotword_module, "create_detector",
                        lambda cfg: StubDetector(scores=[0.0]))
    result = probe(mock_config)
    assert result["ok"] is True
    assert result["backend"] == "stub"
    assert result["silence_score"] == 0.0


# --------------------------------------------------------------- the gate
def test_gating_is_off_until_a_detector_loads(listener):
    assert listener.hotword_active is False
    speak(listener)
    listener._finalize_utterance()
    assert not listener._transcription_jobs_q.empty(), "the old path must still work"


def test_an_utterance_without_the_wake_word_is_not_transcribed(listener):
    listener._hotword = StubDetector()
    speak(listener)
    listener._finalize_utterance()
    assert listener._transcription_jobs_q.empty()


def test_a_heard_wake_word_lets_the_utterance_through(listener):
    listener._hotword = StubDetector()
    speak(listener)
    listener._hotword_hit_at = time.monotonic()
    listener._finalize_utterance()
    assert not listener._transcription_jobs_q.empty()


def test_the_wake_word_window_expires(listener):
    listener._hotword = StubDetector()
    listener.cfg.hotword_window_sec = 1.0
    listener._hotword_hit_at = time.monotonic() - 2.0
    speak(listener)
    listener._finalize_utterance()
    assert listener._transcription_jobs_q.empty()


def test_a_follow_up_inside_the_hot_window_needs_no_wake_word(listener):
    listener._hotword = StubDetector()
    listener._hotword_hit_at = 0.0
    # An open follow-up window: the user is mid-conversation, so the name is
    # not required again.
    from tolesh.listening.state_manager import ListeningState

    listener.state_manager._state = ListeningState.HOT_WINDOW
    listener.state_manager._hot_window_start_time = time.time()
    speak(listener)
    listener._finalize_utterance()
    assert not listener._transcription_jobs_q.empty()


def test_pre_roll_widens_to_cover_a_spoken_name(listener):
    assert listener._pre_roll_ms() == 240
    listener._hotword = StubDetector()
    assert listener._pre_roll_ms() >= 300


def test_a_frame_that_scores_above_the_threshold_opens_the_gate(listener):
    listener._hotword = StubDetector(scores=[0.9])
    listener._score_hotword(silence(0.02, amplitude=0.1))
    assert listener._hotword_hit_at > 0
    assert listener._wake_word_in_audio() is True


def test_a_frame_that_scores_below_the_threshold_does_not(listener):
    listener._hotword = StubDetector(scores=[0.2])
    listener._score_hotword(silence(0.02, amplitude=0.1))
    assert listener._hotword_hit_at == 0.0
    assert listener._wake_word_in_audio() is False


def test_a_broken_detector_disables_itself_instead_of_raising(listener):
    class Exploding(StubDetector):
        def _predict(self, chunk):
            raise RuntimeError("the model exploded")

    detector = Exploding()
    listener._hotword = detector
    listener._score_hotword(silence(0.02, amplitude=0.1))
    assert listener._hotword is None
    assert detector.closed is True
    assert "model exploded" in listener._hotword_error
    # And the listener is back to transcribing everything.
    speak(listener)
    listener._finalize_utterance()
    assert not listener._transcription_jobs_q.empty()


def test_a_detector_that_will_not_load_falls_back_quietly(listener, monkeypatch):
    def refuse(cfg):
        raise HotwordError("openwakeword is not installed")

    monkeypatch.setattr("tolesh.listening.listener.create_detector", refuse)
    listener._ensure_hotword()
    assert listener._hotword is None
    assert "not installed" in listener._hotword_error
    speak(listener)
    listener._finalize_utterance()
    assert not listener._transcription_jobs_q.empty()


def test_the_detector_is_loaded_once(listener, monkeypatch):
    built = []

    def build(cfg):
        built.append(cfg)
        return StubDetector()

    monkeypatch.setattr("tolesh.listening.listener.create_detector", build)
    listener._ensure_hotword()
    listener._ensure_hotword()
    assert len(built) == 1
    assert listener.hotword_active is True


def test_disabling_hotword_is_off_until_configured(mock_config, monkeypatch):
    monkeypatch.setattr("tolesh.listening.listener.create_intent_judge", lambda cfg: None)
    mock_config.hotword_enabled = False
    instance = VoiceListener(None, mock_config, None, None)
    try:
        instance._ensure_hotword()
        assert instance._hotword is None
        assert instance.hotword_active is False
    finally:
        instance.stop()


# -------------------------------------------------------------- the settings
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tolesh.server import create_app
    from tolesh.server.app import AppState

    monkeypatch.setenv("TOLESH_MCP_PATH", str(tmp_path / "mcp.json"))
    monkeypatch.setenv("TOLESH_CONFIG_PATH", str(tmp_path / "config.json"))
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as test_client:
        test_client.state = state  # type: ignore[attr-defined]
        yield test_client
    state.close()


def test_api_reports_the_defaults(client):
    body = client.get("/api/voice/hotword").json()
    assert body["values"]["hotword_enabled"] is False
    assert body["values"]["hotword_backend"] == "auto"
    assert body["status"]["gating"] is False
    assert {b["id"] for b in body["backends"]} == {"auto", "openwakeword", "porcupine"}


def test_api_persists_a_change(client):
    patched = client.patch("/api/voice/hotword", json={
        "hotword_threshold": 0.7, "hotword_backend": "openwakeword",
        "hotword_window_sec": 2.5})
    assert patched.status_code == 200
    values = patched.json()["values"]
    assert values["hotword_threshold"] == 0.7
    assert values["hotword_backend"] == "openwakeword"
    # A second read comes from the config file, not from memory.
    assert client.get("/api/voice/hotword").json()["values"]["hotword_threshold"] == 0.7


@pytest.mark.parametrize("body", [
    {"hotword_backend": "magic"}, {"hotword_threshold": 0.99},
    {"hotword_threshold": "loud"}, {"hotword_window_sec": 30}, {},
])
def test_api_refuses_nonsense(client, body):
    assert client.patch("/api/voice/hotword", json=body).status_code == 400


def test_api_test_endpoint_reports_a_backend_that_will_not_load(client, monkeypatch):
    """The probe must report, not raise, and must say why.

    The factory is stubbed so the result does not depend on which optional
    packages happen to be installed on the machine running the tests.
    """
    def refuse(cfg):
        raise HotwordError("openWakeWord has no model files yet")

    monkeypatch.setattr(hotword_module, "create_detector", refuse)
    body = client.post("/api/voice/hotword/test").json()
    assert body["ok"] is False
    assert "no model files" in body["error"]
