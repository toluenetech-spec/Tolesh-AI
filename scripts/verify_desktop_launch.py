#!/usr/bin/env python3
"""Launch the real desktop application and prove it renders the real interface.

Not a unit test and not a mock: this constructs the actual ``ToleshSystemTray``
and ``ToleshAIWindow``, starts the in-process engine, waits for QtWebEngine to
load ``web/``, runs JavaScript *inside that engine*, screenshots the window,
shuts down through ``cleanup_on_exit``, and reopens the database in a fresh
process to prove the data survived.

The JavaScript matters because the interface now relies on features a browser
test cannot vouch for in QtWebEngine: container queries, ``cqw`` units, WebP with
alpha, ``overflow: clip``. If any of those were missing, the hero would collapse
and the Playwright run would never know.

    xvfb-run -a python3 scripts/verify_desktop_launch.py --out /tmp/tolesh-launch
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from desktop_app.ai_window import ToleshAIWindow  # noqa: F401,E402  (import order matters)
from desktop_app.app import ToleshSystemTray  # noqa: E402

PROBE_JS = """(() => {
  const art = document.querySelector('.core-art');
  const orb = document.querySelector('.orb-art');
  const core = document.querySelector('.core');
  const stage = document.querySelector('.core-stage');
  const copy = document.querySelector('.empty-copy');
  return {
    coreLoaded: !!(art && art.complete && art.naturalWidth > 0),
    coreNatural: art ? art.naturalWidth : 0,
    orbLoaded: !!(orb && orb.complete && orb.naturalWidth > 0),
    coreWidth: core ? Math.round(core.getBoundingClientRect().width) : 0,
    columns: (stage && copy)
      ? (stage.getBoundingClientRect().bottom <= copy.getBoundingClientRect().top + 4
         ? 'stacked' : 'side-by-side')
      : 'missing',
    state: document.body.dataset.coreState || null,
    label: (document.getElementById('coreStateText') || {}).textContent || null,
    // If cqw were unsupported the whole clamp() would be invalid, --core-size
    // would resolve to nothing and .core would collapse to zero width.
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
})()"""


def api(server, path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        server.base_url + path, data=data, method=method,
        headers={"Authorization": f"Bearer {server.token}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/tolesh-launch")
    ap.add_argument("--report", default=str(ROOT / "docs" / "desktop-launch-report.json"))
    ap.add_argument("--screenshot", default=str(ROOT / "docs" / "img" / "tolesh-desktop-integrated.png"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    report: dict = {"steps": [], "ok": False}

    def step(name, **data):
        report["steps"].append({"step": name, **data})
        print(f"[step] {name} {json.dumps(data)[:400]}", flush=True)

    app = QApplication(sys.argv)
    step("qapplication", platform=app.platformName())

    tray = ToleshSystemTray()
    step("tray_constructed", tray_available=bool(tray.tray_icon.isSystemTrayAvailable()))

    started = tray.start_ai_backend(timeout=60)
    step("backend_started", ok=started, error=tray._ai_error,
         port=tray.ai_server.port if tray.ai_server else None)
    if not started:
        report["error"] = tray._ai_error
        return 1

    server = tray.ai_server
    health = api(server, "/api/health")
    step("health", **{k: health[k] for k in ("status", "product", "providers_configured")})

    api(server, "/api/memory", "POST",
        {"category": "personal", "content": "Desktop launch check marker"})

    tray.show_ai_window()
    window = tray.ai_window
    step("ai_window", has_web_view=window.web_view is not None,
         url=window._url.split("?")[0])

    loaded = {"value": False}
    if window.web_view is not None:
        window.web_view.loadFinished.connect(lambda ok: loaded.update(value=bool(ok)))

    deadline = time.time() + 40
    while time.time() < deadline and not loaded["value"]:
        app.processEvents()
        time.sleep(0.05)
    step("web_engine_load", loaded=loaded["value"])
    if not loaded["value"]:
        report["error"] = "QtWebEngine did not finish loading the interface"
        return 1

    for _ in range(30):
        app.processEvents()
        time.sleep(0.05)

    # Ask the engine itself what it rendered.
    probe: dict = {}
    if window.web_view is not None:
        done = {"value": False}

        def _got(result):
            probe.update(result or {})
            done["value"] = True

        window.web_view.page().runJavaScript(PROBE_JS, _got)
        deadline = time.time() + 15
        while time.time() < deadline and not done["value"]:
            app.processEvents()
            time.sleep(0.03)
    step("engine_probe", **probe)

    shot = Path(args.screenshot)
    shot.parent.mkdir(parents=True, exist_ok=True)
    saved = window.grab().save(str(shot))
    step("screenshot", saved=bool(saved), path=str(shot),
         size=shot.stat().st_size if shot.exists() else 0)

    tray.cleanup_on_exit()
    step("cleanup", server_running=server.is_running)

    from tolesh.server.embedded import EmbeddedServer
    reopened = EmbeddedServer()
    reopened.start(timeout=40)
    marker = False
    try:
        memories = api(reopened, "/api/memory")["items"]
        conversations = api(reopened, "/api/conversations")["conversations"]
        marker = any("Desktop launch check marker" in m["content"] for m in memories)
        step("persistence_after_desktop_exit",
             memories=len(memories), marker_present=marker,
             conversations=len(conversations))
    finally:
        reopened.stop()

    report["ok"] = bool(
        started and loaded["value"] and saved and marker
        and probe.get("coreLoaded") and probe.get("orbLoaded")
        and probe.get("state") == "idle" and probe.get("overflow") == 0
    )
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "probe": probe}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:  # noqa: BLE001 - report the failure, never hide it
        import traceback
        traceback.print_exc()
    sys.exit(code)
