/**
 * Tolesh on Cloudflare Workers.
 *
 * The engine is Python: FastAPI, SQLite, faster-whisper, FAISS, Playwright and
 * the OS-level computer tools. None of that runs inside a Worker, and pretending
 * otherwise would mean shipping a crippled product. So this Worker is the edge
 * half of a two-part deployment:
 *
 *   browser ──▶ Worker (this file) ──▶ Cloudflare Tunnel ──▶ Tolesh engine
 *               static assets,            (named tunnel,         (Python,
 *               session auth,              no open port)          SQLite)
 *               rate limits, headers
 *
 * What the Worker owns:
 *   * every static file in ../web, served from Cloudflare's edge;
 *   * the `?token=` handshake — exchanged once for an HttpOnly session cookie
 *     so the bearer token never sits in browser history, a screenshot, or a
 *     Referer header;
 *   * injection of the bearer token on the way to the origin, so the engine's
 *     existing `TOLESH_API_TOKEN` gate keeps working unchanged;
 *   * a hard 401 for unauthenticated `/api/*` requests, decided at the edge —
 *     the origin is never touched;
 *   * streaming pass-through, because `/api/chat/stream` is server-sent events
 *     and buffering it would turn a live answer into a stall.
 */

const SESSION_COOKIE = 'tolesh_session';
const SESSION_TTL_SEC = 7 * 24 * 60 * 60;

/** Routes the origin treats as public; the Worker must not demand a session. */
function isPublicApi(path) {
  return (
    path === '/api/health' ||
    path === '/api/connectors/oauth/callback'
  );
}

const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  // The UI sets a handful of dynamic style properties; no inline <style> tags.
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "media-src 'self' blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ');

function securityHeaders(headers) {
  headers.set('Content-Security-Policy', CSP);
  headers.set('X-Content-Type-Options', 'nosniff');
  headers.set('Referrer-Policy', 'no-referrer');
  headers.set('X-Frame-Options', 'DENY');
  headers.set('Permissions-Policy', 'camera=(), geolocation=(), microphone=(self)');
  return headers;
}

function json(body, status = 200) {
  const res = new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
  res.headers.set('X-Content-Type-Options', 'nosniff');
  return res;
}

/** Compare two secrets without leaking how much of a prefix matched. */
async function safeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || !a || !b) return false;
  const enc = new TextEncoder();
  const [ha, hb] = await Promise.all([
    crypto.subtle.digest('SHA-256', enc.encode(a)),
    crypto.subtle.digest('SHA-256', enc.encode(b)),
  ]);
  const va = new Uint8Array(ha);
  const vb = new Uint8Array(hb);
  let diff = 0;
  for (let i = 0; i < va.length; i += 1) diff |= va[i] ^ vb[i];
  return diff === 0;
}

