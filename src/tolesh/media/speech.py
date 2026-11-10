"""Speech in and out.

Two rules shape this module:

  * it reports what is actually installed. ``capabilities()`` is what the UI
    shows, and it never claims a synthesiser exists because a code path exists;
  * a transcription without a downloaded model fails with an explanation, not
    with a half-downloaded model in the user's cache.

Local synthesis uses Piper when the binary is present (the desktop app ships
one). Otherwise an OpenAI-compatible ``/audio/speech`` endpoint is used if a key
is configured. If neither exists, ``synthesize`` says so.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import wave
from pathlib import Path
from typing import Any

import httpx

MAX_AUDIO_BYTES = 25 * 1024 * 1024


class SpeechError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def _piper() -> str | None:
    return os.environ.get("TOLESH_PIPER_BIN") or shutil.which("piper")


def _whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


def tts_settings() -> dict[str, str]:
    return {
        "base_url": os.environ.get("TOLESH_TTS_BASE_URL", "https://api.openai.com/v1"),
        "api_key": os.environ.get("TOLESH_TTS_API_KEY", ""),
        "model": os.environ.get("TOLESH_TTS_MODEL", "gpt-4o-mini-tts"),
        "voice": os.environ.get("TOLESH_TTS_VOICE", "alloy"),
    }


def capabilities() -> dict[str, Any]:
    """What this machine can really do, with the reason when it cannot."""
    piper = _piper()
    tts = tts_settings()
    whisper = _whisper_available()
    return {
        "transcription": {
            "available": whisper,
            "engine": "faster-whisper" if whisper else "",
            "reason": "" if whisper else
                      "faster-whisper is not installed, so audio cannot be transcribed.",
        },
        "synthesis": {
            "available": bool(piper or tts["api_key"]),
            "engine": ("piper" if piper else ("http" if tts["api_key"] else "")),
            "reason": "" if (piper or tts["api_key"]) else
                      "No local Piper voice and no TTS API key are configured.",
        },
        "image_generation": {
            "available": bool(os.environ.get("TOLESH_IMAGE_API_KEY")),
            "engine": "openai-compatible",
            "reason": "" if os.environ.get("TOLESH_IMAGE_API_KEY") else
                      "Set TOLESH_IMAGE_API_KEY to enable image generation.",
        },
    }


def transcribe(audio: bytes, *, language: str | None = None,
               model_size: str = "base") -> dict[str, Any]:
    """Transcribe audio locally with faster-whisper.

    The model is loaded on first use and cached by the library. A missing model
    download surfaces as a clear error rather than a stack trace.
    """
    if not audio:
        raise SpeechError("The audio file is empty.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise SpeechError("The audio file is larger than 25 MB.")
    if not _whisper_available():
        raise SpeechError(
            "Speech-to-text needs the 'faster-whisper' package, which is not "
            "installed in this build.", kind="unavailable")
    from faster_whisper import WhisperModel

    suffix = ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(audio)
        path = Path(handle.name)
    try:
        try:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
        except Exception as exc:
            raise SpeechError(
                f"The transcription model '{model_size}' could not be loaded "
                f"({exc}). Download it first or choose a smaller size.",
                kind="model") from exc
        segments, info = model.transcribe(str(path), language=language, vad_filter=True)
        parts = []
        for segment in segments:
            parts.append({"start": round(segment.start, 2), "end": round(segment.end, 2),
                          "text": segment.text.strip()})
        text = " ".join(p["text"] for p in parts).strip()
        return {"text": text, "segments": parts,
                "language": getattr(info, "language", "") or "",
                "duration": round(getattr(info, "duration", 0.0) or 0.0, 2)}
    finally:
        path.unlink(missing_ok=True)


def synthesize(text: str, *, voice: str | None = None, model: str | None = None,
               settings: dict[str, str] | None = None,
               client: httpx.Client | None = None) -> tuple[bytes, str, dict[str, Any]]:
    """Return ``(audio_bytes, mime, meta)``.

    Local Piper wins when present: it needs no key and no network.
    """
    if not (text or "").strip():
        raise SpeechError("There is no text to speak.")
    if len(text) > 8000:
        raise SpeechError("Text is limited to 8000 characters per request.")
    piper = _piper()
    if piper:
        with tempfile.TemporaryDirectory(prefix="tolesh-tts-") as folder:
            out = Path(folder) / "speech.wav"
            try:
                process = __import__("subprocess").run(
                    [piper, "--output_file", str(out)],
                    input=text.encode("utf-8"), capture_output=True, timeout=120)
            except Exception as exc:
                raise SpeechError(f"Piper could not run: {exc}", kind="engine") from exc
            if process.returncode != 0 or not out.exists():
                raise SpeechError(
                    "Piper failed: " + process.stderr.decode("utf-8", "replace")[:300],
                    kind="engine")
            return out.read_bytes(), "audio/wav", {"engine": "piper"}

    config = settings or tts_settings()
    if not config.get("api_key"):
        raise SpeechError(
            "No speech synthesiser is available. Install Piper, or set "
            "TOLESH_TTS_API_KEY for an OpenAI-compatible speech endpoint.",
            kind="unconfigured")
    owned = client is None
    http = client or httpx.Client(timeout=120)
    try:
        response = http.post(
            f"{config['base_url'].rstrip('/')}/audio/speech",
            json={"model": model or config["model"], "input": text,
                  "voice": voice or config["voice"], "response_format": "wav"},
            headers={"Authorization": f"Bearer {config['api_key']}"})
    except httpx.HTTPError as exc:
        raise SpeechError(f"The speech endpoint could not be reached: {exc}",
                          kind="network") from exc
    finally:
        if owned:
            http.close()
    if response.status_code >= 400:
        raise SpeechError(f"The speech endpoint returned {response.status_code}: "
                          f"{response.text[:200]}", kind="provider")
    if not response.content:
        raise SpeechError("The speech endpoint returned no audio.")
    mime = response.headers.get("content-type", "audio/wav").split(";")[0]
    return response.content, mime, {"engine": "http", "model": model or config["model"]}


def wav_duration(data: bytes) -> float:
    """Duration of a WAV payload, used to report what was produced."""
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 1
            return round(frames / float(rate), 2)
    except Exception:
        return 0.0
