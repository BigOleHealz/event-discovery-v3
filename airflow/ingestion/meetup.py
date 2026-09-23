"""Meetup GraphQL discovery and staged-payload parsing."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import cast

import httpx

from ingestion.models import MeetupListingPage, MeetupSearchTarget, ParsedListing

LOGGER = logging.getLogger(__name__)
EVENT_SEARCH_QUERY = """
query EventSearch($filter: EventSearchFilter!, $first: Int!, $after: String) {
  eventSearch(filter: $filter, first: $first, after: $after) {
    pageInfo { endCursor hasNextPage }
    edges {
      node {
        id title description eventUrl dateTime endTime eventType
        venue { id name address city state country postalCode lat lon }
        topics { edges { node { name } } }
      }
    }
  }
}
"""


class MeetupConfigurationError(ValueError):
    """Raised when Meetup runtime or target configuration is invalid."""


class MeetupParseError(ValueError):
    """Raised when Meetup returns a payload that cannot be safely staged or parsed."""


@dataclass(frozen=True)
class MeetupGeoRadius:
    latitude: float
    longitude: float
    radius_miles: float


@dataclass(frozen=True)
class MeetupConfig:
    """Process configuration for the official Meetup GraphQL API."""

    access_token: str
    api_url: str
    request_timeout_seconds: float
    page_size: int

    @classmethod
    def from_env(cls) -> MeetupConfig:
        access_token = os.environ.get("MEETUP_ACCESS_TOKEN", "").strip()
        if not access_token:
            raise MeetupConfigurationError("MEETUP_ACCESS_TOKEN is required")
        api_url = os.environ.get("MEETUP_API_URL", "").strip()
        if not api_url:
            raise MeetupConfigurationError("MEETUP_API_URL is required")
        return cls(
            access_token=access_token,
            api_url=api_url,
            request_timeout_seconds=_positive_float_from_env(
                "MEETUP_REQUEST_TIMEOUT_SECONDS", 30.0
            ),
            page_size=_positive_int_from_env("MEETUP_PAGE_SIZE", 20),
        )


def meetup_geo_radius(source_location: Mapping[str, object]) -> MeetupGeoRadius:
    """Validate Meetup's coordinate-and-mile-radius source-native location."""
    if source_location.get("kind") != "meetup_geo_radius":
        raise MeetupConfigurationError(
            "Meetup source_location.kind must be 'meetup_geo_radius'"
        )
    latitude = _number(source_location.get("lat"), "source_location.lat")
    longitude = _number(source_location.get("lon"), "source_location.lon")
    radius_miles = _number(
        source_location.get("radius_miles"), "source_location.radius_miles"
    )
    if not -90 <= latitude <= 90:
        raise MeetupConfigurationError("Meetup source_location.lat must be within [-90, 90]")
    if not -180 <= longitude <= 180:
        raise MeetupConfigurationError("Meetup source_location.lon must be within [-180, 180]")
    if radius_miles <= 0:
        raise MeetupConfigurationError("Meetup source_location.radius_miles must be positive")
    return MeetupGeoRadius(latitude, longitude, radius_miles)


