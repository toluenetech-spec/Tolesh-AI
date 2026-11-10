#!/usr/bin/env python3
"""Capture the core in each state it can actually reach.

Three of the four states are produced by the real application flow:

    idle        the page at rest
    thinking    after Send, before the first token
    responding  while tokens are arriving
    offline     every /api request aborted at the network layer, then reloaded

Nothing is faked by writing ``data-core-state`` from the test: if the app stops
setting the state, these screenshots stop showing it.

    python3 scripts/capture_core_states.py --url http://127.0.0.1:8787
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def wait_for_health(url: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # server still starting
            last = str(exc)
            time.sleep(0.4)
    raise RuntimeError(f"server did not become healthy: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img"))
    ap.add_argument("--report", default=str(ROOT / "docs" / "core-states.json"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    url = args.url.rstrip("/")

    report: dict = {"url": url, "states": {}, "errors": []}
    wait_for_health(url)
    # A screenshot from an earlier run must never stand in for a state this run
    # failed to observe.
    for stale in out.glob("core-state-*.png"):
        stale.unlink()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("console", lambda m: report["errors"].append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: report["errors"].append(str(e)))
        page.goto(url, wait_until="load")
        page.wait_for_selector("#input", state="visible")
        page.wait_for_timeout(1100)

        def snap(name: str) -> None:
            state = page.evaluate("() => document.body.dataset.coreState")
            label = page.evaluate(
                "() => document.getElementById('coreStateText').textContent.trim()")
            path = out / f"core-state-{name}.png"
            page.screenshot(path=str(path))
            report["states"][name] = {"state": state, "label": label,
                                      "screenshot": str(path.relative_to(ROOT))}

        snap("idle")

        page.locator("#input").fill("Tell me one thing you remember about me.")
        # Record every transition instead of sampling: "responding" can be
        # shorter than a polling interval if the gateway buffers its chunks.
        page.evaluate("""() => {
          window.__coreStates = [];
          new MutationObserver((recs) => {
            window.__coreStates.push(document.body.dataset.coreState);
          }).observe(document.body, { attributes: true, attributeFilter: ['data-core-state'] });
        }""")
        page.locator("#sendBtn").click()
        try:
            page.wait_for_function(
                "() => document.body.dataset.coreState === 'thinking'", timeout=15000)
            snap("thinking")
        except Exception:
            report["states"]["thinking"] = {"skipped": "not observed"}
        try:
            # rAF polling: catches a window far shorter than a 100 ms sample.
            page.wait_for_function(
                "() => document.body.dataset.coreState === 'responding'", timeout=90000)
            snap("responding")
        except Exception:
            report["states"]["responding"] = {"skipped": "not observed"}
        report["transitions"] = page.evaluate("() => window.__coreStates")

        # The reply is finished when the send button is usable again.
        page.wait_for_function(
            "() => !document.getElementById('sendBtn').disabled", timeout=120000)
        page.wait_for_timeout(300)
        snap("idle-after-reply")
        ctx.close()

        # Offline: abort every API call at the network layer and reload. This is
        # the code path a crashed backend takes, not a CSS demo.
        octx = browser.new_context(viewport={"width": 1280, "height": 800})
        opage = octx.new_page()
        # Console errors are expected here — the aborted requests are the point.
        # They are not collected, so they cannot be mistaken for a UI defect.
        opage.route("**/api/**", lambda route: route.abort())
        opage.goto(url, wait_until="load")
        opage.wait_for_selector("#input", state="visible")
        opage.wait_for_timeout(1200)
        path = out / "core-state-offline.png"
        opage.screenshot(path=str(path))
        report["states"]["offline"] = {
            "state": opage.evaluate("() => document.body.dataset.coreState"),
            "label": opage.evaluate(
                "() => document.getElementById('coreStateText').textContent.trim()"),
            "screenshot": str(path.relative_to(ROOT)),
        }
        octx.close()
        browser.close()

    expected = {"idle": "idle", "thinking": "thinking", "offline": "offline"}
    report["ok"] = (not report["errors"]
                    and all(report["states"].get(k, {}).get("state") == v
                            for k, v in expected.items())
                    and report["states"].get("idle-after-reply", {}).get("state") == "idle")
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
