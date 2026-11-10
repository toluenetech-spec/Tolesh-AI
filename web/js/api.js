/* Tolesh API client. Same origin when served by the Tolesh backend; falls back
   to the local server when the HTML is opened directly as a file. */

const BASE = location.protocol === 'file:' ? 'http://127.0.0.1:8787' : '';

/* The desktop shell opens this page once with ?token=… so the embedded
   browser can authenticate against the in-process engine. Keep the token and
   strip it from the address bar so it is not left in screenshots or history. */
const _params = new URLSearchParams(location.search);
const _urlToken = _params.get('token');
if (_urlToken) {
  try { localStorage.setItem('tolesh_api_token', _urlToken); } catch (_) { /* private mode */ }
  _params.delete('token');
  const _clean = location.pathname + (_params.toString() ? `?${_params}` : '') + location.hash;
  try { history.replaceState(null, '', _clean); } catch (_) { /* file:// */ }
}

function token() {
  return localStorage.getItem('tolesh_api_token') || '';
}

function headers(json = false) {
  const h = {};
  if (json) h['Content-Type'] = 'application/json';
  const t = token();
  if (t) h['Authorization'] = `Bearer ${t}`;
  return h;
}

async function request(path, options = {}) {
  const res = await fetch(BASE + path, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch (_) { /* keep status text */ }
    throw new Error(detail);
  }
  return res.json();
}

export const get = (path) => request(path, { headers: headers() });
export const post = (path, body) =>
  request(path, { method: 'POST', headers: headers(true), body: JSON.stringify(body) });
export const patch = (path, body) =>
  request(path, { method: 'PATCH', headers: headers(true), body: JSON.stringify(body) });
export const put = (path, body) =>
  request(path, { method: 'PUT', headers: headers(true), body: JSON.stringify(body) });
export const del = (path) => request(path, { method: 'DELETE', headers: headers() });

export async function health() {
  return get('/api/health');
}

/** Stream a chat completion. `onEvent` receives decoded RouterEvent objects. */
export async function chatStream(body, onEvent, signal) {
  const res = await fetch(BASE + '/api/chat', {
    method: 'POST',
    headers: headers(true),
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* noop */ }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';
    for (const part of parts) {
      for (const line of part.split('\n')) {
        if (!line.startsWith('data:')) continue;
        const raw = line.slice(5).trim();
        if (!raw) continue;
        try { onEvent(JSON.parse(raw)); } catch (_) { /* skip malformed frame */ }
      }
    }
  }
  if (buffer.trim()) {
    for (const line of buffer.split('\n')) {
      if (!line.startsWith('data:')) continue;
      try { onEvent(JSON.parse(line.slice(5).trim())); } catch (_) { /* noop */ }
    }
  }
}
