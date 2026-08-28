"""Typed values passed between Eventbrite ingestion steps."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class ParsedEventbriteListing:
    """The Eventbrite fields needed by later geocode and upsert phases."""

    source_event_id: str
    url: str
    title: str
    description: str | None
    starts_at: datetime
    ends_at: datetime | None
    timezone: str
    online_event: bool
    venue_name: str | None
    venue_address: str | None
    venue_city: str | None
    venue_region: str | None
    venue_country: str | None
    latitude: float | None
    longitude: float | None
    primary_category: str | None


@dataclass(frozen=True)
class FetchedPage:
    """One recorded page returned by the Eventbrite API."""

    url: str
    http_status: int
    duration_ms: int
    byte_count: int
    events: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class CrawlTarget:
    """One source query attached to a canonical market."""

    id: uuid.UUID
    source: str
    market_id: uuid.UUID
    market_slug: str
    market_name: str
    source_location: Mapping[str, object]
    category: str
    window_days: int
    page_cap: int


@dataclass(frozen=True)
class CrawlMarketTargets:
    """Enabled targets that share one independently tracked canonical market run."""

    source: str
    market_id: uuid.UUID
    market_slug: str
    market_name: str
    targets: tuple[CrawlTarget, ...]

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(target.category for target in self.targets)

    @property
    def maximum_window_days(self) -> int:
        return max(target.window_days for target in self.targets)


@dataclass(frozen=True)
class EventbriteSearchTarget:
    """One public Eventbrite listing query within a canonical market crawl."""

    crawl_target_id: uuid.UUID
    location_slug: str
    category: str
    window_start: date
    window_end: date
    page_cap: int

    @property
    def label(self) -> str:
        return f"{self.category}:{self.window_start.isoformat()}..{self.window_end.isoformat()}"


@dataclass(frozen=True)
class EventbriteEventReference:
    """Exact identity discovered on a public Eventbrite listing page."""

    event_id: str
    canonical_url: str


@dataclass(frozen=True)
class EventbriteListingPage:
    """One fetched public listing page and its unique event references."""

    crawl_target_id: uuid.UUID
    url: str
    search_target: str
    page_number: int
    http_status: int
    duration_ms: int
    byte_count: int
    event_references: tuple[EventbriteEventReference, ...]


@dataclass(frozen=True)
class EventbriteCrawlSummary:
    """Public crawl funnel before detail fetching."""

    pages_fetched: int
    listing_appearances: int
    event_references: tuple[EventbriteEventReference, ...]

    @property
    def unique_ids(self) -> int:
        return len(self.event_references)


@dataclass(frozen=True)
class EventbriteDetailSummary:
    """Detail-fetch funnel and any rate-limit interruption."""

    staged: int
    fetched: int
    cached: int
    partial: bool
    partial_reason: str | None


@dataclass(frozen=True)
class FilteredParseSummary:
    """Counts and survivors produced by the layered parse filter."""

    accepted: tuple[ParsedEventbriteListing, ...]
    rejected_online: int
    rejected_no_location: int

    @property
    def rejected_total(self) -> int:
        return self.rejected_online + self.rejected_no_location

    @property
    def processed_total(self) -> int:
        return len(self.accepted) + self.rejected_total
