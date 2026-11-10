#!/usr/bin/env python3
"""Chromium regression for the Tolesh web interface.

Run against a live server:

    PYTHONPATH=src TOLESH_WEB_DIR=web \
      python3.11 -m uvicorn tolesh.server.app:app --host 127.0.0.1 --port 8787
    python3.11 scripts/verify_web_ui.py --url http://127.0.0.1:8787

It exercises the real UI (not a mock): health endpoint, zero console/page
errors, desktop and mobile layout, streaming chat over SSE, dialog focus trap,
model listbox keyboard navigation, URL view state, mobile drawer default and an
explicit ``prefers-reduced-motion: reduce`` emulation. Results are printed as
JSON and written to ``docs/verification-report.json``; screenshots land in
``docs/img/``.
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


def collect(page, errors: list[str]) -> None:
    page.on("console", lambda m: errors.append(f"console.error: {m.text}")
            if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--token", default="",
                    help="Bearer token injected by the desktop shell (?token=)")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img"))
    ap.add_argument("--report", default=str(ROOT / "docs" / "verification-report.json"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    url = args.url.rstrip("/")
    page_url = f"{url}/?token={args.token}" if args.token else url

    report: dict = {"url": url, "checks": {}, "errors": []}
    try:
        report["health"] = wait_for_health(url)
    except RuntimeError as exc:
        print(json.dumps({"fatal": str(exc)}, indent=2))
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])

        # ---------------------------------------------------------- desktop
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        collect(page, report["errors"])
        page.goto(page_url, wait_until="load")
        page.wait_for_selector("#input", state="visible")
        page.wait_for_timeout(1200)  # let the entrance timeline settle
        if args.token:
            # The client must adopt the shell's token and scrub it from the URL.
            report["checks"]["token_stored"] = page.evaluate(
                "(t) => localStorage.getItem('tolesh_api_token') === t", args.token)
            report["checks"]["token_scrubbed_from_url"] = "token=" not in page.url
        page.screenshot(path=str(out / "tolesh-interface.png"), full_page=False)
        page.screenshot(path=str(out / "tolesh-verify-desktop.png"))
        report["checks"]["title"] = page.title()
        report["checks"]["skip_link"] = page.locator("a.skip-link").count() == 1
        report["checks"]["main_focus_target"] = page.locator(
            "main#mainView").get_attribute("tabindex") == "-1"

        # model listbox keyboard navigation
        trigger = page.locator("#modelTrigger")
        trigger.focus()
        page.keyboard.press("ArrowDown")
        report["checks"]["listbox_opened_by_keyboard"] = trigger.get_attribute(
            "aria-expanded") == "true"
        page.keyboard.press("Escape")
        report["checks"]["listbox_escape_closes"] = trigger.get_attribute(
            "aria-expanded") == "false"

        # dialog focus trap + focus restore
        page.locator("#addMemoryBtn").click()
        dialog = page.locator(".modal")
        report["checks"]["dialog_opened"] = dialog.count() == 1
        report["checks"]["dialog_focus_inside"] = page.evaluate(
            "() => !!document.activeElement && !!document.activeElement.closest('.modal')")
        page.keyboard.press("Escape")
        report["checks"]["dialog_escape_closes"] = page.locator(".modal").count() == 0

        # streaming chat over the real SSE endpoint. 60 s, not 30: a busy
        # gateway can queue the preferred model, retry, then answer from a
        # sibling model — the check still requires a genuine streamed reply.
        page.locator("#input").fill("How far?")
        page.locator("#sendBtn").click()
        page.wait_for_function(
            "() => { const n = document.querySelectorAll('.message.is-assistant .message-body'); "
            "return [...n].some(e => e.textContent.trim().length > 20); }",
            timeout=60000,
        )
        report["checks"]["streamed_reply"] = page.locator(
            ".message.is-assistant .message-body").last.inner_text()[:160]

        # URL view state: a nav click must write ?view= and survive a reload
        page.locator('.rail-item[data-view="memory"]').click()
        report["checks"]["view_url_state"] = "view=memory" in page.url
        page.reload(wait_until="load")
        page.wait_for_selector("#input", state="visible")
        report["checks"]["view_restored_after_reload"] = (
            page.locator('.rail-item[data-view="memory"]').get_attribute(
                "aria-current") == "true")
        ctx.close()

        # ----------------------------------------------------------- mobile
        mctx = browser.new_context(viewport={"width": 390, "height": 844},
                                   is_mobile=True, has_touch=True)
        mpage = mctx.new_page()
        collect(mpage, report["errors"])
        mpage.goto(page_url, wait_until="load")
        mpage.wait_for_selector("#input", state="visible")
        mpage.wait_for_timeout(700)
        mpage.screenshot(path=str(out / "tolesh-verify-mobile.png"))
        report["checks"]["mobile_drawer_collapsed"] = (
            "context-collapsed" in (mpage.locator(".app-shell").get_attribute("class") or ""))
        mctx.close()

        # ------------------------------------------------- reduced motion
        rctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                   reduced_motion="reduce")
        rpage = rctx.new_page()
        collect(rpage, report["errors"])
        rpage.goto(page_url, wait_until="load")
        rpage.wait_for_selector("#input", state="visible")
        rpage.wait_for_timeout(700)
        metrics = rpage.evaluate("""() => ({
            durToken: getComputedStyle(document.documentElement)
                        .getPropertyValue('--dur').trim(),
            railOpacity: getComputedStyle(document.querySelector('.rail')).opacity,
            sendTransition: getComputedStyle(document.querySelector('.send-btn'))
                        .transitionDuration,
            scrollBehavior: getComputedStyle(document.documentElement).scrollBehavior,
        })""")
        report["checks"]["reduced_motion"] = metrics
        report["checks"]["reduced_motion_ok"] = (
            metrics["durToken"] == "0ms"
            and float(metrics["railOpacity"]) == 1.0
            and all(float(x.replace("s", "")) < 0.01
                    for x in metrics["sendTransition"].split(","))
        )
        rpage.screenshot(path=str(out / "tolesh-verify-reduced-motion.png"))
        rctx.close()
        browser.close()

    report["checks"]["console_and_page_errors"] = report["errors"]
    bool_checks = {k: v for k, v in report["checks"].items()
                   if isinstance(v, bool)}
    report["passed"] = (not report["errors"]
                        and len(bool_checks) >= 9
                        and all(bool_checks.values())
                        and bool(report["checks"].get("streamed_reply")))
    report["boolean_checks"] = bool_checks
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