class MeetupClient:
    """Cursor-paginate the official Meetup eventSearch GraphQL query."""

    def __init__(
        self,
        config: MeetupConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {config.access_token}"},
            timeout=config.request_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MeetupClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def iter_event_pages(self, target: MeetupSearchTarget) -> Iterator[MeetupListingPage]:
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for page_number in range(1, target.page_cap + 1):
            variables: dict[str, object] = {
                "filter": {
                    "lat": target.latitude,
                    "lon": target.longitude,
                    "radius": target.radius_miles,
                    "query": "",
                    "topicCategoryId": target.topic_category_id,
                    "startDateRange": target.window_start.isoformat(),
                    "endDateRange": target.window_end.isoformat(),
                    "doConsolidateEvents": False,
                },
                "first": self._config.page_size,
                "after": cursor,
            }
            started = monotonic()
            response = self._client.post(
                self._config.api_url,
                json={"query": EVENT_SEARCH_QUERY, "variables": variables},
            )
            duration_ms = round((monotonic() - started) * 1000)
            response.raise_for_status()
            events, next_cursor, has_next_page = _parse_search_response(response.content)
            yield MeetupListingPage(
                crawl_target_id=target.crawl_target_id,
                url=str(response.request.url),
                search_target=target.label,
                page_number=page_number,
                http_status=response.status_code,
                duration_ms=duration_ms,
                byte_count=len(response.content),
                events=events,
            )
            if not has_next_page:
                break
            if next_cursor is None or next_cursor in seen_cursors:
                raise MeetupParseError("Meetup pagination repeated or omitted its next cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            LOGGER.warning(
                "Meetup page cap %s reached for %s; split the date window",
                target.page_cap,
                target.label,
            )


def meetup_staging_identity(payload: Mapping[str, object]) -> tuple[str, str]:
    return _required_string(payload, "id"), _required_string(payload, "eventUrl")


def parse_meetup_event(
    payload: Mapping[str, object], *, market_timezone: str
) -> ParsedListing:
    """Parse one raw Meetup Event using the canonical market's IANA timezone."""
    event_type = _required_string(payload, "eventType")
    if event_type not in {"ONLINE", "PHYSICAL", "HYBRID"}:
        raise MeetupParseError(f"unsupported Meetup eventType {event_type!r}")
    venue = _optional_mapping(payload, "venue")
    topics = _topic_names(payload)
    starts_at = _parse_datetime(_required_string(payload, "dateTime"), "dateTime")
    end_time = payload.get("endTime")
    if end_time is not None and not isinstance(end_time, str):
        raise MeetupParseError("endTime must be a string or null")

    return ParsedListing(
        source_event_id=_required_string(payload, "id"),
        url=_required_string(payload, "eventUrl"),
        title=_required_string(payload, "title"),
        description=_optional_string(payload, "description"),
        starts_at=starts_at,
        ends_at=_parse_datetime(end_time, "endTime") if end_time is not None else None,
        timezone=market_timezone,
        online_event=event_type == "ONLINE",
        venue_name=_optional_string(venue, "name"),
        venue_address=_meetup_address(venue),
        venue_city=_optional_string(venue, "city"),
        venue_region=_optional_string(venue, "state"),
        venue_country=_optional_string(venue, "country"),
        latitude=_optional_float(venue, "lat"),
        longitude=_optional_float(venue, "lon"),
        primary_category=topics[0] if topics else None,
    )


def _parse_search_response(
    content: bytes,
) -> tuple[tuple[dict[str, object], ...], str | None, bool]:
    try:
        body = json.loads(content)
    except json.JSONDecodeError as error:
        raise MeetupParseError("Meetup returned invalid JSON") from error
    if not isinstance(body, dict):
        raise MeetupParseError("Meetup response must be an object")
    if body.get("errors"):
        raise MeetupParseError("Meetup GraphQL response contained errors")
    data = _required_mapping(cast(dict[str, object], body), "data")
    search = _required_mapping(data, "eventSearch")
    page_info = _required_mapping(search, "pageInfo")
    has_next_page = page_info.get("hasNextPage")
    if not isinstance(has_next_page, bool):
        raise MeetupParseError("Meetup pageInfo.hasNextPage must be a boolean")
    end_cursor = page_info.get("endCursor")
    if end_cursor is not None and not isinstance(end_cursor, str):
        raise MeetupParseError("Meetup pageInfo.endCursor must be a string or null")
    edges = search.get("edges")
    if not isinstance(edges, list):
        raise MeetupParseError("Meetup eventSearch.edges must be a list")
    events: list[dict[str, object]] = []
    for edge in edges:
        if not isinstance(edge, dict):
            raise MeetupParseError("Meetup eventSearch edge must be an object")
        node = edge.get("node")
        if not isinstance(node, dict):
            raise MeetupParseError("Meetup eventSearch edge.node must be an object")
        events.append(cast(dict[str, object], node))
    return tuple(events), end_cursor, has_next_page


def _topic_names(payload: Mapping[str, object]) -> tuple[str, ...]:
    topics = _optional_mapping(payload, "topics")
    if topics is None:
        return ()
    edges = topics.get("edges")
    if not isinstance(edges, list):
        raise MeetupParseError("topics.edges must be a list")
    names: list[str] = []
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("node"), dict):
            raise MeetupParseError("topics edge.node must be an object")
        names.append(_required_string(cast(dict[str, object], edge["node"]), "name"))
    return tuple(names)


def _meetup_address(venue: Mapping[str, object] | None) -> str | None:
    if venue is None:
        return None
    pieces = [
        _optional_string(venue, "address"),
        _optional_string(venue, "city"),
        _optional_string(venue, "state"),
        _optional_string(venue, "postalCode"),
        _optional_string(venue, "country"),
    ]
    values = [piece.strip() for piece in pieces if piece is not None and piece.strip()]
    return ", ".join(values) if values else None


def _required_mapping(
    payload: Mapping[str, object], key: str
) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise MeetupParseError(f"{key} must be an object")
    return value


def _optional_mapping(
    payload: Mapping[str, object], key: str
) -> Mapping[str, object] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MeetupParseError(f"{key} must be an object or null")
    return value


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MeetupParseError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_string(payload: Mapping[str, object] | None, key: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MeetupParseError(f"{key} must be a string or null")
    return value.strip() or None


def _optional_float(payload: Mapping[str, object] | None, key: str) -> float | None:
    if payload is None:
        return None
    value = payload.get(key)
    if value is None:
        return None
    return _number(value, key)


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MeetupConfigurationError(f"Meetup {field} must be numeric")
    return float(value)


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MeetupParseError(f"{field} must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None:
        raise MeetupParseError(f"{field} must include an offset")
    return parsed


def _positive_float_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError as error:
        raise MeetupConfigurationError(f"{name} must be numeric") from error
    if value <= 0:
        raise MeetupConfigurationError(f"{name} must be positive")
    return value


def _positive_int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as error:
        raise MeetupConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise MeetupConfigurationError(f"{name} must be positive")
    return value
