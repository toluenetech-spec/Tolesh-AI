"""Read a web page and return its text, safely.

Two rules drive this module:

1. **Untrusted by construction.** Whatever a page says is data. The caller wraps
   the result in an explicit ``<web_page>`` block and the system prompt tells the
   model that instructions inside it are content, not commands.
2. **No surprise network access.** Only ``http``/``https``, only public
   addresses, a hard byte cap and a timeout. A URL that resolves to a loopback
   or private address is refused, because "fetch this URL" must not become a way
   to read the user's own machine or router.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

MAX_BYTES = 2_000_000
MAX_TEXT = 12_000
TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36 Tolesh/0.1"
)


class FetchError(RuntimeError):
    """Raised with a message that is safe to show to the user."""


def _is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"Could not resolve {host}.") from exc
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def normalise(url: str) -> str:
    """Accept a bare domain the way a person types it."""
    candidate = (url or "").strip()
    if not candidate:
        raise FetchError("No URL given.")
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"Only http and https URLs can be read, not {parsed.scheme}.")
    if not parsed.hostname:
        raise FetchError("That URL has no host.")
    return candidate


def _html_to_text(html: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "") or ""
    for tag in soup(["script", "style", "noscript", "template", "svg", "form",
                     "nav", "footer", "aside", "iframe"]):
        tag.decompose()
    # Prefer the article body when the page marks one up; otherwise take the
    # whole document. Both paths end up as plain text with paragraph breaks.
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = root.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines()]
    return title, "\n".join(ln for ln in lines if ln)


def fetch_page(url: str, *, max_text: int = MAX_TEXT) -> dict[str, Any]:
    import requests

    target = normalise(url)
    host = urlparse(target).hostname or ""
    if not _is_public(host):
        raise FetchError(
            f"{host} resolves to a private or local address, so Tolesh will not fetch it."
        )

    try:
        resp = requests.get(target, timeout=TIMEOUT, stream=True, allow_redirects=True,
                            headers={"User-Agent": USER_AGENT,
                                     "Accept": "text/html,application/xhtml+xml,text/plain"})
    except requests.RequestException as exc:
        raise FetchError(f"Could not reach {host}: {exc.__class__.__name__}") from exc

    with resp:
        if resp.status_code >= 400:
            raise FetchError(f"{host} answered HTTP {resp.status_code}.")
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and not (ctype.startswith("text/") or "html" in ctype
                          or "xml" in ctype or "json" in ctype):
            raise FetchError(f"{host} returned {ctype}, which is not readable text.")

        chunks: list[bytes] = []
        size = 0
        for block in resp.iter_content(65536):
            size += len(block)
            if size > MAX_BYTES:
                chunks.append(block[: max(0, MAX_BYTES - (size - len(block)))])
                break
            chunks.append(block)
        raw = b"".join(chunks).decode(resp.encoding or "utf-8", "replace")

    if "html" in ctype or raw.lstrip()[:1] == "<":
        title, text = _html_to_text(raw)
    else:
        title, text = "", raw.strip()

    truncated = len(text) > max_text
    return {"url": resp.url, "title": title or host, "text": text[:max_text],
            "truncated": truncated, "chars": len(text), "status": resp.status_code}


def page_block(page: dict[str, Any]) -> str:
    """The prompt-safe wrapper for fetched content.

    The delimiters are part of the contract: the system prompt refers to them by
    name, so a page cannot pretend to be the user by closing the tag itself —
    the closing marker is stripped from the body first.
    """
    body = page["text"].replace("</web_page>", "")
    return (
        f"<web_page url=\"{page['url']}\" title=\"{page['title']}\">\n"
        f"{body}\n</web_page>"
    )
