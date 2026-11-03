#!/usr/bin/env python3
"""Build a bounded, source-attributed public morning-news edition.

The module uses only the Python standard library. It consumes RSS/Atom titles and
National Weather Service forecasts; it never copies article bodies or invents
recommendations.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from validate_publication import validate_edition

MAX_RESPONSE_BYTES = 1_000_000
NETWORK_TIMEOUT_SECONDS = 12.0
MAX_WORKERS = 4
MAX_STORIES_PER_FEED = 2
MAX_SLIDES = 50
FRESHNESS = timedelta(hours=48)
WEATHER_FRESHNESS = timedelta(hours=24)
FUTURE_TOLERANCE = timedelta(minutes=15)
EASTERN = ZoneInfo("America/New_York")
USER_AGENT = "MorningNewsletterCloud/1.0 (public-news-digest; contact: local-operator)"
DESKS = (
    "news",
    "sports",
    "science-tech",
    "architecture-housing",
    "markets",
    "betting",
    "lifestyle",
    "weather",
)
FETCH_HOST_ALLOWLIST = frozenset(
    {
        "feeds.bbci.co.uk",
        "www.espn.com",
        "www.nasa.gov",
        "www.dezeen.com",
        "www.budgetbytes.com",
        "api.weather.gov",
    }
)

JsonObject = dict[str, Any]
Fetcher = Callable[[str], bytes]


class FeedError(ValueError):
    """Raised when a feed is unsafe or malformed."""


class FetchError(RuntimeError):
    """Raised when a bounded network fetch fails."""


class WeatherError(ValueError):
    """Raised when a weather response is invalid or stale."""


class GenerationFailure(RuntimeError):
    """Raised when minimum publication requirements are not met."""


@dataclass(frozen=True, slots=True)
class Story:
    """A current feed title with publication provenance."""

    headline: str
    url: str
    published: str
    label: str
    desk: str
    section: str
    slide_id: str


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject every redirect so an allowlisted URL cannot change hosts."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        raise FetchError("redirect-blocked")


def utc_iso(value: datetime) -> str:
    """Return an aware datetime in canonical UTC form."""
    if value.tzinfo is None:
        raise ValueError("timezone-required")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO timestamp and require an explicit timezone."""
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timezone-required")
    return parsed.astimezone(UTC)


def validate_public_url(url: str, allowed_hosts: set[str] | frozenset[str]) -> str:
    """Validate a fetch URL against an exact HTTPS host allowlist."""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or host not in allowed_hosts:
        raise ValueError("url-not-allowlisted")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url-credentials-forbidden")
    if parsed.port not in (None, 443):
        raise ValueError("url-port-forbidden")
    if parsed.query or parsed.fragment:
        raise ValueError("url-query-forbidden")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("private-address-forbidden")
    return url


