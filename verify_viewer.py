"""Browser acceptance checks; requires Playwright only for developer verification.

Run: python verify_viewer.py [https://ndooley-11.github.io/morning-newsletter/]
No live actions, external source collection, mail, or account access.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent


def check(url: str, screenshot: Path) -> dict[str, object]:
    """Verify actual viewer, then isolated browser-only fault injection."""
    with sync_playwright() as runner:
        browser = runner.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        response = page.goto(url)
        assert response and response.ok
        expect(page.locator("#slide-counter")).to_have_text(re.compile(r"1 / [1-9]\d*"))
        page.get_by_role("button", name="Pause", exact=True).click()
        counter = page.locator("#slide-counter")
        total = int(counter.inner_text().split("/")[1])
        # Measure every slide at all target TV sizes, not just the opening slide.
        for width, height in ((1920, 1080), (1366, 768), (1280, 720)):
            page.set_viewport_size({"width": width, "height": height})
            for _ in range(total):
                assert not page.evaluate(
                    "document.documentElement.scrollWidth > innerWidth+1"
                ), (width, counter.inner_text())
                assert not page.evaluate(
                    "document.documentElement.scrollHeight > innerHeight+1"
                ), (height, counter.inner_text())
                assert not page.evaluate(
                    "document.querySelector('#slide-stage').scrollHeight > document.querySelector('#slide-stage').clientHeight+1"
                ), ("stage overflow", width, counter.inner_text())
                page.get_by_role("button", name="Next", exact=True).click()
        page.set_viewport_size({"width": 1920, "height": 1080})
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        # The local speed choice must survive a successful content refresh.
        page.get_by_label("Slide duration").select_option("30")
        page.get_by_role("button", name="Reload", exact=True).click()
        expect(page.locator("#edition-status")).not_to_contain_text("Checking")
        expect(page.get_by_label("Slide duration")).to_have_value("30")
        page.clock.install()
        speed_position = counter.inner_text()
        page.get_by_role("button", name="Resume", exact=True).click()
        page.clock.run_for(13000)
        assert counter.inner_text() == speed_position
        page.clock.run_for(18000)
        assert counter.inner_text() != speed_position
        page.get_by_role("button", name="Pause", exact=True).click()
        first = counter.inner_text()
        page.keyboard.press("ArrowRight")
        assert counter.inner_text() != first
        page.keyboard.press("ArrowLeft")
        assert counter.inner_text() == first
        # A late/missing update retains existing slide and pause intent.
        page.route(
            "**/content.json",
            lambda route: route.fulfill(status=503, body="Unavailable"),
        )
        page.get_by_role("button", name="Reload", exact=True).click()
        expect(page.locator("#edition-status")).to_contain_text("Update failed")
        assert counter.inner_text() == first
        expect(page.get_by_role("button", name="Resume", exact=True)).to_be_visible()
        page.unroute("**/content.json")
        # Synthetic data is isolated to route interception, never persisted/published.
        value = page.request.get(url + "content.json").json()
        stale_weather = copy.deepcopy(value)
        stale_weather["weather"][0]["asOf"] = "2000-01-01T00:00:00Z"
        page.route("**/content.json", lambda route: route.fulfill(json=stale_weather))
        page.get_by_role("button", name="Reload", exact=True).click()
        expect(page.locator("#edition-status")).to_contain_text("STALE")
        page.unroute("**/content.json")
        stale = copy.deepcopy(value)
        stale["edition"]["date"] = "2000-01-01"
        stale["edition"]["id"] = "fixture-stale"
        page.route("**/content.json", lambda route: route.fulfill(json=stale))
        page.get_by_role("button", name="Reload", exact=True).click()
        expect(page.locator("#edition-status")).to_contain_text("STALE")
        page.unroute("**/content.json")
        injected = copy.deepcopy(value)
        injected["slides"][0]["headline"] = '<img src=x onerror="window.INJECTED=1">'
        injected["slides"][0]["summary"] = "<script>window.INJECTED=2</script>"
        injected["private"] = {"subject": "PRIVATE_TEST_CANARY_001"}
        page.route("**/content.json", lambda route: route.fulfill(json=injected))
        page.reload()
        expect(page.locator("#slide-stage h2")).to_have_text(
            injected["slides"][0]["headline"]
        )
        assert page.evaluate("typeof window.INJECTED") == "undefined"
        assert "PRIVATE_TEST_CANARY_001" not in page.locator("body").inner_text()
        assert page.locator("#slide-stage img").count() == 0
        page.unroute("**/content.json")
        page.emulate_media(reduced_motion="reduce", color_scheme="dark")
        page.set_viewport_size({"width": 375, "height": 812})
        page.reload()
        expect(page.get_by_role("button", name="Resume", exact=True)).to_be_visible()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth+1")
        assert not errors, errors
        context.close()
        browser.close()
        return {
            "url": url,
            "slides": total,
            "tv_sizes": 3,
            "mobile": "passed",
            "failure_retention": "passed",
            "stale_warning": "passed",
            "injection": "passed",
            "page_errors": errors,
            "screenshot": str(screenshot),
        }


def main() -> None:
    """Serve the public site only, or verify an already-deployed URL."""
    screenshot = (
        Path.home() / "AppData/Local/hermes/cache/scratch/newsletter-cloud-preview.png"
    )
    if len(sys.argv) > 1:
        print(json.dumps(check(sys.argv[1], screenshot), indent=2))
        return
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(SimpleHTTPRequestHandler, directory=str(ROOT / "site")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print(
            json.dumps(
                check(f"http://127.0.0.1:{server.server_port}/", screenshot), indent=2
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
