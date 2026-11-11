#!/usr/bin/env python3
"""Chromium regression for the Tolesh workspace drawer.

Run against a live server:

    PYTHONPATH=src TOLESH_WEB_DIR=web \\
      python3.11 -m uvicorn tolesh.server.app:app --host 127.0.0.1 --port 8787
    python3.11 scripts/verify_workspace_ui.py --url http://127.0.0.1:8787

The script drives the real UI against the real API. It opens the drawer, walks
all eight tabs, then performs one live action per tab (add and test a stdio MCP
server, upload a CSV and profile it, schedule a background task, save an
assistant, flip a privacy setting). It records console errors, checks for
horizontal overflow from 320 px to 1920 px, and writes a JSON report plus
screenshots to ``docs/img/workspace/``.

Exit code is 0 only when every check passed. A failure is reported, never
hidden: the report is written on both paths.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TABS = ["conversation", "agents", "mcp", "connections", "files", "tasks",
        "assistants", "settings"]
WIDTHS = [320, 375, 768, 1024, 1440, 1920]


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
    def on_console(message):
        if message.type == "error":
            errors.append(f"console.error: {message.text}")

    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))


def write_csv(path: Path) -> None:
    path.write_text(
        "region,amount,units\n"
        "Lagos,1200,40\nAbuja,900,31\nKano,640,22\nIbadan,510,18\n"
        "Port Harcourt,470,16\nEnugu,300,11\n",
        encoding="utf-8",
    )


def no_overflow(page) -> dict:
    return page.evaluate(
        """() => {
          const de = document.documentElement;
          return {
            scrollWidth: de.scrollWidth,
            clientWidth: de.clientWidth,
            overflow: de.scrollWidth > de.clientWidth + 1,
          };
        }"""
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--token", default="")
    ap.add_argument("--out", default=str(ROOT / "docs" / "img" / "workspace"))
    ap.add_argument("--report",
                    default=str(ROOT / "docs" / "workspace-verification.json"))
    ap.add_argument("--fixture", default=str(
        ROOT / "tests" / "fixtures" / "mcp_fixture_server.py"))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    url = args.url.rstrip("/")
    page_url = f"{url}/?token={args.token}" if args.token else url
    # A throwaway upload, not a repository artefact.
    csv_path = Path(tempfile.mkdtemp(prefix="tolesh-ui-")) / "sales.csv"
    write_csv(csv_path)

    report: dict = {"url": url, "checks": {}, "errors": [], "shots": []}
    try:
        report["health"] = wait_for_health(url)
    except RuntimeError as exc:
        print(json.dumps({"fatal": str(exc)}, indent=2))
        return 1

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            collect(page, report["errors"])
            _run(page, url, out, report, Path(args.fixture), csv_path)
            browser.close()
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        report["crashed"] = f"{type(exc).__name__}: {exc}"

    checks = {k: v for k, v in report["checks"].items() if isinstance(v, bool)}
    report["passed"] = (
        "crashed" not in report
        and not report["errors"]
        and all(checks.values())
        and bool(checks)
    )
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"],
        "checks": checks,
        "errors": report["errors"][:10],
        "crashed": report.get("crashed"),
        "report": args.report,
    }, indent=2))
    return 0 if report["passed"] else 1


def _run(page, url: str, out: Path, report: dict, fixture: Path,
         csv_path: Path) -> None:
    check = report["checks"]

    def shot(name: str) -> None:
        path = out / f"{name}.png"
        page.screenshot(path=str(path), full_page=False)
        report["shots"].append(str(path.relative_to(ROOT)))

    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("#workspaceRailBtn", timeout=15000)
    check["rail_button_present"] = True

    page.click("#workspaceRailBtn")
    page.wait_for_selector("#wsDrawer.is-open", timeout=5000)
    check["drawer_opens"] = page.is_visible("#wsDrawer")

    tabs = page.eval_on_selector_all(".ws-tab", "els => els.map(e => e.id)")
    check["eight_tabs"] = tabs == [f"ws-tab-{t}" for t in TABS]

    # Every tab must render a panel, and no panel may be stuck on "Loading…".
    # A fixed sleep turned a slow fetch into a false regression once, so poll
    # for the panel to settle instead of guessing how long it needs.
    stuck = []
    for tab in TABS:
        page.click(f"#ws-tab-{tab}")
        page.wait_for_selector(".ws-panel", timeout=5000)
        settled = False
        deadline = time.time() + 8
        while time.time() < deadline:
            if "Loading…" not in page.inner_text("#wsBody"):
                settled = True
                break
            page.wait_for_timeout(150)
        if not settled:
            stuck.append(tab)
        shot(f"tab-{tab}")
    check["all_tabs_render"] = not stuck
    if stuck:
        report["stuck_tabs"] = stuck

    # --- live action per tab -------------------------------------------------
    # MCP: register the stdio fixture server, test it, list its tools.
    page.click("#ws-tab-mcp")
    page.wait_for_selector("#wsMcpForm")
    page.fill("#wsMcpForm [name=name]", "fixture")
    page.select_option("#wsMcpForm [name=transport]", "stdio")
    page.fill("#wsMcpForm [name=command]", sys.executable)
    page.fill("#wsMcpForm [name=args]", str(fixture))
    page.click("#wsMcpForm button[type=submit]")
    page.wait_for_selector("#wsMcpList .ws-card", timeout=10000)
    check["mcp_server_saved"] = "fixture" in page.inner_text("#wsMcpList")

    page.click("#wsMcpList .ws-card [data-act=test]")
    # Cold start spawns a Python process and completes a handshake; poll the
    # badge instead of guessing how long that takes.
    try:
        page.wait_for_function(
            """() => {
                 const el = document.getElementById('wsMcpList');
                 return !!el && /\\bconnected\\b/i.test(el.innerText);
               }""",
            timeout=20000,
        )
    except Exception:  # noqa: BLE001 - the check below records the truth
        pass
    check["mcp_server_connected"] = "connected" in page.inner_text("#wsMcpList").lower()
    shot("mcp-connected")

    # The Test button reloads the card, so the tools click may land on a node
    # that is about to be replaced. Retry once if the panel stayed empty.
    for attempt in range(2):
        if page.locator("#wsMcpList [data-tools] .ws-row").count():
            break
        page.click("#wsMcpList .ws-card [data-act=tools]")
        try:
            page.wait_for_selector("#wsMcpList [data-tools] .ws-row", timeout=8000)
        except Exception:  # noqa: BLE001 - second attempt follows
            if attempt:
                break
    body = page.inner_text("#wsMcpList")
    check["mcp_tools_listed"] = "add" in body.lower() and "run" in body.lower()

    # Agents: one real run. It either answers or reports an error; both are a
    # pass for the UI, which must never leave the log blank.
    page.click("#ws-tab-agents")
    page.wait_for_selector("#wsGoal")
    page.fill("#wsGoal", "Say hello in one short sentence, then stop.")
    page.click("#wsRun")
    page.wait_for_function(
        """() => {
             const s = document.getElementById('wsRunState');
             return !!s && /Done in|Stopped|Cancelled|Waiting/.test(s.textContent);
           }""",
        timeout=120000,
    )
    page.wait_for_timeout(300)
    state = page.inner_text("#wsRunState")
    log_text = page.inner_text("#wsLog")
    check["agent_run_reports"] = ("final:" in log_text or "error:" in log_text) and bool(state)
    report["agent_run"] = {"state": state, "log": log_text[-600:]}
    shot("agents-run")

    # Files: upload a CSV, profile it, chart it.
    page.click("#ws-tab-files")
    page.wait_for_selector("#wsFile")
    page.set_input_files("#wsFile", str(csv_path))
    page.click("#wsUpload")
    page.wait_for_selector("#wsAttachments .ws-card-title", timeout=10000)
    check["upload_lands"] = "sales.csv" in page.inner_text("#wsAttachments")

    page.click("#wsAnalyse")
    page.wait_for_selector("#wsProfile:not([hidden])", timeout=10000)
    profile = page.inner_text("#wsProfile")
    check["profile_runs"] = "rows" in profile.lower() and "region" in profile.lower()

    page.select_option("#wsChartType", "bar")
    page.fill("#wsChartX", "region")
    page.fill("#wsChartY", "amount")
    page.fill("#wsChartTitle", "Revenue by region")
    page.click("#wsChart")
    page.wait_for_selector("#wsArtifacts img", timeout=20000)
    page.wait_for_timeout(600)
    loaded = page.evaluate(
        """() => { const i = document.querySelector('#wsArtifacts img');
                   return !!i && i.complete && i.naturalWidth > 0; }"""
    )
    check["chart_renders"] = bool(loaded)
    shot("files-artifact")
    page.locator("#wsArtifacts").scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    shot("files-artifacts-grid")

    # The tile is small by design; a click must open the chart at full size,
    # and Escape must close the viewer without closing the drawer.
    page.click("#wsArtifacts img")
    page.wait_for_selector(".ws-lightbox img", timeout=5000)
    page.wait_for_timeout(500)
    check["artifact_zooms"] = page.evaluate(
        """() => { const i = document.querySelector('.ws-lightbox img');
                   return !!i && i.complete && i.naturalWidth > 400; }"""
    )
    shot("artifact-lightbox")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check["lightbox_escape_keeps_drawer"] = (
        page.locator(".ws-lightbox").count() == 0 and page.is_visible("#wsDrawer.is-open")
    )

    # Tasks: schedule a one-off task.
    page.click("#ws-tab-tasks")
    page.wait_for_selector("#wsTaskForm")
    page.fill("#wsTaskForm [name=goal]", "Summarise the uploaded sales table.")
    page.click("#wsTaskForm button[type=submit]")
    page.wait_for_selector("#wsJobs .ws-card", timeout=10000)
    check["task_scheduled"] = "sales table" in page.inner_text("#wsJobs")

    # Assistants: save one and select it.
    page.click("#ws-tab-assistants")
    page.wait_for_selector("#wsAssistantForm")
    page.fill("#wsAssistantForm [name=name]", "Editor")
    page.fill("#wsAssistantForm [name=instructions]", "Answer in one short paragraph.")
    page.fill("#wsAssistantForm [name=greeting]", "What should I edit today?")
    page.click("#wsAssistantForm button[type=submit]")
    page.wait_for_selector("#wsAssistantList .ws-card", timeout=10000)
    check["assistant_saved"] = "Editor" in page.inner_text("#wsAssistantList")
    page.click("#wsAssistantList .ws-card [data-act=use]")
    page.wait_for_timeout(500)
    check["assistant_selectable"] = "selected" in page.inner_text("#wsAssistantList").lower()
    check["assistant_sets_composer"] = page.input_value("#input") == "What should I edit today?"

    # Settings: flip a real toggle and confirm the server agreed.
    page.click("#ws-tab-settings")
    page.wait_for_selector(".ws-toggle input")
    before = page.is_checked('.ws-toggle input[data-key="memory_enabled"]')
    page.click('.ws-toggle input[data-key="memory_enabled"]')
    page.wait_for_timeout(700)
    after = page.is_checked('.ws-toggle input[data-key="memory_enabled"]')
    with urllib.request.urlopen(f"{url}/api/settings", timeout=5) as r:
        stored = json.loads(r.read().decode("utf-8"))["values"]["memory_enabled"]
    check["settings_persist"] = before != after and stored == after
    check["telemetry_locked"] = page.is_disabled('.ws-toggle input[data-key="telemetry"]')

    # The temperature chosen here must reach the request the chat sends.
    page.fill("#wsTemp", "0.3")
    page.dispatch_event("#wsTemp", "change")
    page.wait_for_timeout(400)
    check["temperature_saved"] = page.evaluate(
        "() => localStorage.getItem('tolesh.temperature')") == "0.3"
    shot("settings")

    # Connections: the provider catalogue must list all six.
    page.click("#ws-tab-connections")
    page.wait_for_selector("#wsBody .ws-card", timeout=10000)
    text = page.inner_text("#wsBody")
    check["connectors_catalogued"] = all(
        name in text for name in ["GitHub", "Google Drive", "Gmail",
                                  "Google Calendar", "Notion", "Slack"]
    )

    # A real message, to prove the setting is carried on the wire.
    captured: dict = {}

    def capture(request):
        if request.method == "POST" and request.url.endswith("/api/chat"):
            try:
                captured["body"] = json.loads(request.post_data or "{}")
            except Exception:  # noqa: BLE001 - a malformed body is recorded as empty
                captured["body"] = {}

    page.on("request", capture)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.fill("#input", "Say ok and nothing else.")
    page.click("#sendBtn")
    page.wait_for_timeout(3000)
    body = captured.get("body") or {}
    check["temperature_in_request"] = body.get("temperature") == 0.3
    report["chat_request"] = {k: body.get(k) for k in
                              ("model", "task", "temperature", "assistant_id")}
    # Reloading stops the stream and proves the app still boots cleanly.
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#workspaceRailBtn", timeout=15000)

    # --- layout --------------------------------------------------------------
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    overflows = []
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(350)
        closed = no_overflow(page)
        if not page.is_visible("#wsDrawer.is-open"):
            page.click("#workspaceRailBtn")
        page.wait_for_timeout(350)
        opened = no_overflow(page)
        if closed["overflow"] or opened["overflow"]:
            overflows.append({"width": width, "closed": closed, "opened": opened})
        if width in (320, 768):
            shot(f"width-{width}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
    check["no_horizontal_overflow"] = not overflows
    if overflows:
        report["overflow"] = overflows

    # Escape must close, and focus must return to the rail button.
    page.set_viewport_size({"width": 1440, "height": 900})
    page.click("#workspaceRailBtn")
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check["escape_closes"] = page.get_attribute("#wsDrawer", "aria-hidden") == "true"

    # Focus must stay inside a drawer that declares aria-modal.
    page.click("#workspaceRailBtn")
    page.wait_for_timeout(300)
    inside = True
    for _ in range(28):
        page.keyboard.press("Tab")
        inside = inside and page.evaluate(
            "() => !!document.activeElement && !!document.activeElement.closest('#wsDrawer')")
    check["focus_trapped"] = inside

    # The tablist answers to arrow keys, and each tab names the panel it opens.
    page.click("#ws-tab-conversation")
    page.wait_for_timeout(200)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    check["tab_arrow_keys"] = page.get_attribute("#ws-tab-agents", "aria-selected") == "true"
    check["tab_controls_panel"] = page.locator("#ws-panel-agents").count() == 1
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check["focus_returns"] = page.evaluate(
        "() => document.activeElement && document.activeElement.id === 'workspaceRailBtn'"
    )


if __name__ == "__main__":
    raise SystemExit(main())