function b64url(bytes) {
  let s = '';
  const arr = new Uint8Array(bytes);
  for (let i = 0; i < arr.length; i += 1) s += String.fromCharCode(arr[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function sessionKey(env) {
  // Fall back to the API token only if a dedicated secret was not set, so a
  // half-configured deployment still fails closed rather than issuing cookies
  // that nobody can verify.
  return env.SESSION_SECRET || env.TOLESH_API_TOKEN || '';
}

async function signExpiry(env, exp) {
  const key = sessionKey(env);
  if (!key) return '';
  const cryptoKey = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(key),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const sig = await crypto.subtle.sign('HMAC', cryptoKey, new TextEncoder().encode(`v1:${exp}`));
  return b64url(sig);
}

async function makeSession(env) {
  const exp = Math.floor(Date.now() / 1000) + SESSION_TTL_SEC;
  const sig = await signExpiry(env, exp);
  if (!sig) return '';
  return `${exp}.${sig}`;
}

async function sessionIsValid(env, value) {
  if (!value) return false;
  const dot = value.lastIndexOf('.');
  if (dot <= 0) return false;
  const exp = Number(value.slice(0, dot));
  const sig = value.slice(dot + 1);
  if (!Number.isFinite(exp) || exp <= Math.floor(Date.now() / 1000)) return false;
  const expected = await signExpiry(env, exp);
  return safeEqual(sig, expected);
}

function readCookie(request, name) {
  const raw = request.headers.get('Cookie') || '';
  for (const part of raw.split(';')) {
    const eq = part.indexOf('=');
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() === name) return part.slice(eq + 1).trim();
  }
  return '';
}

/** A request is allowed if it carries the bearer token or a valid session. */
async function isAuthorized(request, env) {
  const supplied = request.headers.get('Authorization') || '';
  if (env.TOLESH_API_TOKEN && (await safeEqual(supplied, `Bearer ${env.TOLESH_API_TOKEN}`))) {
    return true;
  }
  return sessionIsValid(env, readCookie(request, SESSION_COOKIE));
}

async function rateLimited(request, env) {
  // The binding is optional: without it the deployment still works, it just
  // relies on the origin and Cloudflare's own DDoS protection.
  if (!env.RATE_LIMITER) return false;
  try {
    const ip = request.headers.get('CF-Connecting-IP') || 'anonymous';
    const { success } = await env.RATE_LIMITER.limit({ key: ip });
    return !success;
  } catch (_) {
    return false;
  }
}

function originBase(env) {
  return String(env.ORIGIN || '').replace(/\/+$/, '');
}

/**
 * Forward one request to the Python engine and hand the response straight back.
 * The body is never buffered: SSE chunks and file downloads stream through.
 */
async function proxy(request, env, { injectAuth }) {
  const origin = originBase(env);
  if (!origin) return json({ detail: 'ORIGIN is not configured for this deployment' }, 502);

  const url = new URL(request.url);
  let target;
  try {
    target = new URL(origin + url.pathname + url.search);
  } catch (_) {
    return json({ detail: 'ORIGIN is not a valid URL' }, 502);
  }

  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('cf-connecting-ip');
  headers.delete('cf-ipcountry');
  headers.set('X-Forwarded-Host', url.host);
  headers.set('X-Forwarded-Proto', url.protocol.replace(':', ''));
  if (injectAuth && env.TOLESH_API_TOKEN) {
    headers.set('Authorization', `Bearer ${env.TOLESH_API_TOKEN}`);
  }

  const init = { method: request.method, headers, redirect: 'manual' };
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    init.body = request.body;
  }

  let upstream;
  try {
    upstream = await fetch(target.toString(), init);
  } catch (err) {
    return json({ detail: `origin unreachable: ${err && err.message ? err.message : err}` }, 502);
  }

  const out = new Headers(upstream.headers);
  // Workers decodes the body itself; keeping these would make the client wait
  // for a length or try to gunzip twice.
  out.delete('content-encoding');
  out.delete('content-length');
  out.delete('transfer-encoding');
  out.delete('connection');

  const location = out.get('location');
  if (location && location.startsWith(origin)) {
    out.set('location', location.slice(origin.length) || '/');
  }
  out.set('X-Content-Type-Options', 'nosniff');

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: out,
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // ---- token handshake -------------------------------------------------
    // The desktop shell and any shared link arrive as /?token=… . Exchange it
    // for a cookie and redirect, so the token leaves the address bar at once.
    const suppliedToken = url.searchParams.get('token');
    if (suppliedToken) {
      if (!(await safeEqual(suppliedToken, env.TOLESH_API_TOKEN || ''))) {
        return json({ detail: 'Unauthorized' }, 401);
      }
      const session = await makeSession(env);
      if (!session) return json({ detail: 'SESSION_SECRET is not configured' }, 500);

      url.searchParams.delete('token');
      const headers = new Headers({ Location: url.pathname + url.search + url.hash });
      const secure = url.protocol === 'https:' ? '; Secure' : '';
      headers.append(
        'Set-Cookie',
        `${SESSION_COOKIE}=${session}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_TTL_SEC}${secure}`,
      );
      headers.set('Cache-Control', 'no-store');
      return new Response(null, { status: 302, headers });
    }

    // ---- API -------------------------------------------------------------
    if (path.startsWith('/api/')) {
      if (!isPublicApi(path)) {
        if (await rateLimited(request, env)) {
          return json({ detail: 'Too many requests' }, 429);
        }
        if (!(await isAuthorized(request, env))) {
          return json({ detail: 'Unauthorized' }, 401);
        }
      }
      return proxy(request, env, { injectAuth: !isPublicApi(path) });
    }

    // ---- shared conversations -------------------------------------------
    // A share link is its own credential: the origin decides whether the id is
    // real, and it only ever exposes the one conversation that was shared.
    if (path.startsWith('/share/')) {
      return proxy(request, env, { injectAuth: false });
    }

    // ---- static assets ---------------------------------------------------
    const asset = await env.ASSETS.fetch(request);
    const headers = new Headers(asset.headers);
    securityHeaders(headers);
    // Hashed assets never change; the HTML shell must always be revalidated.
    if (path === '/' || path.endsWith('.html')) {
      headers.set('Cache-Control', 'no-cache');
    }
    return new Response(asset.body, {
      status: asset.status,
      statusText: asset.statusText,
      headers,
    });
  },
};
