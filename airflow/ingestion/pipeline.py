"""Composable Eventbrite ingestion steps called by Airflow tasks and tests."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import timedelta

from ingestion.clock import Clock
from ingestion.database import IngestionRepository
from ingestion.eventbrite import (
    EventbriteListingClient,
    EventbriteRateLimited,
    EventDetailFetcher,
    parse_eventbrite_event,
    staging_identity,
)
from ingestion.models import (
    CrawlMarketTargets,
    CrawlTarget,
    EventbriteCrawlSummary,
    EventbriteDetailSummary,
    EventbriteEventReference,
    EventbriteSearchTarget,
    FetchedPage,
    FilteredParseSummary,
    ParsedEventbriteListing,
)
from ingestion.online_filter import apply_online_filter

LOGGER = logging.getLogger(__name__)


def group_crawl_targets_by_market(
    targets: Iterable[CrawlTarget],
) -> tuple[CrawlMarketTargets, ...]:
    """Group source-native queries into independently tracked canonical market runs."""
    grouped: dict[tuple[str, uuid.UUID], list[CrawlTarget]] = {}
    for target in targets:
        grouped.setdefault((target.source, target.market_id), []).append(target)
    return tuple(
        CrawlMarketTargets(
            source=source,
            market_id=market_id,
            market_slug=rows[0].market_slug,
            market_name=rows[0].market_name,
            targets=tuple(rows),
        )
        for (source, market_id), rows in grouped.items()
    )


def fetch_and_stage(
    *,
    pages: Iterable[FetchedPage],
    repository: IngestionRepository,
    run_id: uuid.UUID,
    clock: Clock,
) -> int:
    """Persist raw events page-by-page before any semantic parser sees them."""
    events_found = 0
    for page in pages:
        events_found += repository.record_and_stage_page(run_id=run_id, page=page, seen_at=clock())
    return events_found


def crawl_eventbrite_location(
    *,
    client: EventbriteListingClient,
    targets: Iterable[EventbriteSearchTarget],
    repository: IngestionRepository,
    run_id: uuid.UUID,
    clock: Clock,
) -> EventbriteCrawlSummary:
    """Record every tuple page and union exact event ids before detail fetching."""
    pages_fetched = 0
    listing_appearances = 0
    references: dict[str, EventbriteEventReference] = {}
    for target in targets:
        for page in client.iter_listing_pages(target):
            repository.record_page_fetch(run_id=run_id, page=page, fetched_at=clock())
            pages_fetched += 1
            listing_appearances += len(page.event_references)
            for reference in page.event_references:
                references.setdefault(reference.event_id, reference)
    return EventbriteCrawlSummary(
        pages_fetched=pages_fetched,
        listing_appearances=listing_appearances,
        event_references=tuple(references.values()),
    )


def fetch_and_stage_eventbrite_details(
    *,
    event_references: Iterable[EventbriteEventReference],
    fetcher: EventDetailFetcher,
    repository: IngestionRepository,
    run_id: uuid.UUID,
    clock: Clock,
    cache_ttl: timedelta,
) -> EventbriteDetailSummary:
    """Fetch or reuse one detail per deduplicated id, staging before semantic parsing."""
    staged = 0
    fetched = 0
    cached = 0
    partial_reason: str | None = None
    for reference in event_references:
        observed_at = clock()
        payload = repository.cached_event_detail(
            source_event_id=reference.event_id,
            checked_at=observed_at,
        )
        if payload is None:
            try:
                payload = fetcher.fetch_event_detail(reference.event_id)
            except EventbriteRateLimited as error:
                partial_reason = str(error)
                LOGGER.warning(
                    "Eventbrite detail crawl stopped after %s staged events: %s",
                    staged,
                    error,
                )
                break
            payload_id, _ = staging_identity(payload)
            if payload_id != reference.event_id:
                raise ValueError(
                    f"Eventbrite detail id {payload_id} did not match {reference.event_id}"
                )
            repository.cache_event_detail(
                source_event_id=reference.event_id,
                payload=payload,
                fetched_at=observed_at,
                ttl=cache_ttl,
            )
            fetched += 1
        else:
            cached += 1
        repository.stage_event_detail(run_id=run_id, payload=payload, seen_at=observed_at)
        staged += 1
    return EventbriteDetailSummary(
        staged=staged,
        fetched=fetched,
        cached=cached,
        partial=partial_reason is not None,
        partial_reason=partial_reason,
    )


def parse_staged(
    *, repository: IngestionRepository, run_id: uuid.UUID
) -> tuple[ParsedEventbriteListing, ...]:
    """Parse only payloads already committed to source_listing.raw_payload."""
    return tuple(parse_eventbrite_event(payload) for payload in repository.staged_payloads(run_id))


def parse_and_filter_staged(
    *, repository: IngestionRepository, run_id: uuid.UUID, clock: Clock
) -> FilteredParseSummary:
    """Parse staged payloads, persist every rejection, and return only in-person listings."""
    accepted: list[ParsedEventbriteListing] = []
    rejected_online = 0
    rejected_no_location = 0
    for payload in repository.staged_payloads(run_id):
        listing = parse_eventbrite_event(payload)
        decision = apply_online_filter(listing)
        if decision.keep:
            accepted.append(listing)
            continue
        if decision.reason is None or decision.rule is None:
            raise AssertionError("rejected listing must include a reason and rule")
        repository.reject_staged_listing(
            run_id=run_id,
            payload=payload,
            reason=decision.reason,
            rejected_at=clock(),
        )
        LOGGER.info(
            "Rejected Eventbrite listing %s via %s",
            listing.source_event_id,
            decision.rule,
        )
        if decision.reason == "online":
            rejected_online += 1
        else:
            rejected_no_location += 1
    return FilteredParseSummary(
        accepted=tuple(accepted),
        rejected_online=rejected_online,
        rejected_no_location=rejected_no_location,
    )


@contextmanager
def fail_run_on_error(
    *, repository: IngestionRepository, run_id: uuid.UUID, clock: Clock
) -> Iterator[None]:
    """Mark an opened run failed when an ingestion step raises."""
    try:
        yield
    except Exception as error:
        repository.mark_failed(run_id=run_id, error=error, finished_at=clock())
        raise
