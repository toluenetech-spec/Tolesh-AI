"""Short-lived signed URLs for files the browser loads by itself.

An ``<img src>`` cannot send an ``Authorization`` header, so a token-guarded
API breaks every preview the moment Tolesh is exposed beyond localhost: the
chart tile renders as a broken image and the console fills with 401s. Putting
the API token in the query string would fix the image and leak the credential
into every access log, proxy cache and screenshot of the URL.

Instead the server hands out a URL that is only good for one artifact, for one
hour, and only for the file endpoint. The signature is an HMAC over the id and
the expiry, keyed by a secret that lives in memory for the life of the process:
a leaked URL stops working when the server restarts, and it cannot be replayed
against any other route.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

DEFAULT_TTL = 3600


def sign(secret: bytes, artifact_id: str, *, ttl: int = DEFAULT_TTL,
         now: float | None = None) -> dict[str, str]:
    expires = int((now or time.time()) + ttl)
    signature = hmac.new(secret, f"{artifact_id}:{expires}".encode("utf-8"),
                         hashlib.sha256).hexdigest()[:32]
    return {"exp": str(expires), "sig": signature}


def verify(secret: bytes, artifact_id: str, *, exp: str | None,
           sig: str | None) -> bool:
    if not exp or not sig:
        return False
    try:
        expires = int(exp)
    except (TypeError, ValueError):
        return False
    if expires < time.time():
        return False
    expected = hmac.new(secret, f"{artifact_id}:{expires}".encode("utf-8"),
                        hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expected, sig)


def download_url(secret: bytes, artifact_id: str, *, ttl: int = DEFAULT_TTL) -> str:
    params = sign(secret, artifact_id, ttl=ttl)
    return (f"/api/artifacts/{quote(artifact_id)}/download"
            f"?exp={params['exp']}&sig={params['sig']}")


def with_signed_url(secret: bytes, artifact: dict, *,
                    ttl: int = DEFAULT_TTL) -> dict:
    """Copy of the artifact metadata whose ``download_url`` is signed."""
    out = dict(artifact)
    if out.get("id"):
        out["download_url"] = download_url(secret, out["id"], ttl=ttl)
    return out