def validate_story_url(url: str) -> str:
    """Validate an unfetched article link for safe public display."""
    parsed = urllib.parse.urlsplit(url.strip())
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host:
        raise ValueError("story-url-invalid")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("story-url-invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("story-url-invalid")
    return urllib.parse.urlunsplit(parsed)


def _assert_public_dns(host: str) -> None:
    """Reject DNS answers that point at local, private, or reserved networks."""
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FetchError("dns-failed") from exc
    if not answers:
        raise FetchError("dns-empty")
    for answer in answers:
        raw_address = answer[4][0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise FetchError("dns-invalid") from exc
        if not address.is_global:
            raise FetchError("dns-private")


def read_bounded_response(response: Any) -> bytes:
    """Read at most one megabyte from an HTTP-like response."""
    raw_length = response.headers.get("Content-Length")
    if raw_length is not None:
        try:
            if int(raw_length) > MAX_RESPONSE_BYTES:
                raise FetchError("response-too-large")
        except ValueError as exc:
            raise FetchError("content-length-invalid") from exc
    payload = response.read(MAX_RESPONSE_BYTES + 1)
    if not isinstance(payload, bytes):
        raise FetchError("response-not-bytes")
    if len(payload) > MAX_RESPONSE_BYTES:
        raise FetchError("response-too-large")
    return payload


def fetch_https(url: str) -> bytes:
    """Fetch one allowlisted HTTPS resource without redirects or retries."""
    validated = validate_public_url(url, FETCH_HOST_ALLOWLIST)
    host = urllib.parse.urlsplit(validated).hostname
    if host is None:
        raise FetchError("host-missing")
    _assert_public_dns(host)
    request = urllib.request.Request(
        validated,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/json",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            if final_url != validated:
                raise FetchError("unexpected-final-url")
            return read_bounded_response(response)
    except FetchError:
        raise
    except (
        OSError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
    ) as exc:
        raise FetchError("network-failed") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ET.Element, names: set[str]) -> str | None:
    for child in list(element):
        if _local_name(child.tag) in names and child.text:
            value = " ".join(child.text.split())
            if value:
                return value
    return None


def _entry_link(element: ET.Element) -> str | None:
    for child in list(element):
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return None


def _parse_publication(value: str) -> datetime:
    try:
        return parse_iso_datetime(value)
    except (ValueError, TypeError):
        pass
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise FeedError("publication-date-invalid") from exc
    if parsed is None or parsed.tzinfo is None:
        raise FeedError("publication-date-invalid")
    return parsed.astimezone(UTC)


def _story_id(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"story-{digest}"


def parse_feed_xml(
    payload: bytes,
    label: str,
    desk: str,
    section: str,
    now: datetime,
) -> list[Story]:
    """Extract at most two fresh RSS/Atom titles without article content."""
    try:
        upper_prefix = payload.decode("utf-8-sig").upper()
    except UnicodeDecodeError as exc:
        raise FeedError("xml-encoding-invalid") from exc
    if "<!DOCTYPE" in upper_prefix or "<!ENTITY" in upper_prefix:
        raise FeedError("xml-declaration-forbidden")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FeedError("xml-malformed") from exc
    current = now.astimezone(UTC)
    candidates = [
        node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}
    ]
    stories: list[Story] = []
    seen_urls: set[str] = set()
    for entry in candidates:
        title = _child_text(entry, {"title"})
        link = _entry_link(entry)
        date_text = _child_text(entry, {"pubdate", "published", "updated", "date"})
        if not title or not link or not date_text:
            continue
        try:
            published = _parse_publication(date_text)
            safe_url = validate_story_url(link)
        except (FeedError, ValueError):
            continue
        age = current - published
        if age > FRESHNESS or published - current > FUTURE_TOLERANCE:
            continue
        if safe_url in seen_urls:
            continue
        seen_urls.add(safe_url)
        stories.append(
            Story(
                headline=title[:250],
                url=safe_url,
                published=utc_iso(published),
                label=label[:120],
                desk=desk,
                section=section[:120],
                slide_id=_story_id(safe_url),
            )
        )
        if len(stories) >= MAX_STORIES_PER_FEED:
            break
    return stories


def parse_weather_forecast(payload: bytes, location: str, now: datetime) -> JsonObject:
    """Parse a fresh NWS forecast into the compact weather schema."""
    try:
        document = json.loads(payload.decode("utf-8"))
        properties = document["properties"]
        updated_value = (
            properties.get("updateTime")
            or properties.get("updated")
            or properties.get("generatedAt")
        )
        if not isinstance(updated_value, str):
            raise TypeError
        updated = parse_iso_datetime(updated_value)
        periods = properties["periods"]
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise WeatherError("forecast-invalid") from exc
    current = now.astimezone(UTC)
    if current - updated > WEATHER_FRESHNESS:
        raise WeatherError("forecast-stale")
    if updated - current > FUTURE_TOLERANCE:
        raise WeatherError("forecast-future")
    pieces: list[str] = []
    if not isinstance(periods, list):
        raise WeatherError("forecast-periods-invalid")
    for period in periods[:3]:
        if not isinstance(period, dict):
            continue
        name = " ".join(str(period.get("name", "")).split())
        detail = " ".join(str(period.get("detailedForecast", "")).split())
        if name and detail:
            pieces.append(f"{name}: {detail}")
    if not pieces:
        raise WeatherError("forecast-empty")
    return {
        "location": location[:200],
        "summary": " ".join(pieces)[:1000],
        "asOf": utc_iso(updated),
    }


def _parse_points(payload: bytes) -> str:
    try:
        document = json.loads(payload.decode("utf-8"))
        forecast_url = document["properties"]["forecast"]
        if not isinstance(forecast_url, str):
            raise TypeError
        return validate_public_url(forecast_url, {"api.weather.gov"})
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise WeatherError("points-invalid") from exc


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate config shape, desks, and exact public fetch endpoints."""
    if config.get("schemaVersion") != 1:
        raise ValueError("config-schema-invalid")
    feeds = config.get("feeds")
    weather = config.get("weather")
    if not isinstance(feeds, list) or not isinstance(weather, list):
        raise ValueError("config-lists-required")
    for source in feeds:
        if not isinstance(source, dict):
            raise ValueError("feed-invalid")
        required = ("label", "url", "host", "desk", "section")
        if any(
            not isinstance(source.get(key), str) or not source[key] for key in required
        ):
            raise ValueError("feed-fields-invalid")
        host = source["host"].lower().rstrip(".")
        if host not in FETCH_HOST_ALLOWLIST:
            raise ValueError("feed-host-invalid")
        validate_public_url(source["url"], {host})
        if source["desk"] not in DESKS or source["desk"] == "weather":
            raise ValueError("feed-desk-invalid")
    for place in weather:
        if not isinstance(place, dict):
            raise ValueError("weather-invalid")
        if not isinstance(place.get("location"), str) or not place["location"]:
            raise ValueError("weather-location-invalid")
        if place.get("host") != "api.weather.gov":
            raise ValueError("weather-host-invalid")
        validate_public_url(place.get("pointsUrl", ""), {"api.weather.gov"})


def _safe_error(source: str, code: str) -> JsonObject:
    return {"source": source[:120], "code": code}


def _feed_job(
    source: Mapping[str, str], now: datetime, fetcher: Fetcher
) -> tuple[list[Story], JsonObject | None]:
    try:
        payload = fetcher(source["url"])
    except Exception:
        return [], _safe_error(source["label"], "fetch-failed")
    try:
        stories = parse_feed_xml(
            payload,
            source["label"],
            source["desk"],
            source["section"],
            now,
        )
    except FeedError:
        return [], _safe_error(source["label"], "feed-invalid")
    if not stories:
        return [], _safe_error(source["label"], "no-current-stories")
    return stories, None


def _weather_job(
    place: Mapping[str, str], now: datetime, fetcher: Fetcher
) -> tuple[JsonObject | None, JsonObject | None]:
    label = place["location"]
    try:
        points_payload = fetcher(place["pointsUrl"])
        forecast_url = _parse_points(points_payload)
        forecast_payload = fetcher(forecast_url)
    except Exception:
        return None, _safe_error(label, "weather-fetch-failed")
    try:
        return parse_weather_forecast(forecast_payload, label, now), None
    except WeatherError as exc:
        code = (
            str(exc)
            if str(exc) in {"forecast-stale", "forecast-future"}
            else "weather-invalid"
        )
        return None, _safe_error(label, code)


def _notice_slide(desk: str, section: str, headline: str, summary: str) -> JsonObject:
    key = f"{desk}|{section}|{headline}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"notice-{digest}",
        "type": "story",
        "desk": desk,
        "section": section,
        "headline": headline[:250],
        "summary": summary[:12000],
    }


def _story_slide(story: Story, captured: str) -> JsonObject:
    return {
        "id": story.slide_id,
        "type": "story",
        "desk": story.desk,
        "section": story.section,
        "headline": story.headline,
        "summary": (
            f"Published {story.published}; captured {captured}. "
            f"RSS/Atom title from {story.label}; no article body or long summary was copied."
        )[:12000],
        "source": {"label": story.label, "url": story.url, "asOf": story.published},
    }


def _coverage_notices(unavailable_desks: Sequence[str]) -> list[JsonObject]:
    slides: list[JsonObject] = []
    for desk in unavailable_desks:
        if desk in {"markets", "betting"}:
            title = desk.title()
            for horizon in ("Short horizon", "Long horizon"):
                slides.append(
                    _notice_slide(
                        desk,
                        f"{title} coverage — {horizon}",
                        f"{title} coverage unavailable — {horizon.lower()}",
                        "No current verifiable free public feed was configured or returned usable data. "
                        "No pick, trade, wager, forecast, or recommendation is provided.",
                    )
                )
        elif desk != "weather":
            slides.append(
                _notice_slide(
                    desk,
                    "Coverage notice",
                    f"{desk.replace('-', ' ').title()} coverage unavailable",
                    "No current story title was available from the configured free public sources at capture time.",
                )
            )
    return slides


def build_edition(
    config: Mapping[str, Any], now: datetime, fetcher: Fetcher = fetch_https
) -> JsonObject:
    """Collect sources and return a validated edition object in memory."""
    validate_config(config)
    current = now.astimezone(UTC)
    captured = utc_iso(current)
    feeds: list[Mapping[str, str]] = config["feeds"]
    places: list[Mapping[str, str]] = config["weather"]
    stories: list[Story] = []
    weather: list[JsonObject] = []
    errors: list[JsonObject] = []
    successful_sources = 0

    jobs: list[tuple[str, Mapping[str, str]]] = [
        *(("feed", source) for source in feeds),
        *(("weather", place) for place in places),
    ]
    results: list[tuple[int, str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map: dict[concurrent.futures.Future[Any], tuple[int, str]] = {}
        for index, (kind, item) in enumerate(jobs):
            function = _feed_job if kind == "feed" else _weather_job
            future_map[executor.submit(function, item, current, fetcher)] = (
                index,
                kind,
            )
        for future, (index, kind) in future_map.items():
            try:
                result = future.result()
            except Exception:
                result = (
                    ([], _safe_error("unknown", "worker-failed"))
                    if kind == "feed"
                    else (None, _safe_error("unknown", "worker-failed"))
                )
            results.append((index, kind, result))

    for _index, kind, result in sorted(results, key=lambda entry: entry[0]):
        value, error = result
        if error is not None:
            errors.append(error)
        else:
            successful_sources += 1
        if kind == "feed":
            stories.extend(value)
        elif value is not None:
            weather.append(value)

    unique_stories: list[Story] = []
    seen_story_urls: set[str] = set()
    for story in stories:
        if story.url in seen_story_urls:
            continue
        seen_story_urls.add(story.url)
        unique_stories.append(story)
    stories = unique_stories
    story_desks = sorted({story.desk for story in stories})
    if len(story_desks) < 2 or not weather:
        raise GenerationFailure("minimum-coverage-not-met")

    unavailable_desks = [
        desk
        for desk in DESKS
        if desk not in story_desks and not (desk == "weather" and weather)
    ]
    total_configured = len(feeds) + len(places)
    partial = bool(errors or unavailable_desks or successful_sources < total_configured)
    mode = "partial" if partial else "live"
    edition_date = current.astimezone(EASTERN).date().isoformat()
    intro = _notice_slide(
        "news",
        "Edition status",
        f"Current capture: {captured}",
        (
            f"This is a {mode} public-source digest captured at {captured}, not AI research. "
            f"Current story desks: {len(story_desks)}; unavailable desks: {len(unavailable_desks)}; "
            f"valid weather locations: {len(weather)}. RSS/Atom titles only; publication dates appear in source provenance."
        ),
    )
    story_slides = [_story_slide(story, captured) for story in stories]
    weather_slides = [
        _notice_slide(
            "weather",
            "Local forecast",
            place["location"],
            f"NWS forecast updated {place['asOf']}. {place['summary']}",
        )
        for place in weather
    ]
    weather_cards = [
        dict(place, summary=" ".join(place["summary"].split()[:14]) + "…")
        for place in weather
    ]
    slides = [
        intro,
        *story_slides,
        *weather_slides,
        *_coverage_notices(unavailable_desks),
    ][:MAX_SLIDES]
    canonical = json.dumps(
        slides, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return {
        "schemaVersion": 1,
        "edition": {
            "id": f"{edition_date}-{digest}",
            "date": edition_date,
            "title": "The Morning Edition",
            "subtitle": "Savannah & Hinsdale",
            "timezone": "America/New_York",
            "isExample": False,
            "generatedAt": captured,
        },
        "playback": {"secondsPerSlide": 12, "refreshSeconds": 300},
        "weather": weather_cards,
        "slides": slides,
        "quality": {
            "mode": mode,
            "captureTime": captured,
            "coverage": {
                "desksTotal": len(DESKS),
                "desksWithCurrentStories": len(story_desks),
                "currentStoryDesks": story_desks,
                "unavailableDesks": unavailable_desks,
                "configuredSources": total_configured,
                "successfulSources": successful_sources,
                "failedSources": len(errors),
                "weatherLocationsConfigured": len(places),
                "weatherLocationsValid": len(weather),
                "stories": len(stories),
                "slides": len(slides),
            },
            "errors": errors,
        },
    }


def atomic_write_json(output: Path, document: Mapping[str, Any]) -> None:
    """Atomically replace an output file with canonical UTF-8 JSON."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.{secrets.token_hex(8)}.tmp"
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def generate_to_path(
    config: Mapping[str, Any],
    output: Path,
    now: datetime,
    fetcher: Fetcher = fetch_https,
) -> JsonObject:
    """Build an edition and atomically persist it only after all gates pass."""
    edition = build_edition(config, now, fetcher)
    validate_edition(edition, now=now)
    if len(json.dumps(edition, ensure_ascii=False, indent=2)) > 299999:
        raise GenerationFailure("edition-too-large")
    atomic_write_json(output, edition)
    return edition


def scheduled_gate(
    now: datetime, existing_date: str | None, force: bool
) -> tuple[bool, str]:
    """Apply the 07:00–10:59 America/New_York schedule and duplicate gate."""
    if force:
        return True, "forced"
    local = now.astimezone(EASTERN)
    if not 7 <= local.hour < 11:
        return False, "outside-window"
    if existing_date == local.date().isoformat():
        return False, "already-generated"
    return True, "run"


def _existing_success_date(output: Path, now: datetime) -> str | None:
    try:
        document = json.loads(output.read_text(encoding="utf-8"))
        validate_edition(document, now=now)
        captured = parse_iso_datetime(document["edition"]["generatedAt"]).astimezone(
            EASTERN
        )
        if captured.hour < 7:
            return None
        if document.get("schemaVersion") != 1:
            return None
        if document.get("quality", {}).get("mode") not in {"live", "partial"}:
            return None
        value = document["edition"]["date"]
        return value if isinstance(value, str) else None
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
    ):
        return None


def _load_config(path: Path) -> JsonObject:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("config-unreadable") from exc
    if not isinstance(document, dict):
        raise ValueError("config-object-required")
    return document


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return parse_iso_datetime(value)


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--now", help="Aware ISO timestamp; intended for deterministic tests"
    )
    parser.add_argument(
        "--scheduled", action="store_true", help="Enforce the Eastern 07:00–10:59 gate"
    )
    parser.add_argument(
        "--force", action="store_true", help="Bypass schedule and same-date gates"
    )
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("sources.json")
    )
    args = parser.parse_args(argv)
    try:
        now = _parse_now(args.now)
        if args.scheduled:
            should_run, reason = scheduled_gate(
                now, _existing_success_date(args.output, now), args.force
            )
            if not should_run:
                print(
                    json.dumps(
                        {"status": "skipped", "reason": reason}, separators=(",", ":")
                    )
                )
                return 0
        config = _load_config(args.config)
        edition = generate_to_path(config, args.output, now)
    except (ValueError, GenerationFailure) as exc:
        code = str(exc) if str(exc) and " " not in str(exc) else "generation-failed"
        print(
            json.dumps({"status": "failure", "code": code}, separators=(",", ":")),
            file=sys.stderr,
        )
        return 1
    except Exception:
        print('{"status":"failure","code":"unexpected-failure"}', file=sys.stderr)
        return 1
    coverage = edition["quality"]["coverage"]
    print(
        json.dumps(
            {
                "status": edition["quality"]["mode"],
                "stories": coverage["stories"],
                "slides": coverage["slides"],
                "weather": coverage["weatherLocationsValid"],
                "errors": coverage["failedSources"],
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
