from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast
from unittest.mock import patch

import httpx
import pytest

from ingestion.eventbrite import (
    ApiEventDetailFetcher,
    EventbriteConfig,
    EventbriteConfigurationError,
    EventbriteListingClient,
    EventbriteParseError,
    JsonLdEventDetailFetcher,
    eventbrite_location_slug,
    parse_eventbrite_event,
    parse_listing_event_references,
    parse_rate_limit_header,
)
from ingestion.models import EventbriteSearchTarget

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "eventbrite"
DETAIL_FIXTURE_PATH = FIXTURE_DIRECTORY / "event_detail_1999042913166.json"
ORGANIZATION_FIXTURE_PATH = FIXTURE_DIRECTORY / "organization_events_page_1.json"
LISTING_FIXTURE_PATH = FIXTURE_DIRECTORY / "public_listing_page_excerpt.html"


def load_detail() -> dict[str, object]:
    value = json.loads(DETAIL_FIXTURE_PATH.read_text())
    if not isinstance(value, dict):
        raise TypeError("recorded Eventbrite detail must be an object")
    return cast(dict[str, object], value)


def config() -> EventbriteConfig:
    return EventbriteConfig(
        api_token="recorded-test-token",
        api_base_url="https://eventbrite.test/v3",
        web_base_url="https://www.eventbrite.com",
        request_timeout_seconds=5,
        detail_cache_ttl_hours=24,
    )


def target(category: str = "science-and-tech") -> EventbriteSearchTarget:
    return EventbriteSearchTarget(
        crawl_target_id=uuid.UUID("4c33ed98-a96b-4d72-946f-5bd923db9506"),
        location_slug="pa--philadelphia",
        category=category,
        window_start=date(2026, 8, 27),
        window_end=date(2026, 8, 31),
        page_cap=10,
    )


def test_eventbrite_adapter_validates_its_source_native_location() -> None:
    assert (
        eventbrite_location_slug({"kind": "eventbrite_slug", "slug": "pa--philadelphia"})
        == "pa--philadelphia"
    )
    with pytest.raises(EventbriteConfigurationError, match="kind"):
        eventbrite_location_slug(
            {"kind": "coordinates_radius", "latitude": 39.95, "longitude": -75.16}
        )


def test_config_uses_numeric_defaults_for_blank_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVENTBRITE_API_TOKEN", "recorded-test-token")
    monkeypatch.setenv("EVENTBRITE_API_BASE_URL", "https://eventbrite.test/v3")
    monkeypatch.setenv("EVENTBRITE_REQUEST_TIMEOUT_SECONDS", "")
    monkeypatch.setenv("EVENTBRITE_DETAIL_CACHE_TTL_HOURS", "")

    parsed = EventbriteConfig.from_env()

    assert parsed.request_timeout_seconds == 30
    assert parsed.detail_cache_ttl_hours == 24


def listing_document(urls: list[str]) -> bytes:
    server_data = {
        "search_data": {
            "events": {
                "results": [
                    {"id": url.split("?", 1)[0].rsplit("-", 1)[-1], "url": url} for url in urls
                ],
                "promoted_results": [],
                "pagination": {},
            }
        }
    }
    return f"<script>window.__SERVER_DATA__ = {json.dumps(server_data)};</script>".encode()


def test_listing_parser_extracts_real_urls_once_and_strips_attribution() -> None:
    references = parse_listing_event_references(
        LISTING_FIXTURE_PATH.read_bytes(), web_base_url="https://www.eventbrite.com"
    )

    assert [reference.event_id for reference in references] == [
        "1979191438872",
        "1991788406742",
        "1984139883804",
        "1992314891472",
    ]
    assert references[0].canonical_url == (
        "https://www.eventbrite.com/e/visit-the-wagner-museum-2026-tickets-1979191438872"
    )
    assert all("?aff=" not in reference.canonical_url for reference in references)


def test_pagination_stops_after_an_empty_page() -> None:
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        requested_pages.append(page)
        content = LISTING_FIXTURE_PATH.read_bytes() if page == "1" else listing_document([])
        return httpx.Response(200, content=content, request=request)

    with EventbriteListingClient(config(), transport=httpx.MockTransport(handler)) as client:
        pages = tuple(client.iter_listing_pages(target()))

    assert requested_pages == ["1", "2"]
    assert len(pages[0].event_references) == 4
    assert pages[1].event_references == ()


