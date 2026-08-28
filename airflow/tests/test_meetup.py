from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import cast

import httpx
import pytest

from ingestion.eventbrite import EventbriteConfigurationError, eventbrite_location_slug
from ingestion.meetup import (
    MeetupClient,
    MeetupConfig,
    MeetupConfigurationError,
    meetup_geo_radius,
    parse_meetup_event,
)
from ingestion.models import MeetupSearchTarget

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "meetup"
    / "event_search_page.json"
)


def response_fixture() -> dict[str, object]:
    value = json.loads(FIXTURE_PATH.read_text())
    if not isinstance(value, dict):
        raise TypeError("recorded Meetup response must be an object")
    return cast(dict[str, object], value)


def recorded_event() -> dict[str, object]:
    body = response_fixture()
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("eventSearch"), dict):
        raise TypeError("recorded Meetup response must contain eventSearch")
    search = data["eventSearch"]
    edges = search.get("edges")
    if not isinstance(edges, list) or not edges or not isinstance(edges[0], dict):
        raise TypeError("recorded Meetup response must contain an event edge")
    node = edges[0].get("node")
    if not isinstance(node, dict):
        raise TypeError("recorded Meetup edge must contain an event node")
    return cast(dict[str, object], node)


def config() -> MeetupConfig:
    return MeetupConfig(
        access_token="recorded-meetup-token",
        api_url="https://api.meetup.test/gql-ext",
        request_timeout_seconds=5,
        page_size=20,
    )


def target() -> MeetupSearchTarget:
    return MeetupSearchTarget(
        crawl_target_id=uuid.UUID("b8d299ce-bf03-4bc8-aa35-16f25fd95d34"),
        latitude=39.9526,
        longitude=-75.1652,
        radius_miles=25,
        topic_category_id="546",
        window_start=datetime.fromisoformat("2026-10-06T00:00:00-04:00"),
        window_end=datetime.fromisoformat("2026-10-10T23:59:59-04:00"),
        page_cap=20,
    )


def test_adapters_validate_only_their_own_source_location_shapes() -> None:
    meetup_location = {
        "kind": "meetup_geo_radius",
        "lat": 39.9526,
        "lon": -75.1652,
        "radius_miles": 25,
    }
    parsed = meetup_geo_radius(meetup_location)

    assert parsed.latitude == 39.9526
    assert parsed.longitude == -75.1652
    assert parsed.radius_miles == 25
    with pytest.raises(MeetupConfigurationError, match="kind"):
        meetup_geo_radius({"kind": "eventbrite_slug", "slug": "pa--philadelphia"})
    with pytest.raises(EventbriteConfigurationError, match="kind"):
        eventbrite_location_slug(meetup_location)


def test_client_uses_geo_radius_topic_window_and_cursor_pagination() -> None:
    requests: list[httpx.Request] = []
    bodies: list[dict[str, object]] = []
    first = response_fixture()
    data = first["data"]
    assert isinstance(data, dict)
    search = data["eventSearch"]
    assert isinstance(search, dict)
    page_info = search["pageInfo"]
    assert isinstance(page_info, dict)
    page_info["hasNextPage"] = True
    second = deepcopy(first)
    second_data = second["data"]
    assert isinstance(second_data, dict)
    second_search = second_data["eventSearch"]
    assert isinstance(second_search, dict)
    second_search["edges"] = []
    second_page_info = second_search["pageInfo"]
    assert isinstance(second_page_info, dict)
    second_page_info["endCursor"] = None
    second_page_info["hasNextPage"] = False

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert isinstance(body, dict)
        bodies.append(cast(dict[str, object], body))
        return httpx.Response(
            200,
            json=first if len(requests) == 1 else second,
            request=request,
        )

    with MeetupClient(config(), transport=httpx.MockTransport(handler)) as client:
        pages = tuple(client.iter_event_pages(target()))

    assert len(pages) == 2
    assert len(pages[0].events) == 1
    assert pages[1].events == ()
    assert requests[0].headers["Authorization"] == "Bearer recorded-meetup-token"
    variables = bodies[0]["variables"]
    assert isinstance(variables, dict)
    assert variables["filter"] == {
        "lat": 39.9526,
        "lon": -75.1652,
        "radius": 25,
        "query": "",
        "topicCategoryId": "546",
        "startDateRange": "2026-10-06T00:00:00-04:00",
        "endDateRange": "2026-10-10T23:59:59-04:00",
        "doConsolidateEvents": False,
    }
    assert bodies[1]["variables"] != bodies[0]["variables"]
    second_variables = bodies[1]["variables"]
    assert isinstance(second_variables, dict)
    assert second_variables["after"] == "meetup-cursor-1"


def test_parser_maps_official_meetup_event_shape() -> None:
    parsed = parse_meetup_event(recorded_event(), market_timezone="America/New_York")

    assert parsed.source_event_id == "305912103"
    assert parsed.title == "Go Hard"
    assert parsed.starts_at == datetime.fromisoformat("2026-10-06T10:00:00-04:00")
    assert parsed.ends_at == datetime.fromisoformat("2026-10-06T12:00:00-04:00")
    assert parsed.timezone == "America/New_York"
    assert parsed.online_event is False
    assert parsed.venue_name == "Philadelphia City Hall"
    assert parsed.venue_address == (
        "1400 John F Kennedy Boulevard, Philadelphia, PA, 19107, US"
    )
    assert parsed.latitude == 39.9528
    assert parsed.longitude == -75.1634833
    assert parsed.primary_category == "Technology"
