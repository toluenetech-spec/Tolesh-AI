"""Audio-level wake-word detection.

``wake_detection.py`` decides from the *transcript*, so the decision arrives
after Whisper has already transcribed the utterance — which means every sound
in the room is transcribed whether or not it was meant for Tolesh. This module
decides from the *audio*, before any transcription happens, so a gate can drop
an utterance without spending a Whisper pass on it.

Every backend here is optional and imported lazily. A missing package is a
normal, reportable state — not an import error and never a hard dependency.
When no backend can load, the listener keeps transcribing everything and the
transcript-level detector keeps working exactly as before.

Nothing in this module runs on the real-time audio callback. ``feed`` is called
from the audio-frame worker thread, so a slow model delays transcription, never
the microphone.
"""

from __future__ import annotations

import os
from typing import Any

try:  # numpy is optional elsewhere in the listener, so match that tolerance.
    import numpy as np
except Exception:  # pragma: no cover - numpy is present in every supported install
    np = None  # type: ignore[assignment]

__all__ = ["HotwordError", "HotwordDetector", "OpenWakeWordDetector",
           "PorcupineDetector", "create_detector", "probe"]


class HotwordError(RuntimeError):
    """No detector could be built from the current configuration."""


class HotwordDetector:
    """Base class: buffers arbitrary frame sizes into the backend's chunk.

    The microphone delivers whatever block size the driver chose (often 20 ms),
    while wake-word models want a fixed window (openWakeWord: 1280 samples,
    Porcupine: 512). Buffering here keeps that mismatch out of the listener.
    """

    name = "base"
    sample_rate = 16000
    chunk = 1280

    def __init__(self, threshold: float = 0.5):
        self.threshold = max(0.0, min(1.0, float(threshold)))
        self._buffer: Any = None

    # ---------------------------------------------------------------- public
    def feed(self, frame: Any) -> float:
        """Consume one frame and return the highest score it produced.

        A frame shorter than the backend's chunk returns 0.0 and is buffered;
        the next call finishes it. Callers compare the result to ``threshold``.
        """
        if np is None or frame is None:
            return 0.0
        data = np.asarray(frame, dtype=np.float32).reshape(-1)
        if data.size == 0:
            return 0.0
        if self._buffer is not None and self._buffer.size:
            data = np.concatenate((self._buffer, data))
        best = 0.0
        start = 0
        while data.size - start >= self.chunk:
            best = max(best, float(self._predict(data[start:start + self.chunk])))
            start += self.chunk
        self._buffer = data[start:].copy() if data.size > start else None
        return best

    def reset(self) -> None:
        """Drop buffered audio — used between utterances."""
        self._buffer = None

    def close(self) -> None:
        """Release the backend's resources. Safe to call twice."""

    def status(self) -> dict[str, Any]:
        return {"backend": self.name, "threshold": self.threshold,
                "sample_rate": self.sample_rate,
                "chunk_ms": round(self.chunk * 1000 / self.sample_rate)}

    # --------------------------------------------------------------- backend
    def _predict(self, chunk: Any) -> float:  # pragma: no cover - abstract
        raise NotImplementedError


class OpenWakeWordDetector(HotwordDetector):
    """openWakeWord (Apache-2.0): no access key, models download on first use."""

    name = "openwakeword"

    def __init__(self, model: str | None = None, threshold: float = 0.5):
        super().__init__(threshold)
        try:
            from openwakeword.model import Model
        except Exception as exc:
            raise HotwordError(f"openwakeword is not installed ({exc})") from exc
        kwargs: dict[str, Any] = {}
        if model:
            kwargs["wakeword_models"] = [str(model)]
        try:
            self._model = Model(**kwargs, inference_framework="onnx")
        except ValueError as exc:
            if "Could not open" in str(exc):
                # The package ships without model files; a first run that says
                # only "Could not open" is a dead end for the user.
                raise HotwordError(
                    "openWakeWord has no model files yet — run "
                    "`python -m openwakeword.utils.download_models` once, "
                    "then test again") from exc
            raise
        except Exception:
            # onnxruntime missing but tflite present (or vice versa): let the
            # library choose rather than refusing to start.
            try:
                self._model = Model(**kwargs)
            except ValueError as exc:
                if "Could not open" in str(exc):
                    raise HotwordError(
                        "openWakeWord has no model files yet — run "
                        "`python -m openwakeword.utils.download_models` once, "
                        "then test again") from exc
                raise
        self.models = list(getattr(self._model, "models", {}) or {})
        if not self.models:
            raise HotwordError("openWakeWord loaded no wake-word models")

    def _predict(self, chunk: Any) -> float:
        scores = self._model.predict(np.clip(chunk, -1.0, 1.0))
        if not scores:
            return 0.0
        return float(max(scores.values()))

    def reset(self) -> None:
        super().reset()
        try:
            self._model.reset()
        except Exception:
            pass

    def status(self) -> dict[str, Any]:
        return {**super().status(), "models": self.models}


