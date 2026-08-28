"""Eventbrite public discovery, detail fetching, and detail-payload parsing."""

from __future__ import annotations

import html
import json
import logging
import os
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from time import monotonic, sleep
from typing import Protocol, cast
from urllib.parse import urlsplit, urlunsplit

import httpx

from ingestion.models import (
    EventbriteEventReference,
    EventbriteListingPage,
    EventbriteSearchTarget,
    ParsedEventbriteListing,
)

LOGGER = logging.getLogger(__name__)
EVENT_URL_PATTERN = re.compile(
    r'(?P<url>(?:https://www\.eventbrite\.com)?/e/[^"\'<>\s]+-'
    r'(?P<event_id>\d+)(?:\?[^"\'<>\s]*)?)'
)
SERVER_DATA_MARKER = "window.__SERVER_DATA__ = "
RATE_LIMIT_PATTERN = re.compile(
    r"(?:token|key):\S+\s+(?P<used>\d+)/(?P<limit>\d+)\s+reset=(?P<reset>\d+)s"
)


class EventbriteConfigurationError(ValueError):
    """Raised when required Eventbrite configuration is absent or invalid."""


class EventbriteParseError(ValueError):
    """Raised when an Eventbrite event cannot be parsed."""


class EventbriteRateLimited(RuntimeError):
    """Raised when detail fetching must stop without failing the ingestion run."""

    def __init__(self, retry_after_seconds: float | None) -> None:
        self.retry_after_seconds = retry_after_seconds
        message = "Eventbrite detail API returned 429"
        if retry_after_seconds is not None:
            message += f"; retry after {retry_after_seconds:g} seconds"
        super().__init__(message)


@dataclass(frozen=True)
class EventbriteConfig:
    """Runtime configuration for the two-stage public-discovery crawl."""

    api_token: str
    api_base_url: str
    web_base_url: str
    request_timeout_seconds: float
    detail_cache_ttl_hours: float

    @classmethod
    def from_env(cls) -> EventbriteConfig:
        """Build configuration exclusively from environment variables."""
        api_token = os.environ.get("EVENTBRITE_API_TOKEN", "").strip()
        if not api_token:
            raise EventbriteConfigurationError("EVENTBRITE_API_TOKEN is required")
        api_base_url = os.environ.get("EVENTBRITE_API_BASE_URL", "").strip().rstrip("/")
        if not api_base_url:
            raise EventbriteConfigurationError("EVENTBRITE_API_BASE_URL is required")
        web_base_url = (
            os.environ.get("EVENTBRITE_WEB_BASE_URL", "https://www.eventbrite.com")
            .strip()
            .rstrip("/")
        )
        if not web_base_url:
            raise EventbriteConfigurationError("EVENTBRITE_WEB_BASE_URL is required")

        return cls(
            api_token=api_token,
            api_base_url=api_base_url,
            web_base_url=web_base_url,
            request_timeout_seconds=_positive_float_from_env(
                "EVENTBRITE_REQUEST_TIMEOUT_SECONDS", 30.0
            ),
            detail_cache_ttl_hours=_positive_float_from_env(
                "EVENTBRITE_DETAIL_CACHE_TTL_HOURS", 24.0
            ),
        )

    def listing_url(self, target: EventbriteSearchTarget, page_number: int) -> str:
        """Return one configured public discovery URL."""
        return (
            f"{self.web_base_url}/d/{target.location_slug}/{target.category}--events/"
            f"?page={page_number}&start_date={target.window_start.isoformat()}"
            f"&end_date={target.window_end.isoformat()}"
        )

    def detail_url(self, event_id: str) -> str:
        """Return the official per-event detail API URL."""
        return f"{self.api_base_url}/events/{event_id}/"


def eventbrite_location_slug(source_location: Mapping[str, object]) -> str:
    """Validate and return Eventbrite's source-native public-search slug."""
    if source_location.get("kind") != "eventbrite_slug":
        raise EventbriteConfigurationError(
            "Eventbrite source_location.kind must be 'eventbrite_slug'"
        )
    slug = source_location.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        raise EventbriteConfigurationError(
            "Eventbrite source_location.slug must be a non-empty string"
        )
    return slug.strip()


