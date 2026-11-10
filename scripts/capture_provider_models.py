#!/usr/bin/env python3
"""Capture the provider dialog while it pulls a live model list.

Run against a live backend:

    python3 scripts/capture_provider_models.py --url http://127.0.0.1:8787

Writes ``docs/img/web-provider-models.png`` and prints the ids the endpoint
actually reported, so the screenshot and the evidence come from the same run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img" / "web-provider-models.png"))
    args = ap.parse_args()

    result: dict = {"url": args.url}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(args.url, wait_until="networkidle")
        page.click('[data-view="settings"]')
        page.wait_for_selector('.provider-row:has-text("Dahl")')
        page.locator('.provider-row:has-text("Dahl")').locator("[data-configure]").click()
        page.wait_for_selector("#provModelsNote")
        page.click("[data-load-models]")
        # The button is re-enabled when discovery returns; wait for that.
        page.wait_for_function(
            "() => { const b=document.querySelector('[data-load-models]');"
            " return b && !b.disabled; }", timeout=45000)
        page.wait_for_timeout(400)
        result["note"] = page.inner_text("#provModelsNote").strip()
        result["listed"] = page.eval_on_selector_all(
            "#provModels input", "els => els.map(e => e.value)")
        page.screenshot(path=args.out)
        result["screenshot"] = args.out
        result["console_errors"] = errors
        browser.close()

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("listed") else 1


if __name__ == "__main__":
    sys.exit(main())
