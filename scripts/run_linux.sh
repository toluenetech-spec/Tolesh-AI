#!/usr/bin/env bash
#
# Launch Tolesh on Linux.
#
#   ./scripts/run_linux.sh              # desktop application (default)
#   ./scripts/run_linux.sh --daemon     # headless voice daemon only
#   ./scripts/run_linux.sh --check      # verify the environment, start nothing
#
# The desktop application starts its own AI engine (FastAPI) in-process on a
# loopback port; you never launch uvicorn yourself.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

MODE="desktop"
for arg in "$@"; do
  case "$arg" in
    --daemon) MODE="daemon" ;;
    --desktop) MODE="desktop" ;;
    --check) MODE="check" ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

if [ ! -d .venv ]; then
  echo "📦 Creating virtual environment (.venv)..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if [ "${TOLESH_SKIP_INSTALL:-0}" != "1" ]; then
  echo "📦 Installing dependencies..."
  python -m pip install -q --upgrade pip
  python -m pip install -q -r requirements.txt
fi

export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export TOLESH_VOICE_DEBUG="${TOLESH_VOICE_DEBUG:-0}"

# Qt's xcb platform plugin needs these at runtime. Ubuntu 22.04+/Zorin ship
# libxcb-cursor0 separately, and without it the app dies with an opaque
# "could not load the Qt platform plugin xcb" abort. Say so plainly instead.
check_qt_runtime() {
  if ! python - <<'PY'
import sys
try:
    import PyQt6.QtWidgets  # noqa: F401
except Exception as exc:
    print(f"PyQt6 import failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
  then
    echo ""
    echo "⚠️  PyQt6 could not be imported. Install the desktop dependencies:"
    echo "    python -m pip install 'PyQt6>=6.6' 'PyQt6-WebEngine>=6.6'"
    exit 1
  fi

  if command -v ldconfig >/dev/null 2>&1; then
    if ! ldconfig -p 2>/dev/null | grep -q 'libxcb-cursor\.so\.0'; then
      echo ""
      echo "⚠️  libxcb-cursor0 is missing — Qt cannot open a window without it."
      echo "    Debian/Ubuntu/Zorin: sudo apt install libxcb-cursor0"
      echo "    Fedora:              sudo dnf install xcb-util-cursor"
      echo "    Arch:                sudo pacman -S xcb-util-cursor"
      echo ""
      echo "    (Set TOLESH_IGNORE_QT_DEPS=1 to skip this check.)"
      [ "${TOLESH_IGNORE_QT_DEPS:-0}" = "1" ] || exit 1
    fi
  fi

  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo "⚠️  No DISPLAY or WAYLAND_DISPLAY is set — there is no desktop session"
    echo "    to draw into. Run Tolesh from your desktop, or use --daemon."
    exit 1
  fi
}

case "$MODE" in
  check)
    check_qt_runtime
    echo "✅ Environment looks usable for the Tolesh desktop app."
    ;;
  daemon)
    echo "🎙️  Starting Tolesh voice daemon (headless)..."
    exec python -m tolesh.daemon
    ;;
  desktop)
    check_qt_runtime
    echo "🚀 Starting Tolesh desktop application..."
    echo "   The AI engine starts with the app; the tray icon holds the menu."
    exec python -m desktop_app
    ;;
esac