class PorcupineDetector(HotwordDetector):
    """Picovoice Porcupine: needs an access key and its own licence terms.

    The key is read from ``TOLESH_PORCUPINE_KEY`` and never stored in config,
    logged, or echoed in an error message.
    """

    name = "porcupine"

    def __init__(self, access_key: str, keyword: str | None = None,
                 threshold: float = 0.5):
        super().__init__(threshold)
        if not access_key:
            raise HotwordError("Porcupine needs TOLESH_PORCUPINE_KEY set")
        try:
            import pvporcupine
        except Exception as exc:
            raise HotwordError(f"pvporcupine is not installed ({exc})") from exc
        kwargs: dict[str, Any] = {"access_key": access_key}
        if keyword:
            kwargs["keywords"] = [keyword]
        kwargs["sensitivities"] = [self.threshold]
        try:
            self._ppn = pvporcupine.create(**kwargs)
        except Exception as exc:
            message = str(exc).replace(access_key, "***")
            raise HotwordError(f"Porcupine refused the configuration: {message}") from exc
        self.chunk = int(self._ppn.frame_length)
        self.sample_rate = int(self._ppn.sample_rate)
        self.keyword = keyword or "built-in"

    def _predict(self, chunk: Any) -> float:
        pcm = (np.clip(chunk, -1.0, 1.0) * 32767).astype(np.int16)
        return 1.0 if self._ppn.process(pcm) >= 0 else 0.0

    def close(self) -> None:
        try:
            self._ppn.delete()
        except Exception:
            pass

    def status(self) -> dict[str, Any]:
        return {**super().status(), "keyword": self.keyword}


def create_detector(cfg: Any) -> HotwordDetector:
    """Build the configured detector, or explain why none could be built.

    ``hotword_backend="auto"`` tries openWakeWord first: it needs no key and no
    account, so it is the one a user can actually get working on their own.
    """
    backend = str(getattr(cfg, "hotword_backend", "auto") or "auto").strip().lower()
    threshold = float(getattr(cfg, "hotword_threshold", 0.5) or 0.5)
    model = str(getattr(cfg, "hotword_model", "") or "").strip() or None
    wanted = ["openwakeword", "porcupine"] if backend == "auto" else [backend]
    problems: list[str] = []
    for name in wanted:
        try:
            if name == "openwakeword":
                return OpenWakeWordDetector(model=model, threshold=threshold)
            if name == "porcupine":
                key = os.environ.get("TOLESH_PORCUPINE_KEY", "").strip()
                return PorcupineDetector(access_key=key, keyword=model, threshold=threshold)
            problems.append(f"unknown backend {name!r}")
        except HotwordError as exc:
            problems.append(str(exc))
        except Exception as exc:  # a backend must never crash the caller
            problems.append(f"{name}: {type(exc).__name__}: {exc}")
    raise HotwordError("; ".join(problems) or "no wake-word backend configured")


def probe(cfg: Any) -> dict[str, Any]:
    """Load the configured detector, score one chunk of silence, unload it.

    This is what the Settings "test" button calls: it proves the package
    imports and the model loads, and it reports the silence score — a detector
    that fires on silence is broken even though it loaded fine.
    """
    try:
        detector = create_detector(cfg)
    except Exception as exc:
        return {"ok": False, "backend": None, "error": str(exc), "silence_score": None}
    try:
        score = 0.0
        if np is not None:
            score = detector.feed(np.zeros(detector.chunk, dtype=np.float32))
        return {"ok": True, "error": None, **detector.status(),
                "silence_score": round(float(score), 3)}
    finally:
        detector.close()
