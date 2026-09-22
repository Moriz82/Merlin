#!/usr/bin/env python3
"""Bounded browser acceptance for installed Harbinger/Merlin builds.

Loads app assets from the supplied loopback service. Every /api request is
redirected to this process's synthetic loopback fixture, including native SSE.
Never writes workspace data or uses account credentials. See BROWSER-QA.md.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

FIXTURE_VERSION = "synthetic-browser-v1"
NOW = "2026-09-22T12:00:00Z"
UNSAFE_MARKDOWN = """## Synthetic markdown check

<b>Raw HTML must be omitted</b>

[Blocked script link](javascript:void(0))

[Blocked external link](https://example.invalid/blocked)

![Blocked remote image](https://example.invalid/blocked.png)

[Allowed fragment](#synthetic-section)
"""


def record(kind, suffix, data):
    return {"id": f"00000000-0000-4000-8000-{suffix:012d}", "kind": kind,
            "revision_id": f"synthetic-revision-{suffix}", "updated_at": NOW, "data": data}


class Fixture:
    def __init__(self, app, origin):
        self.app, self.origin = app, origin
        self.authenticated = False
        self.disconnected = threading.Event()
        self.requests, self.unhandled, self.login_checks = [], [], []
        self.assets = [{"id": f"asset-{n}", "revision_id": f"asset-revision-{n}",
                        "label": f"Synthetic host {n}", "kind": "host", "track": "network"}
                       for n in (1, 2)]
        self.finding = record("finding", 1, {"title": "Synthetic observation", "observation": "Synthetic source observation",
            "impact": "Synthetic impact", "steps": "Local-only example", "asset_ids": [], "evidence_ids": [],
            "writing_state": "draft", "evidence_state": "observed", "notes": "", "owner_id": "", "evidence_needed": ""})
        self.lead = record("lead", 2, {**self.finding["data"], "finding_id": self.finding["id"]})
        self.draft = record("draft", 3, {"title": "Synthetic report section", "description": "## Synthetic section\n\nSaved synthetic prose.",
            "impact": "Synthetic impact", "remediation": "Synthetic remediation", "references": "",
            "evidence_ids": [], "owner_id": "", "lead_id": ""})
        self.evidence = record("upload", 4, {"filename": "synthetic-proof.txt", "sha256": "0" * 64, "reviewed_for_export": False})

    def session(self):
        return {"user": {"id": "synthetic-user", "name": "Synthetic reviewer", "role": "captain" if self.app == "Harbinger" else "lead_scribe"} if self.authenticated else None,
                "csrf": "synthetic-fixture-not-an-authentication-secret" if self.authenticated else None,
                "app": self.app, "engagement": {"id": "synthetic-engagement", "name": "Synthetic browser acceptance"}, "mode": "lan"}

    def response(self, method, target, body):
        url = urlsplit(target)
        path, query = url.path, parse_qs(url.query)
        self.requests.append({"method": method, "path": path})
        if self.disconnected.is_set():
            return 503, {"detail": "Synthetic fixture connection unavailable"}
        if path == "/api/session":
            return 200, self.session()
        if path == "/api/login" and method == "POST":
            self.login_checks.append(body == {"name": "synthetic-reviewer", "password": "synthetic-fixture-only"})
            self.authenticated = True
            return 200, self.session()
        if path == "/api/logout" and method == "POST":
            self.authenticated = False
            return 200, {"ok": True}
        if path == "/api/readiness":
            return 200, {"write_ready": True}
        if path == "/api/assets":
            return 200, {"items": self.assets, "total": len(self.assets)}
        if path == "/api/graph":
            return 200, {"nodes": self.assets, "edges": [], "total_nodes": 2, "total_edges": 0}
        if path == "/api/users":
            return 200, {"items": [{"id": "synthetic-user", "name": "Synthetic reviewer"}]}
        if path == "/api/records" and method == "GET":
            rows = {"finding": [self.finding], "lead": [self.lead], "draft": [self.draft], "question": []}.get(query.get("kind", [""])[0])
            if rows is not None:
                return 200, {"items": rows, "total": len(rows)}
        if path == "/api/evidence":
            return 200, {"items": [self.evidence], "total": 1}
        if path == f"/api/evidence/{self.evidence['id']}/preview":
            return 200, {"text": "Synthetic text preview. No real source bytes.", "quarantined": False,
                         "base_revision_id": self.evidence["revision_id"], "artifact_sha256": "0" * 64,
                         "image_available": False}
        if path in ("/api/uploads", "/api/transfers/conflicts", "/api/deliveries"):
            return 200, {"items": [], "total": 0}
        if path == "/api/connections":
            return 200, {"peers": [], "ghostwriter": {"status": "not_configured", "report_id": ""}}
        for attr in ("finding", "draft", "lead"):
            item = getattr(self, attr)
            if path == f"/api/records/{item['id']}":
                if method == "PUT":
                    item = {**item, "revision_id": item["revision_id"] + "-saved", "data": body["data"]}
                    setattr(self, attr, item)
                return 200, item
        self.unhandled.append({"method": method, "path": path})
        return 501, {"detail": "Missing acceptance fixture endpoint"}


def fixture_server(fixture):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", fixture.origin)
            self.send_header("Access-Control-Allow-Headers", "content-type,x-csrf-token")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,OPTIONS")
            self.end_headers()

        def handle_request(self):
            if self.path == "/api/events" and not fixture.disconnected.is_set():
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", fixture.origin)
                self.end_headers()
                try:
                    self.wfile.write(b": synthetic native event stream\n\n")
                    self.wfile.flush()
                    fixture.disconnected.wait(45)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            status, result = fixture.response(self.command, self.path, body)
            payload = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", fixture.origin)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = do_POST = do_PUT = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def layout(page):
    result = page.evaluate("""() => ({width:innerWidth,height:innerHeight,
        documentWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth,
        overflow:Math.max(document.documentElement.scrollWidth,document.body.scrollWidth)>innerWidth+1})""")
    check(not result["overflow"], f"Page-wide horizontal overflow: {result}")
    return result


def capture(page, output, name):
    target = output / f"{name}.png"
    page.screenshot(path=str(target), full_page=False)
    png = target.read_bytes()
    return {"file": target.name, "sha256": hashlib.sha256(png).hexdigest(),
            "application": name.split("-")[0].capitalize(), "route": urlsplit(page.url).fragment,
            "viewport": page.viewport_size, "image_dimensions": {"width": int.from_bytes(png[16:20], "big"), "height": int.from_bytes(png[20:24], "big")},
            "role": "captain" if name.startswith("harbinger-") else "lead_scribe",
            "fixture": FIXTURE_VERSION, "operator": "automated Chromium acceptance",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "full_page": False, "browser_zoom": "default; no zoom command applied"}


def accessibility(page):
    session = page.context.new_cdp_session(page)
    nodes = session.send("Accessibility.getFullAXTree")["nodes"]
    session.detach()
    controls = [n for n in nodes if not n.get("ignored") and n.get("role", {}).get("value") in
                ("button", "link", "textbox", "combobox", "checkbox")]
    unnamed = [n.get("role", {}).get("value") for n in controls if not n.get("name", {}).get("value", "").strip()]
    check(not unnamed, f"Unnamed accessibility controls: {unnamed}")
    mains = sum(n.get("role", {}).get("value") == "main" and not n.get("ignored") for n in nodes)
    check(mains == 1, f"Expected one main landmark, found {mains}")
    return {"named_controls": len(controls), "unnamed_controls": unnamed,
            "main_landmarks": mains,
            "method": "Chromium accessibility tree; not screen-reader speech or usability proof"}


def keyboard_dialog(page, navigation, app):
    navigation.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    initial = dialog.get_by_role("button", name="Stay" if app == "Harbinger" else "Stay and edit", exact=True)
    expect(initial).to_be_focused()
    first = dialog.get_by_role("button").first
    first.focus()
    page.keyboard.press("Shift+Tab")
    expect(dialog.get_by_role("button").last).to_be_focused()
    page.keyboard.press("Tab")
    expect(first).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(navigation).to_be_focused()
    return {"initial_focus": True, "tab_wrap": True, "escape": True, "focus_restored": True}


def loaded_route(page, app, label):
    """Do not measure a route's loading skeleton as its final content."""
    if label == "Map":
        expect(page.get_by_role("region", name="Accessible relationship list").get_by_role("button", name="Synthetic host 1")).to_be_visible()
    elif label == "Imports":
        expect(page.get_by_text("No imports yet", exact=True)).to_be_visible()
    elif label == "Findings":
        expect(page.get_by_label("Observation", exact=True)).to_have_value("Synthetic source observation")
    elif label == "Evidence":
        expect(page.get_by_role("button", name=re.compile(r"^synthetic-proof\.txt(?: |$)"))).to_be_visible()
    elif label == "Inbox":
        expect(page.get_by_role("button", name=re.compile("Synthetic observation"))).to_be_visible()
    elif label == "Drafts":
        expect(page.get_by_role("button", name=re.compile("Synthetic report section"))).to_be_visible()
    elif label == "Transfer":
        expect(page.get_by_text("No transfer conflicts", exact=True)).to_be_visible()
        if app == "Harbinger":
            expect(page.get_by_role("checkbox", name="Synthetic observation", exact=True)).to_be_visible()


def run_case(browser, app, origin, output, width, height):
    fixture = Fixture(app, origin)
    server = fixture_server(fixture)
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(7000)
    errors, external, failed, responses = [], [], [], []
    phase = {"name": "online"}
    result = {"viewport": {"width": width, "height": height}, "screenshots": [], "routes": {}}

    def route_request(route):
        url = urlsplit(route.request.url)
        if url.hostname != "127.0.0.1":
            external.append(route.request.url)
            return route.abort()
        if url.path.startswith("/api/"):
            return route.continue_(url=f"http://127.0.0.1:{server.server_port}{url.path}" + (f"?{url.query}" if url.query else ""))
        route.continue_()

    context.route("**/*", route_request)
    page.on("pageerror", lambda error: errors.append({"phase": phase["name"], "kind": "pageerror", "message": str(error)}))
    page.on("console", lambda message: errors.append({"phase": phase["name"], "kind": "console", "message": message.text}) if message.type == "error" else None)
    page.on("requestfailed", lambda request: failed.append({"phase": phase["name"], "path": urlsplit(request.url).path, "failure": request.failure}))
    page.on("response", lambda response: responses.append({"phase": phase["name"], "path": urlsplit(response.url).path, "status": response.status}) if response.status >= 400 else None)
    try:
        page.goto(origin, wait_until="domcontentloaded")
        expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_visible()
        result["login_layout"] = layout(page)
        page.get_by_label("Account name").fill("synthetic-reviewer")
        page.get_by_label("Password", exact=True).fill("synthetic-fixture-only")
        page.get_by_role("button", name="Sign in", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("navigation", name="Primary navigation")).to_be_visible()
        expect(page.get_by_role("button", name="Sign out", exact=True)).to_be_enabled()
        check(fixture.login_checks == [True], "Login did not submit the expected synthetic form")
        result["synthetic_login"] = "passed; browser form and fixture contract only"
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_role("button", name="Sign out", exact=True)).to_be_enabled()
        page.keyboard.press("Tab")
        expect(page.get_by_role("link", name="Skip to content")).to_be_focused()
        outline = page.get_by_role("link", name="Skip to content").evaluate("e=>({style:getComputedStyle(e).outlineStyle,width:getComputedStyle(e).outlineWidth,visible:e.getBoundingClientRect().top>=0})")
        check(outline["style"] != "none" and outline["width"] != "0px" and outline["visible"], "Skip link focus is not visibly styled")
        page.keyboard.press("Enter")
        expect(page.get_by_role("main")).to_be_focused()
        result["keyboard_skip"] = {"first_tab": True, "main_focus": True, "focus_style": outline}
        reduced = page.evaluate("""() => ({matched:matchMedia('(prefers-reduced-motion: reduce)').matches,
            activeAnimations:document.getAnimations().filter(a=>a.playState==='running').length,
            nonzeroTransitions:[...document.querySelectorAll('*')].filter(e=>getComputedStyle(e).transitionDuration.split(',').some(v=>parseFloat(v)>0)).length})""")
        check(reduced["matched"] and not reduced["activeAnimations"] and not reduced["nonzeroTransitions"], "Reduced motion not respected")
        result["reduced_motion"] = reduced
        if app == "Harbinger":
            reminder = page.locator("em").filter(has_text=re.compile("^report as you go$"))
            expect(reminder).to_have_count(1)
            expect(reminder).to_be_visible()
            check(reminder.evaluate("e=>getComputedStyle(e).fontStyle") == "italic", "Reminder is not italic")
            result["exact_italic_reminder"] = True
        routes = [("Map", "Evidence map"), ("Imports", "Imports"), ("Findings", "Findings"), ("Evidence", "Evidence"), ("Transfer", "Encrypted transfer")] if app == "Harbinger" else [("Inbox", "Inbox"), ("Drafts", "Drafts"), ("Evidence", "Evidence"), ("Transfer", "Transfer")]
        nav = page.get_by_role("navigation", name="Primary navigation")
        for label, heading in routes:
            link = nav.get_by_role("link", name=label, exact=True)
            link.focus()
            page.keyboard.press("Enter")
            expect(link).to_have_attribute("aria-current", "page")
            expect(page.get_by_role("heading", name=heading, exact=True).first).to_be_visible()
            loaded_route(page, app, label)
            result["routes"][label] = {"layout": layout(page), "accessibility": accessibility(page)}
        nav.get_by_role("link", name="Map" if app == "Harbinger" else "Inbox", exact=True).click()
        loaded_route(page, app, "Map" if app == "Harbinger" else "Inbox")
        result["screenshots"].append(capture(page, output, f"{app.lower()}-home-{width}x{height}"))
        if app == "Harbinger":
            node = page.get_by_role("region", name="Accessible relationship list").get_by_role("button", name="Synthetic host 2")
            node.focus()
            page.keyboard.press("Enter")
            expect(page.get_by_role("heading", name="Details for Synthetic host 2")).to_be_visible()
            result["keyboard_graph_alternative"] = True
            nav.get_by_role("link", name="Findings", exact=True).click()
            editor = page.get_by_label("Observation", exact=True)
            expect(editor).to_have_value("Synthetic source observation")
            editor.fill("Synthetic local unsaved observation")
            result["dialog"] = keyboard_dialog(page, nav.get_by_role("link", name="Imports", exact=True), app)
        else:
            nav.get_by_role("link", name="Drafts", exact=True).click()
            page.get_by_role("button", name=re.compile("Synthetic report section")).click()
            editor = page.get_by_label("Draft description in Markdown")
            expect(editor).to_be_visible()
            editor.fill(UNSAFE_MARKDOWN)
            preview = page.get_by_label("Markdown preview")
            expect(preview.get_by_text("Unsafe link omitted.", exact=True)).to_have_count(2)
            expect(preview.get_by_text("Embedded image omitted. Attach reviewed evidence instead.", exact=True)).to_be_visible()
            expect(preview.locator("b,img,script,iframe")).to_have_count(0)
            expect(preview.get_by_role("link", name="Allowed fragment")).to_have_attribute("href", "#synthetic-section")
            check(not external, "Unsafe markdown triggered an external request")
            result["unsafe_markdown"] = {"html_omitted": True, "unsafe_links_omitted": 2, "remote_image_omitted": True, "external_requests": 0}
        result["editor_layout"] = layout(page)
        result["editor_accessibility"] = accessibility(page)
        result["screenshots"].append(capture(page, output, f"{app.lower()}-editor-{width}x{height}"))
        phase["name"] = "intentional_connection_loss"
        fixture.disconnected.set()
        expect(page.locator(".connection-banner")).to_contain_text("Server writes are disabled")
        expect(page.get_by_role("button", name="Sign out", exact=True)).to_be_disabled()
        editor.fill("Synthetic offline text is retained")
        save = page.get_by_role("button", name="Save" if app == "Harbinger" else "Save now", exact=True)
        expect(save).to_be_disabled()
        expect(editor).to_have_value("Synthetic offline text is retained")
        expect(page.locator(".save-state")).to_contain_text(re.compile("[Nn]ot saved|[Cc]onnection"))
        writes_before = sum(row["method"] in ("POST", "PUT") for row in fixture.requests)
        page.wait_for_timeout(1200)
        check(writes_before == sum(row["method"] in ("POST", "PUT") for row in fixture.requests), "Offline edit attempted a write")
        if app == "Merlin":
            result["dialog"] = keyboard_dialog(page, nav.get_by_role("link", name="Inbox", exact=True), app)
        result["connection_loss"] = {"native_eventsource_stream_closed": True, "banner": page.locator(".connection-banner").inner_text(),
                                     "write_control_disabled": True, "local_text_retained": True, "no_offline_write": True}
        result["screenshots"].append(capture(page, output, f"{app.lower()}-offline-{width}x{height}"))
        result["offline_layout"] = layout(page)
        check(not fixture.unhandled, f"Unhandled fixture routes: {fixture.unhandled}")
        check(not external, f"Unexpected external requests: {external}")
        check(not [row for row in errors + failed + responses if row["phase"] == "online"], f"Unexpected online browser/network errors: {errors + failed + responses}")
        check(not [row for row in errors if row["kind"] == "pageerror"], f"JavaScript exceptions: {errors}")
        result["browser_errors"] = errors
        result["failed_requests"] = failed
        result["http_errors"] = responses
        result["unexpected_fixture_routes"] = fixture.unhandled
        result["status"] = "passed"
    except Exception as error:
        result["status"], result["failure"] = "failed", str(error)
        result["browser_errors"], result["failed_requests"], result["http_errors"] = errors, failed, responses
        result["unexpected_fixture_routes"] = fixture.unhandled
        result["screenshots"].append(capture(page, output, f"{app.lower()}-failure-{width}x{height}"))
    finally:
        fixture.disconnected.set()
        context.close()
        server.shutdown()
        server.server_close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=("Harbinger", "Merlin"), required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chromium", default="/usr/sbin/chromium")
    args = parser.parse_args()
    url = urlsplit(args.origin)
    check(url.scheme == "http" and url.hostname == "127.0.0.1" and url.port and not url.username and url.path in ("", "/"), "Origin must be a plain loopback HTTP service")
    args.output.mkdir(parents=True, exist_ok=True)
    origin = args.origin.rstrip("/")
    (args.output / "RESULTS.json").write_text(json.dumps({"app": args.app, "origin": origin,
        "status": "incomplete", "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "This run has not completed. An interrupted or unavailable-service run must not retain a prior passing manifest."}, indent=2) + "\n")
    with urlopen(origin, timeout=5) as response:
        html = response.read()
    assets = re.findall(rb'(?:src|href)="(/assets/[^"]+)"', html)
    asset_hashes = {}
    for asset in assets:
        path = asset.decode()
        with urlopen(origin + path, timeout=5) as response:
            asset_hashes[path] = hashlib.sha256(response.read()).hexdigest()
    summary = {"app": args.app, "origin": origin, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
               "fixture": FIXTURE_VERSION, "playwright": importlib.metadata.version("playwright"),
               "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "served_asset_sha256": asset_hashes,
               "scope": "Real browser and real served built assets; all API/session/login data use synthetic local fixture contracts. Native SSE fixture connection is deliberately closed. No live backend writes or credentials.",
               "limitations": ["683x384 is a compact reflow proxy only; actual 200 percent browser zoom requires manual acceptance.",
                               "Accessibility tree, focus, roles and names are automated; actual screen-reader output and usability require manual acceptance.",
                               "Synthetic login does not prove live authentication; backend behavior requires separate integration tests."]}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium, headless=True, args=["--no-sandbox"])
        summary["chromium"] = browser.version
        live = browser.new_page(viewport={"width": 1366, "height": 768})
        live.goto(origin, wait_until="domcontentloaded")
        expect(live.get_by_label("Account name")).to_be_visible()
        expect(live.get_by_label("Password", exact=True)).to_be_visible()
        expect(live.get_by_role("button", name="Sign in", exact=True)).to_be_visible()
        summary["live_unauthenticated_screen"] = "passed; real session request renders login form, no credentials submitted"
        live.close()
        summary["cases"] = [run_case(browser, args.app, origin, args.output, *viewport) for viewport in ((1366, 768), (1920, 1080), (683, 384))]
        browser.close()
    summary["status"] = "passed" if all(case["status"] == "passed" for case in summary["cases"]) else "failed"
    (args.output / "RESULTS.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"app": args.app, "status": summary["status"], "cases": [{"viewport": case["viewport"], "status": case["status"], "failure": case.get("failure")} for case in summary["cases"]], "result": str(args.output / "RESULTS.json")}, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
