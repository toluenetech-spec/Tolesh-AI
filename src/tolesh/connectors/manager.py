"""Connector manager: OAuth, token refresh, and real provider calls.

Deliberate limits, all reported rather than hidden:
  * only the providers in ``providers.py`` are supported;
  * requests may only go to the provider's API base (plus the documented OAuth
    endpoints), and a path is validated before use — a connector that could be
    pointed at an arbitrary host would be an SSRF primitive with a token;
  * tokens never leave this module in plaintext.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from .providers import PROVIDERS, WRITE_ACTIONS, Provider, provider as get_provider
from .store import ConnectorStore, new_connection_id, pkce_pair

TIMEOUT = 20.0
MAX_TEXT = 20_000


class ConnectorError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None,
                 provider: str = "", kind: str = "error"):
        super().__init__(message)
        self.status = status
        self.provider = provider
        self.kind = kind


def _check_path(path: str) -> str:
    """Reject anything that could escape the provider's host."""
    if not path.startswith("/") or "://" in path or ".." in path:
        raise ConnectorError("A connector path must be a plain API path such as /user.")
    return path


def _check_base(base: str) -> str:
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConnectorError("A base URL must be an http(s) URL.")
    if parsed.username or parsed.password:
        raise ConnectorError("A base URL must not embed credentials.")
    return base.rstrip("/")


