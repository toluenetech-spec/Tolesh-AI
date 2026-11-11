#!/usr/bin/env bash
# Serve Tolesh and expose it on a public Cloudflare URL.
#
#   scripts/serve_public.sh                 # local :8787 + quick tunnel
#   scripts/serve_public.sh --port 9000     # another local port
#   scripts/serve_public.sh --no-tunnel     # local only
#
# The API is gated by a bearer token (TOLESH_API_TOKEN). The script keeps the
# token in .run/token so the link you handed out yesterday still works today;
# the quick-tunnel hostname itself is new on every start, which is a property
# of trycloudflare.com, not of this script. Pass --token to override.
#
# A quick tunnel is a convenience, not a deployment: it has no SLA, the
# hostname changes on restart, and Cloudflare terminates TLS in front of a
# plain-HTTP local server. Use a named tunnel with your own domain for anything
# that has to stay up.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8787
TOKEN=""
TUNNEL=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --no-tunnel) TUNNEL=0; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

RUN="$ROOT/.run"
mkdir -p "$RUN/data" "$RUN/config"

if [[ -z "$TOKEN" ]]; then
  if [[ -f "$RUN/token" ]]; then
    TOKEN="$(cat "$RUN/token")"
  else
    TOKEN="$(python3.11 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    printf '%s' "$TOKEN" > "$RUN/token"
    chmod 600 "$RUN/token"
  fi
fi

if [[ -z "${XDG_DATA_HOME:-}" ]]; then export XDG_DATA_HOME="$RUN/data"; fi
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export TOLESH_WEB_DIR="$ROOT/web"
export TOLESH_API_TOKEN="$TOKEN"

cleanup() {
  [[ -n "${SERVER_PID:-}" ]] && kill "$SERVER_PID" 2>/dev/null || true
  [[ -n "${TUNNEL_PID:-}" ]] && kill "$TUNNEL_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$ROOT"
python3.11 -m uvicorn tolesh.server.app:app --host 127.0.0.1 --port "$PORT" \
  > "$RUN/server.log" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then break; fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "server exited during startup; see $RUN/server.log" >&2
    tail -20 "$RUN/server.log" >&2
    exit 1
  fi
  sleep 0.5
done

echo "local   http://127.0.0.1:$PORT/?token=$TOKEN"

if [[ "$TUNNEL" -eq 0 ]]; then
  wait "$SERVER_PID"
  exit 0
fi

command -v cloudflared > /dev/null || { echo "cloudflared is not installed" >&2; exit 1; }
cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate \
  > "$RUN/cloudflared.log" 2>&1 &
TUNNEL_PID=$!

PUBLIC=""
for _ in $(seq 1 60); do
  PUBLIC="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$RUN/cloudflared.log" 2>/dev/null | head -1 || true)"
  [[ -n "$PUBLIC" ]] && break
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "cloudflared exited; see $RUN/cloudflared.log" >&2
    tail -20 "$RUN/cloudflared.log" >&2
    exit 1
  fi
  sleep 0.5
done

if [[ -z "$PUBLIC" ]]; then
  echo "no public hostname appeared; see $RUN/cloudflared.log" >&2
  exit 1
fi

echo "public  $PUBLIC/?token=$TOKEN"
echo
echo "Anyone without the token gets 401 on every /api route. Ctrl-C stops both."

wait "$SERVER_PID"
