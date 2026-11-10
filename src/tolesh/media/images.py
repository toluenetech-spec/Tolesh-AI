"""Image understanding and generation.

Understanding goes through the normal router: an image is sent as an
OpenAI-style content part, which is what every vision-capable model on these
gateways accepts. If the chosen model cannot read images the provider answers
400 and the router stops the chain, so the user gets that answer instead of a
silent fallback to a text-only model pretending to see.

Generation needs a provider; there is no local diffusion model in this build.
Without one configured, the API says so instead of returning a placeholder.
"""

from __future__ import annotations

import base64
import os
from typing import Any
from urllib.parse import urlparse

import httpx

MAX_IMAGE_BYTES = 12 * 1024 * 1024


class MediaError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def image_settings() -> dict[str, str]:
    return {
        "base_url": os.environ.get("TOLESH_IMAGE_BASE_URL", "https://api.openai.com/v1"),
        "api_key": os.environ.get("TOLESH_IMAGE_API_KEY", ""),
        "model": os.environ.get("TOLESH_IMAGE_MODEL", "gpt-image-1"),
    }


def vision_message(prompt: str, image: bytes, mime: str = "image/png") -> dict[str, Any]:
    """One user message carrying text and an inline image."""
    if len(image) > MAX_IMAGE_BYTES:
        raise MediaError("The image is larger than 12 MB.")
    encoded = base64.b64encode(image).decode("ascii")
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:{mime or 'image/png'};base64,{encoded}"}},
        ],
    }


def describe_image(router: Any, image: bytes, *, prompt: str = "Describe this image.",
                   model: str | None = None, mime: str = "image/png") -> dict[str, Any]:
    """Ask a vision model about an image."""
    message = vision_message(prompt, image, mime)
    try:
        result = router.complete([message], task="chat", model=model,
                                 temperature=0.2, max_tokens=700)
    except Exception as exc:
        text = str(exc)
        if "400" in text or "image" in text.lower():
            raise MediaError(
                "The selected model did not accept the image. Choose a "
                "vision-capable model (for example a GPT-4o or a VLM on your "
                f"gateway). Provider said: {text[:200]}", kind="unsupported") from exc
        raise MediaError(f"The image could not be analysed: {text[:300]}") from exc
    return {"text": getattr(result, "text", "") or "",
            "model": getattr(result, "model_id", "") or "",
            "chars": len(getattr(result, "text", "") or "")}


def generate_image(prompt: str, *, size: str = "1024x1024",
                   model: str | None = None, settings: dict[str, str] | None = None,
                   client: httpx.Client | None = None) -> tuple[bytes, dict[str, Any]]:
    """Generate one image through an OpenAI-compatible images endpoint."""
    if not (prompt or "").strip():
        raise MediaError("A prompt is required.")
    config = settings or image_settings()
    if not config.get("api_key"):
        raise MediaError(
            "Image generation is not configured. Set TOLESH_IMAGE_API_KEY (and "
            "optionally TOLESH_IMAGE_BASE_URL / TOLESH_IMAGE_MODEL) or add the "
            "key in Settings.", kind="unconfigured")
    payload = {"model": model or config["model"], "prompt": prompt, "n": 1,
               "size": size}
    owned = client is None
    http = client or httpx.Client(timeout=120)
    try:
        response = http.post(f"{config['base_url'].rstrip('/')}/images/generations",
                             json=payload,
                             headers={"Authorization": f"Bearer {config['api_key']}"})
        if response.status_code >= 400:
            detail = ""
            try:
                detail = response.json().get("error", {}).get("message", "")
            except Exception:
                detail = response.text[:200]
            raise MediaError(f"The image provider returned {response.status_code}: {detail}",
                             kind="provider")
        data = response.json()
        items = data.get("data") or []
        if not items:
            raise MediaError("The image provider returned no image.")
        item = items[0]
        if item.get("b64_json"):
            image = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            url = str(item["url"])
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise MediaError("The provider returned a non-http image URL.")
            download = http.get(url)
            if download.status_code >= 400:
                raise MediaError("The generated image could not be downloaded.")
            image = download.content
        else:
            raise MediaError("The image provider returned neither data nor a URL.")
        if len(image) > MAX_IMAGE_BYTES:
            raise MediaError("The generated image is larger than 12 MB.")
        return image, {"model": payload["model"], "size": size,
                       "revised_prompt": item.get("revised_prompt", "")}
    except httpx.HTTPError as exc:
        raise MediaError(f"The image provider could not be reached: {exc}",
                         kind="network") from exc
    finally:
        if owned:
            http.close()
