# Tolesh on Cloudflare

This directory deploys Tolesh to Cloudflare. The Worker is the edge half; the
Python engine stays on a machine you control and is reached through a
Cloudflare Tunnel.

```
browser ──▶ Worker (this directory) ──▶ Cloudflare Tunnel ──▶ Tolesh engine
            static files from ../web        (no open port)      FastAPI, SQLite,
            session cookie, 401 at edge                         whisper, FAISS
            streaming pass-through                              OS controls
```

## Why not everything in a Worker

The engine is Python: FastAPI, SQLite, `faster-whisper`, FAISS, Playwright and
the system-control tools that drive volume, brightness and screen lock. Workers
run JavaScript and a restricted Python (Pyodide) runtime. None of those
libraries load there, and a Worker has no persistent disk, no microphone, no
window manager and no long-lived process.

A Worker-only rewrite would not be Tolesh; it would be a chat box that had lost
voice, memory files, local tools and desktop control. So the split above keeps
every feature and still puts Cloudflare in front of all traffic.

## What the Worker actually does

| Concern | Behaviour |
| --- | --- |
| Static files | Serves `../web` from Cloudflare's edge (26 files, 1.3 MB) |
| `?token=` handshake | Checks the token, sets an HttpOnly `tolesh_session` cookie, 302s to a clean URL |
| Auth on `/api/*` | 401 decided at the edge — the origin is never contacted |
| Token forwarding | Injects `Authorization: Bearer $TOLESH_API_TOKEN` for the engine's existing gate |
| Public routes | `/api/health`, `/api/connectors/oauth/callback`, `/share/*` pass through untouched |
| Streaming | `upstream.body` is handed back unbuffered, so `/api/chat/stream` SSE stays live |
| Redirects | An origin redirect is rewritten to a relative one, so the tunnel hostname never leaks |
| Headers | CSP, HSTS-ready, `nosniff`, `no-referrer`, `frame-ancestors 'none'` |
| Rate limiting | Optional `RATE_LIMITER` binding; skipped when absent |

The session cookie holds `<expiry>.<HMAC>`, not the bearer token. Nothing in the
browser can be replayed as an API key, and the token never lands in history, a
screenshot or a `Referer` header.

## Local development

```bash
# 1. engine (separate terminal)
scripts/serve_public.sh --no-tunnel

# 2. edge
cd cloudflare
npm install
cp .dev.vars.example .dev.vars     # then fill in the three values
npx wrangler dev --port 8788
```

Open `http://127.0.0.1:8788/?token=<TOLESH_API_TOKEN>`.

`.dev.vars` is git-ignored. It holds:

```
TOLESH_API_TOKEN=...   # must match the engine's token
SESSION_SECRET=...     # random 32+ bytes; signs the session cookie
ORIGIN=http://127.0.0.1:8787
```

## Production

### 1. A named tunnel, so the origin has a stable hostname

```bash
cloudflared tunnel login
cloudflared tunnel create tolesh
cloudflared tunnel route dns tolesh origin.your-domain.com
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: tolesh
credentials-file: /home/you/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: origin.your-domain.com
    service: http://127.0.0.1:8787
  - service: http_status:404
```

Run it as a service (`sudo cloudflared service install`) so it survives reboot.
The origin now has a fixed HTTPS name and **no inbound port open**.

### 2. Secrets and deploy

```bash
cd cloudflare
npx wrangler secret put TOLESH_API_TOKEN    # same value the engine uses
npx wrangler secret put SESSION_SECRET      # openssl rand -base64 32
# set ORIGIN in wrangler.jsonc to https://origin.your-domain.com
npx wrangler deploy
```

### 3. Optional: rate limiting

Add to `wrangler.jsonc`, then deploy:

```jsonc
"ratelimits": [
  { "name": "RATE_LIMITER", "namespace_id": "1001", "simple": { "limit": 120, "period": 60 } }
]
```

The Worker checks it only for authenticated `/api/*` calls and fails open if the
binding is missing.

## Limits worth knowing

- **Request body**: 100 MB on Workers Free/Pro. Voice uploads and attachments
  above that need the desktop app or a direct origin connection.
- **CPU**: 10 ms per request on the free plan is enough for proxy + static, but
  not for transcoding anything at the edge — and nothing is transcoded here.
- **Tunnel**: the Python host must stay up. Cloudflare gives you TLS, DDoS
  protection and a stable name; it does not make the engine highly available.
- **Ephemeral media**: `media_secret` is per-process, so artifact URLs die when
  the engine restarts. That is deliberate.
- **No Durable Objects / D1 yet.** Conversation state lives in the engine's
  SQLite. Moving it to D1 would mean rewriting the memory and job layers, and
  would break the desktop app's single-file database.

## Verified locally

```
GET /?token=wrong                 -> 401
GET /?token=<valid>               -> 302, Set-Cookie: tolesh_session=...; HttpOnly; SameSite=Lax
GET /api/config (no cookie)       -> 401
GET /api/config (with cookie)     -> 200
GET /                             -> 200, 16579 bytes
CSP / X-Frame-Options present     -> yes
Browser through the Worker        -> chat sent, SSE streamed, "WORKER OK" rendered, 0 console errors
```
