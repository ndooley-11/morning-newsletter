from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import generate_edition as ge

NOW = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)


def rss(items: list[tuple[str, str, str]]) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><link>{url}</link><pubDate>{published}</pubDate></item>"
        for title, url, published in items
    )
    return f"<?xml version='1.0'?><rss><channel>{body}</channel></rss>".encode()


def atom(items: list[tuple[str, str, str]]) -> bytes:
    body = "".join(
        f"<entry><title>{title}</title><link href='{url}'/><updated>{published}</updated></entry>"
        for title, url, published in items
    )
    return (
        "<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>"
        + body
        + "</feed>"
    ).encode()


class FakeResponse:
    def __init__(self, payload: bytes, content_length: int | None = None) -> None:
        self._stream = io.BytesIO(payload)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class MemoryFetcher:
    def __init__(self, mapping: dict[str, bytes | Exception]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        value = self.mapping[url]
        if isinstance(value, Exception):
            raise value
        return value


class UrlValidationTests(unittest.TestCase):
    def test_accepts_allowlisted_public_https_url(self) -> None:
        self.assertEqual(
            ge.validate_public_url(
                "https://feeds.bbci.co.uk/news/rss.xml", {"feeds.bbci.co.uk"}
            ),
            "https://feeds.bbci.co.uk/news/rss.xml",
        )

    def test_rejects_http_credentials_ports_queries_and_wrong_hosts(self) -> None:
        bad = [
            "http://feeds.bbci.co.uk/news/rss.xml",
            "https://user:pass@feeds.bbci.co.uk/news/rss.xml",
            "https://feeds.bbci.co.uk:444/news/rss.xml",
            "https://feeds.bbci.co.uk/news/rss.xml?token=secret",
            "https://127.0.0.1/feed",
            "https://evil.example/feed",
        ]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(ValueError):
                ge.validate_public_url(url, {"feeds.bbci.co.uk"})


class SchedulingTests(unittest.TestCase):
    def test_dst_aware_gate_accepts_6am_in_winter_and_summer(self) -> None:
        winter = datetime(2026, 1, 15, 11, 17, tzinfo=UTC)
        summer = datetime(2026, 7, 15, 10, 17, tzinfo=UTC)
        self.assertEqual(ge.scheduled_gate(winter, None, False), (True, "run"))
        self.assertEqual(ge.scheduled_gate(summer, None, False), (True, "run"))

    def test_gate_boundaries_are_6am_inclusive_and_11am_exclusive(self) -> None:
        before = datetime(2026, 1, 15, 10, 59, tzinfo=UTC)
        opening = datetime(2026, 1, 15, 11, 0, tzinfo=UTC)
        closing = datetime(2026, 1, 15, 15, 59, tzinfo=UTC)
        after = datetime(2026, 1, 15, 16, 0, tzinfo=UTC)
        self.assertEqual(
            ge.scheduled_gate(before, None, False), (False, "outside-window")
        )
        self.assertEqual(ge.scheduled_gate(opening, None, False), (True, "run"))
        self.assertEqual(ge.scheduled_gate(closing, None, False), (True, "run"))
        self.assertEqual(
            ge.scheduled_gate(after, None, False), (False, "outside-window")
        )

    def test_duplicate_gate_handles_the_two_dst_covering_cron_runs(self) -> None:
        summer_first = datetime(2026, 7, 15, 10, 17, tzinfo=UTC)
        summer_second = datetime(2026, 7, 15, 11, 17, tzinfo=UTC)
        winter_first = datetime(2026, 1, 15, 10, 17, tzinfo=UTC)
        winter_second = datetime(2026, 1, 15, 11, 17, tzinfo=UTC)

        self.assertEqual(ge.scheduled_gate(summer_first, None, False), (True, "run"))
        self.assertEqual(
            ge.scheduled_gate(summer_second, "2026-07-15", False),
            (False, "already-generated"),
        )
        self.assertEqual(
            ge.scheduled_gate(winter_first, None, False),
            (False, "outside-window"),
        )
        self.assertEqual(ge.scheduled_gate(winter_second, None, False), (True, "run"))
        self.assertEqual(
            ge.scheduled_gate(winter_first, "2026-01-15", True),
            (True, "forced"),
        )


class FeedParsingTests(unittest.TestCase):
    def test_rss_filters_old_future_and_undated_and_caps_at_two(self) -> None:
        fresh = "Tue, 03 Nov 2026 13:00:00 GMT"
        payload = rss(
            [
                ("Fresh one", "https://www.bbc.com/news/1", fresh),
                ("Fresh two", "https://www.bbc.com/news/2", fresh),
                ("Fresh three", "https://www.bbc.com/news/3", fresh),
                ("Old", "https://www.bbc.com/news/4", "Sat, 31 Oct 2026 10:00:00 GMT"),
                (
                    "Future",
                    "https://www.bbc.com/news/5",
                    "Tue, 03 Nov 2026 14:16:00 GMT",
                ),
                ("", "https://www.bbc.com/news/6", fresh),
            ]
        )
        stories = ge.parse_feed_xml(payload, "BBC News", "news", "World", NOW)
        self.assertEqual(
            [story.headline for story in stories], ["Fresh one", "Fresh two"]
        )
        self.assertEqual(stories[0].published, "2026-11-03T13:00:00Z")

    def test_atom_is_supported_and_stable_id_depends_on_url(self) -> None:
        payload = atom(
            [("NASA item", "https://www.nasa.gov/item", "2026-11-03T13:30:00Z")]
        )
        first = ge.parse_feed_xml(payload, "NASA", "science-tech", "Science", NOW)[0]
        second = ge.parse_feed_xml(payload, "NASA", "science-tech", "Science", NOW)[0]
        self.assertEqual(first.slide_id, second.slide_id)
        self.assertTrue(first.slide_id.startswith("story-"))

    def test_doctype_entity_and_malformed_xml_are_rejected(self) -> None:
        for payload in [
            b"<!DOCTYPE rss><rss/>",
            b"<!ENTITY x 'y'><rss/>",
            b"<rss><channel>",
        ]:
            with self.subTest(payload=payload), self.assertRaises(ge.FeedError):
                ge.parse_feed_xml(payload, "Bad", "news", "News", NOW)


class NetworkBoundsTests(unittest.TestCase):
    def test_payload_limit_rejects_header_and_stream_overflow(self) -> None:
        with self.assertRaises(ge.FetchError):
            ge.read_bounded_response(FakeResponse(b"small", ge.MAX_RESPONSE_BYTES + 1))
        with self.assertRaises(ge.FetchError):
            ge.read_bounded_response(FakeResponse(b"x" * (ge.MAX_RESPONSE_BYTES + 1)))


class WeatherTests(unittest.TestCase):
    def test_forecast_summary_and_updated_time_are_recorded(self) -> None:
        payload = json.dumps(
            {
                "properties": {
                    "updated": "2026-11-03T13:00:00+00:00",
                    "periods": [
                        {
                            "name": "Today",
                            "detailedForecast": "Sunny, with a high near 72.",
                        },
                        {
                            "name": "Tonight",
                            "detailedForecast": "Clear, with a low near 51.",
                        },
                    ],
                }
            }
        ).encode()
        weather = ge.parse_weather_forecast(payload, "Savannah, GA 31401", NOW)
        self.assertEqual(weather["asOf"], "2026-11-03T13:00:00Z")
        self.assertIn("Today: Sunny", weather["summary"])
        self.assertLessEqual(len(weather["summary"]), 1000)

    def test_nws_update_time_field_is_supported(self) -> None:
        payload = json.dumps(
            {
                "properties": {
                    "updateTime": "2026-11-03T13:00:00Z",
                    "generatedAt": "2026-11-03T13:05:00Z",
                    "periods": [{"name": "Today", "detailedForecast": "Sunny."}],
                }
            }
        ).encode()
        weather = ge.parse_weather_forecast(payload, "Savannah, GA 31401", NOW)
        self.assertEqual(weather["asOf"], "2026-11-03T13:00:00Z")

    def test_forecast_older_than_24_hours_or_future_is_rejected(self) -> None:
        for updated in ["2026-11-02T13:59:59Z", "2026-11-03T14:15:01Z"]:
            payload = json.dumps(
                {"properties": {"updated": updated, "periods": []}}
            ).encode()
            with self.subTest(updated=updated), self.assertRaises(ge.WeatherError):
                ge.parse_weather_forecast(payload, "Savannah, GA 31401", NOW)


class GenerationTests(unittest.TestCase):
    def config(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "feeds": [
                {
                    "label": "BBC News",
                    "url": "https://feeds.bbci.co.uk/news/rss.xml",
                    "host": "feeds.bbci.co.uk",
                    "desk": "news",
                    "section": "World",
                },
                {
                    "label": "NASA",
                    "url": "https://www.nasa.gov/feed/",
                    "host": "www.nasa.gov",
                    "desk": "science-tech",
                    "section": "Science & Technology",
                },
                {
                    "label": "ESPN",
                    "url": "https://www.espn.com/espn/rss/news",
                    "host": "www.espn.com",
                    "desk": "sports",
                    "section": "Sports",
                },
            ],
            "weather": [
                {
                    "location": "Savannah, GA 31401",
                    "pointsUrl": "https://api.weather.gov/points/32.0809,-81.0912",
                    "host": "api.weather.gov",
                }
            ],
        }

    def mapping(self) -> dict[str, bytes | Exception]:
        recent = "Tue, 03 Nov 2026 13:00:00 GMT"
        points = json.dumps(
            {
                "properties": {
                    "forecast": "https://api.weather.gov/gridpoints/CHS/57,52/forecast"
                }
            }
        ).encode()
        forecast = json.dumps(
            {
                "properties": {
                    "updated": "2026-11-03T13:00:00Z",
                    "periods": [{"name": "Today", "detailedForecast": "Sunny."}],
                }
            }
        ).encode()
        return {
            "https://feeds.bbci.co.uk/news/rss.xml": rss(
                [("World headline", "https://www.bbc.com/news/world-1", recent)]
            ),
            "https://www.nasa.gov/feed/": rss(
                [("NASA headline", "https://www.nasa.gov/nasa-1", recent)]
            ),
            "https://www.espn.com/espn/rss/news": RuntimeError(
                "private detail token=secret"
            ),
            "https://api.weather.gov/points/32.0809,-81.0912": points,
            "https://api.weather.gov/gridpoints/CHS/57,52/forecast": forecast,
        }

    def test_degraded_output_has_notices_public_errors_and_exact_schema_shape(
        self,
    ) -> None:
        edition = ge.build_edition(self.config(), NOW, MemoryFetcher(self.mapping()))
        self.assertEqual(edition["schemaVersion"], 1)
        self.assertEqual(edition["quality"]["mode"], "partial")
        self.assertEqual(edition["quality"]["coverage"]["desksWithCurrentStories"], 2)
        self.assertEqual(
            edition["quality"]["errors"], [{"source": "ESPN", "code": "fetch-failed"}]
        )
        text = " ".join(
            slide["headline"] + " " + slide["summary"] for slide in edition["slides"]
        )
        self.assertIn("not AI research", edition["slides"][0]["summary"])
        self.assertIn("Markets coverage unavailable", text)
        self.assertIn("Betting coverage unavailable", text)
        self.assertNotIn("secret", json.dumps(edition))
        self.assertLessEqual(len(edition["slides"]), 50)
        self.assertEqual(
            len({slide["id"] for slide in edition["slides"]}), len(edition["slides"])
        )
        story = next(
            slide
            for slide in edition["slides"]
            if slide["headline"] == "World headline"
        )
        self.assertEqual(story["source"]["asOf"], "2026-11-03T13:00:00Z")
        self.assertNotIn("description", story)

    def test_duplicate_story_urls_across_feeds_are_emitted_once(self) -> None:
        config = self.config()
        mapping = self.mapping()
        recent = "Tue, 03 Nov 2026 13:00:00 GMT"
        duplicate_url = "https://public.example/shared-story"
        mapping["https://feeds.bbci.co.uk/news/rss.xml"] = rss(
            [("Shared", duplicate_url, recent)]
        )
        mapping["https://www.nasa.gov/feed/"] = rss(
            [("Shared again", duplicate_url, recent)]
        )
        mapping["https://www.espn.com/espn/rss/news"] = rss(
            [("Sports current", "https://www.espn.com/story/current", recent)]
        )
        edition = ge.build_edition(config, NOW, MemoryFetcher(mapping))
        matching = [
            slide
            for slide in edition["slides"]
            if slide.get("source", {}).get("url") == duplicate_url
        ]
        self.assertEqual(len(matching), 1)

    def test_catastrophic_failure_preserves_previous_output(self) -> None:
        config = self.config()
        mapping = self.mapping()
        mapping["https://www.nasa.gov/feed/"] = RuntimeError("down")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "edition.json"
            output.write_text('{"previous":true}', encoding="utf-8")
            with self.assertRaises(ge.GenerationFailure):
                ge.generate_to_path(config, output, NOW, MemoryFetcher(mapping))
            self.assertEqual(output.read_text(encoding="utf-8"), '{"previous":true}')

    def test_atomic_write_replaces_valid_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "edition.json"
            edition = ge.generate_to_path(
                self.config(), output, NOW, MemoryFetcher(self.mapping())
            )
            on_disk = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["edition"]["id"], edition["edition"]["id"])
            leftovers = list(output.parent.glob(f".{output.name}.*.tmp"))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