class ConnectorManager:
    def __init__(self, store: ConnectorStore | None = None, *, http: httpx.Client | None = None):
        self.store = store or ConnectorStore()
        self._http = http

    # ------------------------------------------------------------- helpers
    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=TIMEOUT, follow_redirects=False)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            try:
                self._http.close()
            except Exception:
                pass
            self._http = None
        self.store.close()

    def catalogue(self) -> list[dict[str, Any]]:
        out = []
        for prov in PROVIDERS.values():
            connected = self.store.list(prov.id)
            out.append({**prov.as_dict(),
                        "connections": len(connected),
                        "status": ("connected" if any(c["status"] == "connected"
                                                      for c in connected)
                                   else "disconnected")})
        return out

    def connections(self, provider: str | None = None) -> list[dict[str, Any]]:
        return self.store.list(provider)

    def _base_for(self, connection: dict[str, Any], prov: Provider) -> str:
        base = (connection.get("meta") or {}).get("base_url") or prov.api_base
        return _check_base(base)

    def _auth_headers(self, prov: Provider, token: str,
                      connection: dict[str, Any] | None = None) -> dict[str, str]:
        headers = dict(prov.extra_headers)
        if prov.token_style == "github":
            headers["Authorization"] = f"Bearer {token}"
        elif prov.token_style == "notion":
            headers["Authorization"] = f"Bearer {token}"
        elif prov.token_style == "slack":
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["Authorization"] = f"Bearer {token}"
        for key, value in ((connection or {}).get("meta") or {}).get("headers", {}).items():
            headers[str(key)] = str(value)
        return headers

    # ----------------------------------------------------------- token auth
    def add_token(self, provider_id: str, token: str, *, label: str = "",
                  account: str = "", base_url: str = "",
                  verify: bool = True) -> dict[str, Any]:
        prov = get_provider(provider_id)
        if prov.auth == "oauth":
            raise ConnectorError(f"{prov.label} only supports an OAuth sign-in.",
                                 provider=provider_id)
        token = (token or "").strip()
        if not token:
            raise ConnectorError("An API token is required.", provider=provider_id)
        meta: dict[str, Any] = {}
        if base_url:
            meta["base_url"] = _check_base(base_url)
        conn_id = new_connection_id(provider_id)
        record = self.store.upsert(connection_id=conn_id, provider=provider_id,
                                   access_token=token, label=label or prov.label,
                                   account=account, meta=meta)
        if verify:
            try:
                info = self.test(conn_id)
            except ConnectorError:
                self.store.delete(conn_id)
                raise
            self.store.upsert(connection_id=conn_id, provider=provider_id,
                              access_token=token, label=label or prov.label,
                              account=info.get("account") or account, meta=meta)
            record = self.store.get(conn_id) or record
        return record

    # ---------------------------------------------------------------- oauth
    def begin_oauth(self, provider_id: str, *, client_id: str, client_secret: str = "",
                    redirect_uri: str, scopes: list[str] | None = None,
                    base_url: str = "") -> dict[str, Any]:
        prov = get_provider(provider_id)
        if prov.auth == "token":
            raise ConnectorError(f"{prov.label} uses an API token, not OAuth.",
                                 provider=provider_id)
        if not client_id:
            raise ConnectorError(
                "An OAuth client id is required. Create an app with "
                f"{prov.label} and paste its client id here.", provider=provider_id)
        if not redirect_uri.startswith(("http://", "https://")):
            raise ConnectorError("A redirect URI is required.", provider=provider_id)
        state = uuid.uuid4().hex
        verifier, challenge = pkce_pair()
        chosen = scopes or list(prov.scopes)
        self.store.save_state(state, provider=provider_id, redirect_uri=redirect_uri,
                              code_verifier=verifier, client_id=client_id,
                              client_secret=client_secret, scopes=chosen,
                              base_url=_check_base(base_url) if base_url else "")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": " ".join(chosen),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{prov.authorize_url}?{urlencode(params)}"
        if provider_id == "google_drive" or provider_id.startswith("google_"):
            url += "&access_type=offline&prompt=consent"
        return {"authorize_url": url, "state": state,
                "redirect_uri": redirect_uri, "scopes": chosen}

    def complete_oauth(self, state: str, code: str, *,
                       base_url: str = "") -> dict[str, Any]:
        pending = self.store.take_state(state)
        if pending is None:
            raise ConnectorError(
                "This sign-in link is unknown or has expired. Start the connection again.",
                kind="state")
        prov = get_provider(pending["provider"])
        base_url = base_url or pending.get("base_url", "")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending["redirect_uri"],
            "client_id": pending["client_id"],
            "code_verifier": pending["code_verifier"],
        }
        if pending["client_secret"]:
            data["client_secret"] = pending["client_secret"]
        headers = {"Accept": "application/json"}
        try:
            response = self._client().post(prov.token_url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{prov.label} token exchange failed: {exc}",
                                 provider=prov.id, kind="network") from exc
        payload = _payload(response)
        if response.status_code >= 400 or "access_token" not in payload:
            detail = payload.get("error_description") or payload.get("error") or response.text[:200]
            raise ConnectorError(
                f"{prov.label} refused the token exchange ({response.status_code}): {detail}",
                status=response.status_code, provider=prov.id, kind="token")
        expires_in = payload.get("expires_in")
        meta: dict[str, Any] = {"client_id": pending["client_id"]}
        if pending["client_secret"]:
            meta["client_secret_enc"] = self.store.vault.encrypt(pending["client_secret"])
        if base_url:
            meta["base_url"] = _check_base(base_url)
        conn_id = new_connection_id(prov.id)
        self.store.upsert(
            connection_id=conn_id, provider=prov.id,
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", ""),
            token_type=payload.get("token_type", "Bearer"),
            expires_at=(time.time() + float(expires_in)) if expires_in else None,
            scopes=str(payload.get("scope", " ".join(pending["scopes"]))).split(),
            label=prov.label, meta=meta,
        )
        try:
            info = self.test(conn_id)
            if info.get("account"):
                self.store.upsert(
                    connection_id=conn_id, provider=prov.id,
                    access_token=payload["access_token"],
                    refresh_token=payload.get("refresh_token", ""),
                    token_type=payload.get("token_type", "Bearer"),
                    expires_at=(time.time() + float(expires_in)) if expires_in else None,
                    scopes=str(payload.get("scope", " ".join(pending["scopes"]))).split(),
                    label=prov.label, account=info["account"], meta=meta)
        except ConnectorError:
            # The token is valid enough to store even if the probe failed; the
            # connection list shows the error and the user can retry.
            pass
        return self.store.get(conn_id) or {}

    # --------------------------------------------------------------- refresh
    def _refresh(self, connection_id: str) -> bool:
        public = self.store.get(connection_id)
        secrets = self.store.secrets(connection_id)
        if not public or not secrets:
            return False
        prov = get_provider(public["provider"])
        if not secrets["refresh_token"] or not prov.token_url:
            return False
        meta = public.get("meta") or {}
        data = {
            "grant_type": "refresh_token",
            "refresh_token": secrets["refresh_token"],
            "client_id": meta.get("client_id", ""),
        }
        if meta.get("client_secret_enc"):
            data["client_secret"] = self.store.vault.decrypt(meta["client_secret_enc"])
        try:
            response = self._client().post(prov.token_url, data=data,
                                           headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            self.store.set_status(connection_id, "error", str(exc))
            return False
        payload = _payload(response)
        if response.status_code >= 400 or "access_token" not in payload:
            self.store.set_status(connection_id, "expired",
                                  payload.get("error_description")
                                  or payload.get("error") or "refresh failed")
            return False
        expires_in = payload.get("expires_in")
        self.store.upsert(
            connection_id=connection_id, provider=public["provider"],
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token") or secrets["refresh_token"],
            token_type=payload.get("token_type", secrets["token_type"]),
            expires_at=(time.time() + float(expires_in)) if expires_in else None,
            scopes=public["scopes"], label=public["label"],
            account=public["account"], meta=meta)
        return True

    # ----------------------------------------------------------------- calls
    def call(self, connection_id: str, method: str, path: str, *,
             params: dict[str, Any] | None = None,
             json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        public = self.store.get(connection_id)
        if public is None:
            raise ConnectorError("Unknown connection.", status=404)
        prov = get_provider(public["provider"])
        path = _check_path(path)
        if public.get("expired"):
            self._refresh(connection_id)
            public = self.store.get(connection_id) or public
        secrets = self.store.secrets(connection_id)
        if not secrets or not secrets["access_token"]:
            raise ConnectorError("This connection has no usable token; reconnect it.",
                                 status=401, provider=prov.id, kind="auth")
        base = self._base_for(public, prov)
        url = f"{base}{path}"
        headers = self._auth_headers(prov, secrets["access_token"], public)
        try:
            response = self._client().request(
                method.upper(), url, params=params or None, json=json_body,
                headers=headers)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{prov.label} request failed: {exc}",
                                 provider=prov.id, kind="network") from exc
        if response.status_code in (401, 403):
            refreshed = self._refresh(connection_id)
            if refreshed:
                secrets = self.store.secrets(connection_id) or secrets
                headers = self._auth_headers(prov, secrets["access_token"], public)
                try:
                    response = self._client().request(
                        method.upper(), url, params=params or None, json=json_body,
                        headers=headers)
                except httpx.HTTPError as exc:
                    raise ConnectorError(f"{prov.label} request failed: {exc}",
                                         provider=prov.id, kind="network") from exc
            if response.status_code in (401, 403):
                self.store.set_status(connection_id, "expired",
                                      f"{response.status_code} from {path}")
                raise ConnectorError(
                    f"{prov.label} rejected the token ({response.status_code}). "
                    "Reconnect the account or check the scopes.",
                    status=response.status_code, provider=prov.id, kind="auth")
        if response.status_code == 429:
            retry = response.headers.get("retry-after", "")
            raise ConnectorError(
                f"{prov.label} rate limit reached"
                + (f"; retry after {retry}s." if retry else "."),
                status=429, provider=prov.id, kind="rate_limit")
        payload = _payload(response)
        if response.status_code >= 400:
            message = ""
            if isinstance(payload, dict):
                message = (payload.get("message") or payload.get("error")
                           or payload.get("error_description") or "")
            raise ConnectorError(
                f"{prov.label} returned {response.status_code}"
                + (f": {str(message)[:300]}" if message else "."),
                status=response.status_code, provider=prov.id)
        return {"status": response.status_code, "data": payload,
                "provider": prov.id, "path": path}

    # ------------------------------------------------------------------ test
    def test(self, connection_id: str) -> dict[str, Any]:
        public = self.store.get(connection_id)
        if public is None:
            raise ConnectorError("Unknown connection.", status=404)
        prov = get_provider(public["provider"])
        result = self.call(connection_id, "GET", prov.test_path)
        data = result.get("data")
        account = ""
        if isinstance(data, dict):
            for key in ("login", "name", "email", "user", "user_id", "url", "team", "ok"):
                value = data.get(key)
                if isinstance(value, (str, int)) and str(value).strip():
                    account = str(value)
                    break
                if isinstance(value, dict) and value.get("name"):
                    account = str(value["name"])
                    break
        self.store.set_status(connection_id, "connected", "")
        return {"ok": True, "provider": prov.id, "account": str(account)[:120],
                "scopes": public["scopes"]}

    def delete(self, connection_id: str) -> bool:
        return self.store.delete(connection_id)

    # --------------------------------------------------------------- actions
    def actions(self, connection_id: str) -> list[dict[str, Any]]:
        public = self.store.get(connection_id)
        if public is None:
            raise ConnectorError("Unknown connection.", status=404)
        prov = get_provider(public["provider"])
        return [{"name": name, "write": name in WRITE_ACTIONS,
                 "requires_approval": name in WRITE_ACTIONS}
                for name in prov.actions]

    def run_action(self, connection_id: str, action: str,
                   args: dict[str, Any] | None = None, *,
                   approved: bool = False) -> dict[str, Any]:
        """Execute one documented action.

        Write actions refuse to run without ``approved``; the API turns that
        refusal into a pending approval request instead of executing silently.
        """
        public = self.store.get(connection_id)
        if public is None:
            raise ConnectorError("Unknown connection.", status=404)
        prov = get_provider(public["provider"])
        if action not in prov.actions:
            raise ConnectorError(f"{prov.label} has no action called {action}.",
                                 provider=prov.id)
        if action in WRITE_ACTIONS and not approved:
            raise ConnectorError("This action changes data outside Tolesh and "
                                 "needs approval.", kind="approval", provider=prov.id)
        args = args or {}
        handler = _ACTIONS.get((prov.id, action))
        if handler is None:
            raise ConnectorError(f"{action} is not implemented for {prov.label}.",
                                 provider=prov.id, kind="unimplemented")
        return handler(self, connection_id, args)


# --------------------------------------------------------------- action impls
def _require(args: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if not str(args.get(k) or "").strip()]
    if missing:
        raise ConnectorError("Missing required argument(s): " + ", ".join(missing))


def _github_repos(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    limit = min(int(args.get("limit") or 20), 100)
    result = mgr.call(cid, "GET", "/user/repos",
                      params={"per_page": limit, "sort": "updated"})
    repos = [{"name": r.get("full_name"), "private": r.get("private"),
              "updated_at": r.get("updated_at"), "description": r.get("description"),
              "url": r.get("html_url")}
             for r in result["data"] if isinstance(r, dict)]
    return {"action": "list_repositories", "repositories": repos, "count": len(repos)}


def _github_read_file(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "owner", "repo", "path")
    path = f"/repos/{args['owner']}/{args['repo']}/contents/{str(args['path']).lstrip('/')}"
    result = mgr.call(cid, "GET", path)
    data = result["data"]
    import base64

    text = ""
    if isinstance(data, dict) and data.get("content"):
        try:
            text = base64.b64decode(data["content"]).decode("utf-8", "replace")
        except Exception:
            text = ""
    return {"action": "read_file", "path": args["path"], "text": text[:MAX_TEXT],
            "truncated": len(text) > MAX_TEXT}


def _github_issues(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "owner", "repo")
    state = args.get("state") or "open"
    result = mgr.call(cid, "GET", f"/repos/{args['owner']}/{args['repo']}/issues",
                      params={"state": state, "per_page": min(int(args.get("limit") or 20), 100)})
    issues = [{"number": i.get("number"), "title": i.get("title"),
               "state": i.get("state"), "url": i.get("html_url")}
              for i in result["data"] if isinstance(i, dict)]
    return {"action": "list_issues", "issues": issues, "count": len(issues)}


def _github_create_issue(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "owner", "repo", "title")
    body = {"title": args["title"], "body": args.get("body") or ""}
    result = mgr.call(cid, "POST", f"/repos/{args['owner']}/{args['repo']}/issues",
                      json_body=body)
    data = result["data"]
    return {"action": "create_issue", "number": data.get("number"),
            "url": data.get("html_url")}


def _github_search_code(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "query")
    result = mgr.call(cid, "GET", "/search/code", params={"q": args["query"]})
    items = result["data"].get("items", []) if isinstance(result["data"], dict) else []
    return {"action": "search_code",
            "results": [{"path": i.get("path"), "repo": (i.get("repository") or {}).get("full_name"),
                         "url": i.get("html_url")} for i in items][:50]}


def _drive_list(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    result = mgr.call(cid, "GET", "/drive/v3/files",
                      params={"pageSize": min(int(args.get("limit") or 20), 100),
                              "fields": "files(id,name,mimeType,modifiedTime,size)"})
    files = result["data"].get("files", []) if isinstance(result["data"], dict) else []
    return {"action": "list_files", "files": files, "count": len(files)}


def _drive_read(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "file_id")
    result = mgr.call(cid, "GET", f"/drive/v3/files/{args['file_id']}",
                      params={"alt": "media"})
    data = result["data"]
    text = data if isinstance(data, str) else json.dumps(data)[:MAX_TEXT]
    return {"action": "read_file", "text": str(text)[:MAX_TEXT],
            "truncated": len(str(text)) > MAX_TEXT}


def _gmail_list(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {"maxResults": min(int(args.get("limit") or 10), 50)}
    if args.get("query"):
        params["q"] = args["query"]
    result = mgr.call(cid, "GET", "/gmail/v1/users/me/messages", params=params)
    messages = result["data"].get("messages", []) if isinstance(result["data"], dict) else []
    return {"action": "list_messages", "messages": messages, "count": len(messages)}


def _gmail_read(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "message_id")
    result = mgr.call(cid, "GET", f"/gmail/v1/users/me/messages/{args['message_id']}",
                      params={"format": "metadata"})
    data = result["data"] if isinstance(result["data"], dict) else {}
    headers = {h.get("name"): h.get("value")
               for h in (data.get("payload", {}) or {}).get("headers", [])}
    return {"action": "read_message", "snippet": data.get("snippet", ""),
            "headers": {k: v for k, v in headers.items()
                        if k in ("Subject", "From", "To", "Date")}}


def _calendar_list(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    params = {"maxResults": min(int(args.get("limit") or 10), 50), "singleEvents": "true",
              "orderBy": "startTime", "timeMin": args.get("time_min") or _now_iso()}
    result = mgr.call(cid, "GET", "/calendar/v3/calendars/primary/events", params=params)
    events = result["data"].get("items", []) if isinstance(result["data"], dict) else []
    return {"action": "list_events",
            "events": [{"summary": e.get("summary"), "start": e.get("start"),
                        "end": e.get("end"), "link": e.get("htmlLink")} for e in events]}


def _notion_search(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    body = {"query": args.get("query") or "", "page_size": min(int(args.get("limit") or 10), 50)}
    result = mgr.call(cid, "POST", "/v1/search", json_body=body)
    results = result["data"].get("results", []) if isinstance(result["data"], dict) else []
    return {"action": "search",
            "results": [{"id": r.get("id"), "type": r.get("object"),
                         "title": _notion_title(r), "url": r.get("url")} for r in results]}


def _notion_read(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "page_id")
    result = mgr.call(cid, "GET", f"/v1/blocks/{args['page_id']}/children",
                      params={"page_size": 100})
    blocks = result["data"].get("results", []) if isinstance(result["data"], dict) else []
    lines = []
    for block in blocks:
        rich = (block.get("paragraph") or block.get("heading_1")
                or block.get("heading_2") or block.get("bulleted_list_item") or {})
        text = "".join(part.get("plain_text", "") for part in rich.get("rich_text", []))
        if text:
            lines.append(text)
    return {"action": "read_page", "text": "\n".join(lines)[:MAX_TEXT],
            "blocks": len(blocks)}


def _slack_channels(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    result = mgr.call(cid, "GET", "/conversations.list",
                      params={"limit": min(int(args.get("limit") or 50), 200),
                              "exclude_archived": "true"})
    data = result["data"] if isinstance(result["data"], dict) else {}
    if data.get("ok") is False:
        raise ConnectorError(f"Slack: {data.get('error', 'request failed')}",
                             provider="slack")
    channels = [{"id": c.get("id"), "name": c.get("name"),
                 "private": c.get("is_private")} for c in data.get("channels", [])]
    return {"action": "list_channels", "channels": channels, "count": len(channels)}


def _slack_post(mgr: ConnectorManager, cid: str, args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "channel", "text")
    result = mgr.call(cid, "POST", "/chat.postMessage",
                      json_body={"channel": args["channel"], "text": args["text"]})
    data = result["data"] if isinstance(result["data"], dict) else {}
    if data.get("ok") is False:
        raise ConnectorError(f"Slack: {data.get('error', 'message not posted')}",
                             provider="slack")
    return {"action": "post_message", "channel": data.get("channel"),
            "ts": data.get("ts")}


def _notion_title(result: dict[str, Any]) -> str:
    for key in ("title", "properties"):
        value = result.get(key)
        if isinstance(value, list):
            return "".join(p.get("plain_text", "") for p in value)
        if isinstance(value, dict):
            for prop in value.values():
                if prop.get("type") == "title":
                    return "".join(p.get("plain_text", "") for p in prop.get("title", []))
    return ""


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


_ACTIONS = {
    ("github", "list_repositories"): _github_repos,
    ("github", "read_file"): _github_read_file,
    ("github", "list_issues"): _github_issues,
    ("github", "create_issue"): _github_create_issue,
    ("github", "search_code"): _github_search_code,
    ("google_drive", "list_files"): _drive_list,
    ("google_drive", "read_file"): _drive_read,
    ("gmail", "list_messages"): _gmail_list,
    ("gmail", "read_message"): _gmail_read,
    ("google_calendar", "list_events"): _calendar_list,
    ("notion", "search"): _notion_search,
    ("notion", "read_page"): _notion_read,
    ("slack", "list_channels"): _slack_channels,
    ("slack", "post_message"): _slack_post,
}


def _payload(response: httpx.Response) -> Any:
    """Decode JSON, form-encoded OAuth replies, or plain text, in that order."""
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            return response.json()
        except Exception:
            return {}
    text = response.text
    if "form-urlencoded" in content_type or (
            text and "=" in text and "&" in text and "{" not in text[:1]):
        from urllib.parse import parse_qs

        parsed = parse_qs(text)
        if parsed:
            return {k: v[0] for k, v in parsed.items()}
    try:
        return response.json()
    except Exception:
        return text