class EventbriteListingClient:
    """Fetch and parse public listing pages with defensive pagination termination."""

    def __init__(
        self,
        config: EventbriteConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._client = httpx.Client(
            headers={"User-Agent": "event-discovery-v3/1.0"},
            timeout=config.request_timeout_seconds,
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> EventbriteListingClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def iter_listing_pages(self, target: EventbriteSearchTarget) -> Iterator[EventbriteListingPage]:
        """Yield pages until empty, repeated, or capped, recording the terminal fetch."""
        previous_ids: frozenset[str] | None = None
        for page_number in range(1, target.page_cap + 1):
            url = self._config.listing_url(target, page_number)
            started = monotonic()
            response = self._client.get(url)
            duration_ms = round((monotonic() - started) * 1000)
            response.raise_for_status()
            references = parse_listing_event_references(
                response.content, web_base_url=self._config.web_base_url
            )
            yield EventbriteListingPage(
                crawl_target_id=target.crawl_target_id,
                url=str(response.request.url),
                search_target=target.label,
                page_number=page_number,
                http_status=response.status_code,
                duration_ms=duration_ms,
                byte_count=len(response.content),
                event_references=references,
            )
            page_ids = frozenset(reference.event_id for reference in references)
            if not page_ids or page_ids == previous_ids:
                break
            previous_ids = page_ids
        else:
            LOGGER.warning(
                "Eventbrite page cap %s reached for %s; split the date window",
                target.page_cap,
                target.label,
            )


class EventDetailFetcher(Protocol):
    """Interchangeable source for full Eventbrite detail payloads."""

    def fetch_event_detail(self, event_id: str) -> dict[str, object]:
        """Fetch one full event detail payload."""


@dataclass(frozen=True)
class RateLimitQuota:
    """Quota state derived from Eventbrite's X-Rate-Limit response header."""

    remaining: int
    reset_seconds: int

    @property
    def pace_seconds(self) -> float:
        return self.reset_seconds / max(self.remaining, 1)


class ApiEventDetailFetcher:
    """Official Eventbrite per-event API implementation with quota-derived pacing."""

    def __init__(
        self,
        config: EventbriteConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self._config = config
        self._sleeper = sleeper
        self._quota: RateLimitQuota | None = None
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {config.api_token}"},
            timeout=config.request_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ApiEventDetailFetcher:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def fetch_event_detail(self, event_id: str) -> dict[str, object]:
        if self._quota is not None and self._quota.pace_seconds > 0:
            self._sleeper(self._quota.pace_seconds)
        response = self._client.get(
            self._config.detail_url(event_id),
            params={"expand": "venue,category,organizer"},
        )
        if response.status_code == 429:
            raise EventbriteRateLimited(_retry_after_seconds(response))
        response.raise_for_status()
        quota = parse_rate_limit_header(response.headers.get("x-rate-limit"))
        if quota is not None:
            self._quota = quota
        elif self._quota is None:
            LOGGER.warning("Eventbrite detail response omitted X-Rate-Limit")
        return _json_object(response.content)


class JsonLdEventDetailFetcher:
    """Reserved fallback for parsing schema.org/Event from the public event page."""

    def fetch_event_detail(self, event_id: str) -> dict[str, object]:
        raise NotImplementedError(f"JSON-LD Eventbrite detail fallback for {event_id}")


def parse_listing_event_references(
    content: bytes, *, web_base_url: str
) -> tuple[EventbriteEventReference, ...]:
    """Extract normalized ids only from Eventbrite's actual search-results payload."""
    document = content.decode("utf-8", errors="replace")
    marker_index = document.find(SERVER_DATA_MARKER)
    if marker_index < 0:
        raise EventbriteParseError("Eventbrite listing page is missing __SERVER_DATA__")
    encoded = document[marker_index + len(SERVER_DATA_MARKER) :]
    try:
        server_data, _ = json.JSONDecoder().raw_decode(encoded)
    except json.JSONDecodeError as error:
        raise EventbriteParseError("Eventbrite listing __SERVER_DATA__ is invalid") from error
    if not isinstance(server_data, dict):
        raise EventbriteParseError("Eventbrite listing __SERVER_DATA__ must be an object")
    search_data = _required_mapping(cast(dict[str, object], server_data), "search_data")
    events = _required_mapping(search_data, "events")
    results = events.get("results")
    if not isinstance(results, list):
        raise EventbriteParseError("Eventbrite listing results must be a list")

    references: dict[str, EventbriteEventReference] = {}
    for result in results:
        if not isinstance(result, dict):
            raise EventbriteParseError("Eventbrite listing result must be an object")
        typed_result = cast(dict[str, object], result)
        result_id = _required_string(typed_result, "id")
        raw_url = _required_string(typed_result, "url")
        match = EVENT_URL_PATTERN.fullmatch(html.unescape(raw_url))
        if match is None:
            raise EventbriteParseError("Eventbrite listing result URL has no event id")
        event_id = match.group("event_id")
        if event_id != result_id:
            raise EventbriteParseError(
                f"Eventbrite listing result id {result_id} does not match URL id {event_id}"
            )
        matched_url = match.group("url")
        absolute_url = (
            matched_url if matched_url.startswith("http") else f"{web_base_url}{matched_url}"
        )
        parsed = urlsplit(absolute_url)
        canonical_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        references.setdefault(
            event_id,
            EventbriteEventReference(event_id=event_id, canonical_url=canonical_url),
        )
    return tuple(references.values())


def parse_rate_limit_header(value: str | None) -> RateLimitQuota | None:
    """Parse Eventbrite's `used/limit reset=Ns` quota representation."""
    if value is None:
        return None
    match = RATE_LIMIT_PATTERN.search(value)
    if match is None:
        LOGGER.warning("Unrecognized Eventbrite X-Rate-Limit header")
        return None
    used = int(match.group("used"))
    limit = int(match.group("limit"))
    return RateLimitQuota(
        remaining=max(limit - used, 0),
        reset_seconds=int(match.group("reset")),
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None


def parse_eventbrite_event(payload: Mapping[str, object]) -> ParsedEventbriteListing:
    """Parse one staged Eventbrite event without performing network or database I/O."""
    source_event_id = _required_string(payload, "id")
    url = _required_string(payload, "url")
    name = _required_mapping(payload, "name")
    title = _required_string(name, "text")
    start = _required_mapping(payload, "start")
    end = _optional_mapping(payload, "end")
    venue = _optional_mapping(payload, "venue")
    venue_address = _optional_mapping(venue, "address") if venue is not None else None
    description = _optional_mapping(payload, "description")
    category = _optional_mapping(payload, "category")

    online_event = payload.get("online_event")
    if not isinstance(online_event, bool):
        raise EventbriteParseError("online_event must be a boolean")

    return ParsedEventbriteListing(
        source_event_id=source_event_id,
        url=url,
        title=title,
        description=_optional_string(description, "text"),
        starts_at=_parse_datetime(_required_string(start, "utc"), "start.utc"),
        ends_at=(
            _parse_datetime(_required_string(end, "utc"), "end.utc") if end is not None else None
        ),
        timezone=_required_string(start, "timezone"),
        online_event=online_event,
        venue_name=_optional_string(venue, "name"),
        venue_address=_optional_string(venue_address, "localized_address_display"),
        venue_city=_optional_string(venue_address, "city"),
        venue_region=_optional_string(venue_address, "region"),
        venue_country=_optional_string(venue_address, "country"),
        latitude=_optional_float(venue_address, "latitude"),
        longitude=_optional_float(venue_address, "longitude"),
        primary_category=_optional_string(category, "name"),
    )


def staging_identity(payload: Mapping[str, object]) -> tuple[str, str]:
    """Extract only the identity needed to save raw JSON before semantic parsing."""
    return _required_string(payload, "id"), _required_string(payload, "url")


def _json_object(content: bytes) -> dict[str, object]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise EventbriteParseError("Eventbrite returned invalid JSON") from error
    if not isinstance(value, dict):
        raise EventbriteParseError("Eventbrite response must be a JSON object")
    return cast(dict[str, object], value)


def _required_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise EventbriteParseError(f"{key} must be an object")
    return cast(dict[str, object], value)


def _optional_mapping(
    payload: Mapping[str, object] | None, key: str
) -> Mapping[str, object] | None:
    if payload is None:
        return None
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise EventbriteParseError(f"{key} must be an object or null")
    return cast(dict[str, object], value)


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EventbriteParseError(f"{key} must be a non-empty string")
    return value


def _optional_string(payload: Mapping[str, object] | None, key: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EventbriteParseError(f"{key} must be a string or null")
    return value or None


def _optional_float(payload: Mapping[str, object] | None, key: str) -> float | None:
    value = _optional_string(payload, key)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError as error:
        raise EventbriteParseError(f"{key} must be numeric") from error


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EventbriteParseError(f"{field} must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None:
        raise EventbriteParseError(f"{field} must include a timezone")
    return parsed


def _positive_float_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip() or str(default)
    try:
        value = float(raw)
    except ValueError as error:
        raise EventbriteConfigurationError(f"{name} must be numeric") from error
    if value <= 0:
        raise EventbriteConfigurationError(f"{name} must be positive")
    return value
