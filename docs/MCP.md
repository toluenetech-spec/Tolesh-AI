# MCP servers in Tolesh

Tolesh speaks the [Model Context Protocol](https://modelcontextprotocol.io) so a
tool written for another client — a filesystem browser, a database gateway, an
issue tracker — can be used here without changing Tolesh's code.

This document is the operating manual: what is supported, how to add a server,
how permissions work, what happens when something breaks, and what is
deliberately missing.

---

## What is supported

| Item | State | Notes |
|---|---|---|
| `stdio` transport | ✅ | A child process, spoken to over stdin/stdout. |
| Streamable HTTP transport | ✅ | `POST` + SSE responses, with optional static headers. |
| Legacy HTTP+SSE transport (2024-11-05) | ⛔ | Superseded by streamable HTTP; supporting both doubles the client surface for no user-visible gain. |
| OAuth / dynamic client registration for MCP | ⛔ | The specification is still moving. Use a static bearer token in a header, or a PAT, and rotate it yourself. |
| Tool discovery | ✅ | `tools/list`, cached per session, refreshed on reconnect. |
| Tool execution | ✅ | `tools/call`, one call at a time per server, with the server's own `timeout_sec`. |
| Resource discovery and reading | ✅ | `resources/list`, `resources/read`. |
| Prompt discovery and use | ✅ | `prompts/list`, `prompts/get` — the prompt's messages are returned to the caller, not injected silently. |
| Per-tool permissions | ✅ | `allow` / `ask` / `deny`, per server with per-tool overrides. |
| Approval round-trip | ✅ | `ask` parks at the permission gate; the call resumes after approval. |
| Connection testing | ✅ | Reports tool count and round-trip time, or the reason it failed. |
| Audit log | ✅ | Every call, with arguments masked for anything that looks like a secret. |

Protocol version negotiated with the Python SDK is `2025-06-18` (`mcp==1.13.1`).

---

## Where configuration lives

```
~/.config/tolesh/mcp_servers.json      (mode 0600)
```

Override the location with `TOLESH_MCP_PATH`, or the directory with
`TOLESH_CONFIG_DIR`. The file is written atomically and is **never** served
back with real secrets: `GET /api/mcp/servers` returns `abc***yz` previews.
Posting a masked preview back is a no-op, so a round-trip through the UI cannot
overwrite a real token with asterisks.

A server entry:

```json
{
  "servers": {
    "files": {
      "name": "files",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/me/notes"],
      "env": { "LOG_LEVEL": "warn" },
      "enabled": true,
      "timeout_sec": 30,
      "default_policy": "allow",
      "tool_permissions": { "write_file": "ask", "delete_file": "deny" },
      "notes": "Read-mostly access to the notes folder."
    },
    "issues": {
      "name": "issues",
      "transport": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer abc***yz" },
      "enabled": true,
      "timeout_sec": 45,
      "default_policy": "ask"
    }
  }
}
```

Names are 1–40 characters of letters, digits, `.`, `_` or `-`, and must start
with a letter or digit. The name becomes part of a tool id the model sees, so
it is validated rather than trusted.

---

## Adding a server

### In the app

1. Open **Workspace → MCP**.
2. Fill in a name, a transport, and either a command (stdio) or a URL (HTTP).
3. Choose the default tool permission. Leave it on `allow` for read-only
   servers; choose `ask` for anything that writes.
4. **Add server**, then **Test**. A green `connected` badge with a tool count
   means the process started, completed the handshake and answered `tools/list`.
5. **Tools** lists what the server exposes, with each tool's effective
   permission and a **Run** button for a manual call.

### Over HTTP

```bash
curl -X POST http://127.0.0.1:8787/api/mcp/servers \
  -H 'Content-Type: application/json' \
  -d '{"name":"files","transport":"stdio","command":"npx",
       "args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]}'

curl -X POST http://127.0.0.1:8787/api/mcp/servers/files/test
```

---

## Tool ids and permissions

A tool is addressed as `mcp__<server>__<tool>`, for example
`mcp__files__read_file`. The same id is used in the agent loop, in
`/api/mcp/tools` and in the audit log, so a call can be traced from the model's
transcript to the process that ran it.

The effective permission for a tool is:

```
tool_permissions[tool]  ??  default_policy  ??  "allow"
```

| Permission | Behaviour |
|---|---|
| `allow` | The call runs immediately. |
| `ask` | The call is parked. `POST /api/mcp/invoke` returns `{"status": "pending-approval", "approval": {...}}`. The user approves through `/api/permissions/{id}/approve`, then repeats the call with `approval_id` (and `approval_token` when one was issued). |
| `deny` | The call is refused before anything reaches the server, and the refusal is recorded. |

A server can be disabled (`enabled: false`) without deleting it; its tools then
disappear from `/api/mcp/tools` and from the agent's catalogue.

### Why the approval is a separate request

The permission gate is the single place where a human says yes. Routing an
`ask` tool through it means the MCP layer does not need its own approval UI, and
a connector write and an MCP write look identical to the person approving them.
The cost is one extra round-trip, which the API makes explicit rather than
hiding inside a long request.

---

## Errors you will actually see

| Situation | What the API says |
|---|---|
| Command not found | `MCP server "x" could not start: the command "npx" was not found.` |
| Process exits immediately | The server's stderr tail is included in the error, clipped. |
| Handshake never completes | `Timed out after 30s while connecting.` |
| HTTP 401/403 | The status and the response body's first 300 characters. |
| A tool raises | `{"status": "error", "is_error": true}` with the server's message; the session stays open. |
| Session dropped mid-call | One automatic reconnect, then a clear error rather than a hung request. |

A failed connection is stored on the server's status, so the list shows
`error` with the reason instead of pretending the server is idle.

---

## Security notes, stated plainly

* **A stdio server is arbitrary code running as you.** It inherits your user,
  your files and your network. Tolesh passes a stripped environment (no
  provider keys), but that is hygiene, not a sandbox. Only add servers you
  would run from a shell.
* **An HTTP server is a remote party.** Its tools are described by it and its
  responses are treated as data: the agent wraps every tool result in
  `<tool_result>` and the persona is instructed not to follow instructions found
  inside one.
* **Secrets stay in one file.** `0600`, outside the repository, masked on read.
  They are not included in the data export (`/api/export` redacts them) and not
  in the audit log.
* **`deny` is enforced before the call**, not by asking the model nicely.

---

## API reference

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/mcp/servers` | Configured servers, secrets masked, live state attached. |
| `POST` | `/api/mcp/servers` | Create or replace a server. |
| `PATCH` | `/api/mcp/servers/{name}` | Update fields; disconnects the live session. |
| `DELETE` | `/api/mcp/servers/{name}` | Disconnect and remove the configuration. |
| `POST` | `/api/mcp/servers/{name}/test` | Connect for real; returns `ok`, `tools`, `duration_ms` or `error`. |
| `POST` | `/api/mcp/servers/{name}/connect` | Force a fresh session. |
| `POST` | `/api/mcp/servers/{name}/disconnect` | Close the session, keep the configuration. |
| `GET` | `/api/mcp/servers/{name}/tools` | Tools, resources and prompts with schemas. |
| `GET` | `/api/mcp/servers/{name}/resources` | Resources only. |
| `POST` | `/api/mcp/servers/{name}/resources/read` | `{"uri": "..."}`. |
| `GET` | `/api/mcp/servers/{name}/prompts` | Prompts only. |
| `POST` | `/api/mcp/servers/{name}/prompts/run` | `{"prompt": "...", "arguments": {...}}`. |
| `GET` | `/api/mcp/tools` | Every tool on every reachable server, qualified. |
| `POST` | `/api/mcp/invoke` | `{"server", "tool", "args", "approval_id"?}`. |
| `GET` | `/api/mcp/calls` | Recent calls from the audit log. |

---

## Sessions and concurrency

One background asyncio loop owns every session, so a server that keeps state
between calls (a browser, an open database handle) keeps it. Each server has a
lock: two calls to the same server are serialised, because most servers are not
concurrency-safe. Different servers run in parallel. Closing the app closes
every session; `AppState.close()` does it before the database so a server
cannot write into a closed store.

---

## Testing

The repository ships two real fixture servers, used by the test suite and
useful for a manual check:

```bash
# stdio
python3.11 tests/fixtures/mcp_fixture_server.py

# streamable HTTP on port 8791
python3.11 tests/fixtures/mcp_http_fixture_server.py
```

The stdio fixture exposes `echo`, `add` and `explode` (which always fails), one
resource and one prompt — enough to exercise discovery, execution and error
handling. `tests/test_tolesh_mcp_bridge.py` and `tests/test_tolesh_api_mcp.py`
drive them through the real client and the real HTTP API.

One trap worth recording: a fixture must **not** use
`from __future__ import annotations`. The SDK resolves tool signatures at
registration time, and string annotations make `issubclass()` fail with
`TypeError: issubclass() arg 1 must be a class`.
