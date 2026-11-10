"""Phase 10 — image understanding, image generation, speech.

Image generation and synthesis are exercised against local HTTP servers that
speak the OpenAI-compatible shapes. What cannot be verified here (a real
provider key, a downloaded Whisper model) is reported as unavailable by the
capability endpoint rather than pretended.
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tolesh.media import (MediaError, SpeechError, capabilities, describe_image,
                          generate_image, image_settings, synthesize, vision_message)
from tolesh.server import create_app
from tolesh.server.app import AppState

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

SEEN: list[dict] = []


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 800)
    return buffer.getvalue()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def _send(self, code: int, body: bytes, mime: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        SEEN.append({"path": self.path, "body": raw, "headers": dict(self.headers)})
        if self.path.endswith("/images/generations"):
            payload = json.loads(raw or "{}")
            if not payload.get("prompt"):
                return self._send(400, b'{"error":{"message":"prompt required"}}',
                                  "application/json")
            body = json.dumps({"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
            return self._send(200, body.encode(), "application/json")
        if self.path.endswith("/audio/speech"):
            return self._send(200, _wav_bytes(), "audio/wav")
        return self._send(404, b"{}", "application/json")


@pytest.fixture
def fake_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    SEEN.clear()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class CapturingRouter:
    def __init__(self, text="A small red square.", fail: Exception | None = None):
        self.text = text
        self.fail = fail
        self.messages: list[list[dict]] = []

    def complete(self, messages, **kwargs):
        self.messages.append(messages)
        if self.fail:
            raise self.fail
        return SimpleNamespace(text=self.text, model_id="vision-model")


# ------------------------------------------------------------- understanding
def test_vision_message_carries_the_image_as_a_data_url():
    message = vision_message("What is this?", PNG, "image/png")
    parts = message["content"]
    assert parts[0] == {"type": "text", "text": "What is this?"}
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG


def test_describe_image_returns_the_model_text():
    router = CapturingRouter("It is a 1x1 pixel.")
    result = describe_image(router, PNG, prompt="What is it?")
    assert result["text"] == "It is a 1x1 pixel."
    sent = router.messages[0][0]["content"]
    assert any(part.get("type") == "image_url" for part in sent)


def test_describe_image_explains_a_model_that_cannot_see():
    router = CapturingRouter(fail=RuntimeError("400 Bad Request: unsupported image"))
    with pytest.raises(MediaError) as excinfo:
        describe_image(router, PNG)
    assert "vision-capable" in str(excinfo.value)


# --------------------------------------------------------------- generation
def test_generate_image_requires_a_key(monkeypatch):
    monkeypatch.delenv("TOLESH_IMAGE_API_KEY", raising=False)
    with pytest.raises(MediaError) as excinfo:
        generate_image("a cat")
    assert excinfo.value.kind == "unconfigured"


def test_generate_image_returns_png_bytes(fake_api):
    image, meta = generate_image("a red square", settings={
        "api_key": "test-key", "base_url": fake_api, "model": "fake-image"})
    assert image.startswith(b"\x89PNG")
    assert meta["model"] == "fake-image"
    assert SEEN[0]["headers"].get("Authorization") == "Bearer test-key"


def test_generate_image_reports_provider_errors(fake_api):
    with pytest.raises(MediaError) as excinfo:
        generate_image("", settings={"api_key": "k", "base_url": fake_api})
    assert "prompt" in str(excinfo.value).lower()


# ------------------------------------------------------------------- speech
def test_synthesize_uses_the_http_engine_when_configured(fake_api):
    audio, mime, meta = synthesize("hello there", settings={
        "api_key": "k", "base_url": fake_api, "model": "fake-tts", "voice": "alloy"})
    assert audio.startswith(b"RIFF")
    assert mime == "audio/wav"
    assert meta["engine"] == "http"
    assert json.loads(SEEN[0]["body"])["input"] == "hello there"


def test_synthesize_without_any_engine_is_explicit(monkeypatch):
    monkeypatch.setattr("tolesh.media.speech._piper", lambda: None)
    monkeypatch.delenv("TOLESH_TTS_API_KEY", raising=False)
    with pytest.raises(SpeechError) as excinfo:
        synthesize("hello")
    assert excinfo.value.kind == "unconfigured"


def test_capabilities_state_the_reason_for_each_gap(monkeypatch):
    monkeypatch.delenv("TOLESH_IMAGE_API_KEY", raising=False)
    monkeypatch.delenv("TOLESH_TTS_API_KEY", raising=False)
    monkeypatch.setattr("tolesh.media.speech._piper", lambda: None)
    report = capabilities()
    assert report["image_generation"]["available"] is False
    assert "TOLESH_IMAGE_API_KEY" in report["image_generation"]["reason"]
    assert report["synthesis"]["available"] is False
    assert report["transcription"]["reason"] == "" or not report["transcription"]["available"]


# ---------------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    monkeypatch.setenv("TOLESH_MCP_PATH", str(tmp_path / "mcp.json"))
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as test_client:
        test_client.state = state  # type: ignore[attr-defined]
        yield test_client
    state.close()


def test_media_capabilities_endpoint(client):
    payload = client.get("/api/media/capabilities").json()
    assert set(payload) == {"transcription", "synthesis", "image_generation"}
    assert isinstance(payload["image_generation"]["available"], bool)


def test_image_endpoint_returns_503_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("TOLESH_IMAGE_API_KEY", raising=False)
    response = client.post("/api/media/images", json={"prompt": "a cat"})
    assert response.status_code == 503
    assert "TOLESH_IMAGE_API_KEY" in response.json()["detail"]


def test_image_endpoint_saves_an_artifact(client, fake_api, monkeypatch):
    monkeypatch.setenv("TOLESH_IMAGE_API_KEY", "test-key")
    monkeypatch.setenv("TOLESH_IMAGE_BASE_URL", fake_api)
    response = client.post("/api/media/images",
                           json={"prompt": "a red square", "size": "512x512"})
    assert response.status_code == 200, response.text
    artifact = response.json()["artifact"]
    assert artifact["kind"] == "image"
    download = client.get(artifact["download_url"])
    assert download.content.startswith(b"\x89PNG")


def test_speak_endpoint_returns_audio_and_reports_the_engine(client, fake_api,
                                                             monkeypatch):
    monkeypatch.setenv("TOLESH_TTS_API_KEY", "k")
    monkeypatch.setenv("TOLESH_TTS_BASE_URL", fake_api)
    response = client.post("/api/media/speak", json={"text": "hello"})
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"RIFF")
    assert response.headers["x-tolesh-voice-engine"] == "http"
    assert response.headers["content-type"].startswith("audio/")


def test_speak_endpoint_is_explicit_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("TOLESH_TTS_API_KEY", raising=False)
    monkeypatch.setattr("tolesh.media.speech._piper", lambda: None)
    response = client.post("/api/media/speak", json={"text": "hello"})
    assert response.status_code == 503


def test_transcribe_rejects_empty_audio(client):
    response = client.post("/api/media/transcribe", content=b"")
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_transcribe_is_503_when_the_engine_is_absent(client, monkeypatch):
    monkeypatch.setattr("tolesh.media.speech._whisper_available", lambda: False)
    response = client.post("/api/media/transcribe", content=b"RIFF....")
    assert response.status_code == 503
    assert "faster-whisper" in response.json()["detail"]


def test_describe_endpoint_rejects_a_non_image(client):
    upload = client.post("/api/attachments?filename=notes.txt", content=b"hello",
                         headers={"Content-Type": "text/plain"})
    attachment_id = upload.json()["attachment"]["id"]
    response = client.post("/api/media/describe", json={"attachment_id": attachment_id})
    assert response.status_code == 400
    assert "not an image" in response.json()["detail"]


def test_describe_endpoint_uses_the_router(client, monkeypatch):
    upload = client.post("/api/attachments?filename=pixel.png", content=PNG,
                         headers={"Content-Type": "image/png"})
    attachment_id = upload.json()["attachment"]["id"]
    client.state.router = CapturingRouter("A single pixel.")
    response = client.post("/api/media/describe",
                           json={"attachment_id": attachment_id, "prompt": "What is it?"})
    assert response.status_code == 200, response.text
    assert response.json()["result"]["text"] == "A single pixel."
