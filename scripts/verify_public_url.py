#!/usr/bin/env python3
"""Verify the public Cloudflare URL with a real browser.

Loads the tunnelled origin with ``?token=``, confirms the token is captured and
stripped from the address bar, sends one real chat message, opens the workspace
drawer, and records console errors plus a screenshot.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--shot", default="/tmp/public-ui.png")
    args = ap.parse_args()

    errors: list[str] = []
    report: dict = {"url": args.url, "checks": [], "errors": errors}

    def check(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(f"{args.url}/?token={args.token}", wait_until="load", timeout=60000)
        page.wait_for_timeout(2500)

        stored = page.evaluate("localStorage.getItem('tolesh_api_token')")
        check("token captured from ?token", stored == args.token, str(stored)[:12])
        check("token stripped from address bar", "token=" not in page.url, page.url)

        # The composer only enables once /api/health answered.
        try:
            page.wait_for_selector("#composer-input, textarea[name=message], textarea", timeout=20000)
            check("composer rendered", True)
        except Exception as exc:
            check("composer rendered", False, str(exc)[:120])

        # One real round trip through the tunnel.
        box = page.locator("textarea").first
        box.fill("Say hello in five words.")
        box.press("Enter")
        reply = ""
        deadline = time.time() + 90
        while time.time() < deadline:
            page.wait_for_timeout(1000)
            text = page.inner_text("body")
            if "hello" in text.lower() and len(text) > 400:
                reply = text
                break
        check("chat round trip", bool(reply), f"{len(reply)} chars of page text")

        # Workspace drawer, the surface built last round.
        opened = False
        for sel in ["#workspace-toggle", "[data-action=workspace]", "button[title*=Workspace]",
                    "text=Workspace"]:
            try:
                page.click(sel, timeout=4000)
                opened = True
                break
            except Exception:
                continue
        page.wait_for_timeout(1200)
        drawer_visible = page.evaluate(
            "() => { const d = document.querySelector('#workspace-drawer, .ws-drawer, aside');"
            " if (!d) return false; const r = d.getBoundingClientRect();"
            " return r.width > 200 && getComputedStyle(d).visibility !== 'hidden'; }")
        check("workspace drawer opens", opened and drawer_visible, f"opened={opened}")

        # The drawer must describe the conversation that is actually open, not
        # the empty page it was built on.
        panel_text = page.evaluate(
            "() => { const p = document.querySelector('#ws-panel-conversation');"
            " return p ? p.innerText : ''; }")
        check("conversation panel is current", "Open or start a conversation first" not in panel_text,
              panel_text[:80].replace("\n", " "))

        page.screenshot(path=args.shot, full_page=False)
        report["screenshot"] = args.shot
        report["final_url"] = page.url
        browser.close()

    ok = all(c["ok"] for c in report["checks"]) and not errors
    report["ok"] = ok
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
