#!/usr/bin/env python3
"""Send one real message through the web UI and screenshot the answer.

    python3 scripts/capture_web_conversation.py --url http://127.0.0.1:8787

The reply comes from whatever the backend routes to (Dahl, as configured), and
the printed transcript is read back from the page, not synthesised.
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
    ap.add_argument("--prompt", default="Explain in two sentences what you remember about me and what you do not.")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img" / "web-chat-reply.png"))
    args = ap.parse_args()

    out: dict = {"url": args.url, "prompt": args.prompt}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(args.url, wait_until="networkidle")
        page.fill("#input", args.prompt)
        page.click("#sendBtn")
        page.wait_for_function(
            "() => { const b=document.getElementById('sendBtn');"
            " return b && !b.disabled && !b.classList.contains('is-busy'); }",
            timeout=180000,
        )
        page.wait_for_timeout(800)
        out["messages"] = page.eval_on_selector_all(
            "#messages > *", "els => els.map(e => e.innerText.trim()).filter(Boolean)")
        out["model_label"] = page.inner_text("#modelTrigger").replace("\n", " ").strip()
        page.screenshot(path=args.out)
        out["screenshot"] = args.out
        out["console_errors"] = errors
        browser.close()

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("messages") else 1


if __name__ == "__main__":
    sys.exit(main())