def test_pagination_stops_when_the_previous_page_id_set_repeats() -> None:
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_pages.append(request.url.params["page"])
        return httpx.Response(200, content=LISTING_FIXTURE_PATH.read_bytes(), request=request)

    with EventbriteListingClient(config(), transport=httpx.MockTransport(handler)) as client:
        pages = tuple(client.iter_listing_pages(target()))

    assert requested_pages == ["1", "2"]
    assert {reference.event_id for reference in pages[0].event_references} == {
        reference.event_id for reference in pages[1].event_references
    }


def test_page_cap_logs_a_warning_and_halts() -> None:
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_pages.append(request.url.params["page"])
        event_id = 1000 + int(request.url.params["page"])
        content = listing_document([f"https://www.eventbrite.com/e/test-tickets-{event_id}"])
        return httpx.Response(200, content=content, request=request)

    with patch("ingestion.eventbrite.LOGGER.warning") as warning:
        with EventbriteListingClient(config(), transport=httpx.MockTransport(handler)) as client:
            pages = tuple(client.iter_listing_pages(replace(target(), page_cap=2)))

    assert len(pages) == 2
    assert requested_pages == ["1", "2"]
    warning.assert_called_once_with(
        "Eventbrite page cap %s reached for %s; split the date window",
        2,
        "science-and-tech:2026-08-27..2026-08-31",
    )


def test_api_detail_fetcher_uses_detail_endpoint_and_header_derived_pacing() -> None:
    detail = load_detail()
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=detail,
            headers={"X-Rate-Limit": "token:test 2/2000 reset=100s"},
            request=request,
        )

    with ApiEventDetailFetcher(
        config(), transport=httpx.MockTransport(handler), sleeper=sleeps.append
    ) as fetcher:
        first = fetcher.fetch_event_detail("1999042913166")
        fetcher.fetch_event_detail("1999042913166")

    assert first["id"] == "1999042913166"
    assert requests[0].url.path == "/v3/events/1999042913166/"
    assert requests[0].url.params["expand"] == "venue,category,organizer"
    assert requests[0].headers["Authorization"] == "Bearer recorded-test-token"
    assert sleeps == [pytest.approx(100 / 1998)]


def test_rate_limit_header_reports_remaining_quota() -> None:
    quota = parse_rate_limit_header(
        "token:redacted 2/2000 reset=2625s, key:redacted 2/2000 reset=2625s"
    )

    assert quota is not None
    assert quota.remaining == 1998
    assert quota.reset_seconds == 2625


def test_json_ld_detail_fetcher_is_an_explicit_stub() -> None:
    with pytest.raises(NotImplementedError, match="JSON-LD Eventbrite detail fallback"):
        JsonLdEventDetailFetcher().fetch_event_detail("1999042913166")


def test_parser_extracts_real_eventbrite_detail_payload() -> None:
    parsed = parse_eventbrite_event(load_detail())

    assert parsed.source_event_id == "1999042913166"
    assert parsed.title == "Go Hard"
    assert parsed.description == "yo yo yo"
    assert parsed.starts_at == datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
    assert parsed.ends_at == datetime(2026, 10, 6, 16, 0, tzinfo=UTC)
    assert parsed.timezone == "America/New_York"
    assert parsed.online_event is False
    assert parsed.venue_name == "Philadelphia City Hall"
    assert parsed.venue_address == ("1400 John F Kennedy Boulevard, Philadelphia, PA 19107")
    assert parsed.venue_city == "Philadelphia"
    assert parsed.venue_region == "PA"
    assert parsed.venue_country == "US"
    assert parsed.latitude == 39.9528
    assert parsed.longitude == -75.1634833


def test_organization_embedded_event_matches_the_detail_fixture_shape() -> None:
    organization_page = json.loads(ORGANIZATION_FIXTURE_PATH.read_text())
    if not isinstance(organization_page, dict):
        raise TypeError("recorded organization response must be an object")
    events = organization_page.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[0], dict):
        raise TypeError("recorded organization response must contain an event")

    assert events[0] == load_detail()


def test_parser_rejects_missing_semantic_fields() -> None:
    event = load_detail()
    event.pop("name")

    with pytest.raises(EventbriteParseError, match="name must be an object"):
        parse_eventbrite_event(event)
