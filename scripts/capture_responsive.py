#!/usr/bin/env python3
"""Screenshot the Tolesh interface at several widths, in one run.

Used to check the responsive layout against real rendering rather than by
reading media queries. Writes ``docs/img/responsive-<label>.png`` plus a small
JSON summary of the measurements that matter (overflow, core size, columns).

    python3 scripts/capture_responsive.py --url http://127.0.0.1:8787
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

# width, height, label — the actual devices/browsers this UI is opened in,
# including the 320 px floor and the collapsed-context desktop case.
VIEWPORTS = [
    (320, 640, "320-phone-small"),
    (390, 844, "390-phone"),
    (768, 1024, "768-tablet"),
    (1024, 768, "1024-laptop-narrow"),
    (1280, 800, "1280-laptop"),
    (1440, 900, "1440-desktop"),
    (1920, 1080, "1920-wide"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img"))
    ap.add_argument("--report", default=str(ROOT / "docs" / "responsive-report.json"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    report: dict = {"url": args.url, "viewports": [], "errors": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        for w, h, label in VIEWPORTS:
            ctx = browser.new_context(viewport={"width": w, "height": h},
                                      device_scale_factor=2 if w <= 390 else 1,
                                      is_mobile=w <= 480, has_touch=w <= 480)
            page = ctx.new_page()
            page.on("console", lambda m: report["errors"].append(f"{m.type}: {m.text}")
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: report["errors"].append(f"pageerror: {e}"))
            page.goto(args.url, wait_until="load")
            page.wait_for_selector("#input", state="visible")
            page.wait_for_timeout(900)
            # First frame: what a visitor sees on arrival, before any scrolling.
            page.screenshot(path=str(out / f"responsive-{label}.png"), full_page=False)

            metrics = page.evaluate("""() => {
              const core = document.querySelector('.core');
              const stage = document.querySelector('.core-stage');
              const copy = document.querySelector('.empty-copy');
              const rail = document.querySelector('.rail');
              const cs = getComputedStyle(core);
              const stageBox = stage.getBoundingClientRect();
              const copyBox = copy.getBoundingClientRect();
              // Stacked means the core sits entirely above the copy; a
              // left-edge comparison is unreliable because the stage is
              // centre-justified inside its own grid area.
              const stacked = stageBox.bottom <= copyBox.top + 4;
              const composer = document.querySelector('.composer-wrap');
              return {
                coreWidth: Math.round(core.getBoundingClientRect().width),
                coreLoaded: document.querySelector('.core-art').complete
                            && document.querySelector('.core-art').naturalWidth > 0,
                coreNatural: document.querySelector('.core-art').naturalWidth,
                columns: stacked ? 'stacked'
                       : (stageBox.left > copyBox.left ? 'copy-left' : 'core-left'),
                horizontalOverflow: document.documentElement.scrollWidth
                                    - document.documentElement.clientWidth,
                // A fixed rail can overflow without growing scrollWidth, so it
                // is measured on its own. Clipped overflow is not a defect —
                // only a rail that can actually scroll sideways is.
                railOverflow: Math.max(0, rail.scrollWidth - rail.clientWidth),
                railScrollable: ['auto', 'scroll'].includes(
                    getComputedStyle(rail).overflowX)
                    && rail.scrollWidth > rail.clientWidth,
                emptyStateVisible: getComputedStyle(
                    document.getElementById('emptyState')).display !== 'none',
                stateLabel: document.getElementById('coreStateText').textContent,
                coreAnimation: cs.animationName,
                orbWidth: Math.round(
                    document.getElementById('voiceOrb').getBoundingClientRect().width),
                bodyOverflow: getComputedStyle(document.body).overflow,
                composerFixed: getComputedStyle(composer).position === 'fixed',
                composerHeight: Math.round(composer.getBoundingClientRect().height),
                composerTop: Math.round(composer.getBoundingClientRect().top),
                titleBottom: Math.round(
                    document.querySelector('.empty-title').getBoundingClientRect().bottom),
                viewportHeight: window.innerHeight,
                lastContentBottom: null,
              };
            }""")
            # What a visitor sees on arrival: the greeting must not be sliced by
            # the fixed composer or by the bottom of the screen.
            metrics["titleFullyVisible"] = metrics["titleBottom"] <= min(
                metrics["composerTop"], metrics["viewportHeight"]) + 1

            # Then scroll to the end: on phones the page scrolls, and content
            # hidden under the fixed composer only shows up at the bottom.
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(250)
            metrics["lastContentBottom"] = page.evaluate(
                "() => { const l = [...document.querySelectorAll("
                "'.empty-state, .messages .message')].pop();"
                " return l ? Math.round(l.getBoundingClientRect().bottom) : null; }")
            metrics.update({"width": w, "height": h, "label": label})
            report["viewports"].append(metrics)

            # The context drawer must be reachable on touch: open it, check it
            # fits, close it again.
            if w <= 980:
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(200)
                page.locator("#contextToggle").click()
                page.wait_for_timeout(420)
                drawer = page.evaluate("""() => {
                  const d = document.getElementById('contextPanel').getBoundingClientRect();
                  return { left: Math.round(d.left), width: Math.round(d.width),
                           fits: d.left >= 0 && d.right <= window.innerWidth + 1 };
                }""")
                report["viewports"][-1]["drawer"] = drawer
                if w <= 480:
                    page.screenshot(path=str(out / f"responsive-{label}-drawer.png"))
                page.locator("#contextClose").click()
                page.wait_for_timeout(260)
            ctx.close()
        browser.close()

    for v in report["viewports"]:
        # Only meaningful when the composer is fixed over the content; on
        # desktop it is a flex sibling and the stream scrolls on its own.
        v["contentHiddenBehindComposer"] = bool(
            v["composerFixed"] and v["lastContentBottom"] is not None
            and v["lastContentBottom"] > v["composerTop"] + 1)
    bad = [v for v in report["viewports"]
           if v["horizontalOverflow"] > 0 or v["railScrollable"]
           or not v["coreLoaded"] or v["contentHiddenBehindComposer"]
           or not v["titleFullyVisible"]]
    report["ok"] = not bad and not report["errors"]
    report["problems"] = [
        {"label": v["label"], "overflow": v["horizontalOverflow"],
         "railScrollable": v["railScrollable"], "coreLoaded": v["coreLoaded"],
         "contentHidden": v["contentHiddenBehindComposer"],
         "titleSliced": not v["titleFullyVisible"]} for v in bad]
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
