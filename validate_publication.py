"""Fail-closed validation of the public Pages artifact; standard library only."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

SITE_FILES = {"index.html", "app.js", "styles.css", "content.json"}


def text(value: Any, maximum: int, *, empty: bool = False) -> None:
    """Require bounded plain text, not a raw object or unbounded provider response."""
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value):
        raise ValueError("invalid-text-field")


def _timestamp(value: Any) -> datetime:
    text(value, 40)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ValueError("invalid-timestamp")
    return datetime.fromisoformat(value).astimezone(UTC)


def _nested_and_fresh(value: Any, now: datetime) -> None:
    """Validate all nested public fields and recheck freshness when publishing."""

    def exact(item: Any, fields: set[str]) -> None:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("unexpected-nested-fields")

    captured = _timestamp(value["edition"]["generatedAt"])

    def fresh(raw: Any, hours: int) -> None:
        parsed = _timestamp(raw)
        if now - parsed > timedelta(hours=hours) or parsed - min(
            now, captured
        ) > timedelta(minutes=15):
            raise ValueError("stale-or-future-timestamp")

    fresh(value["edition"]["generatedAt"], 24)
    if (
        captured.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        != value["edition"]["date"]
    ):
        raise ValueError("issue-capture-date-mismatch")
    playback = value["playback"]
    exact(playback, {"secondsPerSlide", "refreshSeconds"})
    for key, lower, upper in (("secondsPerSlide", 8, 60), ("refreshSeconds", 60, 3600)):
        if type(playback[key]) is not int or not lower <= playback[key] <= upper:
            raise ValueError("invalid-playback")
    for weather in value["weather"]:
        fresh(weather["asOf"], 24)
    source_slides = [slide for slide in value["slides"] if "source" in slide]
    for slide in source_slides:
        fresh(slide["source"]["asOf"], 48)
    quality = value["quality"]
    if _timestamp(quality["captureTime"]) != captured:
        raise ValueError("capture-time-mismatch")
    coverage = quality["coverage"]
    exact(
        coverage,
        {
            "desksTotal",
            "desksWithCurrentStories",
            "currentStoryDesks",
            "unavailableDesks",
            "configuredSources",
            "successfulSources",
            "failedSources",
            "weatherLocationsConfigured",
            "weatherLocationsValid",
            "stories",
            "slides",
        },
    )
    desks = {
        "news",
        "sports",
        "science-tech",
        "architecture-housing",
        "markets",
        "betting",
        "lifestyle",
        "weather",
    }
    for key in set(coverage) - {"currentStoryDesks", "unavailableDesks"}:
        if type(coverage[key]) is not int or not 0 <= coverage[key] <= 100:
            raise ValueError("invalid-coverage-count")
    for key in ("currentStoryDesks", "unavailableDesks"):
        entries = coverage[key]
        if (
            not isinstance(entries, list)
            or any(not isinstance(d, str) or d not in desks for d in entries)
            or len(set(entries)) != len(entries)
        ):
            raise ValueError("invalid-coverage-desks")
    current_desks = {slide["desk"] for slide in source_slides}
    missing = desks - current_desks - {"weather"}
    if (
        set(coverage["currentStoryDesks"]) != current_desks
        or set(coverage["unavailableDesks"]) != missing
    ):
        raise ValueError("coverage-desks-mismatch")
    if (
        coverage["desksTotal"] != 8
        or coverage["desksWithCurrentStories"] != len(current_desks)
        or coverage["stories"] != len(source_slides)
    ):
        raise ValueError("coverage-count-mismatch")
    if (
        coverage["weatherLocationsValid"] != len(value["weather"])
        or not len(value["weather"]) <= coverage["weatherLocationsConfigured"] <= 2
    ):
        raise ValueError("weather-count-mismatch")
    errors = quality["errors"]
    codes = {
        "fetch-failed",
        "feed-invalid",
        "no-current-stories",
        "weather-fetch-failed",
        "forecast-stale",
        "forecast-future",
        "weather-invalid",
        "worker-failed",
    }
    if not isinstance(errors, list) or len(errors) > 20:
        raise ValueError("invalid-errors")
    for error in errors:
        exact(error, {"source", "code"})
        text(error["source"], 120)
        if error["code"] not in codes:
            raise ValueError("invalid-error-code")
    if (
        coverage["failedSources"] != len(errors)
        or coverage["successfulSources"] + len(errors) != coverage["configuredSources"]
    ):
        raise ValueError("source-count-mismatch")
    if quality["mode"] != ("partial" if missing or errors else "live"):
        raise ValueError("mode-mismatch")


def validate_edition(value: Any, *, now: datetime | None = None) -> str:
    """Validate exactly the public schema the generator emits, returning its ID."""
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "edition",
        "playback",
        "weather",
        "slides",
        "quality",
    }:
        raise ValueError("unexpected-public-fields")
    if value["schemaVersion"] != 1:
        raise ValueError("unsupported-schema")
    edition = value["edition"]
    if not isinstance(edition, dict) or set(edition) != {
        "id",
        "date",
        "title",
        "subtitle",
        "timezone",
        "isExample",
        "generatedAt",
    }:
        raise ValueError("invalid-edition-fields")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", edition["date"]):
        raise ValueError("invalid-date")
    date.fromisoformat(edition["date"])
    text(edition["id"], 100)
    if not re.fullmatch(r"[0-9a-f-]+", edition["id"]):
        raise ValueError("unsafe-edition-id")
    for field in ("title", "subtitle", "timezone", "generatedAt"):
        text(edition[field], 1000)
    if edition["isExample"] is not False or edition["timezone"] != "America/New_York":
        raise ValueError("non-live-edition")
    stamp = datetime.fromisoformat(edition["generatedAt"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("capture-timezone-required")
    weather = value["weather"]
    if not isinstance(weather, list) or not 1 <= len(weather) <= 2:
        raise ValueError("weather-required")
    for item in weather:
        if not isinstance(item, dict) or set(item) != {"location", "summary", "asOf"}:
            raise ValueError("invalid-weather-fields")
        for val in item.values():
            text(val, 1000)
    slides = value["slides"]
    if not isinstance(slides, list) or not 1 <= len(slides) <= 50:
        raise ValueError("invalid-slide-count")
    ids: set[str] = set()
    for slide in slides:
        if (
            not isinstance(slide, dict)
            or not {"id", "type", "desk", "section", "headline", "summary"}
            <= set(slide)
            or set(slide)
            - {"id", "type", "desk", "section", "headline", "summary", "source"}
        ):
            raise ValueError("invalid-slide-fields")
        if slide["type"] != "story":
            raise ValueError("invalid-slide-type")
        for field in ("id", "desk", "section"):
            text(slide[field], 1000)
        text(slide["headline"], 250)
        text(slide["summary"], 12000, empty=True)
        if slide["id"] in ids:
            raise ValueError("duplicate-slide-id")
        ids.add(slide["id"])
        if "source" in slide:
            source = slide["source"]
            if not isinstance(source, dict) or set(source) != {"label", "url", "asOf"}:
                raise ValueError("invalid-source-fields")
            for val in source.values():
                text(val, 1000)
            parsed = urlsplit(source["url"])
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("unsafe-source-url")
    quality = value["quality"]
    if (
        not isinstance(quality, dict)
        or set(quality) != {"mode", "captureTime", "coverage", "errors"}
        or quality["mode"] not in {"live", "partial"}
    ):
        raise ValueError("invalid-quality")
    if (
        quality["coverage"]["stories"] < 2
        or quality["coverage"]["desksWithCurrentStories"] < 2
    ):
        raise ValueError("insufficient-coverage")
    if quality["coverage"]["slides"] != len(slides):
        raise ValueError("slide-count-mismatch")
    _nested_and_fresh(value, now or datetime.now(UTC))
    return str(edition["id"])


def validate_site(root: Path, *, now: datetime | None = None) -> str:
    """Reject unrelated files, symlinks, private payload keys, and oversized JSON."""
    found = {path.name for path in root.iterdir()}
    if found != SITE_FILES or any(
        path.is_symlink() or not path.is_file() for path in root.iterdir()
    ):
        raise ValueError("site-file-allowlist-violation")
    content = (root / "content.json").read_text(encoding="utf-8")
    if len(content) > 300000:
        raise ValueError("edition-too-large")
    forbidden = re.compile(
        r'"(?:access_token|refresh_token|api_key|password|inbox|recipient|private)"\s*:',
        re.IGNORECASE,
    )
    if forbidden.search(content):
        raise ValueError("private-field-in-public-content")
    return validate_edition(json.loads(content), now=now)


if __name__ == "__main__":
    print(validate_site(Path(__file__).parent / "site"))
