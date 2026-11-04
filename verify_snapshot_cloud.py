"""Browser verification for the cloud snapshot viewer.

Runs a local HTTP server and exercises the public viewer in Microsoft Edge.
No repository content is modified by this script.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
MODE = {"value": "valid"}


def fixture_content() -> dict:
    now = datetime.now(timezone.utc)
    old_source = (now - timedelta(days=4)).isoformat(timespec="seconds")
    weather_stamp = now.isoformat(timespec="seconds")
    desks = [
        ("news", "National"),
        ("news", "World"),
        ("markets", "Business"),
        ("science tech", "Technology"),
        ("architecture housing", "Housing"),
        ("sports", "Sports"),
    ]
    slides = [{
        "id": "capture-notice",
        "type": "story",
        "desk": "news",
        "section": "Coverage",
        "headline": "Current capture uses public feeds and identifies unavailable coverage",
        "summary": "This notice is intentionally unsourced and must not become a ticker priority or lead photo.",
    }]
    for index in range(18):
        desk, section = desks[index % len(desks)]
        slides.append(
            {
                "id": f"story-{index + 1:02d}",
                "type": "story",
                "desk": desk,
                "section": section,
                "headline": f"Verification headline {index + 1} covers the complete cloud edition",
                "summary": "This is a browser-verification summary with enough words to exercise the optional story view without changing the published content or relying on external services.",
                "source": {
                    "label": "Public verification source",
                    "url": f"https://example.com/story-{index + 1}",
                    "asOf": old_source if index == 0 else weather_stamp,
                },
            }
        )
    return {
        "schemaVersion": 1,
        "edition": {
            "id": "browser-verification",
            "date": now.date().isoformat(),
            "title": "The Morning Edition",
            "subtitle": "Cloud verification",
            "timezone": "America/New_York",
            "isExample": False,
            "generatedAt": weather_stamp,
        },
        "playback": {"secondsPerSlide": 12, "refreshSeconds": 300},
        "weather": [
            {"location": "New York", "summary": "Clear, 61°F", "asOf": weather_stamp},
            {"location": "Washington", "summary": "Cloudy, 58°F", "asOf": weather_stamp},
        ],
        "slides": slides,
    }


FIXTURE = fixture_content()
EXPECTED_IDS = [slide["id"] for slide in FIXTURE["slides"]]
EXPECTED_HEADLINES = [slide["headline"] for slide in FIXTURE["slides"]]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE), **kwargs)

    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        if urlparse(self.path).path.endswith("/content.json"):
            if MODE["value"] == "invalid":
                body = b"{not-json"
            else:
                body = json.dumps(copy.deepcopy(FIXTURE)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


class ActualSiteHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE), **kwargs)

    def log_message(self, _format, *_args):
        pass


def check(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def wait_loaded(page):
    page.goto("/", wait_until="domcontentloaded")
    page.locator("#news-ticker:not([hidden])").wait_for(state="visible")
    page.locator(".snapshot-card").first.wait_for(state="visible")


def collect_snapshot_ids(page):
    page.get_by_role("button", name="Pause", exact=True).click()
    counter = page.locator("#slide-counter").inner_text().strip()
    total = int(counter.split("/")[1].strip())
    found = []
    for number in range(1, total + 1):
        check(page.locator("#slide-counter").inner_text().strip() == f"{number} / {total}", "snapshot page counter is inconsistent")
        found.extend(page.locator(".snapshot-card").evaluate_all("nodes => nodes.map(node => node.dataset.storyId)"))
        if number < total:
            page.get_by_role("button", name="Next", exact=True).click()
    return found, total


def verify_viewport(browser, base_url, width, height, mobile=False):
    context = browser.new_context(
        base_url=base_url,
        viewport={"width": width, "height": height},
        is_mobile=mobile,
        has_touch=mobile,
    )
    page = context.new_page()
    wait_loaded(page)

    check(page.locator("#tv-frame").evaluate("node => node.classList.contains('snapshot-mode')"), "snapshot must be the default view")
    ids, page_total = collect_snapshot_ids(page)
    if page_total > 1:
        page.get_by_role("button", name="Next", exact=True).click()
    counts = Counter(ids)
    check(set(ids) == set(EXPECTED_IDS), f"not all headline IDs are represented at {width}x{height}")
    check(all(counts[item] == 1 for item in EXPECTED_IDS), f"headline IDs must appear exactly once across snapshot pages at {width}x{height}")
    check(len(page.locator(".snapshot-photo").all()) <= 2, "snapshot exceeds the two-image cap")

    font_sizes = page.locator(".snapshot-headline").evaluate_all("nodes => nodes.map(node => parseFloat(getComputedStyle(node).fontSize))")
    check(font_sizes and min(font_sizes) >= 20, f"snapshot headline font fell below 20px at {width}x{height}: {font_sizes}")

    ticker = page.locator("#news-ticker")
    check(ticker.is_visible(), "bottom ticker is not visible")
    check("Quote unavailable" in page.locator("#ticker-quotes").inner_text(), "missing explicit quote-unavailable state")
    check("Current capture" not in page.locator("#ticker-window").inner_text(), "unsourced capture notice leaked into the ticker")
    check("Market quotes and photos unavailable" in page.locator("#coverage-disclosure").inner_text(), "missing compact unsupported-coverage disclosure")
    viewport_fit = page.evaluate(
        """() => {
          const ticker = document.querySelector('#news-ticker').getBoundingClientRect();
          const controls = document.querySelector('.controls-bar').getBoundingClientRect();
          return {tickerTop: ticker.top, tickerBottom: ticker.bottom, controlsBottom: controls.bottom, height: innerHeight};
        }"""
    )
    check(viewport_fit["tickerTop"] >= viewport_fit["controlsBottom"] - 1, f"ticker overlaps controls at {width}x{height}: {viewport_fit}")
    if not mobile:
        check(viewport_fit["tickerBottom"] <= height + 1, f"ticker is outside the TV viewport at {width}x{height}: {viewport_fit}")

    weather_time = page.locator(".weather-asof").first.inner_text()
    check(FIXTURE["weather"][0]["asOf"] in weather_time, "weather ISO datetime metadata was not preserved")
    source_titles = page.locator(".snapshot-meta[title]").evaluate_all("nodes => nodes.map(node => node.title)")
    check(FIXTURE["slides"][1]["source"]["asOf"] in source_titles, "source timestamp metadata was not preserved")
    check("STALE" in page.locator("#edition-status").inner_text(), "old source timestamp did not retain stale-edition status")
    check("Science & technology" in page.locator("#slide-stage").inner_text(), "lowercase desk-label mapping failed")

    page.get_by_role("button", name="Story view", exact=True).click()
    check(page.locator(".slide-story").is_visible(), "optional Story view did not render")
    page.get_by_role("button", name="Snapshot", exact=True).click()
    check(page.locator(".snapshot-card").first.is_visible(), "snapshot view did not restore")

    page.get_by_role("button", name="Resume", exact=True).click()
    page.get_by_role("button", name="Pause ticker", exact=True).click()
    check(page.locator("#news-ticker").evaluate("node => node.classList.contains('ticker-stopped')"), "ticker pause button did not stop motion")
    page.get_by_role("button", name="Resume ticker", exact=True).click()
    page.get_by_role("button", name="Pause", exact=True).click()
    check(page.locator("#news-ticker").evaluate("node => node.classList.contains('ticker-stopped')"), "playback pause did not stop ticker motion")

    print(f"PASS viewport {width}x{height}: {len(ids)} IDs exactly once across {page_total} snapshot page(s)")
    context.close()


def verify_reduced_motion(browser, base_url):
    context = browser.new_context(base_url=base_url, viewport={"width": 1280, "height": 720}, reduced_motion="reduce")
    page = context.new_page()
    wait_loaded(page)
    check(page.get_by_role("button", name="Resume", exact=True).get_attribute("aria-pressed") == "true", "reduced motion did not pause playback")
    check(page.locator("#btn-ticker").is_disabled(), "reduced motion did not disable ticker animation control")
    animation = page.locator(".ticker-track").evaluate("node => getComputedStyle(node).animationName")
    check(animation == "none", f"ticker animation remains active under reduced motion: {animation}")
    print("PASS reduced motion: playback and ticker animation paused")
    context.close()


def verify_refresh_failure(browser, base_url):
    MODE["value"] = "valid"
    context = browser.new_context(base_url=base_url, viewport={"width": 1366, "height": 768})
    page = context.new_page()
    wait_loaded(page)
    before = page.locator(".snapshot-card").evaluate_all("nodes => nodes.map(node => node.dataset.storyId)")
    MODE["value"] = "invalid"
    page.get_by_role("button", name="Reload", exact=True).click()
    page.locator("#edition-status").filter(has_text="Update failed").wait_for(state="visible")
    after = page.locator(".snapshot-card").evaluate_all("nodes => nodes.map(node => node.dataset.storyId)")
    check(before == after and after, "failed JSON refresh did not retain the last good snapshot")
    check("retained last good edition" in page.locator("#edition-status").inner_text(), "failed refresh retention status is missing")
    print("PASS invalid JSON refresh: last good edition retained")
    context.close()
    MODE["value"] = "valid"


def verify_actual_cloud_content(browser):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ActualSiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/"
    try:
        context = browser.new_context(base_url=base_url, viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        wait_loaded(page)
        actual = page.evaluate("async () => (await fetch('./content.json', {cache: 'no-store'})).json()")
        expected = [slide["id"] for slide in actual["slides"]]
        ids, pages = collect_snapshot_ids(page)
        counts = Counter(ids)
        check(set(ids) == set(expected), "actual cloud content has headlines missing from snapshot pages")
        check(all(counts[item] == 1 for item in expected), "actual cloud headline IDs are not represented exactly once")
        check(len(page.locator(".snapshot-photo").all()) <= 2, "actual cloud snapshot exceeds the image cap")
        no_source = next((slide["headline"] for slide in actual["slides"] if not slide.get("source")), None)
        if no_source:
            check(no_source not in page.locator("#ticker-window").inner_text(), "actual unsourced coverage notice leaked into ticker priorities")
        check(page.locator("#news-ticker").is_visible(), "actual cloud ticker is hidden")
        print(f"PASS actual cloud content: {len(ids)} IDs exactly once across {pages} snapshot page(s)")
        context.close()
    finally:
        server.shutdown()
        server.server_close()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            try:
                for width, height, mobile in [
                    (1920, 1080, False),
                    (1366, 768, False),
                    (1280, 720, False),
                    (390, 844, True),
                ]:
                    verify_viewport(browser, base_url, width, height, mobile)
                verify_reduced_motion(browser, base_url)
                verify_refresh_failure(browser, base_url)
                verify_actual_cloud_content(browser)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("ALL SNAPSHOT CLOUD VERIFICATIONS PASSED")


if __name__ == "__main__":
    main()
