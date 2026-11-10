"""Web search with explicit, honest backends.

No keyless search engine will serve a datacentre IP reliably — DuckDuckGo
answers 202 with a bot check, Mojeek 403, Brave 429. Rather than ship a search
box that silently returns nothing, Tolesh picks the first backend the user has
actually configured and says which one answered:

* ``TOLESH_TAVILY_API_KEY``   → Tavily (built for LLM agents)
* ``TOLESH_BRAVE_API_KEY``    → Brave Search API
* ``TOLESH_SEARXNG_URL``      → your own SearXNG instance (JSON output enabled)
* Wikipedia (no key)          → real results, encyclopedic queries only

When none of them can answer, :class:`SearchUnavailable` is raised with the
reason, and the caller reports it instead of inventing results.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

TIMEOUT = 15
USER_AGENT = "Tolesh/0.1 (+https://github.com/toluenetech-spec/Tolesh-AI)"


class SearchUnavailable(RuntimeError):
    """No configured backend could answer."""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


def configured_backends() -> list[str]:
    names = []
    if os.environ.get("TOLESH_TAVILY_API_KEY"):
        names.append("tavily")
    if os.environ.get("TOLESH_BRAVE_API_KEY"):
        names.append("brave")
    if os.environ.get("TOLESH_SEARXNG_URL"):
        names.append("searxng")
    if os.environ.get("TOLESH_SEARCH_DISABLE_WIKIPEDIA", "") not in ("1", "true", "yes"):
        names.append("wikipedia")
    return names


def _tavily(query: str, limit: int) -> list[dict[str, str]]:
    import requests

    resp = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": os.environ["TOLESH_TAVILY_API_KEY"], "query": query,
              "max_results": limit, "search_depth": "basic"},
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    return [{"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": _clean(r.get("content", ""))}
            for r in (resp.json().get("results") or [])][:limit]


def _brave(query: str, limit: int) -> list[dict[str, str]]:
    import requests

    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": limit},
        timeout=TIMEOUT,
        headers={"X-Subscription-Token": os.environ["TOLESH_BRAVE_API_KEY"],
                 "Accept": "application/json", "User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    results = ((resp.json().get("web") or {}).get("results") or [])[:limit]
    return [{"title": _clean(r.get("title", "")), "url": r.get("url", ""),
             "snippet": _clean(r.get("description", ""))} for r in results]


def _searxng(query: str, limit: int) -> list[dict[str, str]]:
    import requests

    base = os.environ["TOLESH_SEARXNG_URL"].rstrip("/")
    resp = requests.get(f"{base}/search", params={"q": query, "format": "json"},
                        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    payload = resp.json()
    if "results" not in payload:
        raise SearchUnavailable(
            "That SearXNG instance has JSON output disabled (settings.yml → "
            "search.formats must include json)."
        )
    return [{"title": _clean(r.get("title", "")), "url": r.get("url", ""),
             "snippet": _clean(r.get("content", ""))}
            for r in payload["results"][:limit]]


def _wikipedia(query: str, limit: int) -> list[dict[str, str]]:
    import requests

    resp = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": limit},
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    hits = (((resp.json().get("query") or {}).get("search")) or [])[:limit]
    return [{"title": _clean(h.get("title", "")),
             "url": "https://en.wikipedia.org/wiki/"
                    + str(h.get("title", "")).replace(" ", "_"),
             "snippet": _clean(h.get("snippet", ""))} for h in hits]


_BACKENDS = {"tavily": _tavily, "brave": _brave, "searxng": _searxng,
             "wikipedia": _wikipedia}


def search(query: str, *, limit: int = 5,
           backend: str | None = None) -> dict[str, Any]:
    """Search the web and say which engine answered.

    Returns ``{"backend": name, "results": [...], "attempts": [...]}``. If a
    configured backend fails, the next one is tried and the failure is kept in
    ``attempts`` so the UI can show a real reason rather than a blank list.
    """
    term = (query or "").strip()
    if not term:
        raise SearchUnavailable("Nothing to search for.")

    order = [backend] if backend else configured_backends()
    attempts: list[dict[str, str]] = []
    for name in order:
        fn = _BACKENDS.get(name)
        if fn is None:
            attempts.append({"backend": name, "error": "unknown backend"})
            continue
        try:
            results = fn(term, limit)
        except SearchUnavailable as exc:
            attempts.append({"backend": name, "error": str(exc)})
            continue
        except Exception as exc:                     # network, HTTP, JSON
            attempts.append({"backend": name, "error": f"{exc.__class__.__name__}: {exc}"})
            continue
        if results:
            return {"backend": name, "query": term, "results": results,
                    "attempts": attempts}
        attempts.append({"backend": name, "error": "no results"})

    raise SearchUnavailable(
        "No search backend answered. Configure TOLESH_TAVILY_API_KEY, "
        "TOLESH_BRAVE_API_KEY or TOLESH_SEARXNG_URL in Settings → Providers. "
        + ("; ".join(f"{a['backend']}: {a['error']}" for a in attempts) if attempts else "")
    )


def results_block(payload: dict[str, Any]) -> str:
    """Prompt-safe wrapper for search results, same untrusted-data rule."""
    lines = [f"<web_search query=\"{payload['query']}\" backend=\"{payload['backend']}\">"]
    for i, r in enumerate(payload["results"], 1):
        host = urlparse(r["url"]).hostname or r["url"]
        lines.append(f"{i}. {r['title']} ({host})\n   {r['url']}\n   {r['snippet']}")
    lines.append("</web_search>")
    return "\n".join(lines)
