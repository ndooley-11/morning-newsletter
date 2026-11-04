"""Publication and adversarial parser regressions; synthetic inputs only."""

from __future__ import annotations

import copy
import json
import tempfile
import textwrap
import unittest
from datetime import UTC, datetime
from pathlib import Path

import generate_edition as ge
import test_generate_edition as fixtures
from test_generate_edition import NOW, MemoryFetcher, rss
from validate_publication import validate_edition, validate_site


class PublicationTests(unittest.TestCase):
    def edition(self):
        fixture = fixtures.GenerationTests()
        return ge.build_edition(fixture.config(), NOW, MemoryFetcher(fixture.mapping()))

    def test_real_generated_shape_passes(self):
        value = self.edition()
        self.assertEqual(validate_edition(value, now=NOW), value["edition"]["id"])

    def test_corrupt_or_private_payload_rejected(self):
        base = self.edition()
        for modify in (
            lambda p: p.update(private={"subject": "PRIVATE_CANARY"}),
            lambda p: p["edition"].update(id="bad\noutput=injection"),
            lambda p: p["slides"].append(copy.deepcopy(p["slides"][0])),
            lambda p: p["slides"][1]["source"].update(url="javascript:alert(1)"),
            lambda p: p["edition"].update(isExample=True),
        ):
            value = copy.deepcopy(base)
            modify(value)
            with self.assertRaises((ValueError, TypeError)):
                validate_edition(value, now=NOW)

    def test_site_allowlist_blocks_unrelated_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("index.html", "app.js", "styles.css"):
                (root / name).write_text("test", encoding="utf-8")
            (root / "content.json").write_text(
                json.dumps(self.edition()), encoding="utf-8"
            )
            validate_site(root, now=NOW)
            (root / "private.json").write_text("PRIVATE_CANARY", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_site(root, now=NOW)

    def test_padding_and_utf16_entities_rejected(self):
        for value in (
            b" " * 5000 + b"<!DOCTYPE rss [<!ENTITY x 'payload'>]><rss>&x;</rss>",
            "<!DOCTYPE rss [<!ENTITY x 'payload'>]><rss>&x;</rss>".encode("utf-16"),
        ):
            with self.assertRaises(ge.FeedError):
                ge.parse_feed_xml(value, "Synthetic", "news", "test", NOW)

    def test_old_future_and_undated_entries_before_current_are_skipped(self):
        data = rss(
            [
                ("Old", "https://example.org/old", "Sat, 31 Oct 2026 10:00:00 GMT"),
                (
                    "Future",
                    "https://example.org/future",
                    "Tue, 03 Nov 2026 14:16:00 GMT",
                ),
                ("Undated", "https://example.org/undated", ""),
                (
                    "Current",
                    "https://example.org/current",
                    "Tue, 03 Nov 2026 13:00:00 GMT",
                ),
            ]
        )
        self.assertEqual(
            [
                s.headline
                for s in ge.parse_feed_xml(data, "Synthetic", "news", "test", NOW)
            ],
            ["Current"],
        )

    def test_html_doctype_inside_rss_cdata_is_not_a_real_xml_dtd(self):
        data = rss(
            [
                (
                    "Current NASA headline",
                    "https://www.nasa.gov/current",
                    "Tue, 03 Nov 2026 13:00:00 GMT",
                )
            ]
        )
        data = data.replace(
            b"</item>",
            b"<description><![CDATA[<!DOCTYPE html><html>ARTICLE_BODY_NOT_COPIED</html>]]></description></item>",
        )
        stories = ge.parse_feed_xml(data, "NASA", "science-tech", "Science", NOW)
        self.assertEqual(len(stories), 1)
        self.assertNotIn("ARTICLE_BODY_NOT_COPIED", stories[0].headline)
        with self.assertRaises(ge.FeedError):
            ge.parse_feed_xml(
                b'<!DOCTYPE rss SYSTEM "https://private.invalid/evil.dtd"><rss/>',
                "NASA",
                "science-tech",
                "Science",
                NOW,
            )

    def test_xml_encoding_cannot_be_redetected_after_utf8_decode(self):
        data = rss(
            [
                (
                    "ASCII",
                    "https://www.nasa.gov/encoding-test",
                    "Tue, 03 Nov 2026 13:00:00 GMT",
                )
            ]
        ).decode()
        bad = [
            data.encode("utf-16le"),
            data.encode("utf-16be"),
            data.replace(
                "<?xml version='1.0'?>", "<?xml version='1.0' encoding='ISO-8859-1'?>"
            ).encode(),
        ]
        for payload in bad:
            with self.subTest(payload=payload[:40]), self.assertRaises(ge.FeedError):
                ge.parse_feed_xml(payload, "NASA", "science-tech", "Science", NOW)
        good = data.replace("ASCII", "Café").encode("utf-8")
        self.assertEqual(
            ge.parse_feed_xml(good, "NASA", "science-tech", "Science", NOW)[0].headline,
            "Café",
        )

    def test_nested_unknown_fields_and_wrong_types_rejected(self):
        mutations = [
            lambda p: p["playback"].update(secret="PRIVATE_CANARY"),
            lambda p: p["playback"].update(secondsPerSlide=True),
            lambda p: p["quality"]["coverage"].update(email="PRIVATE_CANARY"),
            lambda p: p["quality"]["errors"][0].update(secret="PRIVATE_CANARY"),
            lambda p: p["quality"]["coverage"].update(stories=999),
        ]
        for mutate in mutations:
            value = self.edition()
            mutate(value)
            with self.assertRaises((ValueError, TypeError)):
                validate_edition(value, now=NOW)

    def test_stale_source_weather_and_capture_mismatch_rejected(self):
        mutations = [
            lambda p: p["weather"][0].update(asOf="2026-11-02T13:00:00Z"),
            lambda p: p["slides"][1]["source"].update(asOf="2026-11-01T13:00:00Z"),
            lambda p: p["quality"].update(captureTime="2026-11-03T13:00:00Z"),
            lambda p: p["edition"].update(generatedAt="2026-11-02T13:00:00Z"),
        ]
        for mutate in mutations:
            value = self.edition()
            mutate(value)
            with self.assertRaises(ValueError):
                validate_edition(value, now=NOW)

    def test_midnight_manual_edition_does_not_suppress_morning_refresh(self):
        fixture = fixtures.GenerationTests()
        midnight_mapping = fixture.mapping()
        for key, value in midnight_mapping.items():
            if isinstance(value, bytes):
                midnight_mapping[key] = value.replace(b"13:00:00", b"04:30:00")
        midnight = datetime(2026, 11, 3, 5, 30, tzinfo=UTC)
        midnight_edition = ge.build_edition(
            fixture.config(), midnight, MemoryFetcher(midnight_mapping)
        )

        morning_mapping = fixture.mapping()
        for key, value in morning_mapping.items():
            if isinstance(value, bytes):
                morning_mapping[key] = value.replace(b"13:00:00", b"10:00:00")
        morning = datetime(2026, 11, 3, 11, 0, tzinfo=UTC)
        morning_edition = ge.build_edition(
            fixture.config(), morning, MemoryFetcher(morning_mapping)
        )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "content.json"
            output.write_text(json.dumps(midnight_edition), encoding="utf-8")
            self.assertIsNone(ge._existing_success_date(output, morning))
            output.write_text(json.dumps(morning_edition), encoding="utf-8")
            self.assertEqual(ge._existing_success_date(output, morning), "2026-11-03")

    def test_daily_workflow_uses_dst_covering_cron_and_compilable_python_heredocs(self):
        workflow = Path(__file__).parent / ".github" / "workflows" / "daily.yml"
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("cron: '17 10,11 * * *'", text)
        self.assertNotIn("cron: '17 11,12 * * *'", text)

        lines = text.splitlines()
        heredocs = []
        index = 0
        while index < len(lines):
            if lines[index].strip() not in {"python - <<'PY'", "python3 - <<'PY'"}:
                index += 1
                continue
            index += 1
            body = []
            while index < len(lines) and lines[index].strip() != "PY":
                body.append(lines[index])
                index += 1
            self.assertLess(index, len(lines), "unterminated Python heredoc")
            heredocs.append(textwrap.dedent("\n".join(body)) + "\n")
            index += 1

        self.assertEqual(len(heredocs), 3)
        for number, source in enumerate(heredocs, start=1):
            compile(source, f"daily.yml heredoc {number}", "exec")


if __name__ == "__main__":
    unittest.main()
